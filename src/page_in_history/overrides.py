import datetime
import pathlib
import tomllib
from dataclasses import dataclass, field

from page_in_history.dates import Span, parse
from page_in_history.errata import Erratum
from page_in_history.polities import Polity, Tier
from page_in_history.sources.wikidata import Wikidata

SIDES = ("before", "after")
LIST_FIELDS = {"before": "before", "after": "after", "capital": "capitals", "key_figures": "key_figures",
               "modern_countries": "modern_countries"}
FIELDS = (*LIST_FIELDS, "period", "map_year", "summary", "map", "tier")
TIERS = ("Essential", "Core", "Extended")
ACTIONS = ("add", "set", "none", "dispute")
ADDABLE = ("before", "after", "capital", "key_figures")
DISPUTABLE = ("period", *SIDES)
PERIOD_ENDS = ("start", "end")
PERIOD_KEYS = {"start", "end", "since", "circa"}
MAX_KEY_FIGURES = 4


@dataclass(frozen=True)
class Override:
    civilization: str
    civilization_id: str
    field: str
    action: str
    value: str | int | list | dict = ""
    value_id: str | list = ""
    region: str = ""
    source: str = ""
    quote: str = ""
    retrieved: datetime.date | None = None
    needs_verification: bool = False
    note: str = ""
    sources: list[dict] = field(default_factory=list)
    derivation: str = ""
    same_state: bool = False
    page: str = ""

    @property
    def value_ids(self) -> list[str]:
        return self.value_id if isinstance(self.value_id, list) else [self.value_id] if self.value_id else []

    @property
    def values(self) -> list:
        return self.value if isinstance(self.value, list) else [self.value]

    @property
    def cited(self) -> str:
        return self.source or (self.sources[0]["source"] if self.sources else "")

    @property
    def disputed(self) -> str:
        """What a dispute is about: 'start' or 'end' of the period, or a side."""
        return next(end for end in PERIOD_ENDS if end in self.value) if self.field == "period" else self.field

    def to_json(self) -> dict:
        return {"civilization": self.civilization, "field": self.field, "action": self.action, "value": self.value}


def load_overrides(path: pathlib.Path) -> list[Override]:
    entries = tomllib.loads(path.read_text()).get("override", []) if path.exists() else []
    overrides = [Override(**entry) for entry in entries]
    for override in overrides:
        _validate(override)
    return overrides


def _validate(override: Override) -> None:
    who = f"{override.civilization}, {override.field}"
    if override.sources and not override.derivation.strip():
        raise ValueError(f"{who}: an entry drawn from several sources names the rule that combines them")
    for cited in override.sources:
        if set(cited) - {"page"} != {"fact", "source", "quote"} or not cites(cited["source"], cited.get("page", "")):
            raise ValueError(f"{who}: each source gives the fact it supports, its https page or a book and page, and the quote")
    if override.source and not cites(override.source, override.page):
        raise ValueError(f"{who}: a source is an https page, or a book with its page")
    if override.field not in FIELDS:
        raise ValueError(f"{who}: not a field that overrides may change")
    if override.action not in ACTIONS:
        raise ValueError(f"{who}: the action must be one of {', '.join(ACTIONS)}")
    if override.action == "none":
        if not override.note.strip():
            raise ValueError(f"{who}: a not-applicable entry needs its reason in the note")
        return
    if override.field == "map":
        raise ValueError(f"{who}: a map can only be marked not applicable")
    if override.field == "tier":
        if override.action != "set" or override.value not in TIERS or not override.note.strip():
            raise ValueError(f"{who}: a tier is set to one of {', '.join(TIERS)}, with the reason in the note")
        return
    if override.action == "dispute":
        _validate_dispute(override, who)
    if override.same_state and (override.field not in SIDES or override.action not in ("add", "set")):
        raise ValueError(f"{who}: only a civilization added before or after can be the same state")
    if override.action == "add" and override.field not in ADDABLE:
        raise ValueError(f"{who}: only {', '.join(ADDABLE)} can be added to")
    if override.field in SIDES and not override.region:
        raise ValueError(f"{who}: the {override.field} override needs a region")
    if override.field in ("capital", "key_figures", *SIDES) and len(override.value_ids) != len(override.values):
        raise ValueError(f"{who}: every value needs its Wikidata id")
    if override.field == "period" and (not isinstance(override.value, dict) or set(override.value) - PERIOD_KEYS):
        raise ValueError(f"{who}: a period is a table of {', '.join(sorted(PERIOD_KEYS))}")
    if override.field == "map_year" and not isinstance(override.value, int):
        raise ValueError(f"{who}: a map year is a whole number")
    if override.field == "period" and any(period_span(year) is None for key, year in override.value.items()
                                           if key in ("start", "end", "since")):
        raise ValueError(f"{who}: a period end is a year or a written date such as \"6th century\"")
    if override.field in ("map_year", "summary") and override.action == "add":
        raise ValueError(f"{who}: {override.field} can only be set")
    if override.action == "set" and override.field in LIST_FIELDS and not isinstance(override.value, list) \
            and override.field not in SIDES:
        raise ValueError(f"{who}: setting a list needs the whole list")


