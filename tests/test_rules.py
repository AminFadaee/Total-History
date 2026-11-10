"""The pure rules the pipeline applies to source text, each on the cases that once went wrong or easily could."""
import pathlib

import pytest

from page_in_history.births import Birth, agreed_birth
from page_in_history.dates import Span, centuries, parse, parse_range, period_label
from page_in_history.deck import CIVILIZATION_TEMPLATES, century_tag, with_modern_country
from page_in_history.figures import lifetime
from page_in_history.legacy import CIVILIZATION, LegacyScores, best_line, hide, legacy_sections, name_forms, repeats
from page_in_history.photos import visible
from page_in_history.polities import DATED, RulingRole, row_years, same_name
from page_in_history.sentences import contribution, first_paragraph, sentences
from page_in_history.sources.wikidata import YEAR_PRECISION, Entity, Year


@pytest.mark.parametrize("text, years", [
    ("1192–1199", (1192, 1199)), ("1192", (1192, 1192)), ("r. 1192-1199", (1192, 1199)), ("{{reign|1192|1199}}", (1192, 1199)),
    ("c. 550 – 530 BC", (-550, -530)), ("560 BCE", (-560, -560)), ("27 BC – AD 14", (-27, 14)), ("AD 14 – 37", (14, 37)),
    ("1199–1192", (1199, 1199)), ("165/155–130 BC", (-165, -155)), ("no years", None),
])
def test_row_years(text, years):
    assert row_years(text) == years


@pytest.mark.parametrize("first, second, same", [
    ("Qin", "Qin dynasty", True), ("Kingdom of Castile", "Crown of Castile", True),
    ("Kingdom of Naples", "Kingdom of Naples (Napoleonic)", True), ("Roman Republic", "Roman Empire", False),
    ("Kingdom of England", "Kingdom of Scotland", False), ("Qin dynasty", "Qi", False),
])
def test_same_name(first, second, same):
    assert same_name(first, second) == same


@pytest.mark.parametrize("role, title, place, reign", [
    ("Founder and Emperor of Han Dynasty of China from 202 to 195 BC", "emperor", "China", (-202, -195)),
    ("King of France from 1643 to 1715", "king", "France", (1643, 1715)),
])
def test_ruling_role(role, title, place, reign):
    read = RulingRole.read(role)
    assert (read.title, read.place, read.reign) == (title, place, reign)


@pytest.mark.parametrize("role", ["Queen consort of France", "French philosopher"])
def test_roles_that_are_not_rule(role):
    assert RulingRole.read(role) is None


def years(*values: int) -> list[Year]:
    return [Year(value, YEAR_PRECISION, False) for value in values]


@pytest.mark.parametrize("birth, death, span", [
    (years(1638), years(1715), (1638, 1715)), ([], years(453), (383, 453)), (years(1205), [], (1205, 1275)),
    (years(-499), years(453), (383, 453)), ([], [], None), (years(-250), years(-257), (-327, -257)),
])
def test_lifetime(birth, death, span):
    assert lifetime(birth, death) == span


@pytest.mark.parametrize("number, tag", [
    (-6, "PIH::Century::BC::06th"), (1, "PIH::Century::AD::01st"), (3, "PIH::Century::AD::03rd"), (11, "PIH::Century::AD::11th"),
    (12, "PIH::Century::AD::12th"), (13, "PIH::Century::AD::13th"), (21, "PIH::Century::AD::21st"),
])
def test_century_tag(number, tag):
    assert century_tag(number) == tag


def test_centuries_cross_the_era():
    assert centuries(-150, 150) == [-2, -1, 1, 2]


@pytest.mark.parametrize("text, label", [("1643", "1643"), ("202 BC", "202 BC")])
def test_parse_labels(text, label):
    assert parse(text).label() == label


@pytest.mark.parametrize("text, span", [
    ("370s", Span(370, 379)), ("c. 1430s", Span(1430, 1439, True)), ("370s BC", Span(-379, -370)),
    ("4 September 476", Span(476, 476)), ("April 13, 1375", Span(1375, 1375)),
])
def test_parse_decades_and_days(text, span):
    assert parse(text) == span


def test_period_label_across_the_era():
    start, end = parse_range("27 BC – AD 14")
    assert period_label(start, end) == "27 BC – AD 14"


def test_contribution_ends_at_a_stray_comma():
    paragraph = "Ibn Yunus (c. 950 – 1009) was an Arab and Egyptian astronomer and mathematician,. The Fatimids gave him gifts."
    assert contribution(paragraph, "Egyptian mathematician", None) == "An Arab and Egyptian astronomer and mathematician."


