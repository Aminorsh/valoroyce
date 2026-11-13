import pytest

from valoroyce.glossary import Glossary, fold_kana


@pytest.fixture(scope="module")
def g():
    return Glossary.load()


def zh(g, text):
    return {h.source: h.term.zh for h in g.find(text)}


def test_builtin_names_are_unique(g):
    seen = {}
    for t in g.terms:
        for s in t.spellings:
            assert s.lower() not in seen, f"{s!r} is listed under {seen[s.lower()]} and {t.zh}"
            seen[s.lower()] = t.zh


def test_finds_the_users_examples(g):
    assert zh(g, "Iso and Clove push B")["Iso"] == "壹决"
    assert zh(g, "アイソとクローヴ")=={"アイソ": "壹决", "クローヴ": "暮蝶"}


def test_b_v_spelling_variants_snap_to_canonical(g):
    assert g.correct("バイパーのウルト、バンダル買って") == "ヴァイパーのウルト、ヴァンダル買って"
    assert g.correct("クローブ") == "クローヴ"
    assert fold_kana("ソーヴァ") == fold_kana("ソーバ")


def test_one_character_slips_on_long_names(g):
    assert g.correct("キルジョイ") == "キルジョイ"
    assert g.correct("キルジョン") == "キルジョイ"
    assert g.correct("Kiljoy has ult") == "Killjoy has ult"
    assert g.correct("Vandel") == "Vandal"


def test_ordinary_words_are_left_alone(g):
    # "heaven" is a callout of its own and must not turn into the map Haven.
    assert g.correct("he is heaven") == "he is heaven"
    assert g.correct("スカイプで話そう") == "スカイプで話そう"
    assert "スカイ" not in zh(g, "スカイプで話そう")
    assert g.correct("nice shot") == "nice shot"


def test_known_mishearings(g):
    assert g.correct("愛想がいる") == "アイソがいる"
    assert g.correct("Jet is low") == "Jett is low"


def test_latin_matching_is_case_insensitive_and_whole_word(g):
    assert zh(g, "JETT dashed") == {"JETT": "捷风"}
    assert zh(g, "jettison the plan") == {}


def test_longer_spelling_wins(g):
    hits = zh(g, "they are CT spawn")
    assert hits == {"CT spawn": "警家"}


def test_enforce_replaces_untranslated_names_only(g):
    hits = g.find("Jett is low")
    assert g.enforce("Jett 残血了", hits) == "捷风 残血了"
    assert g.enforce("捷风残血了", hits) == "捷风残血了"
    # Callouts are hints: the translator's wording stands.
    assert g.enforce("捷风血很少", hits) == "捷风血很少"


def test_substitute(g):
    assert g.substitute("アイソがロングにいる") == "壹决が长廊にいる"


def test_extra_file_overrides_builtin(tmp_path):
    f = tmp_path / "mine.toml"
    f.write_text('terms = [{ zh = "壹决", kind = "agent", en = ["Iso"], ja = ["イソ"] },'
                 ' { zh = "牛", kind = "callout", ja = ["つよい"] }]', encoding="utf-8")
    g = Glossary.load([str(f)])
    assert zh(g, "イソつよい") == {"イソ": "壹决", "つよい": "牛"}
    assert zh(g, "アイソ") == {}


def test_newest_map(g):
    assert zh(g, "Summit B site")["Summit"] == "天枢云阙"