def cites(source: str, page: str) -> bool:
    """A web page by its address, or a book by its full reference and the page the quote is on."""
    return source.startswith("https://") or bool(source.strip() and page.strip())


def _validate_dispute(override: Override, who: str) -> None:
    if override.field not in DISPUTABLE:
        raise ValueError(f"{who}: only {', '.join(DISPUTABLE)} can be disputed")
    if override.needs_verification or not (override.source and override.quote or override.sources):
        raise ValueError(f"{who}: a dispute cites the source that gives the other value")
    if override.field == "period" and (not isinstance(override.value, dict)
                                       or len(set(override.value) & set(PERIOD_ENDS)) != 1
                                       or set(override.value) - {*PERIOD_ENDS, "circa"}):
        raise ValueError(f"{who}: a period dispute gives one end, start or end")


def check_against_errata(overrides: list[Override], errata: list[Erratum]) -> None:
    removed = {(erratum.civilization, erratum.field, erratum.value) for erratum in errata}
    for override in overrides:
        if override.field not in LIST_FIELDS or override.action == "none":
            continue
        for value in override.values:
            if (override.civilization, LIST_FIELDS[override.field], value) in removed:
                raise ValueError(f"Override and erratum contradict each other: {override.civilization}, "
                                 f"{override.field}, {value}")


def apply_overrides(polities: list[Polity], overrides: list[Override], wikidata: Wikidata) -> list[Override]:
    by_id = {polity.id: polity for polity in polities}
    entities = wikidata.entities([qid for override in overrides for qid in override.value_ids])

    def name_of(qid: str) -> str:
        return by_id[qid].name if qid in by_id else entities[qid].label

    for override in overrides:
        polity = by_id.get(override.civilization_id)
        if polity is None or polity.name != override.civilization:
            raise ValueError(f"Override names a civilization that is not in the deck: {override.civilization}")
        if override.action == "none":
            polity.checks.setdefault("not_applicable", {})[override.field] = override.note
        elif override.action == "dispute" or override.field == "tier":
            continue
        elif override.field == "period":
            _set_period(polity, override)
        elif override.field in SIDES:
            _fill_home(polity, override, name_of(override.value_id), {o.value_id for o in overrides if o.field == override.field})
        elif override.field == "capital":
            _fill_capitals(polity, override, entities)
        elif override.field == "key_figures":
            _fill_key_figures(polity, override, name_of)
        elif override.field == "modern_countries":
            polity.modern_countries = list(override.value)
        elif override.field == "map_year":
            polity.map_year = override.value
        else:
            polity.summary = override.value
    for polity in polities:
        if any(o.civilization_id == polity.id and o.field == "period" and o.action != "none" for o in overrides) \
                and not (polity.period.confirmed or polity.period.since):
            raise ValueError(f"{polity.name}: the period overrides leave one end open")
    for polity in polities:
        polity.disputes = _disputes(polity, [o for o in overrides if o.civilization_id == polity.id], name_of)
    return overrides


def apply_tiers(polities: list[Polity], overrides: list[Override]) -> None:
    """Tiers set by hand, after the rule has assigned them: an editorial choice with its reason, not a fact."""
    by_id = {polity.id: polity for polity in polities}
    for override in overrides:
        if override.field == "tier" and (polity := by_id.get(override.civilization_id)):
            polity.tier = Tier(override.value)


