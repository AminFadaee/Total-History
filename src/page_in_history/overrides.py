import datetime
import pathlib
import tomllib
from dataclasses import dataclass

from page_in_history.dates import Span
from page_in_history.errata import Erratum
from page_in_history.polities import Polity
from page_in_history.sources.wikidata import Wikidata

SIDES = ("before", "after")
PERIOD_ENDS = {"period_start": "start", "period_end": "end"}
LIST_FIELDS = {"before": "before", "after": "after", "capital": "capitals", "key_figure": "key_figures"}
MAX_KEY_FIGURES = 4


@dataclass(frozen=True)
class Override:
    civilization: str
    civilization_id: str
    field: str
    value: str | int
    source: str
    quote: str
    retrieved: datetime.date
    value_id: str = ""
    region: str = ""
    circa: bool = False
    note: str = ""

    def to_json(self) -> dict:
        return {"civilization": self.civilization, "field": self.field, "value": self.value}


def load_overrides(path: pathlib.Path) -> list[Override]:
    entries = tomllib.loads(path.read_text()).get("override", []) if path.exists() else []
    overrides = [Override(**entry) for entry in entries]
    for override in overrides:
        if override.field not in PERIOD_ENDS and override.field not in LIST_FIELDS:
            raise ValueError(f"{override.civilization}: {override.field} is not a field that overrides may set")
        if override.field in SIDES and not override.region:
            raise ValueError(f"{override.civilization}: the {override.field} override needs a region")
    return overrides


def check_against_errata(overrides: list[Override], errata: list[Erratum]) -> None:
    removed = {(erratum.civilization, erratum.field, erratum.value) for erratum in errata}
    for override in overrides:
        key = (override.civilization, LIST_FIELDS.get(override.field), override.value)
        if key in removed:
            raise ValueError(f"Override and erratum contradict each other: {override.civilization}, {override.field}, "
                             f"{override.value}")


def apply_overrides(polities: list[Polity], overrides: list[Override], wikidata: Wikidata) -> list[Override]:
    by_id = {polity.id: polity for polity in polities}
    entities = wikidata.entities([override.value_id for override in overrides if override.value_id])
    for override in overrides:
        polity = by_id.get(override.civilization_id)
        if polity is None or polity.name != override.civilization:
            raise ValueError(f"Override names a civilization that is not in the deck: {override.civilization}")
        if override.field in PERIOD_ENDS:
            _set_period(polity, override)
        elif override.field in SIDES:
            name = by_id[override.value_id].name if override.value_id in by_id else entities[override.value_id].label
            _fill_home(polity, override, name, {o.value_id for o in overrides if o.field == override.field})
        elif override.field == "capital":
            _add_capital(polity, override, entities[override.value_id])
        else:
            name = by_id[override.value_id].name if override.value_id in by_id else entities[override.value_id].label
            _add_key_figure(polity, override, name)
    for polity in polities:
        if any(o.civilization_id == polity.id and o.field in PERIOD_ENDS for o in overrides) and not polity.period.confirmed:
            raise ValueError(f"{polity.name}: the period overrides leave one end open")
    return overrides


def _set_period(polity: Polity, override: Override) -> None:
    period = polity.period
    setattr(period, PERIOD_ENDS[override.field], Span(override.value, override.value, override.circa))
    period.confirmed, period.core, period.since = bool(period.start and period.end), None, None


def _fill_home(polity: Polity, override: Override, name: str, overridden: set[str]) -> None:
    side = getattr(polity, override.field)
    homes = [item for item in side if item["home"]]
    if any(item["id"] not in overridden for item in homes):
        raise ValueError(f"{polity.name}: the {override.field} side already has a home, so {name} cannot take it")
    side.insert(len(homes), {"id": override.value_id, "region": override.region, "name": name, "home": True})


def _add_capital(polity: Polity, override: Override, entity) -> None:
    founded = min((year.earliest for year in entity.years("P571")), default=None)
    location = entity.coordinates()
    polity.capitals.append({"name": override.value, "location": list(location) if location else None,
                            "founded": founded})


def _add_key_figure(polity: Polity, override: Override, name: str) -> None:
    if len(polity.key_figures) >= MAX_KEY_FIGURES:
        raise ValueError(f"{polity.name}: no room for the key figure {name}")
    polity.key_figures.append({"id": override.value_id, "name": name})
