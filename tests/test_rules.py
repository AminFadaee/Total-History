"""The pure rules the pipeline applies to source text, each on the cases that once went wrong or easily could."""
import pytest

from page_in_history.dates import centuries, parse, parse_range, period_label
from page_in_history.deck import century_tag
from page_in_history.figures import lifetime
from page_in_history.polities import RulingRole, row_years, same_name
from page_in_history.sources.wikidata import YEAR_PRECISION, Year


@pytest.mark.parametrize("text, years", [
    ("1192–1199", (1192, 1199)), ("1192", (1192, 1192)), ("r. 1192-1199", (1192, 1199)), ("{{reign|1192|1199}}", (1192, 1199)),
    ("c. 550 – 530 BC", (-550, -530)), ("560 BCE", (-560, -560)), ("27 BC – AD 14", (-27, 14)), ("AD 14 – 37", (14, 37)),
    ("1199–1192", (1199, 1199)), ("no years", None),
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
