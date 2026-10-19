import datetime
import json

from page_in_history.overrides import LIST_FIELDS, PERIOD_ENDS, load_overrides
import pathlib

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"

OVERRIDES = load_overrides(DATA / "overrides.toml")


def test_every_override_cites_its_source():
    for override in OVERRIDES:
        assert override.source.startswith("https://"), override
        assert override.quote.strip(), override
        assert isinstance(override.retrieved, datetime.date), override


def test_overrides_are_unique():
    keys = [(o.civilization_id, o.field, o.value_id or o.value) for o in OVERRIDES]
    assert len(keys) == len(set(keys))


def reflected(override, civilization) -> bool:
    if override.field in PERIOD_ENDS:
        span = civilization["period"][PERIOD_ENDS[override.field]]
        return civilization["period"]["confirmed"] and span == {
            "earliest": override.value, "latest": override.value, "circa": override.circa}
    items = civilization[LIST_FIELDS[override.field]]
    if override.field == "capital":
        return any(item["name"] == override.value and item["location"] for item in items)
    return any(item["id"] == override.value_id and (item["home"] if override.field in ("before", "after") else True)
               for item in items)


def test_every_override_is_in_the_data(civilizations):
    by_id = {civilization["id"]: civilization for civilization in civilizations.values()}
    unapplied = [o for o in OVERRIDES if not reflected(o, by_id[o.civilization_id])]
    assert not unapplied, unapplied


def test_the_report_lists_every_override():
    applied = json.loads((DATA / "report.json").read_text())["overrides_applied"]
    assert applied == [o.to_json() for o in OVERRIDES]