def test_contribution_spaces_words_once():
    paragraph = "Ugarit (/uːˈɡɑːrɪt/) was an ancient city in what is today northern Syria, at a site known in  Arabic as Ras Shamra."
    assert "  " not in contribution(paragraph, "Ugarit", None)


@pytest.mark.parametrize("paragraph, text", [
    ("The British Empire comprised the dominions, colonies and protectorates ruled by the United Kingdom.",
     "The dominions, colonies and protectorates ruled by the United Kingdom."),
    ("The New Kingdom, also called the Egyptian Empire, refers to ancient Egypt between the 16th century BC and the 11th "
     "century BC.", "Ancient Egypt between the 16th century BC and the 11th century BC."),
    ("The Dutch Republic, also referred to as the United Provinces, was a confederation and great power.", "A confederation and great power."),
])
def test_contribution_cuts_the_subject_at_its_verb(paragraph, text):
    assert contribution(paragraph, "", None) == text


@pytest.mark.parametrize("paragraph, text", [
    ("Shenouda III was the 117th Pope of Alexandria and Patriarch of the See of St. Mark. His papacy lasted 40 years.",
     "The 117th Pope of Alexandria and Patriarch of the See of St. Mark."),
    ("John was a monk. He taught in Damascus c. AD 675; he died in 749.", "He taught in Damascus c. AD 675; he died in 749."),
    ('Ibsen was a playwright. He is often referred to as "the father of modern drama". He pioneered realism.',
     'He is often referred to as "the father of modern drama".'),
    ('Yukawa was a physicist who received the Nobel Prize "for his prediction of mesons." He taught at Kyoto.',
     'A physicist who received the Nobel Prize "for his prediction of mesons."'),
])
def test_contribution_ends_the_sentence_where_it_ends(paragraph, text):
    assert contribution(paragraph, "Monk" if "monk" in paragraph else "", None) == text


def test_contribution_passes_over_a_sentence_left_hanging():
    paragraph = "Ambrósio I was a mwenekongo of the Kingdom of Kongo who ruled from (March 1626 to March 7, 1631.)"
    assert contribution(paragraph, "Manikongo", "Ruler of the Kingdom of Kongo") == "Ruler of the Kingdom of Kongo."


def test_contribution_takes_fewer_new_words_when_asked():
    paragraph = "The Grand Principality of Vladimir was a Russian principality."
    assert contribution(paragraph, "Principality of Rostov-Suzdal", None) == ""
    assert contribution(paragraph, "Principality of Rostov-Suzdal", None, 1) == "A Russian principality."


@pytest.mark.parametrize("extract, paragraph", [
    ("Anton Chekhov (; Russian: Антон Чехов;\n29 January 1860 – 15 July 1904) was a playwright.\nHe was a physician.",
     "Anton Chekhov (; Russian: Антон Чехов; 29 January 1860 – 15 July 1904) was a playwright."),
    ("Note: names are in simplified characters.\nWu Yun was a general.\nHe died in 484 BC.", "Wu Yun was a general."),
    ("\nUgarit was a city.\nIt was found in 1928.", "Ugarit was a city."),
])
def test_first_paragraph(extract, paragraph):
    assert first_paragraph(extract) == paragraph


@pytest.mark.parametrize("description, dated", [
    ("Ancient Mesoamerican city", True), ("Principality of Romania from 1330 to 1862", True),
    ("Historical Iranian region in Azerbaijan", True), ("Pre-Roman civilization of Etruria (9th–1st century BC)", True),
    ("Country in Northern Europe", False), ("Ceremonial county in England", False), ("Sovereign state", False),
])
def test_dated_descriptions(description, dated):
    assert bool(DATED.search(description)) == dated


def test_commons_credit_drops_hidden_copies():
    assert visible('Unknown author<span style="display: none;">Unknown author</span>') == "Unknown author"
    assert visible('<a href="x">Jane</a> &amp; Doe') == "Jane & Doe"


def test_contribution_skips_a_sentence_that_leans_on_the_one_before():
    paragraph = ("Edward I was King of England from 1272 to 1307. Concurrently, he was Lord of Ireland. "
                 "He conquered Wales and fought the Scots.")
    assert contribution(paragraph, "King of England from 1272 to 1307", None) == "He conquered Wales and fought the Scots."


