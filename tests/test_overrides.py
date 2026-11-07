import datetime
import json
import pathlib
from types import SimpleNamespace

import pytest

from page_in_history.dates import Span
from page_in_history.overrides import FIELDS, LIST_FIELDS, apply_overrides, load_overrides
from page_in_history.polities import Period, Polity

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"

OVERRIDES = load_overrides(DATA / "overrides.toml")


def test_every_override_cites_its_source():
    for override in OVERRIDES:
        if override.action == "none" or override.needs_verification:
            assert override.source == "" or override.source.startswith("https://"), override
            continue
        if override.sources:
            assert all(cited["quote"].strip() for cited in override.sources), override
        else:
            assert override.source.startswith("https://"), override
            assert override.quote.strip(), override
        assert isinstance(override.retrieved, datetime.date), override


def test_overrides_are_unique():
    keys = [(o.civilization_id, o.field, o.action, str(o.value_id or o.value)) for o in OVERRIDES]
    assert len(keys) == len(set(keys))


def test_the_overrides_to_verify_are_counted(record_property, capsys):
    pending = [o for o in OVERRIDES if o.needs_verification]
    record_property("needs_verification", len(pending))
    with capsys.disabled():
        print(f"\n{len(pending)} of {len(OVERRIDES)} overrides need verification")


def reflected(override, civilization) -> bool:
    if override.action == "dispute":
        return any(dispute["field"] == override.disputed and
                   any(item["source"] == override.cited for item in dispute["values"][1:])
                   for dispute in civilization.get("disputes", []))
    if override.action == "none":
        return override.field in civilization["checks"].get("not_applicable", {})
    if override.field == "period":
        period = civilization["period"]
        return all(period[end] and (period[end]["earliest"], period[end]["latest"]) == (year, year)
                   for end, year in override.value.items() if end != "circa")
    if override.field in ("map_year", "summary"):
        return civilization[override.field] == override.value
    if override.field == "modern_countries":
        return civilization["modern_countries"] == override.value
    items = civilization[LIST_FIELDS[override.field]]
    if override.field == "capital":
        return all(any(item["name"] == name for item in items) for name in override.values)
    return all(any(item["id"] == qid and (item["home"] if override.field in ("before", "after") else True)
                   for item in items) for qid in override.value_ids)


def test_every_override_is_in_the_data(civilizations):
    by_id = {civilization["id"]: civilization for civilization in civilizations.values()}
    unapplied = [o for o in OVERRIDES if not reflected(o, by_id[o.civilization_id])]
    assert not unapplied, unapplied


def test_the_report_lists_every_override():
    report = json.loads((DATA / "report.json").read_text())
    assert report["overrides_applied"] == [o.to_json() for o in OVERRIDES]
    assert report["needs_verification"] == [o.to_json() for o in OVERRIDES if o.needs_verification]


def gaps(civilization) -> list[str]:
    period, marked = civilization["period"], civilization["checks"].get("not_applicable", {})
    filled = {
        "period": bool(period["start"] and period["end"] or period["since"]),
        "capital": bool(civilization["capitals"]),
        "key_figures": bool(civilization["key_figures"]),
        "before": any(item["home"] for item in civilization["before"]),
        "after": any(item["home"] for item in civilization["after"]),
        "map_year": bool(civilization["map_year"]),
        "map": bool(civilization["map"]),
        "modern_countries": bool(civilization["modern_countries"]),
        "summary": bool(civilization["summary"]),
    }
    return [field for field, ok in filled.items() if not ok and field not in marked]


def test_every_field_is_filled_or_marked_not_applicable(civilizations):
    missing = {name: found for name, civilization in civilizations.items() if (found := gaps(civilization))}
    assert not missing, f"{len(missing)} civilizations have gaps: {missing}"


class FakeEntity:
    def __init__(self, label: str, location=None):
        self.label, self.location = label, location

    def years(self, prop):
        return []

    def coordinates(self):
        return self.location


class FakeWikidata:
    def entities(self, ids):
        return {"Q2": FakeEntity("Oldland"), "Q3": FakeEntity("Newtown", (1.0, 2.0)),
                "Q4": FakeEntity("Ruler A"), "Q5": FakeEntity("Ruler B")}


