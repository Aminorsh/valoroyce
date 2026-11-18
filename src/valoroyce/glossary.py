"""VALORANT terminology: fix what the recogniser misheard, pin what the
translator must say.

Three jobs, in pipeline order:

1. `correct()`  rewrites near-miss transcriptions to the canonical spelling
   ("バイパー" -> "ヴァイパー", "Vandel" -> "Vandal").
2. `find()`     lists the terms present in a sentence, so the translator gets
   a short "X means Y" list instead of the whole glossary.
3. `enforce()`  replaces any name the translator left untranslated.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

NAME_KINDS = {"agent", "weapon", "map"}

_KATAKANA_RUN = re.compile(r"[ァ-ヺー]+")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z/'.]*[A-Za-z]|[A-Za-z]")
_HAS_LATIN = re.compile(r"[A-Za-z]")

# Spelling variation that carries no meaning in katakana loanwords.
_KANA_FOLD = [
    ("ヴァ", "バ"), ("ヴィ", "ビ"), ("ヴェ", "ベ"), ("ヴォ", "ボ"), ("ヴ", "ブ"),
    ("ー", ""),
]


def fold_kana(s: str) -> str:
    for a, b in _KANA_FOLD:
        s = s.replace(a, b)
    return s


def _edit_distance_le1(a: str, b: str) -> bool:
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    if la == lb:
        return a[i + 1:] == b[i + 1:]
    return a[i:] == b[i + 1:]


@dataclass
class Term:
    zh: str
    kind: str = "callout"
    en: list[str] = field(default_factory=list)
    ja: list[str] = field(default_factory=list)
    mishear: list[str] = field(default_factory=list)
    # Other ways players write it in Chinese (nicknames, shorthand), used only
    # when translating outgoing messages. A one-character alias counts only
    # after a site letter ("A大") or after a bracketed lead-in ("(有)大").
    zh_alias: list[str] = field(default_factory=list)

    @property
    def forced(self) -> bool:
        return self.kind in NAME_KINDS

    @property
    def spellings(self) -> list[str]:
        return [*self.ja, *self.en]


@dataclass(frozen=True)
class Hit:
    source: str  # the spelling as it appears in the sentence
    term: Term
    site: str = ""  # "A" when the sentence says "Aメイン" / "A main"

    @property
    def zh(self) -> str:
        return self.site + self.term.zh


# A site letter directly before a place word belongs to it: without this the
# translator is told "メイン = 主道" and drops the "A" from "Aメイン".
_SITE = re.compile(r"(?<![A-Za-z])([A-Ca-c]) ?$")
# "(有)大": the alias 大, valid only right after 有.
_LEAD_IN = re.compile(r"\((.+)\)(.+)")
# Where someone can be, as players write it (with or without a site letter).
_PLACES = ["中路", "长廊", "短道", "小道", "高台", "高台下", "主道", "大道", "大厅", "连接", "窗口",
           "匪家", "包点", "点", "区"]
_CLAUSE_END = r"$|[\s，。,.！!？?、；;]"


def _latin_pattern(spelling: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z]){re.escape(spelling)}(?![A-Za-z])", re.IGNORECASE)


def _kana_pattern(spelling: str) -> re.Pattern[str]:
    # A katakana term must not be glued to more katakana ("スカイ" in "スカイプ").
    if _KATAKANA_RUN.fullmatch(spelling):
        return re.compile(rf"(?<![ァ-ヺー]){re.escape(spelling)}(?![ァ-ヺー])")
    return re.compile(re.escape(spelling))


def _pattern(spelling: str) -> re.Pattern[str]:
    return _latin_pattern(spelling) if _HAS_LATIN.search(spelling) else _kana_pattern(spelling)


class Glossary:
    def __init__(self, terms: list[Term], fuzzy: bool = True):
        self.terms = terms
        self.fuzzy = fuzzy
        # (pattern, replacement) for known wrong transcriptions.
        self._mishear: list[tuple[re.Pattern[str], str]] = []
        # (pattern, spelling, term), longest spelling first so "CT spawn" wins over "CT".
        self._spellings: list[tuple[re.Pattern[str], str, Term]] = []
        # Fuzzy targets: names only, to keep false positives away from normal speech.
        self._kana_names: dict[str, str] = {}   # folded katakana -> canonical
        self._latin_names: dict[str, str] = {}  # lowercase -> canonical
        # Words that already mean something here ("heaven" must not become "Haven").
        self._latin_known: set[str] = set()

        for t in terms:
            canonical = (t.ja or t.en or [t.zh])[0]
            for wrong in t.mishear:
                target = canonical
                if _HAS_LATIN.search(wrong) and t.en:
                    target = t.en[0]
                self._mishear.append((_pattern(wrong), target))
            for s in t.spellings:
                self._spellings.append((_pattern(s), s, t))
                self._latin_known.update(w.lower() for w in _LATIN_WORD.findall(s))
            if t.forced:
                for s in t.ja:
                    if _KATAKANA_RUN.fullmatch(s):
                        self._kana_names[fold_kana(s)] = s
                for s in t.en:
                    self._latin_names[s.lower()] = s
        self._spellings.sort(key=lambda x: -len(x[1]))

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, extra: list[str] | None = None, fuzzy: bool = True) -> Glossary:
        builtin = resources.files("valoroyce.data").joinpath("valorant.toml").read_bytes()
        by_zh: dict[str, Term] = {}
        for raw in tomllib.loads(builtin.decode("utf-8"))["terms"]:
            by_zh[raw["zh"]] = Term(**raw)
        for path in extra or []:
            with open(Path(path).expanduser(), "rb") as f:
                for raw in tomllib.load(f).get("terms", []):
                    by_zh[raw["zh"]] = Term(**raw)
        return cls(list(by_zh.values()), fuzzy=fuzzy)

    # -- 1. fix the transcription -------------------------------------------

    def correct(self, text: str) -> str:
        for pat, target in self._mishear:
            text = pat.sub(target, text)
        if not self.fuzzy:
            return text
        text = _KATAKANA_RUN.sub(self._snap_kana, text)
        text = _LATIN_WORD.sub(self._snap_latin, text)
        return text

    def _snap_kana(self, m: re.Match[str]) -> str:
        run = m.group(0)
        folded = fold_kana(run)
        exact = self._kana_names.get(folded)
        if exact:
            return exact
        # One-character slips, only on words long enough to be unambiguous.
        if len(folded) >= 5:
            for known, canonical in self._kana_names.items():
                if len(known) >= 5 and _edit_distance_le1(folded, known):
                    return canonical
        return run

    def _snap_latin(self, m: re.Match[str]) -> str:
        word = m.group(0)
        low = word.lower()
        if low in self._latin_known or len(low) < 6:
            return word
        for known, canonical in self._latin_names.items():
            if len(known) >= 6 and _edit_distance_le1(low, known):
                return canonical
        return word

    # -- 2. what is in this sentence ----------------------------------------

    def find(self, text: str) -> list[Hit]:
        hits: list[Hit] = []
        taken: list[tuple[int, int]] = []
        seen: set[str] = set()
        for pat, _spelling, term in self._spellings:
            for m in pat.finditer(text):
                span = m.span()
                if any(span[0] < e and s < span[1] for s, e in taken):
                    continue
                taken.append(span)
                source, site = m.group(0), ""
                if not term.forced and (pre := _SITE.search(text, 0, span[0])):
                    source, site = text[pre.start():span[1]], pre.group(1).upper()
                if source.lower() not in seen:
                    seen.add(source.lower())
                    hits.append(Hit(source, term, site))
        return hits

    # -- 3. make sure names came out in Chinese ------------------------------

    def enforce(self, translation: str, hits: list[Hit]) -> str:
        for hit in hits:
            if not hit.term.forced or hit.term.zh in translation:
                continue
            for s in sorted(hit.term.spellings, key=len, reverse=True):
                translation, n = _pattern(s).subn(hit.term.zh, translation)
                if n:
                    break
        return translation

    # -- the other direction: Chinese the player typed -> ja / en -------------

    def shorthand(self, text: str) -> str:
        """Callouts as players type or say them, written out in full so that a
        small translation model gets them right:

            A包 / B包了 / A点下包了   -> 爆能器已安放在A点
            A点两个(人) / A大两个      -> A点有两个敌人 / A大有两个敌人
            中路一把狙 / 对面有狙      -> 中路有一个狙击手 / 对面有一个狙击手
            暮蝶在中                  -> 暮蝶在中路
            中路暮蝶                  -> 暮蝶在中路

        Nicknames (A大, 保安 ...) are spelled out afterwards, by `outgoing`.
        """
        rules = getattr(self, "_shorthand_rules", None)
        if rules is None:
            agents = sorted({zh for t in self.terms if t.kind == "agent" for zh in (t.zh, *t.zh_alias)
                             if len(zh) >= 2}, key=len, reverse=True)
            places = "|".join(map(re.escape, sorted(_PLACES, key=len, reverse=True)))
            place = rf"(?:(?<![A-Za-z])[A-Ca-c] ?(?:{places}|大|小)?|{places})"
            agent = "|".join(map(re.escape, agents))
            weapon = "|".join(map(re.escape, sorted(
                {zh for t in self.terms if t.kind == "weapon" for zh in (t.zh, *t.zh_alias)
                 if len(zh) >= 2 and _all_han(zh)}, key=len, reverse=True)))
            rules = self._shorthand_rules = [
                # plant: the site letter, perhaps 点, perhaps 下, then 包; not 包点
                (re.compile(r"(?<![A-Za-z])([A-Ca-c]) ?(?:点|包点)? ?(?:已经|已)?下?包了?(?!点)"),
                 lambda m: f"爆能器已安放在{m.group(1).upper()}点"
                           + ("，" if _all_han(m.string[m.end():m.end() + 1]) else "")),
                # how many are there: a place, then a number of people
                (re.compile(rf"({place}) ?有?([一两二三四五1-5])个人?(?!敌)(?={_CLAUSE_END})"),
                 lambda m: f"{m.group(1)}有{m.group(2)}个敌人"),
                (re.compile(r"(?:一把|有)狙(?![击])"), lambda m: "有一个狙击手"),
                # a place, then a gun: "中路大狙" is "中路有大狙"
                (re.compile(rf"({place}) ?({weapon})(?={_CLAUSE_END})"), lambda m: f"{m.group(1)}有{m.group(2)}"),
                (re.compile(rf"在中(?={_CLAUSE_END})"), lambda m: "在中路"),
                # a place, then who: "中路暮蝶" is "暮蝶在中路"
                (re.compile(rf"(?:(?<=^)|(?<=[\s，。,！!？?、；;]))({place}) ?({agent})(?={_CLAUSE_END})"),
                 lambda m: f"{m.group(2)}在{m.group(1)}"),
            ] if agent and weapon else []
        for pattern, write in rules:
            text = pattern.sub(write, text)
        return text

    def outgoing(self, text: str, lang: str) -> tuple[str, list[tuple[str, str]]]:
        """Prepare typed Chinese for translation into `lang`.

        Returns the text with nicknames and shorthand spelled out as the
        official term ("保安有大" -> "零有大招"), and the (Chinese term, name in
        `lang`) pairs for the translator. Spelling shorthand out works better
        than hinting it: a model told that "大" means "ult" mostly ignores it.
        """
        text = self.shorthand(text)
        written = [(zh, t) for t in self.terms for zh in (t.zh, *t.zh_alias)]
        found: list[tuple[int, int, Term, str]] = []  # replace [start, end) with term.zh
        folded = text.lower()  # same length as text: aliases like "AK" match "ak"
        taken: list[tuple[int, int]] = []
        # Longest first, measured without the bracketed lead-in: "大狙" must
        # be tried before the 大 of "(有)大".
        def split(alias: str) -> tuple[str, str]:
            m = _LEAD_IN.fullmatch(alias)
            return (m.group(1), m.group(2)) if m else ("", alias)

        for (lead, zh), term in sorted(((split(w), t) for w, t in written),
                                       key=lambda w: -len(w[0][1])):
            pos = 0
            while (at := folded.find((lead + zh).lower(), pos)) >= 0:
                pos = at + 1
                start, end = at + len(lead), at + len(lead) + len(zh)
                if zh.isascii() and (text[max(start - 1, 0):start].isascii() and text[max(start - 1, 0):start].isalnum()
                                     or text[end:end + 1].isascii() and text[end:end + 1].isalnum()):
                    continue  # "ak" inside a longer Latin word
                if any(start < e and s < end for s, e in taken):
                    continue  # inside a longer term: "高台" in "高台下"
                site = None if term.forced or lead else _SITE.search(text, 0, start)
                if len(zh) == 1 and zh != term.zh and not (lead or site):
                    continue
                taken.append((start, end))
                # A site letter travels with the place: "B长廊" -> "Bロング" / "B long".
                letter = site.group(1).upper() if site else ""
                found.append((site.end(1) if site else start, end, term, letter))
        pairs: dict[str, str] = {}
        for start, end, term, letter in sorted(found, key=lambda f: -f[0]):
            names = (term.ja or term.en) if lang == "ja" else (term.en or term.ja)
            text = text[:start] + term.zh + text[end:]
            if names:
                gap = " " if letter and lang != "ja" else ""
                pairs.setdefault(letter + term.zh, letter + gap + names[0])
        return text, list(reversed(pairs.items()))

    def substitute(self, text: str) -> str:
        """Glossary-only 'translation': swap every known term for its Chinese."""
        hits = self.find(text)
        for hit in sorted(hits, key=lambda h: -len(h.source)):
            text = text.replace(hit.source, hit.zh)
        return text

    def spoken_zh(self, text: str) -> str:
        """Chinese the player said, as the recogniser wrote it, with names put
        right: a run of characters that sounds exactly like a name or a
        callout, tones included, is replaced by it (目蝶 / 木蝶 -> 暮蝶,
        一觉 -> 壹决, 回房 -> 回防). Each written character may stand for any
        of its readings; tones keep ordinary words apart (起了 qǐ le is not
        奇乐 qí lè). One-character terms are left alone. Without pypinyin, the
        text is returned as is."""
        try:
            from pypinyin import Style, lazy_pinyin, pinyin
        except ImportError:
            return text
        names = getattr(self, "_spoken_names", None)
        if names is None:
            # Nicknames too (大狙 is said, not 冥驹): written as said, spelled
            # out later by `outgoing`.
            written = {zh for t in self.terms for zh in (t.zh, *t.zh_alias) if len(zh) >= 2 and _all_han(zh)}
            names = self._spoken_names = sorted(
                ((tuple(lazy_pinyin(zh, style=Style.TONE3, neutral_tone_with_five=True)), zh) for zh in written),
                key=lambda pair: -len(pair[0]))
        chars = list(text)
        readings = [set(r) if _all_han(c) else set()
                    for c, r in zip(chars, pinyin(chars, style=Style.TONE3, heteronym=True,
                                                  neutral_tone_with_five=True))]
        i, out = 0, []
        while i < len(chars):
            for sound, name in names:
                n = len(sound)
                if i + n <= len(chars) and all(sound[k] in readings[i + k] for k in range(n)):
                    out.append(name)
                    i += n
                    break
            else:
                out.append(chars[i])
                i += 1
        return "".join(out)


def _all_han(text: str) -> bool:
    return bool(text) and all("一" <= c <= "鿿" or "㐀" <= c <= "䶿" for c in text)