def test_commons_credit_drops_footnote_marks():
    assert visible("Unknown author, restored by Khánh ([1])") == "Unknown author, restored by Khánh"
    assert visible("unknown; according to [1] and [2] a portrait") == "unknown"
    assert visible("derivative work: Snitty (<a>talk</a>) Maome.jpg: Unknown") == "derivative work: Snitty Unknown"
    assert visible("A.Skromnitsky (<a>A.Skromnitsky</a> (<a>talk</a>))") == "A.Skromnitsky"
    assert visible("User:-hax0r") == "-hax0r"
    assert visible("Creator:Wojciech Stefanowski") == "Wojciech Stefanowski"


@pytest.mark.parametrize("paragraph", [
    "Muhammad Shah was Mughal emperor from 1719 to 1748. He was son of Khujista Akhtar.",
    "Euric was King of the Visigoths from 466 to 484. Sometimes he is called Euric II.",
    "Umar Shaikh Mirza was ruler of Fergana from 1469 to 1494. His son was Babur.",
    "Igor was Prince of Kiev from 912 to 945. According to the Chronicle, Rurik was succeeded by Oleg.",
    "Orodes was King of Parthia from 57 to 38 BC. The two brothers quickly fell out.",
])
def test_contribution_skips_parentage_and_name_notes(paragraph):
    assert contribution(paragraph, paragraph.split(". ")[0].split(" was ")[1], None) == ""


def test_contribution_keeps_a_sentence_that_only_opens_with_family():
    paragraph = ("Abbas was Shah of Iran from 1588 to 1629. The third son of Shah Mohammad, he is considered the greatest "
                 "ruler of the Safavid dynasty.")
    assert contribution(paragraph, "Shah of Iran from 1588 to 1629", None).startswith("The third son")


def test_label_falls_back_to_the_multilingual_one():
    assert Entity({"id": "Q189145", "labels": {"mul": {"value": "Antonio López de Santa Anna"}}}).label == "Antonio López de Santa Anna"
    assert Entity({"id": "Q484", "labels": {"en": {"value": "Babur"}, "mul": {"value": "Bābur"}}}).label == "Babur"


def test_commons_credit_drops_file_names():
    assert visible("Honourable Bertrand Russell.jpg: Photographer not identified") == "Photographer not identified"
    assert visible("A_b.jpg: *A.jpg: Alex Ehrenzweig derivative work: RedAppleJack") == "Alex Ehrenzweig derivative work: RedAppleJack"


ULM, NEAR_ULM, MUMBAI, SHIVNERI = (9.99, 48.4), (10.2, 48.6), (72.88, 19.08), (73.86, 19.2)


@pytest.mark.parametrize("places, years, birth", [
    ([ULM, ULM, ULM], [1879, 1879, 1879], Birth(ULM, 1879)),
    ([None, NEAR_ULM, ULM], [None, 1879, 1880], Birth(NEAR_ULM, 1879)),
    ([MUMBAI, SHIVNERI, SHIVNERI], [1630, 1630, 1630], None),
    ([SHIVNERI, SHIVNERI, None], [1630, None, 1631], Birth(SHIVNERI, 1630)),
    ([MUMBAI, SHIVNERI, None], [1630, 1630, 1630], None),
    ([ULM, None, None], [1879, 1879, None], None),
    ([ULM, ULM, None], [1500, None, 1440], None),
])
def test_a_birth_needs_two_sources_on_place_and_year(places, years, birth):
    assert agreed_birth(places, years) == birth


@pytest.mark.parametrize("names, country, shown", [
    (["Ottoman Empire"], "Israel", ["Ottoman Empire (modern-day Israel)"]),
    (["Kingdom of Romania"], "Romania", ["Kingdom of Romania"]),
    (["Qing Dynasty", "Republic of China"], "China", ["Qing Dynasty (modern-day China)", "Republic of China"]),
    (["Han Dynasty"], "", ["Han Dynasty"]),
    ([], "China", []),
])
def test_modern_country_follows_the_first_state(names, country, shown):
    assert with_modern_country(names, {"modern_country": country}) == shown


@pytest.mark.parametrize("value, earliest", [(300, 201), (1240, 1201), (901, 901), (-600, -600), (-550, -600), (-1, -100)])
def test_a_century_is_the_one_holding_the_year(value, earliest):
    assert Year(value, YEAR_PRECISION - 2, False).earliest == earliest