def test_apply_every_field_and_action():
    polity = Polity(id="Q1", slug="testland", name="Testland", wikipedia="Testland", map_year=900,
                    period=Period(None, None, False), key_figures=[{"id": "Q9", "name": "Old"}], summary="Old.")
    overrides = load_overrides(pathlib.Path(__file__).parent / "fixtures_overrides.toml")
    assert {o.field for o in overrides} == set(FIELDS)
    apply_overrides([polity], overrides, FakeWikidata())
    assert polity.period.start == Span(1000, 1000, True) and polity.period.end == Span(1200, 1200, True)
    assert polity.period.confirmed
    assert [c["name"] for c in polity.capitals] == ["Newtown"] and polity.capitals[0]["location"] == [1.0, 2.0]
    assert [f["name"] for f in polity.key_figures] == ["Ruler B", "Ruler A"]
    assert polity.before == [{"id": "Q2", "region": "Testregion", "name": "Oldland", "home": True}]
    assert polity.after == [] and polity.checks["not_applicable"] == {"after": "still exists", "map": "not drawn in Cliopatria"}
    assert polity.map_year == 1100 and polity.modern_countries == ["Testia", "Otheria"]
    assert polity.summary == "A test state."


def test_derived_entries_name_their_rule_and_each_source(tmp_path):
    entry = '''[[override]]
civilization = "Testland"
civilization_id = "Q1"
field = "summary"
action = "set"
value = "A test state."
retrieved = 2026-10-26
sources = [{ fact = "Testland was a state", source = "https://example.org/a", quote = "Testland was a state." }]
'''
    path = tmp_path / "overrides.toml"
    path.write_text(entry)
    with pytest.raises(ValueError, match="rule"):
        load_overrides(path)
    path.write_text(entry + 'derivation = "direct"\n')
    assert load_overrides(path)[0].sources[0]["fact"] == "Testland was a state"
    path.write_text(entry.replace(', quote = "Testland was a state."', "") + 'derivation = "direct"\n')
    with pytest.raises(ValueError, match="each source"):
        load_overrides(path)


DISPUTES = '''[[override]]
civilization = "Testland"
civilization_id = "Q1"
field = "period"
action = "set"
value = { end = 550, circa = true }
source = "https://en.wikipedia.org/wiki/Testland"
quote = "it fell about 550"
retrieved = 2026-11-07

[[override]]
civilization = "Testland"
civilization_id = "Q1"
field = "period"
action = "dispute"
value = { end = 750, circa = true }
source = "https://www.britannica.com/place/Testland"
quote = "it burned about 750"
retrieved = 2026-11-07

[[override]]
civilization = "Testland"
civilization_id = "Q1"
field = "after"
action = "dispute"
value = "Newtown"
value_id = "Q3"
region = "Testregion"
source = "https://www.worldhistory.org/Testland/"
quote = "Newtown took over"
retrieved = 2026-11-07
'''


def test_a_dispute_keeps_the_deck_value_and_lists_each_source(tmp_path):
    from page_in_history.deck import dispute_text, succession_fields
    path = tmp_path / "overrides.toml"
    path.write_text(DISPUTES)
    polity = Polity(id="Q1", slug="testland", name="Testland", wikipedia="Testland", map_year=500,
                    period=Period(Span(-100, -100, True), Span(600, 600), True),
                    after=[{"id": "Q2", "region": "Testregion", "name": "Oldland", "home": True}])
    apply_overrides([polity], load_overrides(path), FakeWikidata())
    assert polity.period.end == Span(550, 550, True) and polity.after[0]["name"] == "Oldland"
    document = polity.to_json()
    assert dispute_text(document, "end") == "Sources differ on the end: c. AD 550 (Wikipedia) · c. AD 750 (Britannica)"
    fields = succession_fields(document)
    assert fields["After"] == "Oldland or Newtown"
    assert fields["AfterDisputed"] == "Sources differ: Oldland · Newtown (World History Encyclopedia)"


def test_a_dispute_cites_its_source_and_names_one_end(tmp_path):
    path = tmp_path / "overrides.toml"
    unsourced = DISPUTES.split("\n\n[[override]]")[1].replace('quote = "it burned about 750"\n', "")
    path.write_text("[[override]]" + unsourced)
    with pytest.raises(ValueError, match="cites"):
        load_overrides(path)
    path.write_text(DISPUTES.replace("{ end = 750, circa = true }", "{ start = 1, end = 750 }"))
    with pytest.raises(ValueError, match="one end"):
        load_overrides(path)
