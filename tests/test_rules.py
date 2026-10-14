"""The pure rules the pipeline applies to source text, each on the cases that once went wrong or easily could."""
import pytest

from page_in_history.dates import centuries, parse, parse_range, period_label
from page_in_history.deck import century_tag
from page_in_history.figures import lifetime
from page_in_history.polities import DATED, RulingRole, row_years, same_name
from page_in_history.sentences import contribution, first_paragraph
from page_in_history.sources.wikidata import YEAR_PRECISION, Year


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
    (years(-499), years(453), (383, 453)), ([], [], None),
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


def test_period_label_across_the_era():
    start, end = parse_range("27 BC – AD 14")
    assert period_label(start, end) == "27 BC – AD 14"


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
    ("John was a monk. He was born in Damascus c. AD 675; he died in 749.", "He was born in Damascus c. AD 675; he died in 749."),
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