def _disputes(polity: Polity, overrides: list[Override], name_of) -> list[dict]:
    """Each disputed value beside the deck's own, with the source of each: the deck's value first, cited by the
    override that set it, or for a period Wikidata and the infobox agree on, by its Wikidata item."""
    disputes = []
    for disputed in dict.fromkeys(o.disputed for o in overrides if o.action == "dispute"):
        others = [o for o in overrides if o.action == "dispute" and o.disputed == disputed]
        if disputed in PERIOD_ENDS:
            own = getattr(polity.period, disputed)
            if own is None:
                raise ValueError(f"{polity.name}: a dispute over the period {disputed} needs a period {disputed}")
            setter = next((o for o in overrides if o.field == "period" and o.action == "set" and disputed in o.value), None)
            confirmed = f"https://www.wikidata.org/wiki/{polity.id}" if polity.period.confirmed else ""
            values = [{"value": own.earliest, "circa": own.circa, "source": setter.cited if setter else confirmed}]
            values += [{"value": o.value[disputed], "circa": o.value.get("circa", False), "source": o.cited} for o in others]
        else:
            home = next((item for item in getattr(polity, disputed) if item["home"]), None)
            if home is None or any(o.region != home["region"] for o in others):
                raise ValueError(f"{polity.name}: a dispute over {disputed} names its home region, {home and home['region']}")
            setter = next((o for o in overrides if o.field == disputed and o.action in ("add", "set")
                           and o.value_id == home["id"]), None)
            values = [{"id": home["id"], "value": home["name"], "source": setter.cited if setter else ""}]
            values += [{"id": o.value_id, "value": name_of(o.value_id), "source": o.cited} for o in others]
        disputes.append({"field": disputed, "values": values})
    return disputes


def period_span(value: int | str, circa: bool = False) -> Span | None:
    """A year, or a written date the sources give instead of one: "6th century", "6th century BC"."""
    if isinstance(value, int):
        return Span(value, value, circa)
    return parse(value) if isinstance(value, str) and "century" in value else None


def _set_period(polity: Polity, override: Override) -> None:
    period, value = polity.period, override.value
    circa = value.get("circa", False)
    for end in ("start", "end", "since"):
        if end in value:
            setattr(period, end, period_span(value[end], circa))
    if "since" in value:
        period.start = period.start or period.since
        period.end = None
    period.confirmed, period.core = bool(period.start and period.end), None
    if period.end:
        period.since = None


def _fill_home(polity: Polity, override: Override, name: str, overridden: set[str]) -> None:
    side = getattr(polity, override.field)
    homes = [item for item in side if item["home"]]
    if override.action == "set":
        side[:] = [item for item in side if not item["home"]]
        homes = []
    elif any(item["id"] not in overridden for item in homes):
        raise ValueError(f"{polity.name}: the {override.field} side already has a home, so {name} cannot take it")
    side[:] = [item for item in side if item["home"] or item["id"] != override.value_id and item["region"] != override.region]
    item = {"id": override.value_id, "region": override.region, "name": name, "home": True}
    side.insert(len(homes), item | {"same_state": True} if override.same_state else item)


def _fill_capitals(polity: Polity, override: Override, entities: dict) -> None:
    capitals = [_capital(name, entities[qid]) for name, qid in zip(override.values, override.value_ids)]
    polity.capitals[:] = polity.capitals + capitals if override.action == "add" else capitals


def _capital(name: str, entity) -> dict:
    founded = min((year.earliest for year in entity.years("P571")), default=None)
    location = entity.coordinates()
    return {"name": name, "location": list(location) if location else None, "founded": founded}


def _fill_key_figures(polity: Polity, override: Override, name_of) -> None:
    figures = [{"id": qid, "name": name_of(qid)} for qid in override.value_ids]
    kept = polity.key_figures if override.action == "add" else []
    if len(kept) + len(figures) > MAX_KEY_FIGURES:
        raise ValueError(f"{polity.name}: no room for the key figures {[figure['name'] for figure in figures]}")
    polity.key_figures[:] = kept + figures
