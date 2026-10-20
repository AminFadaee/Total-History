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
    assert polity.after == [] and polity.checks["not_applicable"] == {"after": "still exists"}
    assert polity.map_year == 1100 and polity.modern_countries == ["Testia", "Otheria"]
    assert polity.summary == "A test state."
