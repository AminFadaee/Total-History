"""The pure rules that choose a civilization's conflicts, on the infobox layouts that once went wrong."""
import pytest

from total_history.conflict_overrides import load_conflict_overrides
from total_history.conflicts import Candidate, Civilization, Conflict, ConflictCollector, Evidence, Infobox

FALL_OF_CONSTANTINOPLE = """{{Infobox military conflict
|combatant1={{flag|Ottoman Empire|1453}}
|combatant2={{plainlist}} *{{flagicon|Byzantine Empire}} [[Byzantine Empire under the Palaiologos dynasty|Byzantine Empire]] *{{flag|Republic of Genoa}}
|result=Ottoman victory<br>[[Byzantine Empire|Fall of the Byzantine Empire]]
}}"""

SUPPORTED = """{{Infobox military conflict
|combatant1=[[Derg]]<br>'''Supported by:'''<br>[[Soviet Union]]<br>[[Cuba]]
|combatant2=[[Tigray People's Liberation Front|TPLF]]
}}"""


def test_infobox_reads_flagicons_and_result_lines():
    sides, results = ConflictCollector._infobox_links(FALL_OF_CONSTANTINOPLE)
    assert "Ottoman Empire" in sides["1"]
    assert {"Byzantine Empire", "Byzantine Empire under the Palaiologos dynasty", "Republic of Genoa"} <= set(sides["2"])
    assert (["Byzantine Empire"], "Fall of the Byzantine Empire") in results


def test_supporters_are_not_combatants():
    sides, _ = ConflictCollector._infobox_links(SUPPORTED)
    assert sides["1"] == ["Derg"]


def civilization(start, end):
    return Civilization("Q1", "Test", "Test", "Test", "Core", start, end, set(), {"Q1"})


def conflict(start, end):
    return Conflict("Q2", "War", "War", start, end, 10, Infobox(), set(), set(), False)


@pytest.mark.parametrize("period, years, during", [
    ((1922, 1991), (1917, 1922), False),   # the USSR and the Russian Civil War meet only in 1922
    ((1035, 1715), (722, 1492), True),     # Castile joined the Reconquista long after it began
    ((-1650, -1180), (-1274, -1274), True),  # a battle of a single year, Kadesh
    ((1836, 1846), (1835, 1836), False),   # the Texas Revolution founded the republic
    ((1900, None), (2003, 2011), True),    # a state that still exists
])
def test_during(period, years, during):
    assert civilization(*period).during(conflict(*years)) is during


@pytest.mark.parametrize("period, years, founded", [
    ((1923, None), (1919, 1923), True),     # the Turkish War of Independence
    ((1836, 1846), (1835, 1836), True),     # the Texas Revolution
    ((1099, 1291), (1101, 1101), False),    # a battle two years after Jerusalem was founded
    ((1918, 1933), (1914, 1916), False),    # over before the state began
])
def test_founded_by(period, years, founded):
    assert civilization(*period).founded_by(conflict(*years)) is founded


def named(qid, name, start, end):
    return Conflict(qid, name, name, start, end, 10, Infobox(), set(), set(), True)


def test_titles_tell_apart_only_shared_names():
    titles = ConflictCollector._titles([named("Q1", "Siege of Jerusalem", -589, -587),
                                        named("Q2", "Siege of Jerusalem", -63, -63), named("Q3", "Battle of Hastings", 1066, 1066)])
    assert titles == {"Q1": "Siege of Jerusalem (589–587 BC)", "Q2": "Siege of Jerusalem (63 BC)", "Q3": "Battle of Hastings"}


def test_central_avoids_the_ending_and_its_battles():
    ending = Candidate(named("Q10", "Conquest", 1519, 1521), {Evidence.INFOBOX}, True, True, True)
    battle = Candidate(named("Q11", "Siege", 1521, 1521), {Evidence.INFOBOX}, True, True, True)
    other = Candidate(named("Q12", "Flower War", 1454, 1519), {Evidence.INFOBOX}, False, True, True)
    state = civilization(1428, 1521)
    state.candidates = [ending, battle, other]
    ancestors = {"Q11": {"Q10"}}
    assert ConflictCollector._central(state, {}, {"Q10"}, ancestors) is other
    state.candidates = [ending, battle]
    assert ConflictCollector._central(state, {}, {"Q10"}, ancestors) is ending


def test_overrides_give_their_grounds(tmp_path):
    path = tmp_path / "conflict_overrides.toml"
    path.write_text('[[conflict]]\ncivilization = "Netherlands"\ncivilization_id = "Q55"\nconflict = "Kosovo War"\n'
                    'conflict_id = "Q83286"\nrole = "second"\naction = "remove"\n')
    with pytest.raises(ValueError, match="reason in the note"):
        load_conflict_overrides(path)
    path.write_text(path.read_text() + 'note = "A NATO air campaign the Netherlands joined, not a war of its own."\n')
    assert load_conflict_overrides(path)[0].action == "remove"


@pytest.mark.parametrize("date, years", [
    ("28 July 1914{{snd}}11 November 1918<br>({{Age in years, months and days|year1=1914|year2=1918}})", (1914, 1918)),
    ("April 12, 1861{{snd}}May 26, 1865{{efn|name=End1|{{Cite web|date=May 29, 1865}}}}", (1861, 1865)),
    ("633–651{{sfn|Pourshariati|2008|pp=469}}", (633, 651)),
    ("{{start and end dates|1846|4|25|1848|2|2}}", (1846, 1848)),
    ("264–146 BC", (-264, -146)),
    ("54 BC – 217 AD", (-54, 217)),
    ("July 1–3, 1863", (1863, 1863)),
])
def test_infobox_years_read_the_date_line_without_its_notes(date, years):
    assert ConflictCollector._infobox_years(f"{{{{Infobox military conflict\n|date={date}\n}}}}") == years
