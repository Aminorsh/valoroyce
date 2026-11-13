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
                if m.group(0).lower() not in seen:
                    seen.add(m.group(0).lower())
                    hits.append(Hit(m.group(0), term))
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

    def substitute(self, text: str) -> str:
        """Glossary-only 'translation': swap every known term for its Chinese."""
        hits = self.find(text)
        for hit in sorted(hits, key=lambda h: -len(h.source)):
            text = _pattern(hit.source).sub(hit.term.zh, text)
        return text
