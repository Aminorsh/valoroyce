import pytest

from valoroyce.glossary import Glossary, fold_kana


@pytest.fixture(scope="module")
def g():
    return Glossary.load()


def zh(g, text):
    return {h.source: h.zh for h in g.find(text)}


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
    assert hits == {"CT spawn": "CT"}


def test_ct_heard_as_city(g):
    # "CT" said aloud sounds like "city" and is written that way.
    assert g.correct("he is city") == "he is CT"
    assert g.correct("シティにいる") == "シーティーにいる"
    assert zh(g, g.correct("シティーに一人")) == {"シーティー": "CT"}
    assert g.correct("キャパシティ") == "キャパシティ"
    assert g.outgoing("人在警家", "en") == ("人在CT", [("CT", "CT")])


def test_their_home_means_the_enemy_spawn(g):
    # Typed Chinese: "他们家" is the enemy's spawn, not somebody's house.
    assert g.outgoing("一个在他们家", "en") == ("一个在敌方重生点", [("敌方重生点", "enemy spawn")])
    assert g.outgoing("一个在他们家", "ja") == ("一个在敌方重生点", [("敌方重生点", "敵スポーン")])
    assert zh(g, "敵スポーンに一人") == {"敵スポーン": "敌方重生点"}


def test_low_health_as_players_say_it(g):
    assert g.outgoing("捷风大残", "en") == ("捷风残血", [("捷风", "Jett"), ("残血", "low")])
    assert g.outgoing("A大残了一个", "ja")[0] == "A残血了一个"


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


def test_site_letter_stays_with_the_place(g):
    assert zh(g, "Aメイン二人、Bリンクに一人") == {"Aメイン": "A主道", "Bリンク": "B连接"}
    assert zh(g, "push A main")["A main"] == "A主道"
    assert zh(g, "メインから来る") == {"メイン": "主道"}
    # Not a site letter: part of a word, or in front of a name.
    assert "main" in zh(g, "data main")
    assert zh(g, "Bジェット") == {"ジェット": "捷风"}


def out(g, text, lang):
    text, pairs = g.outgoing(text, lang)
    return text, dict(pairs)


def test_outgoing_terms_use_the_names_of_the_target_language(g):
    assert g.outgoing("壹决残血", "ja") == ("壹决残血", [("壹决", "アイソ"), ("残血", "ロー")])
    assert g.outgoing("壹决残血", "en") == ("壹决残血", [("壹决", "Iso"), ("残血", "low")])
    assert g.outgoing("你好", "ja") == ("你好", [])


def test_outgoing_site_letter_travels_with_the_place(g):
    assert out(g, "人在B长廊", "ja") == ("人在B长廊", {"B长廊": "Bロング"})
    assert out(g, "人在B长廊", "en") == ("人在B长廊", {"B长廊": "B long"})
    # The longer term wins over the one inside it.
    assert out(g, "零在高台下", "en") == ("零在高台下", {"高台下": "hell", "零": "Cypher"})


def test_outgoing_nicknames_and_shorthand_are_spelled_out(g):
    assert out(g, "保安在B点，奶妈复活我", "ja") == (
        "零在B包点，贤者复活我", {"零": "サイファー", "B包点": "Bサイト", "贤者": "セージ"})
    assert out(g, "我去A大", "en") == ("我去A主道", {"A主道": "A main"})
    # 大 is the ultimate after 有/开/..., the main path after a site letter.
    assert out(g, "对面奶妈有大", "en") == ("对面贤者有大招", {"贤者": "Sage", "大招": "ult"})
    assert out(g, "保安交大了，A大可以压", "ja")[0] == "零交大招了，A主道可以压"
    # On its own it is just a character.
    assert out(g, "大家好", "en") == ("大家好", {})


def test_nicknames_never_reach_captions(g):
    # Incoming speech is always shown with the official name.
    assert zh(g, "Cypher and Sage") == {"Cypher": "零", "Sage": "贤者"}
    assert all("保安" not in h.zh and "奶妈" not in h.zh for h in g.find("サイファーとセージ"))


def test_outgoing_weapon_and_agent_nicknames(g):
    assert out(g, "给我发把ak", "en") == ("给我发把狂徒", {"狂徒": "Vandal"})
    assert out(g, "对面有大狙，M4打不过", "ja") == (
        "对面有冥驹，幻影打不过", {"冥驹": "オペレーター", "幻影": "ファントム"})
    assert out(g, "鸟狙给捷踢", "en")[0] == "飞将给捷风"
    assert out(g, "ko闪一下，鸡妈妈守B点", "en")[0] == "K/O闪一下，奇乐守B包点"
    # Not inside a longer Latin word.
    assert out(g, "他很weak", "en") == ("他很weak", {})


@pytest.mark.parametrize("said, written", [
    ("A包", "爆能器已安放在A点"),
    ("A 包", "爆能器已安放在A点"),
    ("A点下包了", "爆能器已安放在A点"),
    ("B包了快回防", "爆能器已安放在B点，快回防"),
    ("A点两个人", "A点有两个敌人"),
    ("A点两个", "A点有两个敌人"),
    ("A大两个", "A大有两个敌人"),
    ("中路三个", "中路有三个敌人"),
    ("中路一把狙", "中路有一个狙击手"),
    ("对面有狙", "对面有一个狙击手"),
    ("中路大狙", "中路有大狙"),
    ("暮蝶在中", "暮蝶在中路"),
    ("中路暮蝶", "暮蝶在中路"),
    # not shorthand: left as it is
    ("我们两个人去A", "我们两个人去A"),
    ("A包点有人", "A包点有人"),
    ("下包了", "下包了"),
    ("A点两个敌人", "A点两个敌人"),
])
def test_shorthand_callouts_are_written_out(said, written):
    assert Glossary.load().shorthand(said) == written


def test_written_out_shorthand_still_gets_its_names():
    text, pairs = Glossary.load().outgoing("A大两个，中路大狙", "en")
    assert text == "A主道有两个敌人，中路有冥驹"
    assert ("A主道", "A main") in pairs and ("冥驹", "Operator") in pairs