@pytest.mark.parametrize("names, opening, sentence, hidden", [
    (["Alan Turing"], "Alan Mathison Turing (1912 – 1954) was an English mathematician.",
     "Alan Mathison Turing was widely regarded as the father of theoretical computer science",
     "This person was widely regarded as the father of theoretical computer science"),
    (["Wisława Szymborska"], "Maria Wisława Anna Szymborska (1923 – 2012) was a Polish poet.",
     "In Poland, Szymborska's books have reached sales rivaling prose authors",
     "In Poland, this person's books have reached sales rivaling prose authors"),
    (["Zhao Tuo"], "Zhao Tuo, also known as Emperor Wu and as Triệu Đà, was a Chinese general.",
     "Triệu Đà founded the kingdom of Nanyue", "This person founded the kingdom of Nanyue"),
    (["Catherine I of Russia"], "Catherine I (1684 – 1727) was Empress of Russia.",
     "Catherine I was Empress of Russia from 1725", "This person was Empress of Russia from 1725"),
    (["Emperor Wen of Chen"], "Emperor Wen of Chen (522–566) was an emperor.",
     "He was succeeded by his brother, the Emperor Xuan", "He was succeeded by his brother, the Emperor Xuan"),
    (["Francis II Rákóczi"], "Francis II Rákóczi (1676 – 1735) was a Hungarian nobleman.",
     "He led Rákóczi's War of Independence against the Habsburgs", None),
    (["Zoroaster"], "Zoroaster, also known as Zarathustra, was an Iranian reformer.",
     "Zoroaster composed hymns that cover core Zoroastrian beliefs", None),
    (["Tokugawa Yoshinobu"], "Prince Tokugawa Yoshinobu (1837 – 1913) was a Japanese samurai.",
     "Yoshinobu was the 15th and last shōgun of the Tokugawa shogunate", None),
    (["Olav V"], "Olav V (1903 – 1991) was King of Norway.",
     "King Olav's leadership made him a symbol of Norwegian independence",
     "This person's leadership made him a symbol of Norwegian independence"),
])
def test_a_legacy_line_hides_every_part_of_the_name(names, opening, sentence, hidden):
    assert hide(sentence, name_forms(names, opening)) == hidden


def test_legacy_sections_are_found_by_heading():
    text = "Intro.\n\n== Early life ==\nBorn in Ulm.\n\n== Legacy ==\nHe founded modern physics.\n\n== Notes ==\nx"
    assert legacy_sections(text).strip() == "He founded modern physics."


class FixedScores(LegacyScores):
    def __init__(self, scores: dict[str, int]):
        super().__init__(pathlib.Path("unused"))
        self.fixed = scores

    def scores(self, subject, name, role, sentences):
        return [self.fixed.get(sentence, 0) for sentence in sentences]


def test_a_sentence_ends_after_a_single_quote():
    assert sentences("The caliphs were 'rightly guided'. Endowed with piety, their era is a golden age.") == [
        "The caliphs were 'rightly guided'", "Endowed with piety, their era is a golden age."]


def test_a_legacy_line_that_repeats_the_summary_is_not_used():
    summary = "The largest contiguous empire in history."
    assert repeats("The Mongol Empire was the largest contiguous empire in history", summary)
    assert not repeats("It connected East and West in a Pax Mongolica, allowing the exchange of trade and ideas", summary)


def test_a_civilization_legacy_line_stands_on_its_own_and_scores_seven():
    text = ("The empire was the largest in history. However, it spread its laws across the region. "
            "It spread its language, laws, and customs. Its rulers fought many wars with neighbours.")
    scores = FixedScores({"The empire was the largest in history": 9, "However, it spread its laws across the region": 9,
                          "It spread its language, laws, and customs": 7, "Its rulers fought many wars with neighbours.": 6})
    assert best_line(scores, CIVILIZATION, "Mali Empire", "civilization", text, summary="The largest empire in history.") \
        == "It spread its language, laws, and customs."
    assert best_line(scores, CIVILIZATION, "Mali Empire", "civilization", "Its rulers fought many wars with neighbours.") == ""


def test_every_civilization_card_back_shows_the_legacy_line():
    assert all("{{Legacy}}" in template.back for template in CIVILIZATION_TEMPLATES)


def test_a_civilization_legacy_line_does_not_point_back_and_is_tidied():
    text = ("From then on the latter also shaped the language of the court. "
            "It was the legal predecessor of  the present-day Federal Republic of Germany,")
    scores = FixedScores({"From then on the latter also shaped the language of the court": 9,
                          "It was the legal predecessor of  the present-day Federal Republic of Germany,": 8})
    assert best_line(scores, CIVILIZATION, "Brandenburg", "civilization", text) \
        == "It was the legal predecessor of the present-day Federal Republic of Germany."
