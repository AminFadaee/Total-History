import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import StrEnum

import geopandas as gpd
import shapely
from shapely.geometry import Point

from page_in_history.badged import BadgedInfoboxes
from page_in_history.dates import Span
from page_in_history.naming import slugify
from page_in_history.sources import vital
from page_in_history.sources.cliopatria import CAPITAL_TOLERANCE_DEGREES, EQUAL_AREA, Cliopatria
from page_in_history.sources.wikidata import Entity, Wikidata, entity_id, qualifier_year
from page_in_history.sources.wikipedia import (Infobox, Page, Wikipedia, country_infobox, display_name,
                                                  lead_capital_links, person_titles, place_name, ruler_succession)
from page_in_history.regions import home, regions
from page_in_history.sentences import contribution, first_paragraph, opening
from page_in_history.power import CONSORT, OTHER_STATE, PRIVATE, RULER, SERVANT, PowerScores
from page_in_history.consensus import agreed
from page_in_history.succession import WINDOW_YEARS, Anchor, Judge, RegionalSuccession
from page_in_history.succession_text import Article, SuccessionScores, text_score

POLITY_ROOTS = {
    "Q7275",  # state
    "Q96196009",  # former or current state
    "Q1048835",  # political territorial entity
    "Q1140229",  # polity
}
IDENTITY_PADDING_YEARS = 150
MIN_REGION_SHARE = 0.05
MIN_INSIDE_SHARE = 0.25
MIN_PEAK_AREA_KM2 = 50_000
MIN_LANGUAGE_EDITIONS = 60
MAX_VITAL_LEVEL = 4
LEVEL_5_LANGUAGES = 45
LEVEL_5_PEAK_KM2 = 1_000_000
MAX_DRAWN_SHARE = 0.5
LATEST_START = 1945
# civilization (two items), ancient civilization, archaeological culture, historical period, historical ethnic group,
# indigenous people, archaeological site
CULTURE_CLASSES = {"Q8432", "Q2718886", "Q28171280", "Q465299", "Q11514315", "Q4204501", "Q103817", "Q839954"}
CULTURE_LANGUAGES, CULTURE_MIN_YEARS, CULTURE_BEFORE = 40, 50, 1900
CULTURE = "culture or period Cliopatria draws"
SETTLEMENT = "Q486972"
STATE, FORMER_OR_CURRENT_STATE = "Q7275", "Q96196009"
HERITAGE = "Q9259"
SITE = "Q839954"
FIRST_LEVEL_DIVISION, SECOND_LEVEL_DIVISION = "Q10864048", "Q13220204"
LIST_ARTICLE = "Q13406463"
LARGE_PEAK_KM2 = 2_000_000
SHARED_TOLERANCE_YEARS = 25
# head of state, monarch, emperor, king, president (two items)
HEAD_OF_STATE_CLASSES = {"Q48352", "Q116", "Q39018", "Q12097", "Q248577", "Q30461", "Q111840950"}
SHOWN_RULERS, RULER_FAME_SHARE, ADULT_YEARS = 4, 3, 20
RULER_SCORE = 0.5
RULER_RANK, OTHER_FIGURES = 2, 2
MIN_REIGN_YEARS = 3
COLONY_KINDS = {"Q1790360", "Q133156", "Q1351282"}
COLONIAL_POWERS = {"Q1790360", "Q20181813"}  # colonial empire, colonial power
BORDER_DEGREES = 0.1
SPREAD_SHARE = 0.5
KNOWN_NEIGHBOUR_LANGUAGES = 40
LESS_KNOWN_NEIGHBOUR_LANGUAGES = 20
BADGED_LANGUAGES = 2
SUCCESSION_GAP_YEARS = 100
HOME_OVERLAP_YEARS = 5
CONSECUTIVE_YEARS = 5
SUCCESSION_SLOTS = 3
DATE_WINDOWS = {"before": (-40, 30), "after": (-30, 40)}
DATE_DECAY_YEARS = 40
SUCCESSION_PROPERTIES = ("P155", "P156", "P1365", "P1366")
SAME_PLACE_KM = 5
STEM_LETTERS = 5
SOVEREIGN_STATE = "Q3624078"
PERIOD = "Q11514315"
TIER_KINDS = {"Q41710": "people", "Q4204501": "people", "Q8432": "state", "Q2718886": "state", "Q3024240": "state",
              "Q7275": "state", "Q417175": "state", "Q48349": "state"}  # peoples; civilizations and states
CENTURY_PRECISION, DECADE_PRECISION, YEAR_PRECISION = 7, 8, 9
SAME_NAME_WORDS = {"dynasty", "of", "the", "kingdom", "crown", "electorate", "principality", "duchy", "grand", "emirate",
                   "khanate", "caliphate", "sultanate"}
RULING_TITLES = (r"king|queen|emperor|tlatoani|tlahtoāni|tlahtoani|empress|sultan|sultana|shah|shahanshah|caliph|khan|khagan|qaghan|pharaoh|tsar|tsarina|czar"
                 r"|president|shogun|shōgun|chanyu|mansa|emir|amir|negus|tlatoani|huey tlatoani|sapa inca|doge|grand duke"
                 r"|grand prince|monarch|dictator|raja|maharaja|nizam|peshwa|khedive|basileus|chhatrapati"
                 r"|duke of|prince of|archduke|elector|consul|ruler|leader|founder")
GENERIC_TITLES = {"ruler", "leader", "founder"}
HOME_RULER_ROLE = re.compile(rf"\b(?:{RULING_TITLES}|regent|head of|ajaw)\b", re.IGNORECASE)
RULING_ROLE = re.compile(rf"^(?:[\w'’.-]+\s+){{0,3}}?(?:{RULING_TITLES})\b", re.IGNORECASE)
RULING_TITLE = re.compile(rf"\b({RULING_TITLES})\b", re.IGNORECASE)
GENERIC_OF = re.compile(r"\b(?:ruler|leader|founder)\s+of\b", re.IGNORECASE)
NOT_RULING = re.compile(r"\b(?:consort|regent|wife|mother|daughter|son of|heir|pretender|claimant|crown prince|spiritual"
                        r"|religious|reformation|reformer|\w+ism|rebel\w*|uprising|revolt|revolution\w*|independence)\b",
                        re.IGNORECASE)
RULED_PLACE = re.compile(r"\bof\s+(?:the\s+)?(.+?)(?=\s+(?:from|in|for|during|between)\b|[,;(]|$)", re.IGNORECASE)
NUMBERED_DYNASTY = re.compile(r"(?:\w+-)?(?:\d+(?:st|nd|rd|th)|first|second|third|\w+th)\s+dynasty", re.IGNORECASE)
REIGN = re.compile(r"\bfrom\s+(\d{1,4})\s+(?:BCE?\s+)?to\s+(\d{1,4})(\s+BCE?)?", re.IGNORECASE)
ROLE_YEAR = re.compile(r"\b(\d{3,4})\b(?!\s*BC)")
OFFICE_STATES = {"shogunate": "shogun|shōgun", "sultanate": "sultan", "caliphate": "caliph", "khaganate": "khagan|qaghan|khan",
                 "khanate": "khan", "emirate": "emir|amir"}
REPEATED_MAP_YEARS, REPEATED_MAP_OVERLAP = 30, 0.85
FOLD_VITAL_LEVEL = 4
DYNASTY = "Q164950"
GENERIC_WORDS = {"sultanate", "empire", "kingdom", "dynasty", "of", "the", "caliphate", "republic", "state"}
LASTING_AREA_YEARS, LASTING_LANGUAGES, COLONIAL_ERA = 12_500_000, 30, 1500
CORE_AREA_YEARS = 150_000_000
HOUSE_WORDS = {"house", "of", "the", "dynasty"}
EVENT_KINDS = {"Q198", "Q178561", "Q180684", "Q10931", "Q131569"}
EVENT_LANGUAGES = 30
CORE_EVENTS = 15
LIVING_ON_MAP_BEFORE = 1930
PRESENT_DESCRIPTION_FROM = 1900
CIRCUMSTANCE = re.compile(r"Under\b")
DATED = re.compile(r"\d|\b(?:ancient|historical|historic|former|medieval|prehistoric|pre-\w+)\b", re.IGNORECASE)
MIN_WORDS_BEYOND_NAME = 1
PRESENT_OR_PAST = re.compile(r"\b(is|are|was|were)\b")
HUMAN = "Q5"
ALSO_ROLE = re.compile(rf"\b(?:{RULING_TITLES}|statesman|statesmen|politician|general|minister|chancellor|commander|strategist"
                       r"|admiral|adviser|advisor|vizier)\b", re.IGNORECASE)
PRIME_YEARS, ACCESSION_YEARS = 50, 35
FEW_CANDIDATES = 2
ASSUMED_LIFESPAN = 70
ROW_YEAR = re.compile(r"(\bAD\s*)?(\d{1,4})(\s*BCE?\b)?")
LIVING_PEAK_KM2 = 2_000_000
STATE_KINDS = {"state", "historical country", "city-state", "empire", "kingdom"}
MAPLESS_KINDS = {
    "Q7275": "state", "Q3024240": "historical country", "Q133442": "city-state", "Q48349": "empire",
    "Q417175": "kingdom", "Q486972": "settlement", "Q10864048": "subdivision", "Q11514315": "period",
    "Q164950": "dynasty", "Q41710": "ethnic group", "Q8432": "civilization",
}
NEIGHBOUR_ROOTS = {**MAPLESS_KINDS, "Q2577883": "occupied territory", **dict.fromkeys(EVENT_KINDS, "event")}
NOT_MAPLESS = {"subdivision", "period", "dynasty", "ethnic group", "civilization"}
KINDS = {
    "Q3624078": "sovereign state",
    "Q133442": "city-state",
    "Q486972": "settlement",
    "Q10864048": "subdivision",  # first-level administrative country subdivision
    "Q107390": "subdivision",  # federated state
    "Q133156": "colony",
    "Q164142": "colony",  # protectorate
    "Q161243": "colony",  # dependent territory
    "Q3024240": "historical",  # historical country
    "Q4204501": "historical",  # historical ethnic group
    "Q1620908": "historical",  # historical region
    "Q8432": "historical",  # civilization
    "Q28171280": "historical",  # ancient civilization
}
INFOBOX_TOLERANCE_YEARS = 1
AGE_TOLERANCE_SHARE = 0.01
PRESENT_YEAR = 2000
CLIOPATRIA_TOLERANCE_YEARS = 25
DATES = ("P571", "P580", "P576", "P582")
CAPITAL_MATCH_KM = 25
TRANSITION_GROWTH = 1.2
MODERN_SHARE_OF_POLITY = 0.05
MODERN_SHARE_OF_COUNTRY = 0.5

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Identity:
    name: str
    title: str
    qid: str
    how: str


@dataclass
class Period:
    start: Span | None
    end: Span | None
    confirmed: bool
    core: list[int] | None = None
    since: Span | None = None


class Tier(StrEnum):
    ESSENTIAL = "Essential"
    CORE = "Core"
    EXTENDED = "Extended"


@dataclass
class Polity:
    id: str
    slug: str
    name: str
    wikipedia: str
    map_year: int
    period: Period
    capitals: list[dict] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    before: list[dict] = field(default_factory=list)
    after: list[dict] = field(default_factory=list)
    key_figures: list[dict] = field(default_factory=list)
    modern_countries: list[str] = field(default_factory=list)
    overseas_countries: list[str] = field(default_factory=list)
    borders: bool = True
    summary: str = ""
    tier: str = ""
    map: str = ""
    map_asked: bool = True
    disputes: list[dict] = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


def equal_area(frame):
    """Reprojected for area measurements and repaired, since reprojection can make a valid shape self-intersect."""
    projected = frame.to_crs(EQUAL_AREA)
    return projected.set_geometry(projected.geometry.make_valid()) if hasattr(projected, "set_geometry") \
        else projected.make_valid()


def equal_area_shape(geometry, crs):
    return equal_area(gpd.GeoSeries([geometry], crs=crs)).iloc[0]


def area_years(rows: gpd.GeoDataFrame) -> float:
    """The drawn area summed over the years drawn, in km²·years."""
    return float((rows.Area * (rows.ToYear - rows.FromYear + 1)).sum())


def colonial(kinds: set[str], rows: gpd.GeoDataFrame) -> bool:
    """Filed as a colony by Wikidata and drawn from 1500, since the class also marks the Teutonic Order and the
    Ostrogoths."""
    return "colony" in kinds and rows.FromYear.min() >= COLONIAL_ERA


def sparql_year(value: str | None) -> int | None:
    """The year of a SPARQL date such as '1453-05-29T00:00:00Z' or '-0549-01-01T00:00:00Z'."""
    match = re.match(r"(-?)(\d+)-", value or "")
    return (-1 if match.group(1) else 1) * int(match.group(2)) if match else None


def stems(name: str) -> set[str]:
    """The first five letters of each distinctive word of a name, without the kind of state and joining words, so
    'Babylon' meets 'Babylonia' and 'Egyptian' meets 'Egypt': {'ottom'} for the Ottoman Empire."""
    words = set(re.findall(r"\w+", name.lower())) - SAME_NAME_WORDS - {"empire", "republic", "state", "dynasty", "and", "from", "to"}
    return {word[:STEM_LETTERS] for word in words if not word.isdigit()}


def name_words(name: str | None) -> set[str]:
    return set(re.findall(r"\w+", (name or "").lower()))


def lifespan(entity) -> tuple[int, int] | None:
    starts = [year.earliest for year in entity.years("P571") + entity.years("P580")]
    ends = [year.latest for year in entity.years("P576") + entity.years("P582")]
    return (min(starts), max(ends)) if starts and ends else None


def overlap(first: tuple[int, int] | None, second: tuple[int, int] | None) -> bool:
    return bool(first and second and max(first[0], second[0]) <= min(first[1], second[1]))


def same_name(first: str, second: str) -> bool:
    """The two names are the same once "dynasty", a monarch's rank (kingdom, crown, electorate, principality, duchy,
    emirate, khanate, caliphate, sultanate) and brackets are set aside, as Qin and the Qin dynasty, the Kingdom and
    the Crown of Castile, or the Kingdom of Naples and the Kingdom of Naples (Napoleonic). A change of form stays, since it tells the Roman Republic from the Roman Empire."""
    words = lambda name: set(re.findall(r"\w+", re.sub(r"\(.*?\)", "", name).lower())) - SAME_NAME_WORDS
    return bool(words(first)) and words(first) == words(second)


@dataclass(frozen=True)
class RulingRole:
    """What a figure's role says of their rule: 'Founder and Emperor of Han Dynasty of China from 202 to 195 BC' is an
    emperor of China from -202 to -195. The title is the first specific one (not ruler, leader or founder); the place
    is the last 'of' after the last title, when it is a name, and not a numbered dynasty."""
    title: str
    place: str | None
    reign: tuple[int, int] | None

    @classmethod
    def read(cls, role: str) -> "RulingRole | None":
        if not RULING_ROLE.match(role) or NOT_RULING.search(role):
            return None
        clause = re.split(r"\s+from\b|[,;]", role)[0]
        found = list(RULING_TITLE.finditer(clause))
        titles = [match.group(1).lower() for match in found]
        if all(title in GENERIC_TITLES for title in titles) and not GENERIC_OF.search(clause):
            return None
        title = next((title for title in titles if title not in GENERIC_TITLES), titles[0])
        named = RULED_PLACE.search(role, found[-1].start())
        place = re.split(r"\bof\s+(?:the\s+)?", named.group(1))[-1] if named else None
        if place and (NUMBERED_DYNASTY.fullmatch(place) or not re.search(r"\b[A-Z]", place)):
            place = None
        reign = REIGN.search(role)
        years = None
        if reign:
            first, last = int(reign.group(1)), int(reign.group(2))
            years = (-first, -last) if reign.group(3) else (first, last)
        return cls(title, place, years)

    @property
    def places(self) -> list[set[str]]:
        return [found for part in re.split(r"\s+and\s+|,\s*", self.place or "") if (found := stems(part))]

    @property
    def generic(self) -> bool:
        return self.title in GENERIC_TITLES

    def fits_office(self, state: str) -> bool:
        """A state named for its office (a shogunate, sultanate, caliphate, khanate or emirate) is ruled by holders of
        that office: an emperor of Japan is not a ruler of the Kamakura Shogunate."""
        return self.generic or all(word not in state.lower() or re.fullmatch(title, self.title)
                                   for word, title in OFFICE_STATES.items())


def fold(text: str) -> str:
    """Text without accents, so 'Shōgun' meets 'Shogunate'."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def row_years(text: str) -> tuple[int, int] | None:
    """The years an infobox row gives, as '1192–1199', '1192' or '27 BC – AD 14'; a year without an era takes the era of
    the year after it, as in '550–530 BC'."""
    found = ROW_YEAR.findall(re.sub(r"\{\{[^|}]*\|", "", text))
    if not found:
        return None
    years, bc = [], False
    for ad, year, before_christ in reversed(found):
        bc = bool(before_christ) or (bc and not ad)
        years.insert(0, -int(year) if bc else int(year))
    years = years[:2]
    return (years[0], years[-1]) if years[-1] >= years[0] else (years[0], years[0])


def lived_during(person: Entity, start: int, end: int) -> bool:
    """A person, by Wikidata, whose birth or death dates put part of their life within the years."""
    births, deaths = person.years("P569"), person.years("P570")
    if HUMAN not in person.ids("P31", preferred_only=False) or not (births or deaths):
        return False
    born = min(year.earliest for year in births) if births else min(year.earliest for year in deaths) - ASSUMED_LIFESPAN
    died = max(year.latest for year in deaths) if deaths else max(year.latest for year in births) + ASSUMED_LIFESPAN
    return born <= end and died >= start


def years_overlap(entity: Entity, start: int, end: int) -> bool:
    """The item's dates overlap the years given; an item with a start but no end still exists."""
    inception = [year.earliest for year in entity.years("P571") + entity.years("P580")]
    dissolution = [year.latest for year in entity.years("P576") + entity.years("P582")]
    return bool(inception) and min(inception) <= end and (not dissolution or max(dissolution) >= start)


def dates(entity: Entity) -> tuple[int | None, int | None]:
    """A state's first and last year by Wikidata: inception or start time, dissolution or end time."""
    starts = [year.earliest for year in entity.years("P571") + entity.years("P580")]
    ends = [year.latest for year in entity.years("P576") + entity.years("P582")]
    return min(starts, default=None), max(ends, default=None)


def kilometres(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Great-circle distance between two longitude and latitude points."""
    (lon1, lat1), (lon2, lat2) = (map(math.radians, point) for point in (first, second))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def reference(*dates) -> int:
    """A rough year for sizing the tolerance: the first date given, as a year, a Wikidata year or a span."""
    for date in dates:
        if isinstance(date, int):
            return date
        if date is not None:
            return date.earliest
    return PRESENT_YEAR


def tolerances(year: int) -> dict[frozenset, int]:
    """How far apart two sources may be and still agree on a date around the given year."""
    aged = max(INFOBOX_TOLERANCE_YEARS, round(AGE_TOLERANCE_SHARE * (PRESENT_YEAR - year)))
    return {
        frozenset(("infobox", "wikidata")): aged,
        frozenset(("infobox", "cliopatria")): max(CLIOPATRIA_TOLERANCE_YEARS, aged),
        frozenset(("wikidata", "cliopatria")): max(CLIOPATRIA_TOLERANCE_YEARS, aged),
    }


def vote(candidates: dict[str, Span | None], tolerances: dict[frozenset, int]) -> Span | None:
    """A date that another source confirms within that pair's tolerance. Cliopatria's snapshot years only confirm;
    the value shown comes from the most precise confirmed source among the others."""
    confirmed = [
        (source, span) for source, span in candidates.items()
        if span is not None and source != "cliopatria" and any(
            other != source and other_span is not None
            and span.overlaps(other_span, tolerances[frozenset((source, other))])
            for other, other_span in candidates.items()
        )
    ]
    return min(confirmed, key=lambda found: found[1].width)[1] if confirmed else None


class PolityCatalog:
    """Resolves every Cliopatria polity active around the window to the Wikidata item of its Wikipedia article."""

    def __init__(self, cliopatria: Cliopatria, wikipedia: Wikipedia, wikidata: Wikidata, start: int, end: int):
        self.cliopatria = cliopatria
        self.wikipedia = wikipedia
        self.wikidata = wikidata
        rows = cliopatria.active(start - IDENTITY_PADDING_YEARS, end + IDENTITY_PADDING_YEARS)
        links = {name: Counter(zip(group.Wikipedia, group.Wikidata)).most_common(1)[0][0]
                 for name, group in rows.groupby("Name")}
        pages = self._subjects(wikipedia.resolve([title for title, _ in links.values()]))
        self.entities = wikidata.entities([page.qid for page in pages.values()])
        self.entities.update(wikidata.entities([qid for entity in self.entities.values() for qid in entity.ids("P17")]))
        classes = {qid for entity in self.entities.values() for qid in entity.ids("P31", preferred_only=False)}
        self.polity_classes = wikidata.subclass_of(sorted(classes), POLITY_ROOTS)
        others = [page.title for page in pages.values()
                  if page.qid in self.entities and not self.is_polity(self.entities[page.qid])]
        self.infoboxes = {title: country_infobox(text) for title, text in wikipedia.wikitext(others).items()}
        self.unresolved: dict[str, str] = {}
        candidates: dict[str, Identity] = {}
        years = {name: (int(group.FromYear.min()), int(group.ToYear.max())) for name, group in rows.groupby("Name")}
        for name, (title, cliopatria_qid) in links.items():
            page = pages.get(title)
            entity = self.entities.get(page.qid) if page and page.qid else None
            found = self._identify(name, page.title if page else title, entity, cliopatria_qid, *years[name])
            if isinstance(found, Identity):
                candidates[name] = found
            else:
                self.unresolved[name] = found
        candidates |= self._cultures(rows, pages, links)
        candidates |= self._by_name({name: (links[name][1], *years[name]) for name in self.unresolved if name not in candidates})
        for name in candidates:
            self.unresolved.pop(name, None)
        self.qids = {name: identity.qid for name, identity in candidates.items()}
        self.identities = self._without_duplicates(candidates)
        self._name_as_whole()
        self.peoples = self._without_peoples()
        self.by_qid = {identity.qid: identity for identity in self.identities.values()}
        self.mapless: dict[str, tuple[int, int]] = {}
        self.folded: dict[str, list[str]] = {}
        self.vital_levels = self._vital_levels()
        self._fold_dynasties()

    def _subjects(self, pages: dict[str, Page]) -> dict[str, Page]:
        """An article on the history of a civilization, not itself a state, stands for the civilization when Wikidata
        links the two both ways (History of Sumer is a facet of Sumer, whose history it is), so Cliopatria's Sumerian
        city-states are Sumer."""
        found = self.wikidata.entities([page.qid for page in pages.values() if page.qid])
        polities = self.wikidata.subclass_of(sorted({qid for entity in found.values()
                                                     for qid in entity.ids("P31", preferred_only=False)}), POLITY_ROOTS)
        facets = {qid: entity.ids("P1269") for qid, entity in found.items()
                  if not set(entity.ids("P31", preferred_only=False)) & polities}
        subjects = self.wikidata.entities([subject for ids in facets.values() for subject in ids])
        swapped = {}
        for title, page in pages.items():
            subject = next((subjects[qid] for qid in facets.get(page.qid, []) if qid in subjects
                            and page.qid in subjects[qid].ids("P2184") and subjects[qid].enwiki), None)
            swapped[title] = Page(subject.enwiki, subject.id) if subject else page
        return swapped

    def years(self, qid: str) -> tuple[int, int]:
        """When a civilization existed: Cliopatria's first and last year, or Wikidata's dates for one it does not draw."""
        if qid in self.mapless:
            return self.mapless[qid]
        rows = self.cliopatria.rows(self.by_qid[qid].name)
        return int(rows.FromYear.min()), int(rows.ToYear.max())

    def add_mapless(self, identity: Identity, entity: Entity, years: tuple[int, int]) -> None:
        self.add_drawn(identity, entity)
        self.mapless[identity.qid] = years

    def _vital_levels(self) -> dict[str, int]:
        """Each item's highest level on Wikipedia's Vital Articles lists."""
        levels = vital.article_levels(self.wikipedia.http, self.wikipedia)
        found: dict[str, int] = {}
        for title, page in self.wikipedia.resolve(list(levels)).items():
            if page and page.qid:
                found[page.qid] = min(levels[title], found.get(page.qid, levels[title]))
        return found

    def _fold_dynasties(self) -> None:
        """A state on the Vital Articles lists (levels 1–4) that Cliopatria draws only through its dynasties or periods
        takes their shapes: the Delhi Sultanate from its Mamluk, Khalji, Tughlaq, Sayyid and Lodi dynasties, the Chola
        dynasty from Cliopatria's Early Cholas and Chola Empire. A shape folds when it held the state's capital, lies
        within the state's own dates and is a dynasty or carries the state's name; together they must cover at least
        half of its years."""
        entities = self.wikidata.entities([qid for qid, level in self.vital_levels.items()
                                           if level <= FOLD_VITAL_LEVEL and qid not in self.by_qid])
        kinds = self.wikidata.kinds(list(entities), MAPLESS_KINDS)
        dynasties = {qid for qid, found in self.wikidata.kinds(list(self.by_qid), {DYNASTY: "dynasty"}).items() if found}
        capitals = self.wikidata.entities([capital for entity in entities.values() for capital in entity.ids("P36")])
        frame = self.cliopatria.frame
        renamed: dict[str, str] = {}
        for qid, entity in entities.items():
            starts = [year.earliest for year in entity.years("P571") + entity.years("P580")]
            ends = [year.latest for year in entity.years("P576") + entity.years("P582")]
            if not (kinds.get(qid, set()) & STATE_KINDS) or not entity.enwiki or not starts or not ends:
                continue
            first, last = min(starts), max(ends)
            point = next((capitals[c].coordinates() for c in entity.ids("P36") if c in capitals and capitals[c].coordinates()),
                         None) or entity.coordinates()
            if not point:
                continue
            word = next((part for part in re.findall(r"\w+", display_name(entity.enwiki).lower()) if part not in GENERIC_WORDS), "")
            held = frame[frame.geometry.contains(Point(point)) & (frame.ToYear >= first) & (frame.FromYear <= last)]
            parts = [name for name in sorted(set(held.Name)) if name in self.identities and name not in renamed
                     and (self.identities[name].qid in dynasties or (word and word in name.lower()))
                     and self.cliopatria.rows(name).FromYear.min() >= first - SHARED_TOLERANCE_YEARS
                     and self.cliopatria.rows(name).ToYear.max() <= last + SHARED_TOLERANCE_YEARS]
            covered = sum(int(self.cliopatria.rows(name).ToYear.max() - self.cliopatria.rows(name).FromYear.min() + 1)
                          for name in parts)
            if parts and covered * 2 >= last - first + 1:
                name = display_name(entity.enwiki)
                renamed.update({part: name for part in parts})
                self.folded[name] = parts
                for part in parts:
                    old = self.identities.pop(part)
                    self.by_qid.pop(old.qid, None)
                    self.qids.pop(part, None)
                self.add_drawn(Identity(name, entity.enwiki, qid, "state drawn through its dynasties"), entity)
        if renamed:
            self.cliopatria = self.cliopatria.with_frame(frame.assign(Name=frame.Name.replace(renamed)))

    def add_drawn(self, identity: Identity, entity: Entity) -> None:
        self.entities[identity.qid] = entity
        self.identities[identity.name] = identity
        self.by_qid[identity.qid] = identity
        self.qids[identity.name] = identity.qid

    def reidentify(self, spans: dict[str, tuple[int, int]]) -> list[str]:
        """Names whose item's dates never meet their map years point at the wrong state, such as the modern region
        for Cliopatria's early-medieval Aquitaine. A Wikidata state named exactly like the polity, with dates that
        overlap, stands in, as for a failed link; otherwise the name leaves the catalogue. Returns the names that left."""
        found = self._by_name({name: (self.identities[name].qid, *years) for name, years in spans.items()})
        dropped = []
        for name in spans:
            wrong = self.identities.pop(name)
            self.by_qid.pop(wrong.qid, None)
            self.qids.pop(name, None)
            if name in found:
                self.add_drawn(found[name], self.entities[found[name].qid])
            else:
                dropped.append(name)
        return dropped

    def _by_name(self, failed: dict[str, tuple[str, int, int]]) -> dict[str, Identity]:
        """Cliopatria's link sometimes points at the wrong article, such as the island for the Kingdom of Great
        Britain, or at a list, such as the Median dynasty's kings for the Median kingdom. Then its own Wikidata id, or
        a Wikidata state named exactly like the Cliopatria polity (by label or alias), stands in when its dates
        overlap and it is the only such state."""
        options = {name: [cliopatria_qid] + self.wikidata.exact_matches(name)
                   for name, (cliopatria_qid, _, _) in failed.items()}
        entities = self.wikidata.entities([qid for qids in options.values() for qid in qids])
        classes = {qid for entity in entities.values() for qid in entity.ids("P31", preferred_only=False)}
        self.polity_classes |= self.wikidata.subclass_of(sorted(classes - self.polity_classes), POLITY_ROOTS)
        found = {}
        for name, qids in options.items():
            _, start, end = failed[name]
            states = {qid: entities[qid] for qid in qids if qid in entities and self.is_polity(entities[qid])
                      and entities[qid].enwiki and years_overlap(entities[qid], start, end)}
            if len(states) == 1:
                entity = next(iter(states.values()))
                self.entities[entity.id] = entity
                found[name] = Identity(name, entity.enwiki, entity.id, "Wikidata state named like the Cliopatria polity")
        return found

    def _cultures(self, rows, pages: dict, links: dict) -> dict[str, Identity]:
        """Cliopatria also draws civilizations whose article Wikidata files as a civilization, an archaeological culture,
        a historical period, a historical people or an archaeological site rather than a state: the Maya, the Indus
        Valley, the Minoans, Heian Japan, the Huns, Teotihuacan. They count when the article is known in at least 40
        languages and the shape is drawn for 50 years or more, beginning before 1900."""
        drawn = {name: (int(group.FromYear.min()), int((group.ToYear - group.FromYear + 1).sum()))
                 for name, group in rows.groupby("Name")}
        options = {}
        for name, reason in self.unresolved.items():
            page = pages.get(links[name][0])
            entity = self.entities.get(page.qid) if page and page.qid else None
            if entity and "not a polity" in reason and CULTURE_CLASSES & set(entity.ids("P31", preferred_only=False)) \
                    and drawn[name][0] < CULTURE_BEFORE and drawn[name][1] >= CULTURE_MIN_YEARS:
                options[name] = (page.title, entity)
        editions = self.wikidata.language_editions([entity.id for _, entity in options.values()])
        return {name: Identity(name, entity.enwiki or title, entity.id, CULTURE)
                for name, (title, entity) in options.items() if editions.get(entity.id, 0) >= CULTURE_LANGUAGES}

    def is_polity(self, entity: Entity) -> bool:
        return bool(set(entity.ids("P31", preferred_only=False)) & self.polity_classes)

    def _identify(self, name: str, title: str, entity: Entity | None, cliopatria_qid: str,
                  start: int, end: int) -> Identity | str:
        if entity is None:
            return f"Wikipedia link {title!r} has no Wikidata item"
        if LIST_ARTICLE in entity.ids("P31", preferred_only=False):
            return f"Wikipedia link {title!r} is a list article"
        if self.is_polity(entity):
            if entity.id == cliopatria_qid:
                return Identity(name, entity.enwiki or title, entity.id, "same item in Cliopatria and Wikipedia")
            if years_overlap(entity, start, end):
                return Identity(name, entity.enwiki or title, entity.id, "Wikipedia item with overlapping dates")
            return f"{entity.id} from {title!r} disagrees with {cliopatria_qid} and its dates don't overlap"
        dynasty = (entity.label or "").split(" ")[0].lower()
        states = [self.entities[qid] for qid in entity.ids("P17") if qid in self.entities
                  and self.is_polity(self.entities[qid]) and years_overlap(self.entities[qid], start, end)
                  and dynasty in (self.entities[qid].label or "").lower().split()]
        if len(states) == 1 and states[0].enwiki:
            return Identity(name, states[0].enwiki, states[0].id, f"the state named after {entity.label}")
        infobox = self.infoboxes.get(title)
        if infobox and self._infobox_overlaps(infobox, entity, start, end):
            return Identity(name, title, entity.id, "Wikipedia article with a country infobox")
        return f"Wikipedia link {title!r} is not a polity article"

    @staticmethod
    def _infobox_overlaps(infobox: Infobox, entity: Entity, start: int, end: int) -> bool:
        """Wikipedia's editors file the article as a state; its dates, or the item's, must overlap Cliopatria's."""
        if infobox.start and infobox.end:
            return (infobox.start.earliest <= end + CLIOPATRIA_TOLERANCE_YEARS
                    and infobox.end.latest >= start - CLIOPATRIA_TOLERANCE_YEARS)
        return years_overlap(entity, start, end)

    def _without_duplicates(self, candidates: dict[str, Identity]) -> dict[str, Identity]:
        """When several Cliopatria names share one item, the one with the largest territory keeps it. Those drawn
        within the item's own dates are the same civilization in another stretch, as Northern and Southern Song are
        the Song dynasty: their shapes join it under the name of the item's article. Names outside the item's dates,
        often a wrong link (a 1912 "Great Mongol State" for the Yuan dynasty), or of a state that still exists, are
        left out."""
        by_qid: dict[str, list[Identity]] = {}
        for identity in candidates.values():
            by_qid.setdefault(identity.qid, []).append(identity)
        kept, renamed = {}, {}
        for identities in by_qid.values():
            largest = max(identities, key=lambda identity: self.cliopatria.rows(identity.name).Area.max())
            joining = [other for other in identities if other is not largest and self._within_dates(other, largest.qid)]
            for other in identities:
                if other is not largest and other not in joining:
                    self.unresolved[other.name] = f"shares {largest.qid} with {largest.name!r}"
            if joining:
                name = display_name(largest.title)
                renamed.update({identity.name: name for identity in [largest, *joining]})
                kept[name] = Identity(name, largest.title, largest.qid, largest.how)
            else:
                kept[largest.name] = largest
        if renamed:
            frame = self.cliopatria.frame.assign(Name=self.cliopatria.frame.Name.replace(renamed))
            self.cliopatria = self.cliopatria.with_frame(frame)
            self.qids = {renamed.get(name, name): qid for name, qid in self.qids.items()}
        return kept

    def _name_as_whole(self) -> None:
        """Cliopatria sometimes names a whole state after one stretch of it: its Western Jin is the Jin dynasty, Eastern
        Jin included, and its Macedonian Empire the ancient kingdom from the 7th century BC. When a state that has ended
        lists a part named like the polity, or another state carries the polity's name as label or alias, within the
        item's dates (give or take 25 years) and spanning at most half the years Cliopatria draws, the polity takes the
        name of the item's article. A country that still exists keeps the name of its present form (the Republic of
        Austria)."""
        named = {}
        for name, identity in self.identities.items():
            whole, entity = display_name(identity.title), self.entities[identity.qid]
            if self.is_polity(entity) and dates(entity)[1] is not None and not same_name(name, whole) \
                    and not same_name(name, entity.label or "") and whole not in self.identities \
                    and self._names_stretch(name, entity):
                named[name] = whole
        for name, whole in named.items():
            identity = self.identities.pop(name)
            self.identities[whole] = Identity(whole, identity.title, identity.qid, identity.how)
            self.qids[whole] = self.qids.pop(name)
        if named:
            frame = self.cliopatria.frame
            self.cliopatria = self.cliopatria.with_frame(frame.assign(Name=frame.Name.replace(named)))

    def _names_stretch(self, name: str, entity: Entity) -> bool:
        start, end = dates(entity)
        rows = self.cliopatria.rows(name)
        drawn = int(rows.ToYear.max() - rows.FromYear.min())
        parts = self.wikidata.entities(entity.ids("P527", preferred_only=False))
        others = self.wikidata.entities([qid for qid in self.wikidata.exact_matches(name) if qid != entity.id])
        classes = {qid for other in others.values() for qid in other.ids("P31", preferred_only=False)}
        self.polity_classes |= self.wikidata.subclass_of(sorted(classes - self.polity_classes), POLITY_ROOTS)
        stretches = [part for part in parts.values() if any(same_name(name, label) for label in
                                                            (part.label, part.enwiki and display_name(part.enwiki)) if label)]
        stretches += [other for other in others.values() if self.is_polity(other)]
        for stretch in stretches:
            first, last = dates(stretch)
            if first is not None and last is not None and start is not None and 2 * (last - first) <= drawn \
                    and start - SHARED_TOLERANCE_YEARS <= first and last <= end + SHARED_TOLERANCE_YEARS:
                return True
        return False

    def _without_peoples(self) -> dict[str, set[str]]:
        """A people or dynasty drawn beside its own state would be asked twice: the Vandals beside the Vandal Kingdom,
        the Seljuk dynasty beside the Great Seljuk Empire, the Göktürks beside the Göktürk Khaganate. A name whose
        article has no country infobox is the people of a state whose article has one when the state gives it as its
        people (P172), when it gives the state as where it applies (P1001), or when it has ended and the state is the
        only one named like it once the kind of state (kingdom, khaganate…) is set aside whose dates overlap its own by
        more than 25 years and whose land meets its own. The people leaves the catalogue and its shapes stay on the
        maps under its own name. Returns each people's item with the items of its states."""
        forms = stems(" ".join(OFFICE_STATES))
        names = lambda name: {frozenset(found) for title in (name, display_name(self.identities[name].title))
                              if (found := stems(title) - forms)}
        by_qid = {identity.qid: name for name, identity in self.identities.items()}
        spans, ended, named, peoples_of = {}, set(), {}, {}
        for name, identity in self.identities.items():
            entity, rows = self.entities[identity.qid], self.cliopatria.rows(name)
            starts = [year.earliest for year in entity.years("P571") + entity.years("P580")]
            ends = [year.latest for year in entity.years("P576") + entity.years("P582")]
            spans[name] = (min(starts, default=int(rows.FromYear.min())), max(ends, default=int(rows.ToYear.max())))
            if ends:
                ended.add(name)
            for found in names(name):
                named.setdefault(found, set()).add(name)
            for people in entity.ids("P172", preferred_only=False):
                peoples_of.setdefault(people, set()).add(name)
        candidates = {}
        for name, identity in self.identities.items():
            applies = self.entities[identity.qid].ids("P1001", preferred_only=False)
            states = peoples_of.get(identity.qid, set()) | {by_qid[qid] for qid in applies if qid in by_qid}
            alike = {other for found in names(name) for other in named[found] if other != name
                     and min(spans[name][1], spans[other][1]) - max(spans[name][0], spans[other][0]) > SHARED_TOLERANCE_YEARS}
            if name in ended and len(alike) == 1 and self._shape(name).intersects(self._shape(next(iter(alike)))):
                states |= alike
            if states - {name}:
                candidates[name] = states - {name}
        titles = {name: self.identities[name].title for name in candidates.keys() | set().union(*candidates.values())}
        texts = self.wikipedia.wikitext(sorted(set(titles.values())))
        filed = {name: bool(country_infobox(texts.get(title, ""))) for name, title in titles.items()}
        found = {name: kept for name, states in candidates.items()
                 if not filed[name] and (kept := sorted(state for state in states if filed[state]))}
        peoples = {self.identities[name].qid: {self.identities[state].qid for state in states} for name, states in found.items()}
        for name, states in found.items():
            del self.identities[name]
            self.qids.pop(name, None)
            self.unresolved[name] = f"the people of {', '.join(states)}"
        return peoples

    def _shape(self, name: str):
        return shapely.union_all(self.cliopatria.rows(name).geometry.values)

    def _within_dates(self, identity: Identity, qid: str) -> bool:
        entity = self.entities[qid]
        starts = [year.earliest for year in entity.years("P571") + entity.years("P580")]
        ends = [year.latest for year in entity.years("P576") + entity.years("P582")]
        rows = self.cliopatria.rows(identity.name)
        return bool(starts and ends) and rows.FromYear.min() >= min(starts) - SHARED_TOLERANCE_YEARS \
            and rows.ToYear.max() <= max(ends) + SHARED_TOLERANCE_YEARS


class PolityBuilder:
    def __init__(self, catalog: PolityCatalog, countries: gpd.GeoDataFrame):
        self.catalog = catalog
        self.cliopatria = catalog.cliopatria
        self.wikipedia = catalog.wikipedia
        self.wikidata = catalog.wikidata
        self.badged = BadgedInfoboxes(self.wikipedia, self.wikidata)
        self.countries = equal_area(countries)
        self.present = int(self.cliopatria.frame.ToYear.max())
        self.succession = RegionalSuccession(self.cliopatria.frame, catalog.qids, countries, self._stated, self._corroborated,
                                             self._implied, equal_area)
        self.founded_early: set[str] = set()
        self.parents: dict[str, set[str]] = {}
        self.members: dict[str, set[str]] = {}
        self.deck_polities: dict[str, Polity] = {}
        self.office_spans: dict[str, dict[str, list[tuple[int, int]]]] = {}
        self.deck: dict[str, str] = {}
        self.wholes: dict[str, list[str]] = {}
        for name, identity in catalog.identities.items():
            entity = catalog.entities.get(identity.qid)
            for whole in entity.ids("P361", preferred_only=False) if entity else []:
                self.wholes.setdefault(whole, []).append(name)

    def select(self, region: str, start: int, end: int) -> list[Identity]:
        area = self.countries[self.countries.name == region].geometry.union_all()
        rows = equal_area(self.cliopatria.active(start, end))
        selected = []
        for name, group in rows.groupby("Name"):
            if name not in self.catalog.identities or group.Area.max() < MIN_PEAK_AREA_KM2:
                continue
            inside = group.geometry.intersection(area).area
            if (inside / area.area).max() >= MIN_REGION_SHARE or (inside / group.geometry.area).max() >= MIN_INSIDE_SHARE:
                selected.append(self.catalog.identities[name])
        return sorted(selected, key=lambda identity: self.cliopatria.rows(identity.name).FromYear.min())

    def select_notable(self) -> list[Identity]:
        """Civilizations that Wikipedia's editors list as vital, that Wikipedia covers in many languages, or that ruled
        a vast territory. Any one is enough, so small early civilizations and large regional empires both qualify. A
        civilization that began in 1945 or later is left out, as the deck's figures are, since it belongs to the
        present more than to history."""
        identities = list(self.catalog.identities.values())
        qids = [identity.qid for identity in identities]
        editions = self.wikidata.language_editions(qids)
        levels = self.catalog.vital_levels
        kinds = self.wikidata.kinds(qids, KINDS)
        selected = [identity for identity in identities
                    if self._notable(identity, editions.get(identity.qid, 0), levels.get(identity.qid), kinds[identity.qid])]
        chosen = {identity.qid for identity in selected}
        selected = [identity for identity in selected if identity.qid not in self.founded_early
                    or not set(self.catalog.entities[identity.qid].ids("P1365", "P155", preferred_only=False)) & chosen]
        selected += self._mapless(levels, selected)
        selected = [identity for identity in selected if self.catalog.years(identity.qid)[0] < LATEST_START]
        return sorted(selected, key=lambda identity: self.catalog.years(identity.qid)[0])

    def _mapless(self, levels: dict[str, int], drawn: list[Identity]) -> list[Identity]:
        """States Wikipedia's editors list as vital that Cliopatria does not draw, such as Classical Athens or Sparta,
        chosen by the same signals but needing both dates, from Wikidata or else the Wikipedia infobox, since there
        are no borders to date them. City-states count although Wikidata also files them as settlements. Left out:
        items Wikidata files as a civilization (Ancient Egypt) and umbrellas whose parts are drawn (Ancient Rome),
        since the drawn states stand in for them, and a state whose capital, or its own location, lies inside drawn
        civilizations of the deck for most of its years: London for the United Kingdom of Great Britain and Ireland,
        or the city of Babylon inside Babylonia. They get no map; figures can belong to them."""
        qids = [qid for qid in levels if qid not in self.catalog.by_qid and qid not in self.catalog.peoples]
        entities = self.wikidata.entities(qids)
        kinds = self.wikidata.kinds(list(entities), MAPLESS_KINDS)
        editions = self.wikidata.language_editions(list(entities))
        drawn_parts = {whole for state in self.catalog.by_qid
                       for whole in self.catalog.entities[state].ids("P361", preferred_only=False)}
        states = {qid: entity for qid, entity in entities.items()
                  if (kinds.get(qid, set()) & STATE_KINDS) and not (kinds.get(qid, set()) & NOT_MAPLESS)
                  and ("settlement" not in kinds.get(qid, set()) or "city-state" in kinds[qid])
                  and entity.enwiki and qid not in drawn_parts
                  and not any(part in self.catalog.by_qid for part in entity.ids("P527", preferred_only=False))
                  and (levels[qid] <= MAX_VITAL_LEVEL or editions.get(qid, 0) >= LEVEL_5_LANGUAGES)}
        texts = self.wikipedia.wikitext([entity.enwiki for entity in states.values()])
        drawn_frame = self.cliopatria.frame[self.cliopatria.frame.Name.isin({identity.name for identity in drawn})]
        capitals = self.wikidata.entities([capital for entity in states.values() for capital in entity.ids("P36")])
        found = []
        for qid, entity in states.items():
            infobox = country_infobox(texts.get(entity.enwiki, "")) or Infobox()
            starts = [year.earliest for year in entity.years("P571") + entity.years("P580")]
            ends = [year.latest for year in entity.years("P576") + entity.years("P582")]
            first = min(starts) if starts else infobox.start.earliest if infobox.start else None
            last = max(ends) if ends else infobox.end.latest if infobox.end else None
            name = display_name(entity.enwiki)
            if first is None or last is None or name in self.catalog.identities:
                continue
            location = next((capitals[c].coordinates() for c in entity.ids("P36") if c in capitals and capitals[c].coordinates()),
                            entity.coordinates())
            if location and self._drawn_share(drawn_frame, location, first, last) > MAX_DRAWN_SHARE:
                continue
            identity = Identity(name, entity.enwiki, qid, "Vital Articles state without Cliopatria borders")
            self.catalog.add_mapless(identity, entity, (first, last))
            found.append(identity)
        return found

    @staticmethod
    def _drawn_share(frame: gpd.GeoDataFrame, location: tuple[float, float], first: int, last: int) -> float:
        """The share of the years from first to last in which a drawn civilization covers the location."""
        rows = frame[frame.geometry.contains(Point(location))]
        years = {year for start, end in zip(rows.FromYear, rows.ToYear)
                 for year in range(max(int(start), first), min(int(end), last) + 1)}
        return len(years) / (last - first + 1)

    def _founded_early(self, identity: Identity, entity: Entity, rows: gpd.GeoDataFrame) -> bool:
        """A state that still exists but that Cliopatria first draws after 1929 qualifies when Wikidata dates its
        founding before 1930, Cliopatria draws it by 1945, the land around its capital reached 2 million km² and it
        does not continue a state already chosen: Canada (1867) and Argentina (1816), not Denmark, which is that large
        only with Greenland. The last test is made once all are chosen."""
        founded = [year.earliest for year in entity.years("P571")]
        if not founded or min(founded) >= LIVING_ON_MAP_BEFORE or rows.FromYear.min() > LATEST_START:
            return False
        largest = rows.loc[rows.Area.idxmax()].geometry
        found = regions(largest)
        location = entity.coordinates()
        region = home(found, [location] if location else [])
        if equal_area_shape(region.geometry, self.cliopatria.frame.crs).area / 1e6 < LIVING_PEAK_KM2:
            return False
        self.founded_early.add(identity.qid)
        return True

    def _notable(self, identity: Identity, languages: int, level: int | None, kinds: set[str]) -> bool:
        """Listed as vital at level 4 or above, in at least 60 languages, at level 5 with 45 languages or a peak of
        1 million km², at a peak of 2 million km² without being a colony, or a large and lasting state: its drawn area
        summed over its years reaching 12.5 million km²·years (250,000 km² for 50 years), with a peak of at least
        50,000 km² and an article in at least 30 languages, not a colony (the Kamakura shogunate, Shu Han, Funan).
        A sovereign state that still exists qualifies only if it was on the maps before 1930 and reached 2 million
        km², as the United States did. Any other polity without an end date must be filed as historical, since
        Cliopatria sometimes links an old polity to today's place, such as the island of Rhodes, and today's provinces
        and towns never qualify."""
        entity = self.catalog.entities[identity.qid]
        rows = self.cliopatria.rows(identity.name)
        peak = rows.Area.max()
        if not entity.years("P576") + entity.years("P582"):
            if "subdivision" in kinds or ("settlement" in kinds and "city-state" not in kinds):
                return False
            if "sovereign state" in kinds:
                if rows.FromYear.min() < LIVING_ON_MAP_BEFORE:
                    if peak < LIVING_PEAK_KM2:
                        return False
                elif not self._founded_early(identity, entity, rows):
                    return False
            elif "historical" not in kinds and identity.how != CULTURE:
                return False
        level = level or MAX_VITAL_LEVEL + 2
        colony = colonial(kinds, rows)
        return (level <= MAX_VITAL_LEVEL or languages >= MIN_LANGUAGE_EDITIONS
                or (level == MAX_VITAL_LEVEL + 1 and (languages >= LEVEL_5_LANGUAGES or peak >= LEVEL_5_PEAK_KM2))
                or (peak >= LARGE_PEAK_KM2 and not colony)
                or (peak >= MIN_PEAK_AREA_KM2 and languages >= LASTING_LANGUAGES and not colony
                    and area_years(rows) >= LASTING_AREA_YEARS))

    def mark_repeated_maps(self, polities: list[Polity]) -> None:
        """A short regime drawn almost exactly like a neighbouring one in time is not asked from its map, since only
        the year would tell them apart: France's Second Republic and Second Empire, Mexico's centralist and federal
        republics. It lasted 30 years or less, its largest shape and an adjacent civilization's overlap by 85% or
        more (intersection over union) at their map years, and the two lie within 30 years of each other."""
        shapes = {}
        for polity in polities:
            rows = self.cliopatria.rows(polity.name)
            shown = rows[(rows.FromYear <= polity.map_year) & (rows.ToYear >= polity.map_year)]
            if polity.borders and not shown.empty:
                shapes[polity.name] = (equal_area_shape(shapely.union_all(shown.geometry.values), self.cliopatria.frame.crs)
                                       .buffer(0), int(rows.FromYear.min()), int(rows.ToYear.max()))
        for polity in polities:
            if polity.name not in shapes:
                continue
            shape, first, last = shapes[polity.name]
            if last - first + 1 > REPEATED_MAP_YEARS:
                continue
            for other, (other_shape, other_first, other_last) in shapes.items():
                if other == polity.name or other_first > last + REPEATED_MAP_YEARS or first > other_last + REPEATED_MAP_YEARS:
                    continue
                if shape.intersects(other_shape) and \
                        shape.intersection(other_shape).area >= REPEATED_MAP_OVERLAP * shape.union(other_shape).area:
                    polity.map_asked = False
                    polity.checks["map_like"] = other
                    break

    def build(self, identities: list[Identity]) -> list[Polity]:
        """Notes for the selected civilizations. A civilization before or after one of them may come from outside
        the deck, as any resolved civilization can be the answer."""
        known = list(self.catalog.identities.values())
        texts = self.wikipedia.wikitext([identity.title for identity in known], follow=True)
        self.infoboxes = {identity.qid: country_infobox(texts.get(identity.title, "")) or Infobox() for identity in known}
        linked = [title for box in self.infoboxes.values()
                  for title in box.capital + box.predecessors + box.successors + box.leaders]
        self.pages = self.wikipedia.resolve(linked)
        leads = {identity.qid: lead_capital_links(texts.get(identity.title, "")) for identity in identities}
        lead_pages = self.wikipedia.resolve([title for titles in leads.values() for title in titles])
        self.lead_capitals = {qid: list(dict.fromkeys(lead_pages[title].qid for title in titles
                                                      if title in lead_pages and lead_pages[title].qid))
                              for qid, titles in leads.items()}
        self.capital_of = self.wikidata.subjects("P1376", [identity.qid for identity in identities])
        referenced = [self.catalog.entities[identity.qid].ids("P36") for identity in known] + \
            list(self.capital_of.values()) + list(self.lead_capitals.values())
        self.names = self.wikidata.entities([qid for ids in referenced for qid in ids] +
                                            [page.qid for page in self.pages.values() if page.qid])
        roots = {SETTLEMENT: "settlement", HERITAGE: "heritage", SITE: "site", STATE: "state", FORMER_OR_CURRENT_STATE: "state",
                 FIRST_LEVEL_DIVISION: "division", SECOND_LEVEL_DIVISION: "division"}
        capitals = sorted({qid for ids in referenced for qid in ids} |
                          {qid for box in self.infoboxes.values() for qid in self._qids(box.capital)})
        self.capital_kinds = self.wikidata.kinds(capitals, roots)
        self.settlements = {qid for qid, found in self.capital_kinds.items() if found - {"state", "division"} == {"settlement"}}
        self.located = {qid: self.names[qid].ids("P131") for qid, found in self.capital_kinds.items()
                        if "settlement" not in found and qid in self.names}
        towns = sorted({town for ids in self.located.values() for town in ids})
        self.names.update(self.wikidata.entities(towns))
        self.towns = {qid for qid, found in self.wikidata.kinds(towns, roots).items() if "settlement" in found}
        self.introductions = self.wikipedia.introductions([identity.title for identity in identities])
        undated = [self.catalog.entities[identity.qid] for identity in identities]
        self.cultures = self.wikidata.entities([qid for entity in undated if not any(entity.years(prop) for prop in DATES)
                                                for qid in entity.ids("P2596")])
        return [self._polity(identity, self.infoboxes[identity.qid]) for identity in identities]

    def _summary(self, identity: Identity, entity: Entity, last_drawn: int | None) -> str:
        """What the civilization was: the opening sentence of its article without its subject, by the rule used for
        what a figure is known for ("The third caliphate to succeed the Islamic prophet Muhammad."), where one word
        beyond the name is enough ("A Russian principality."); or the article's short description when that sentence
        cannot be cut ("The Etruscans created a civilization …") or tells a circumstance ("Under Islamic rule …").
        When the item still exists and that sentence speaks in the present tense, but the map stops drawing it long
        before today, the sentence describes the present (medieval Norway as "a Nordic country comprising …
        Svalbard"), so only a short description that dates itself is taken ("Ancient Mesoamerican city"), else none."""
        extract, description = self.introductions.get(identity.title, ("", None))
        sentence = opening(first_paragraph(extract))
        tense = PRESENT_OR_PAST.search(sentence)
        present = last_drawn is not None and last_drawn < PRESENT_DESCRIPTION_FROM and tense and tense.group(1) in ("is", "are") \
            and not entity.years("P576") + entity.years("P582")
        if present and not (description and DATED.search(description)):
            return ""
        summary = contribution("" if present else sentence, identity.name, description, MIN_WORDS_BEYOND_NAME)
        if CIRCUMSTANCE.match(summary):
            summary = contribution("", identity.name, description, MIN_WORDS_BEYOND_NAME)
        return summary

    def _capital_terms(self, entity: Entity, period: Period, capitals: list[dict]) -> list[dict]:
        """The capitals with the years Wikidata or the infobox dates them as capital within the period, an open term
        running to its start or end (Berlin 1701–1947 for Prussia), whether either dates them at all, and how many of
        the period's years the dated capitals cover between them; a capital is matched to theirs by being within 25 km."""
        start, end = (period.start.earliest, period.end.latest) if period.confirmed else (None, None)
        dated = []
        for claim in entity.statements("P36", preferred_only=False):
            place = self.names.get(claim["mainsnak"]["datavalue"]["value"].get("id"))
            began, ended = qualifier_year(claim, "P580"), qualifier_year(claim, "P582")
            if place and place.coordinates() and (began is not None or ended is not None):
                dated.append((place.coordinates(), began if began is not None else start, ended if ended is not None else end))
        for location, began, ended in self._infobox_capital_years(entity):
            if not any(kilometres(location, other[0]) <= CAPITAL_MATCH_KM for other in dated):
                dated.append((location, began, ended))
        terms, spans = [], []
        for location, began, ended in dated:
            years = 0
            if began is not None and ended is not None:
                if start is not None:
                    began, ended = max(began, start), min(ended, end)
                years = max(0, ended - began)
                if years:
                    spans.append((began, ended))
            terms.append((location, years))
        covered, reach = 0, None
        for began, ended in sorted(spans):
            began = began if reach is None else max(began, reach)
            covered += max(0, ended - began)
            reach = ended if reach is None else max(reach, ended)
        near = lambda capital, location: capital["location"] and kilometres(capital["location"], location) <= CAPITAL_MATCH_KM
        return [{**capital, "years": sum(years for location, years in terms if near(capital, location)),
                 "dated": any(near(capital, location) for location, _ in terms), "covered": covered}
                for capital in capitals]

    def _infobox_capital_years(self, entity: Entity) -> list[tuple]:
        """Where the infobox's capitals with years written beside them stood, and those years."""
        infobox = self.infoboxes.get(entity.id)
        found = []
        for title, (first, last) in (infobox.capital_years.items() if infobox else []):
            page = self.pages.get(title)
            location = self._capital(page.qid)["location"] if page and page.qid else None
            if location:
                found.append((tuple(location), first, last))
        return found

    def _home(self, entity: Entity, period: Period, capitals: list[dict]) -> dict:
        """What the home is found from: the capitals and their years, the years to weigh them in, the places Wikidata
        gives as its capital or location, for a civilization with no capital in hand, and whether the infobox names
        first a capital no source places."""
        places = [Point(self.names[qid].coordinates()) for qid in entity.ids("P36", preferred_only=False)
                  if qid in self.names and self.names[qid].coordinates()]
        if entity.coordinates():
            places.append(Point(entity.coordinates()))
        years = (period.start.earliest, period.end.latest) if period.confirmed else None
        infobox = self.infoboxes.get(entity.id)
        first = next(iter(self._qids(infobox.capital)), None) if infobox else None
        return {"capitals": self._capital_terms(entity, period, capitals), "years": years, "places": tuple(places),
                "unplaced": first is not None and not self._capital(first)["location"]}

    def _capital_edges(self, entity: Entity, period: Period) -> tuple:
        """Where its first and last capitals stood, when Wikidata or the infobox dates at least two of them within its
        confirmed period (not Constantinople for the Roman Empire, dated from 476; Bianjing and Lin'an for the Song)."""
        start, end = (period.start.earliest, period.end.latest) if period.confirmed else (None, None)
        dated = []
        for claim in entity.statements("P36", preferred_only=False):
            value = claim["mainsnak"].get("datavalue", {}).get("value", {})
            place = self.names.get(value.get("id")) if isinstance(value, dict) else None
            began, ended = qualifier_year(claim, "P580"), qualifier_year(claim, "P582")
            if place and place.coordinates() and (began is not None or ended is not None):
                began, ended = began if began is not None else ended, ended if ended is not None else began
                if start is None or (began <= end and ended >= start):
                    dated.append((began, ended, place.coordinates()))
        for location, began, ended in self._infobox_capital_years(entity):
            if (start is None or (began <= end and ended >= start)) \
                    and not any(kilometres(location, other[2]) <= CAPITAL_MATCH_KM for other in dated):
                dated.append((began, ended, location))
        if len(dated) < 2:
            return None, None
        return min(dated)[2], max(dated, key=lambda found: (found[1], found[0]))[2]

    def link_successions(self, polities: list[Polity], figures: list, texts: SuccessionScores) -> None:
        """Once the deck and its figures are known, the civilizations before and after each one, each in a region
        ("before the Mongol Empire in Iraq: the Abbasid Caliphate; in Kyrgyzstan: the Kara-Khitans"). First the holder
        of its home on the map, which must be in the deck or well known (an article in at least 40 Wikipedia
        languages), else the nearest confirmed holder that is, else a holder known in at least 20 languages that the
        sources name and that took the largest share of its land. Then the state its last (first) infobox leader's own
        infobox names as successor (predecessor), when that person led exactly one other deck civilization, which began
        within 30 years and held the civilization's home on the map (Timurid → Mughal through Babur; not Great Britain
        before Hanover, a shared crown). Then the same links seen from the other side (Achaemenid before
        Macedonia). Every answer must be able to have followed: a predecessor began before the civilization, a
        successor ended after it, the later began within a century of the earlier's end, neither was a part the other
        absorbed nor a phase of it under the same name. One civilization per region, at most three a side. The first,
        asked as its own card, is in its home region (today's country at its longest-held capital); the others are
        other regions. An answer from the other side or a leader fills the
        home only when the two overlap by five years at most, or by 30 when it held land near the home at the change,
        and, when both are on the maps, it held the home point at some time. A map answer outside the deck that changed
        more than five years late gives way to a stated state that changed within five years after (North Vietnam, not
        South Vietnam, after the Nguyễn dynasty). A home still empty takes a state outside the
        deck that the sources name and that the maps or its place put at the home (see _stated_neighbour); failing
        that, the state the quality-badged articles on it agree on (see _badged_neighbours); failing that, the state
        three of the independent voters agree on (see _agreed_neighbours)."""
        deck = self.deck_polities = {polity.id: polity for polity in polities}
        editions = self.wikidata.language_editions(list(self.catalog.by_qid))
        known = lambda other: other in deck or editions.get(other, 0) >= KNOWN_NEIGHBOUR_LANGUAGES
        homes_from = {polity.id: self._home(self.catalog.entities[polity.id], polity.period, polity.capitals)
                      for polity in polities}
        anchors = {polity.id: self.succession.home(polity.id, **homes_from[polity.id]) for polity in polities}
        blocked, weak = set(), set()
        for polity in polities:
            judge = Judge(asked=lambda other, qid=polity.id: known(other) and not self._renamed(qid, other),
                          less_known=lambda other, qid=polity.id: editions.get(other, 0) >= LESS_KNOWN_NEIGHBOUR_LANGUAGES
                          and not self._renamed(qid, other),
                          blocking=lambda other, qid=polity.id: known(other) and self._renamed(qid, other),
                          follows=self._follows, years=self._span, spans=self._spans, in_deck=deck.__contains__,
                          followers=self._followers, home=lambda other: anchors[other].region if anchors.get(other) else None)
            before, after, _ = self.succession.neighbours(
                polity.id, homes_from[polity.id]["capitals"], self._capital_edges(self.catalog.entities[polity.id], polity.period),
                judge, homes_from[polity.id]["years"], homes_from[polity.id]["places"], homes_from[polity.id]["unplaced"])
            for side, found in (("before", before), ("after", after)):
                blocked |= {(polity.id, side) for edge in found if edge.get("blocked")}
                weak |= {(polity.id, side, edge["id"]) for edge in found if edge.get("how") in ("mutual", "wide", "less known")}
                setattr(polity, side, self._edges([{key: value for key, value in edge.items() if key != "how"}
                                                   for edge in found if "id" in edge]))
        homes = {polity.id: anchors[polity.id].region if anchors[polity.id] else self._home_region(polity)
                 for polity in polities}
        for polity, side, other in self._ruler_links(polities, figures):
            anchor = anchors[polity.id]
            if anchor is None or not deck[other].borders or self.succession.held(other, anchor.point):
                self._link(polity, side, deck[other], homes[polity.id], WINDOW_YEARS)
        for polity in polities:
            for side, mirror in (("after", "before"), ("before", "after")):
                for edge in list(getattr(polity, side)):
                    if edge["id"] in deck and not edge.get("reserved"):
                        self._link(deck[edge["id"]], mirror, polity, edge["region"])
        named = self.wikidata.language_editions(sorted({other for polity in polities for later in (False, True)
                                                        for other in self._followers(polity.id, later)}))
        edges_of = {polity.id: self._capital_edges(self.catalog.entities[polity.id], polity.period) for polity in polities}
        unanswered: list[tuple[Polity, str]] = []
        for polity in polities:
            for side in ("before", "after"):
                before = side == "before"
                pair = (lambda other: (other, polity.id)) if before else (lambda other: (polity.id, other))
                edges = [edge for edge in getattr(polity, side) if self._in_order(*pair(edge["id"]), before)
                         and (edge.get("reserved") or self._follows(*pair(edge["id"]), before))]
                reserved = {edge["region"] for edge in edges if edge.get("reserved")}
                edges = [edge for edge in edges if not edge.get("reserved")]
                mapped = [edge for edge in edges if edge.get("main")][:1]
                fallback = [edge for edge in mapped if (polity.id, side, edge["id"]) in weak]
                home = [] if fallback else mapped
                closest: dict[str, dict] = {}
                for edge in sorted((edge for edge in edges if edge not in mapped),
                                   key=lambda edge: (self._gap(polity, deck.get(edge["id"]), side), edge["name"])):
                    if edge["region"] not in closest and edge["region"] not in reserved \
                            and all(edge["region"] != own["region"] and edge["id"] != own["id"] for own in home):
                        closest[edge["region"]] = edge
                others = list(closest.values())
                if not home:
                    region = fallback[0]["region"] if fallback else homes[polity.id]
                    anchor = anchors[polity.id]
                    home = [] if (polity.id, side) in blocked else \
                        [edge for edge in others if edge["region"] == region
                         and (anchor is None or self._fills_home(polity, edge["id"], side, anchor.point))][:1] or fallback
                    others = [edge for edge in others if edge["region"] != region]
                if home and home[0]["id"] not in deck and self._begins_late(polity, side, home[0]["id"]):
                    home = self._stated_neighbour(polity, side, homes[polity.id], anchors[polity.id],
                                                  edges_of[polity.id][side == "after"], named, meeting=True) or home
                if not home and (polity.id, side) not in blocked \
                        and not (side == "after" and self.succession.partitioned(polity.id, self._span(polity.id))):
                    home = self._stated_neighbour(polity, side, homes[polity.id], anchors[polity.id],
                                                  edges_of[polity.id][side == "after"], named)
                    unanswered += [] if home else [(polity, side)]
                others = [edge for edge in others if edge not in home]
                plain = lambda edge: {key: value for key, value in edge.items() if key not in ("main", "reserved")}
                setattr(polity, side, [{**plain(edge), "home": True} for edge in home]
                        + [{**plain(edge), "home": False} for edge in others][:SUCCESSION_SLOTS - 1])
        badged = self._badged_neighbours(unanswered, anchors, homes)
        unanswered = [(polity, side) for polity, side in unanswered if (polity.id, side) not in badged]
        for (qid, side), edge in {**badged, **self._agreed_neighbours(unanswered, anchors, homes, texts)}.items():
            setattr(deck[qid], side, [{**edge, "home": True}] + getattr(deck[qid], side))

    def _begins_late(self, polity: Polity, side: str, other: str) -> bool:
        """Whether the answer outside the deck began (ended) more than five years after (before) the civilization ended
        (began)."""
        mine, theirs = self._span(polity.id), self._span(other)
        return bool(mine and theirs) and (theirs[0] - mine[1] if side == "after" else mine[0] - theirs[1]) > CONSECUTIVE_YEARS

    def _stated_neighbour(self, polity: Polity, side: str, region: str, anchor: Anchor | None, edge,
                          editions: dict[str, int], meeting: bool = False) -> list[dict]:
        """For a home side left empty, the state outside the deck that the sources name before (after) it, known in at
        least 20 languages, that ended (began) within 30 years of its start (end) and is not a city, a subdivision, an
        event or a mere occupation zone. The sources are the civilization's Wikidata and infobox and the state's own
        Wikidata. Drawn on the maps, it must have held land within 2 degrees of the home at that change (Singhasari
        before Majapahit), or at some time the home itself when two sources name it; off the maps, two sources must
        name it (the First Intermediate Period after the Old Kingdom of Egypt, the Seventeen Provinces before the Dutch
        Republic). The most named, then one whose place lies in the home country, then the closest in time, stands."""
        before = side == "before"
        span, entity, infobox = self._span(polity.id), self.catalog.entities.get(polity.id), self.infoboxes.get(polity.id)
        if not span or not entity or not region:
            return []
        moment = span[0] if before else span[1]
        point = anchor and (Point(edge) if edge and self.succession.country(edge) == region else anchor.point)
        named = set(entity.ids(*(("P1365", "P155") if before else ("P1366", "P156"))))
        boxed = set(self._qids((infobox.predecessors if before else infobox.successors) if infobox else []))
        candidates = [other for other in sorted(named | boxed)
                      if other not in self.deck_polities and editions.get(other, 0) >= LESS_KNOWN_NEIGHBOUR_LANGUAGES
                      and not self._renamed(polity.id, other)
                      and self._in_order(*((other, polity.id) if before else (polity.id, other)), before)
                      and self._follows(*((other, polity.id) if before else (polity.id, other)), before)]
        items = self.wikidata.entities(candidates)
        self.names.update({other: item for other, item in items.items() if other not in self.names})
        found = []
        for other in candidates:
            item, years = items.get(other), self._span(other)
            if not item:
                continue
            start, end = dates(item)
            change = end if before else start
            if change is None and years:
                change = years[1 if before else 0]
            if change is None or abs(change - moment) > WINDOW_YEARS:
                continue
            if meeting and not 0 <= (moment - change if before else change - moment) <= CONSECUTIVE_YEARS:
                continue
            votes = (other in named) + (other in boxed) + (polity.id in item.ids(*(("P1366", "P156") if before else ("P1365", "P155"))))
            abroad = False
            if point is None or not self.succession.drawn(other):
                place = item.coordinates() or next((self.names[capital].coordinates() for capital in item.ids("P36", preferred_only=False)
                                                    if capital in self.names and self.names[capital].coordinates()), None)
                abroad = point is not None and place is not None and self.succession.country(place) != region
                fits = votes >= 2
            else:
                fits = self.succession.near(other, moment, point) or (votes >= 2 and self.succession.held(other, point, 0))
            if fits and self._is_state(other):
                found.append((-votes, abroad, abs(change - moment), self._name(other), other))
        return [{"id": other, "region": region, "name": name} for _, _, _, name, other in sorted(found)[:1]]

    def _is_state(self, qid: str) -> bool:
        return self._stateful(self.wikidata.kinds([qid], NEIGHBOUR_ROOTS)[qid])

    @staticmethod
    def _stateful(kinds: set[str]) -> bool:
        """Anything but a subdivision, an event, a territory that is only occupied or a city that is not also a city-state."""
        return not kinds & {"subdivision", "event"} and not ("occupied territory" in kinds and not kinds & STATE_KINDS) \
            and not ("settlement" in kinds and "city-state" not in kinds)

    def _state_like(self, qid: str, kinds: set[str]) -> bool:
        """A state by its kinds, or in the deck, and not a subdivision, an event or a city (not a period, a culture or
        a people, which Wikidata gives no state kind)."""
        return self._stateful(kinds) and (bool(kinds & STATE_KINDS) or qid in self.deck_polities)

    def _badged_neighbours(self, sides: list[tuple[Polity, str]], anchors: dict[str, Anchor | None],
                           homes: dict[str, str]) -> dict[tuple[str, str], dict]:
        """For home sides still empty, the state that the civilization's quality-badged Wikipedia articles name first as
        its predecessor (successor) in their infoboxes, when at least two languages agree on it and no other such state
        does as often (see BadgedInfoboxes). It must be in the deck or known in at least 20 languages, a state by its
        kinds, able to have followed it (see _follows), not named like it, and not yet an answer on that side; drawn on
        the maps, it must have its own home in the region or have held the civilization's home."""
        votes = self.badged.votes(sorted({polity.id for polity, _ in sides}))
        named = {(polity.id, side): {other: languages for other, languages in votes[polity.id][side].items()
                                     if len(languages) >= BADGED_LANGUAGES} for polity, side in sides}
        chosen = sorted({other for found in named.values() for other in found})
        items = self.wikidata.entities(chosen)
        self.names.update({other: item for other, item in items.items() if other not in self.names})
        editions, kinds = self.wikidata.language_editions(chosen), self.wikidata.kinds(chosen, NEIGHBOUR_ROOTS)
        agreed = {}
        for polity, side in sides:
            before, answered = side == "before", {edge["id"] for edge in getattr(polity, side)}
            pair = lambda other: (other, polity.id) if before else (polity.id, other)
            fitting = {other: languages for other, languages in named[polity.id, side].items()
                       if homes[polity.id] and other in items and other not in answered
                       and (other in self.deck_polities or editions.get(other, 0) >= LESS_KNOWN_NEIGHBOUR_LANGUAGES)
                       and self._state_like(other, kinds[other]) and not self._renamed(polity.id, other)
                       and self._in_order(*pair(other), before) and self._follows(*pair(other), before)
                       and self._at_home(other, homes[polity.id], anchors[polity.id], anchors,
                                      (self._span(polity.id) or (None, None))[0 if before else 1])}
            ranked = sorted(fitting, key=lambda other: -len(fitting[other]))
            if ranked and (len(ranked) == 1 or len(fitting[ranked[0]]) > len(fitting[ranked[1]])):
                agreed[polity.id, side] = {"id": ranked[0], "region": homes[polity.id], "name": self._name(ranked[0])}
        return agreed

    def _at_home(self, other: str, region: str, anchor: Anchor | None, anchors: dict[str, Anchor | None], moment: int | None) -> bool:
        """A deck state drawn on the maps may stand in the region when its own home is there or it held the
        civilization's home (not Sweden-Norway after Denmark-Norway). A state outside the deck drawn on the maps must
        have held the home or land within 2 degrees of it within 30 years of the moment (not the Taifa of Granada
        before the Almoravids in Morocco)."""
        if not self.succession.drawn(other):
            return True
        if other not in self.deck_polities:
            return anchor is None or moment is None or self.succession.held(other, anchor.point, 0) \
                or self.succession.near(other, moment, anchor.point)
        return bool(anchors.get(other) and anchors[other].region == region) or bool(anchor and self.succession.held(other, anchor.point, 0))

    def _agreed_neighbours(self, sides: list[tuple[Polity, str]], anchors: dict[str, Anchor | None], homes: dict[str, str],
                           texts: SuccessionScores) -> dict[tuple[str, str], dict]:
        """For home sides still empty, the state the sources agree on. The candidates are those Wikidata names on the
        civilization's side, Wikidata's items that name the civilization, and its infobox, when known in the deck or in
        at least 20 languages, a state by its kinds (not a war, battle, city, period or archaeological culture), able to
        have followed it (see _follows) and not yet an answer; items of one state count once. Each voter puts forward its
        first candidate (tied candidates share the vote; a voter whose best has less than 0.3 of support backs none): the
        civilization's own Wikidata statements, the items naming it, the infobox's order, the date the state ended (began)
        against the civilization's start (end), the maps' holding of the home, and, among several candidates, a
        language-inference model reading the civilization's article. A candidate is filled when three voters put it first
        and, among several, it leads the runner-up by two votes."""
        ids = sorted({polity.id for polity, _ in sides})
        converse = {prop: self.wikidata.subjects(prop, ids) for prop in SUCCESSION_PROPERTIES}
        named = {}
        for polity, side in sides:
            before, infobox = side == "before", self.infoboxes.get(polity.id)
            named[polity.id, side] = {
                "own": self.catalog.entities[polity.id].ids(*(("P1365", "P155") if before else ("P1366", "P156"))),
                "converse": [other for prop in (("P156", "P1366") if before else ("P155", "P1365")) for other in converse[prop][polity.id]],
                "boxed": self._qids((infobox.predecessors if before else infobox.successors) if infobox else [])}
        every = sorted({other for found in named.values() for others in found.values() for other in others})
        items = self.wikidata.entities(every)
        self.names.update({other: item for other, item in items.items() if other not in self.names})
        editions, kinds = self.wikidata.language_editions(every), self.wikidata.kinds(every, NEIGHBOUR_ROOTS)
        usable = lambda other: other in items and (other in self.deck_polities or editions.get(other, 0) >= LESS_KNOWN_NEIGHBOUR_LANGUAGES) \
            and self._state_like(other, kinds[other])
        agreement = {}
        for polity, side in sides:
            before, span = side == "before", self._span(polity.id)
            pair = (lambda other: (other, polity.id)) if before else (lambda other: (polity.id, other))
            found, answered = named[polity.id, side], {edge["id"] for edge in getattr(polity, side)}
            candidates = [other for other in sorted({other for others in found.values() for other in others})
                          if span and homes[polity.id] and other != polity.id and other not in answered and usable(other)
                          and not self._renamed(polity.id, other) and self._in_order(*pair(other), before)
                          and self._follows(*pair(other), before)
                          and self._at_home(other, homes[polity.id], anchors[polity.id], anchors, span[0 if before else 1])]
            states = self._one_state_each(candidates, items, editions)
            votes = self._votes(polity, side, states, found, items, anchors[polity.id], span, texts) if states else []
            if chosen := agreed(votes, len(states)):
                agreement[polity.id, side] = {"id": chosen, "region": homes[polity.id], "name": self._name(chosen)}
        return agreement

    def _votes(self, polity: Polity, side: str, states: dict[str, set[str]], found: dict[str, list[str]],
               items: dict[str, Entity], anchor: Anchor | None, span: tuple[int, int], texts: SuccessionScores) -> list[dict]:
        """The support each voter gives for the candidate states, each state with the items that stand for it."""
        before = side == "before"
        best = lambda support: {state: max((support(other) for other in members), default=0.0) for state, members in states.items()}
        support = [best(lambda other: float(other in found["own"])),
                   best(lambda other: float(other in found["converse"])),
                   best(lambda other: 1 / (1 + found["boxed"].index(other)) if other in found["boxed"] else 0.0),
                   best(lambda other: self._date_support(items[other], other, span, side))]
        if anchor:
            backing = self.succession.backing(polity.id, sorted(set().union(*states.values())), before, anchor, span)
            support.append(best(backing.get))
        if len(states) > 1:
            civilization = self._article(polity.wikipedia)
            support.append(best(lambda other: text_score(side, texts, civilization, polity.name, self._article(items[other].enwiki),
                                                         items[other].label or other)))
        return support

    def _article(self, title: str | None) -> Article:
        return Article.read(title or "", self.wikipedia.wikitext([title], follow=True).get(title, "") if title else "")

    def _one_state_each(self, candidates: list[str], items: dict[str, Entity], editions: dict[str, int]) -> dict[str, set[str]]:
        """The candidates grouped by state, those named alike or one said to be the same as the other (P460) together,
        under the best known: in the deck, then in most languages."""
        groups: list[set[str]] = []
        for other in candidates:
            same = [group for group in groups if any(self._renamed(other, member) or member in items[other].ids("P460", preferred_only=False)
                                                     or other in items[member].ids("P460", preferred_only=False) for member in group)]
            groups = [group for group in groups if group not in same] + [{other}.union(*same)]
        best = lambda group: max(sorted(group), key=lambda member: (member in self.deck_polities, editions.get(member, 0)))
        return {best(group): group for group in groups}

    def _date_support(self, item: Entity, other: str, span: tuple[int, int], side: str) -> float:
        """1 when the state ended (began) within 40 years before to 30 after the civilization's start (30 before to 40 after
        its end), falling off over 40 years beyond that; 0 when its date is unknown."""
        before = side == "before"
        start, end = dates(item)
        change, years = end if before else start, self._span(other)
        if change is None and years:
            change = years[1 if before else 0]
        if change is None:
            return 0.0
        low, high = DATE_WINDOWS[side]
        offset = change - span[0 if before else 1]
        return math.exp(-max(0, low - offset, offset - high) / DATE_DECAY_YEARS)

    def _fills_home(self, polity: Polity, other: str, side: str, point) -> bool:
        """An answer from the other side or a leader may stand in the home region when the two overlap by five years at
        most, or by 30 when it held land within 2 degrees of the home at the change; when both are on the maps, only if
        it held the home point at some time (not Srivijaya, which never held Majapahit's Trowulan, before Majapahit)."""
        if self.succession.drawn(polity.id) and self.succession.drawn(other) and not self.succession.held(other, point, 0):
            return False
        if self._apart(polity.id, other, side, HOME_OVERLAP_YEARS):
            return True
        if not self._apart(polity.id, other, side, WINDOW_YEARS):
            return False
        span = self._span(polity.id)
        if not self.succession.drawn(other) or not span:
            return self.succession.drawn(other) or other not in self.deck_polities or not self.deck_polities[other].borders
        return self.succession.near(other, span[0] if side == "before" else span[1], point)

    def _apart(self, qid: str, other: str, side: str, tolerance: int) -> bool:
        mine, theirs = self._span(qid), self._span(other)
        if not mine or not theirs:
            return True
        return theirs[1] <= mine[0] + tolerance if side == "before" else theirs[0] >= mine[1] - tolerance

    def _follows(self, first: str, last: str, earlier_asked: bool) -> bool:
        """Whether the second can have followed the first: the predecessor asked began before the civilization, the
        successor asked ended after it (Assyria is not followed by the Neo-Assyrian Empire, which ended first); the
        first was not a part absorbed by the second (Wikidata's part of, or the second's parts), unless it ended within
        five years of the second's start (the Kingdom of Castile in the Crown of Castile); the answer is not a part of
        the civilization, by its country or part of, that ended with it (the Antigonid dynasty of Macedonia); and they
        are not phases of one state under a shared name, one period within the other (Qi and Qin)."""
        earlier, later = self._span(first), self._span(last)
        if earlier and later and not (earlier[0] < later[0] if earlier_asked else later[1] > earlier[1]):
            return False
        joined = earlier and later and abs(later[0] - earlier[1]) <= CONSECUTIVE_YEARS
        if (last in self._parents(first) or first in self._members(last)) and not joined:
            return False
        answer, state = (first, last) if earlier_asked else (last, first)
        entity = self.catalog.entities.get(answer) or self.names.get(answer)
        if earlier and later and abs(earlier[1] - later[1]) <= CONSECUTIVE_YEARS and entity \
                and state in entity.ids("P17", "P361", preferred_only=False):
            return False
        return not (earlier and later and stems(self._name(first)) & stems(self._name(last))
                    and (earlier[0] <= later[0] and earlier[1] >= later[1] or later[0] <= earlier[0] and later[1] >= earlier[1]))

    def _in_order(self, first: str, last: str, earlier_asked: bool) -> bool:
        """Whether the second can have followed the first in time: the predecessor asked began before the civilization,
        the successor asked ended after it, and the later began within a century of the earlier's end. The years are the
        confirmed period, else the core its sources agree on, else the years since it began or the years it is drawn."""
        earlier, later = self._period_years(first), self._period_years(last)
        if not earlier or not later:
            return True
        return (earlier[0] < later[0] if earlier_asked else later[1] > earlier[1]) and later[0] - earlier[1] <= SUCCESSION_GAP_YEARS

    def _period_years(self, qid: str) -> tuple[int, int] | None:
        polity = self.deck_polities.get(qid)
        period = polity.period if polity else None
        if period and period.confirmed:
            return period.start.earliest, period.end.latest
        if period and period.core:
            return tuple(period.core)
        if period and period.since:
            return period.since.earliest, PRESENT_YEAR
        return self._span(qid)

    def _renamed(self, qid: str, other: str) -> bool:
        """The two are named alike and are not consecutive states: the later began within five years of the earlier's
        end, and both began and ended more than five years apart (the Kingdom and the Crown of Castile, but not Qin and
        the Qin dynasty, which began inside it)."""
        if not same_name(self._name(qid), self._name(other)):
            return False
        mine, theirs = self._span(qid), self._span(other)
        if not mine or not theirs:
            return True
        earlier, later = sorted((mine, theirs))
        return not (abs(later[0] - earlier[1]) <= CONSECUTIVE_YEARS and later[0] - earlier[0] > CONSECUTIVE_YEARS
                    and later[1] - earlier[1] > CONSECUTIVE_YEARS)

    def _span(self, qid: str) -> tuple[int, int] | None:
        """A state's years: a deck civilization's confirmed period, else the years the maps draw it, else Wikidata's."""
        polity = self.deck_polities.get(qid)
        if polity and polity.period.confirmed:
            return polity.period.start.earliest, polity.period.end.latest
        if qid in self.catalog.by_qid:
            return self.catalog.years(qid)
        rows = self.succession.frame[self.succession.frame.qid == qid]
        return (int(rows.FromYear.min()), int(rows.ToYear.max())) if not rows.empty else None

    def _spans(self, qid: str) -> list[tuple[int, int]]:
        polity = self.deck_polities.get(qid)
        core = [tuple(polity.period.core)] if polity and not polity.period.confirmed and polity.period.core else []
        return [span for span in [self._span(qid)] + core if span]

    def _members(self, qid: str) -> set[str]:
        if qid not in self.members:
            entity = self.catalog.entities.get(qid) or self.wikidata.entities([qid]).get(qid)
            self.members[qid] = set(entity.ids("P527", preferred_only=False)) if entity else set()
        return self.members[qid]

    def _link(self, polity: Polity, side: str, other: Polity, region: str, gap: int = SUCCESSION_GAP_YEARS) -> None:
        """Add the other civilization before or after this one in the region, unless it is listed already, the region
        is unknown, or they do not follow each other: the later one must begin after the earlier begins, within a
        century of its end, and end after it ends (not the Confederacy before the USA), by their periods or, for one
        unconfirmed, its core years (the Four Oirats before the Dzungar Khanate). The closest in time keeps a region
        that several share."""
        edges = getattr(polity, side)
        earlier, later = (other, polity) if side == "before" else (polity, other)
        fits = lambda first, second: first and second and first[0] < second[0] and first[1] < second[1] \
            and abs(second[0] - first[1]) <= gap
        if not region or self._renamed(polity.id, other.id) or not any(
                fits(self._ruled_years(earlier, core), self._ruled_years(later, other_core))
                for core in (False, True) for other_core in (False, True)):
            return
        if all(edge["id"] != other.id for edge in edges):
            edges.append({"id": other.id, "region": region, "name": other.name})

    def _gap(self, polity: Polity, other: Polity | None, side: str) -> int:
        if other is None or not self._ruled_years(polity) or not self._ruled_years(other):
            return SUCCESSION_GAP_YEARS
        earlier, later = (other, polity) if side == "before" else (polity, other)
        return abs(self._ruled_years(later)[0] - self._ruled_years(earlier)[1])

    @staticmethod
    def _home_region(polity: Polity) -> str:
        """The region of the map's own answers, or else the first of today's countries on its territory."""
        own = [edge["region"] for edge in polity.before + polity.after if edge.get("region")]
        return own[0] if own else (polity.modern_countries[0] if polity.modern_countries else "")

    def _ruler_links(self, polities: list[Polity], figures: list) -> list[tuple[Polity, str, str]]:
        """Successions the rulers' own infoboxes give: the first and last leaders a civilization's infobox lists,
        and the predecessor and successor each one's infobox names, when that person led exactly one other deck
        civilization (named among its infobox leaders, or a deck figure filed under it)."""
        led: dict[str, set[str]] = {}
        leaders = {polity.id: self.infoboxes[polity.id].leaders for polity in polities if polity.id in self.infoboxes}
        pages = self.wikipedia.resolve(sorted({title for titles in leaders.values() for title in titles}))
        for qid, titles in leaders.items():
            for title in titles:
                if title in pages and pages[title].qid:
                    led.setdefault(pages[title].qid, set()).add(qid)
        names = {polity.name: polity.id for polity in polities}
        for figure in figures:
            home = next((names[name] for name in figure.civilizations if name in names), None)
            if home:
                led.setdefault(figure.id, set()).add(home)
        ends = sorted({titles[index] for titles in leaders.values() if titles for index in (0, -1)})
        texts = self.wikipedia.wikitext([pages[title].title for title in ends if title in pages])
        named = {title: ruler_succession(texts.get(pages[title].title, "")) for title in ends if title in pages}
        linked = self.wikipedia.resolve(sorted({other for found in named.values() for side in found.values() for other in side}))
        links = []
        by_id = {polity.id: polity for polity in polities}
        for qid, titles in leaders.items():
            if not titles:
                continue
            for side, title, key in (("before", titles[0], "predecessor"), ("after", titles[-1], "successor")):
                people = [linked[other].qid for other in named.get(title, {}).get(key, []) if other in linked and linked[other].qid]
                states = set().union(set(), *(led.get(person, set()) for person in people))
                if len(states) == 1 and qid not in states:
                    links.append((by_id[qid], side, states.pop()))
        return links

    def _stated(self, first: str, last: str) -> bool:
        """Wikidata or a Wikipedia infobox, on either civilization's side, says that the second followed the first."""
        return self._wikidata_says(first, last) or self._infobox_says(first, last)

    def _corroborated(self, first: str, last: str) -> bool:
        """Wikidata and a Wikipedia infobox both say that the second followed the first."""
        return self._wikidata_says(first, last) and self._infobox_says(first, last)

    def _wikidata_says(self, first: str, last: str) -> bool:
        earlier, later = self.catalog.entities.get(first), self.catalog.entities.get(last)
        return bool((earlier and last in earlier.ids("P1366", "P156")) or (later and first in later.ids("P1365", "P155")))

    def _infobox_says(self, first: str, last: str) -> bool:
        return bool((first in self.infoboxes and last in self._qids(self.infoboxes[first].successors))
                    or (last in self.infoboxes and first in self._qids(self.infoboxes[last].predecessors)))

    def _implied(self, first: str, last: str) -> bool:
        """The sources name a part of the second as following the first, or a part of the first as preceding the
        second, by Wikidata's part of or country: the Aztecs were followed by New Spain, part of the Spanish Empire.
        Never between a state and a part of itself (the French Consulate in the First Republic)."""
        if first in self._parents(last) or last in self._parents(first):
            return False
        return any(self._part_of(qid, last, first, later=True) for qid in self._followers(first, later=True)) \
            or any(self._part_of(qid, first, last, later=False) for qid in self._followers(last, later=False))

    def _part_of(self, part: str, state: str, other: str, later: bool) -> bool:
        """Whether the part belongs to the state: Wikidata files it under the state, or under a country that lists
        the state among its phases, when the part began (ended) within 30 years of the other civilization's end
        (start). Carthage was followed in 146 BC by the province of Africa, whose country Ancient Rome lists the
        Roman Republic. A part of a group of states (the Five Dynasties) is not a part of its neighbours."""
        if state in self._parents(part):
            return True
        entity = self.catalog.entities.get(part) or self.wikidata.entities([part]).get(part)
        span = self._span(other)
        if not entity or not span:
            return False
        starts = [year.earliest for year in entity.years("P571") + entity.years("P580")]
        ends = [year.latest for year in entity.years("P576") + entity.years("P582")]
        moment = min(starts, default=None) if later else max(ends, default=None)
        countries = set(entity.ids("P17", preferred_only=False)) - {part}
        return moment is not None and abs(moment - (span[1] if later else span[0])) <= WINDOW_YEARS \
            and any(state in self._members(country) for country in countries)

    def _followers(self, qid: str, later: bool) -> set[str]:
        """The states Wikidata or the infobox say came after (or before) the civilization."""
        entity = self.catalog.entities.get(qid)
        found = set(entity.ids(*(("P1366", "P156") if later else ("P1365", "P155")))) if entity else set()
        if qid in self.infoboxes:
            infobox = self.infoboxes[qid]
            found |= set(self._qids(infobox.successors if later else infobox.predecessors))
        return found

    def _parents(self, qid: str) -> set[str]:
        if qid not in self.parents:
            entity = self.catalog.entities.get(qid) or self.wikidata.entities([qid]).get(qid)
            self.parents[qid] = set(entity.ids("P361", "P17", preferred_only=False)) if entity else set()
        return self.parents[qid]

    def _qids(self, titles: list[str]) -> list[str]:
        return list(dict.fromkeys(self.pages[title].qid for title in titles if title in self.pages and self.pages[title].qid))

    def _edges(self, neighbours: list[dict]) -> list[dict]:
        return [{**neighbour, "name": self._name(neighbour["id"])} for neighbour in neighbours]

    def _capitals(self, entity: Entity, infobox: Infobox, rows, period: Period) -> list[dict]:
        """Capitals that Wikidata and the infobox both name: the same item, or two items for the same place within
        25 km, as when Wikidata names the prefecture Shuntian Fu and the infobox the city of Beijing. They are shown
        under the name of the place Wikidata lists, once per place (Bursa rather than the infobox's Prusa, Ayutthaya rather
        than its historical park); capitals 5 km or more apart are separate places, as Venice and Malamocco are, or the infobox's name when Wikidata's item is wider than a place. An item that
        is not a settlement, such as a historical park or an archaeological site (Ayutthaya Historical Park, Abbasid
        Samarra), is shown as the town it lies in when that town is within 25 km. A state is never a capital, though a city that Wikidata also files as an administrative
        unit or a state, like Paris, Bucharest or Tenochtitlan, is; a state one source names stands for the settlement the other
        names, or that Wikidata files as the civilization's capital, within 25 km (the Free City of Frankfurt for Frankfurt).
        Once the two agree on one, the infobox's others count too when it dates them within the civilization's years and
        they lie in its territory (Nimrud and Nineveh beside Assur). When the two sources share none, a capital either
        names counts if it lies in the civilization's own territory on the map (Hattusa, Susa, Gordion), or, for a
        civilization without a map, within 25 km of where Wikidata places it (Sparta); failing both, a settlement or site
        linked in the first sentence of the article's lead that speaks of a capital, by the same test (Heian-kyō for
        Heian Japan). Wikidata's capitals of every rank count, but
        one it dates only outside the confirmed period (Cairo, seat of the Abbasid caliphs after the fall of
        Baghdad) or only from 1945 on (Brasília) is left out."""
        outside = self._capitals_outside(entity, period)
        listed = [qid for qid in entity.ids("P36", preferred_only=False) if qid not in outside]
        places = [(qid, found.coordinates()) for qid, found in ((qid, self.names.get(qid)) for qid in listed)
                  if found and found.coordinates()]
        stand_ins = places + [(qid, self.names[qid].coordinates()) for qid in self.capital_of.get(entity.id, [])
                              if qid in self.names and self.names[qid].coordinates()]
        capitals = []
        for qid in self._qids(infobox.capital):
            if not self._can_be_capital(qid):
                qid = self._settlement_at(qid, stand_ins)
            if not qid or qid in outside:
                continue
            capital = self._capital(qid)
            location = capital["location"]
            near = [place for place, coordinates in places if location and kilometres(location, coordinates) <= CAPITAL_MATCH_KM]
            nearby = bool(near)
            source = qid
            if near and qid not in listed and near[0] in self.settlements:
                capital, source = {**self._capital(near[0]), "location": location}, near[0]
            town = self._town_of(source, location)
            if town:
                capital = {**self._capital(town), "location": location}
            if not (qid in listed or nearby) or capital["name"] in {found["name"] for found in capitals}:
                continue
            if not any(location and found["location"] and kilometres(location, found["location"]) <= SAME_PLACE_KM
                       for found in capitals):
                capitals.append(capital)
        if rows.empty:
            home = entity.coordinates()
            holds = lambda location: home is not None and kilometres(location, home) <= CAPITAL_MATCH_KM
        else:
            territory = shapely.union_all(rows.geometry.values).buffer(CAPITAL_TOLERANCE_DEGREES)
            holds = lambda location: territory.contains(Point(location))
        if capitals:
            self._add_held(capitals, self._dated_capitals(infobox, period), holds, outside)
            return capitals
        self._add_held(capitals, list(dict.fromkeys(listed + self._qids(infobox.capital))), holds, outside)
        if not capitals:
            lead = [qid for qid in self.lead_capitals.get(entity.id, [])
                    if self.capital_kinds.get(qid, set()) & {"settlement", "site"}]
            self._add_held(capitals, lead, holds, outside)
        return capitals

    def _add_held(self, capitals: list[dict], candidates: list[str], holds, outside: set[str]) -> None:
        """Adds the candidates that lie in the civilization's territory, once per place."""
        for qid in candidates:
            location = self._capital(qid)["location"]
            if self._can_be_capital(qid) and qid not in outside and location and holds(location):
                capital = self._shown(qid, location)
                if capital["name"] not in {found["name"] for found in capitals} and not any(
                        found["location"] and kilometres(location, found["location"]) <= SAME_PLACE_KM for found in capitals):
                    capitals.append(capital)

    def _dated_capitals(self, infobox: Infobox, period: Period) -> list[str]:
        """The infobox's capitals with years written beside them that fall within the confirmed period, or any years
        when the period is not confirmed."""
        found = []
        for title, (first, last) in infobox.capital_years.items():
            if title in self.pages and self.pages[title].qid and (not period.confirmed or (
                    first <= period.end.latest and last >= period.start.earliest)):
                found.append(self.pages[title].qid)
        return list(dict.fromkeys(found))

    @staticmethod
    def _capitals_outside(entity: Entity, period: Period) -> set[str]:
        """Capitals Wikidata dates only outside the confirmed period, or only from 1945 on (Brasília)."""
        start, end = (period.start.earliest, period.end.latest) if period.confirmed else (None, None)
        terms: dict[str, list[bool]] = {}
        for claim in entity.statements("P36", preferred_only=False):
            qid = claim["mainsnak"]["datavalue"]["value"].get("id")
            began, ended = qualifier_year(claim, "P580"), qualifier_year(claim, "P582")
            late = began is not None and began >= LATEST_START
            dated = start is not None and began is not None and ended is not None and (ended < start or began > end)
            terms.setdefault(qid, []).append(late or dated)
        return {qid for qid, outside in terms.items() if all(outside)}

    def _can_be_capital(self, qid: str) -> bool:
        """Any place but a state: a municipality like Bucharest is one, and so is a city that is also a state, even one
        Cliopatria draws (Tenochtitlan, Kathmandu); a kingdom named as a capital is not, nor is a province or county
        an infobox lists beside its city (Phitsanulok province for Ayutthaya)."""
        kinds = self.capital_kinds.get(qid, set())
        return "settlement" in kinds or (qid not in self.catalog.by_qid and not kinds & {"state", "division"})

    def _settlement_at(self, qid: str, places: list) -> str | None:
        """The settlement Wikidata names as a capital where a state the infobox names as one lies."""
        location = self._capital(qid)["location"]
        return next((place for place, coordinates in places if place in self.settlements and self._can_be_capital(place)
                     and location and kilometres(location, coordinates) <= CAPITAL_MATCH_KM), None)

    def _shown(self, qid: str, location) -> dict:
        """A capital under its own name, or the town it lies in when it is a site rather than a settlement."""
        town = self._town_of(qid, location)
        return {**self._capital(town), "location": location} if town else self._capital(qid)

    def _town_of(self, qid: str, location) -> str | None:
        """The town within 25 km that a place which is not itself a settlement lies in. An archaeological site
        keeps its own name unless that is the town's with a description added (Gordion, not the modern Polatlı;
        Abbasid Samarra as Samarra)."""
        found = next((town for town in self.located.get(qid, []) if town in self.towns and location
                      and self.names[town].coordinates()
                      and kilometres(location, self.names[town].coordinates()) <= CAPITAL_MATCH_KM), None)
        if found and self.capital_kinds.get(qid, set()) == {"site"} and self._own_name(found) not in self._own_name(qid):
            return None
        return found

    def _capital(self, qid: str) -> dict:
        entity = self.names.get(qid)
        location = entity.coordinates() if entity else None
        founded = min((year.earliest for year in entity.years("P571")), default=None) if entity else None
        return {"name": place_name(self._own_name(qid)), "location": list(location) if location else None, "founded": founded}

    def _name(self, qid: str) -> str:
        return self.catalog.by_qid[qid].name if qid in self.catalog.by_qid else self._own_name(qid)

    def _own_name(self, qid: str) -> str:
        """The item's own name, so that a city Cliopatria also draws as a state is named as the city (Luang Prabang,
        not the Kingdom of Luang Phrabang)."""
        entity = self.names.get(qid)
        return display_name(entity.enwiki) if entity and entity.enwiki else (entity.label if entity and entity.label else qid)

    def _polity(self, identity: Identity, infobox: Infobox) -> Polity:
        entity = self.catalog.entities[identity.qid]
        rows = self.cliopatria.rows(identity.name)
        drawn = not rows.empty
        first, last = self.catalog.years(identity.qid)
        period = self._period(self._dated(entity), infobox, first if drawn else None, last if drawn else None)
        capitals = self._capitals(entity, infobox, rows, period)
        parts = [self.catalog.by_qid[qid].name for qid in entity.ids("P527", preferred_only=False)
                 if qid in self.catalog.by_qid and qid != identity.qid]
        map_year, extent = self._peak(identity.name, parts, rows, period, capitals) if drawn else (first, None)
        if drawn and (pieces := self._pieces(identity, map_year, parts)):
            parts += pieces
            extent = self._territory([identity.name, *parts], map_year).geometry.union_all()
        home = self._home(entity, period, capitals)
        anchor = self.succession.home(identity.qid, **home)
        modern = self._modern_countries(extent, capitals, entity.coordinates()) if drawn \
            else (self._country_at([capital["location"] for capital in capitals if capital["location"]]
                                   or [entity.coordinates()]), [])
        polity = Polity(
            id=identity.qid,
            slug=slugify(identity.name),
            name=identity.name,
            wikipedia=identity.title,
            map_year=map_year,
            period=period,
            capitals=capitals,
            parts=parts,
            before=[],
            after=[],
            key_figures=self._rulers(identity.qid, self._qids(infobox.leaders))[:SHOWN_RULERS],
            modern_countries=modern[0],
            overseas_countries=modern[1],
            borders=drawn,
            summary=self._summary(identity, entity, last if drawn else None),
        )
        polity.checks = {
            "infobox": bool(infobox.start or infobox.capital or infobox.leaders),
            "capital_sources": {"wikidata": [self._name(q) for q in entity.ids("P36")],
                                "infobox": [self._name(q) for q in self._qids(infobox.capital)]},
            "cliopatria_years": [first, last] if drawn else [],
            "identity": identity.how,
            "home": (anchor.place if anchor else "") or "the spot held longest",
        }
        return polity

    def _pieces(self, identity: Identity, year: int, parts: list[str]) -> list[str]:
        """What the map paints with the civilization in its year besides the parts Wikidata lists: the polities
        Cliopatria files under its grouping for the same item, all of them for a colonial empire or power by its
        Wikidata kind (the British Raj, British Africa and Britain itself in the British Empire, Portugal in the
        Portuguese Empire) and otherwise those bordering its own territory, so a French regime does not repaint the
        French colonial empire; and those whose item Wikidata files as part of it (French Indochina in the French
        colonial empire)."""
        shown = self.cliopatria.at(year)
        members = self.cliopatria.members(identity.qid, year)
        if not self.colonial_power(identity.qid):
            own = self._territory([identity.name, *parts], year).geometry.union_all().buffer(BORDER_DEGREES)
            members = [name for name in members if shown[shown.Name == name].geometry.intersects(own).any()]
        found = members + [name for name in self.wholes.get(identity.qid, []) if name in set(shown.Name)]
        return [name for name in dict.fromkeys(found) if name not in (identity.name, *parts)]

    def follow_map_years(self, polities: list[Polity], overrides: list) -> None:
        """Redraws a civilization whose map year an override set: its parts follow the new year, and so do its modern
        countries unless an override sets them too."""
        years = {o.civilization_id for o in overrides if o.field == "map_year" and o.action == "set"}
        countries = {o.civilization_id for o in overrides if o.field == "modern_countries" and o.action == "set"}
        for polity in polities:
            if polity.id not in years or not polity.borders:
                continue
            identity = self.catalog.identities[polity.name]
            listed = [self.catalog.by_qid[qid].name for qid in self.catalog.entities[identity.qid].ids("P527", preferred_only=False)
                      if qid in self.catalog.by_qid and qid != identity.qid]
            polity.parts = listed + self._pieces(identity, polity.map_year, listed)
            extent = self._territory([identity.name, *polity.parts], polity.map_year).geometry.union_all()
            if polity.id not in countries and not extent.is_empty:
                polity.modern_countries, polity.overseas_countries = self._modern_countries(
                    extent, polity.capitals, self.catalog.entities[identity.qid].coordinates())

    def colonial_power(self, qid: str) -> bool:
        return bool(set(self.catalog.entities[qid].ids("P31", preferred_only=False)) & COLONIAL_POWERS)

    def _territory(self, names: list[str], year: int) -> gpd.GeoDataFrame:
        territory = self.cliopatria.at(year)
        return territory[territory.Name.isin(names)]

    def _peak(self, name: str, parts: list[str], rows: gpd.GeoDataFrame, period: Period, capitals: list[dict]):
        """The year of the largest extent, looking only inside the confirmed period when there is one (a map that begins
        before it is shown from its first year) and up to 1945 where the civilization was drawn by then, counted with
        the parts Wikidata lists (Spain for the Spanish Empire) and preferring years when the civilization holds more
        of its capitals, so that a year of foreign occupation is not shown, nor one when its homeland is drawn as
        another state (Macedon under Polyperchon in 318 BC). A last step that begins when the period ends and is a fifth
        or more larger than the one before it is the successor's shape, not the state's (the German Confederation
        at the Prussian annexations of 1866, Sardinia as Italy in 1861), and is passed over; with that year's territory."""
        first = None
        if period.confirmed:
            within = rows[(rows.ToYear >= period.start.earliest) & (rows.FromYear <= period.end.latest)]
            if not within.empty:
                rows, first = within, period.start.earliest
        historical = rows[rows.FromYear <= LATEST_START]
        rows = historical if not historical.empty else rows
        points = [Point(capital["location"]) for capital in capitals if capital["location"]]
        snapshots = []
        for year in sorted({max(int(year), first) if first is not None else int(year) for year in rows.FromYear}):
            territory = self._territory([name, *parts], year)
            shape = territory.geometry.union_all()
            held = sum(shape.buffer(CAPITAL_TOLERANCE_DEGREES).contains(point) for point in points)
            snapshots.append((held, float(territory.Area.sum()), year, shape))
        if len(snapshots) > 1 and period.confirmed and snapshots[-1][2] >= period.end.earliest \
                and snapshots[-1][1] > TRANSITION_GROWTH * snapshots[-2][1]:
            snapshots.pop()
        _, _, year, shape = max(snapshots, key=lambda snapshot: snapshot[:3])
        return year, shape

    def _period(self, entity: Entity, infobox: Infobox, clio_start: int | None, clio_end: int | None) -> Period:
        """Each end needs two sources to agree. Wikidata's inception or start time and dissolution or end time all
        count. The infobox and Wikidata agree within a year for modern dates and within about 1% of a date's age for
        older ones, since ancient chronologies differ by decades: 27 years at 700 BC, 47 at 2700 BC. An end too coarse to
        follow the start (the 1st millennium BC after the 8th century BC) is no end. A state that became part of its
        successor (the Kingdom of Castile of the Crown of Castile), or was followed by a later phase under its own name
        (Silla by Unified Silla), ends when it joined or the phase began, if the map stops drawing it then."""
        inception = self._dated_as(entity.years("P571") + entity.years("P580"), infobox.start, min, "earliest")
        dissolution = self._dated_as(entity.years("P576") + entity.years("P582"), infobox.end, max, "latest")
        ending = dissolution.as_span() if dissolution else None
        joined = self._joined(entity)
        if joined and clio_end is not None and joined.overlaps(
                Span(clio_end, clio_end), tolerances(clio_end)[frozenset(("wikidata", "cliopatria"))]):
            ending = joined
        start = vote({"infobox": infobox.start, "wikidata": inception.as_span() if inception else None,
                      "cliopatria": Span(clio_start, clio_start) if clio_start is not None else None},
                     tolerances(reference(clio_start, inception, infobox.start)))
        end = vote({"infobox": infobox.end, "wikidata": ending,
                    "cliopatria": Span(clio_end, clio_end) if clio_end is not None else None},
                   tolerances(reference(clio_end, dissolution, infobox.end)))
        if start is not None and end is not None and end.earliest < start.latest:
            end = None
        confirmed = start is not None and end is not None
        return Period(start, end, confirmed, None if confirmed else self._agreed_core(entity, infobox),
                      None if confirmed else self._since(entity, start, clio_end))

    @staticmethod
    def _dated_as(years: list, stated: Span | None, pick, side: str):
        """The earliest inception or latest dissolution Wikidata gives, or the one the infobox states when Wikidata
        gives several (the Akkadian Empire's fall in 2154 BC, not the later 2083 BC)."""
        agreeing = [year for year in years if stated is not None and year.as_span().overlaps(
            stated, tolerances(reference(year))[frozenset(("infobox", "wikidata"))])]
        return pick(agreeing or years, key=lambda year: getattr(year, side), default=None)

    def _dated(self, entity: Entity) -> Entity:
        """The item, or for a site or people Wikidata gives no dates, the culture it names for it (Teotihuacan)."""
        if any(entity.years(prop) for prop in DATES):
            return entity
        return next((self.cultures[qid] for qid in entity.ids("P2596") if qid in self.cultures), entity)

    def _joined(self, entity: Entity) -> Span | None:
        """When Wikidata names a drawn state as both its successor and what it is part of, the year it joined: the
        part-of start, the succession's date, or else the successor's single inception. A drawn successor named like the
        state itself, stated on either side, is its later phase: the state ends when that began (Silla at Unified Silla,
        the Kingdom of Aragon at the Crown of Aragon)."""
        successors = [claim for prop in ("P156", "P1366") for claim in entity.statements(prop, preferred_only=False)]
        for claim in entity.statements("P361", preferred_only=False):
            whole = claim["mainsnak"]["datavalue"]["value"].get("id")
            named = [other for other in successors if other["mainsnak"]["datavalue"]["value"].get("id") == whole]
            if not named or whole not in self.catalog.by_qid:
                continue
            dated = [year for year in [qualifier_year(claim, "P580"), *(qualifier_year(other, "P585") for other in named)]
                     if year is not None]
            if dated:
                return Span(dated[0], dated[0])
            if founded := self._founded(whole):
                return founded
        return self._later_phase(entity)

    def _later_phase(self, entity: Entity) -> Span | None:
        own = self.catalog.by_qid.get(entity.id)
        start, end = dates(entity)
        if own is None or start is None or end is None or not stems(own.name):
            return None
        stated = set(entity.ids("P156", "P1366", preferred_only=False)) | \
            {qid for qid in self.catalog.by_qid
             if entity.id in self.catalog.entities[qid].ids("P155", "P1365", preferred_only=False)}
        for qid in sorted(stated & self.catalog.by_qid.keys() - {entity.id}):
            founded = self._founded(qid)
            if founded and start < founded.earliest and founded.latest < end \
                    and stems(own.name) <= stems(self.catalog.by_qid[qid].name):
                return founded
        return None

    def _founded(self, qid: str) -> Span | None:
        founded = self.catalog.entities[qid].years("P571") + self.catalog.entities[qid].years("P580")
        return founded[0].as_span() if len({year.value for year in founded}) == 1 else None

    def _since(self, entity: Entity, start: Span | None, clio_end: int | None) -> Span | None:
        """For a state that still exists (drawn to the map's last year, with no end date in Wikidata), its confirmed
        start, or Wikidata's single founding year, asked as "since 1867" for Canada."""
        if clio_end is None or clio_end < self.present or entity.years("P576") + entity.years("P582"):
            return None
        if start is not None:
            return start
        founded = entity.years("P571") + entity.years("P580")
        return founded[0].as_span() if len({year.value for year in founded}) == 1 and not founded[0].approximate else None

    @staticmethod
    def _agreed_core(entity: Entity, infobox: Infobox) -> list[int] | None:
        """When the sources do not confirm its dates, Wikidata's (each a single value, known to the century or better),
        or the infobox's where Wikidata gives none or several, within the infobox's, rounded out only as far as
        Wikidata's own precision (to the decade or century when it knows no more): "c. 508–322 BC" for Classical
        Athens, "c. 900–1500" when only the centuries are known. Cliopatria's drawn years do not narrow it, since the map often stops drawing a state before it ended.
        Nothing when an end is missing or the two share no years."""
        def date(found: list, fallback: Span | None) -> tuple[int, int] | None:
            single = len({year.value for year in found}) == 1
            if single and min(year.precision for year in found) >= CENTURY_PRECISION:
                return found[0].value, min(year.precision for year in found)
            if single or not fallback:
                return None
            return fallback.earliest, YEAR_PRECISION if fallback.earliest == fallback.latest else DECADE_PRECISION

        start = date(entity.years("P571") + entity.years("P580"), infobox.start)
        end = date(entity.years("P576") + entity.years("P582"), infobox.end and Span(infobox.end.latest, infobox.end.latest))
        if not start or not end:
            return None
        first, last = start[0], end[0]
        if infobox.start and infobox.end:
            first, last = max(first, infobox.start.earliest), min(last, infobox.end.latest)
        if first > last:
            return None
        step = {YEAR_PRECISION: 1, DECADE_PRECISION: 10}.get(min(start[1], end[1], YEAR_PRECISION), 100)
        return [math.floor(first / step) * step, math.ceil(last / step) * step]

    def assign_tiers(self, polities: list[Polity]) -> None:
        """Tiers from Wikipedia's Vital Articles: Essential for a state on its 1,000 articles (level 3), Core for one on
        its 10,000 (level 4) or for one on its 50,000 (level 5) that was vast and lasting: its drawn area summed over
        its years reaching 150 million km²·years, a million km² for 150 years, not a colony (the Rashidun Caliphate, the
        Ilkhanate), or for any other state that took part in, or was the country of, at least 15 wars, battles,
        revolutions or treaties known in 30 Wikipedia languages (the Dutch Republic, the First French Empire). Extended
        for the rest, which a learner may suspend. A state counts as listed when a listed dynasty is its own, by being
        the state's country or the name the state is named after (the Safavid dynasty for Safavid Iran), when its name
        is that of a listed people or order once the kind of state is set aside (the Aztecs for the Aztec Empire, the
        Teutonic Order for its state), when its name holds that of a listed dynasty that lived at the same time (the House of Habsburg for the
        Habsburg Monarchy), when the catalogue left out a listed people as its own (the Göktürks for the
        Göktürk Khaganate), or when it is a part of a listed civilization that is not in the deck itself (the Roman
        Republic and Empire of Ancient Rome) or of a listed era that names it among its parts (Shu Han of the Three
        Kingdoms). A people, or a civilization with no map, without a capital, rulers or a confirmed period (the Goths,
        the Saxons, Troy; not the Hittites or the Huns), a country that still exists, whose item is today's country (the Kingdom of Sweden, Mexico), and an
        era with no capital or confirmed period, whatever figures it has (the Viking Age), are Extended. An Extended state whose neighbouring phase, the one before or after it in Wikidata
        under the same name once the kind of state is set aside, is Core or Essential becomes Core (the Caliphate of
        Córdoba beside the Emirate of Córdoba), unless it is one of those excluded above."""
        qids = [polity.id for polity in polities]
        deck = set(qids)
        levels = self.catalog.vital_levels
        candidates = [qid for qid, level in levels.items() if level <= MAX_VITAL_LEVEL and qid not in deck]
        dynasties = [qid for qid, found in self.wikidata.kinds(candidates, {DYNASTY: "dynasty"}).items() if found]
        listed = self.wikidata.entities(dynasties)
        through: dict[str, int] = {}
        for qid, entity in listed.items():
            for state in entity.ids("P17", preferred_only=False):
                through[state] = min(levels[qid], through.get(state, levels[qid]))
        listed_kinds = self.wikidata.kinds(candidates, TIER_KINDS | {PERIOD: "period"})
        topics = [qid for qid, found in listed_kinds.items() if "state" in found]
        for qid, entity in self.wikidata.entities(topics).items():
            for part in entity.ids("P527", preferred_only=False):
                through[part] = min(levels[qid], through.get(part, levels[qid]))
        eras = [qid for qid, found in listed_kinds.items() if found == {"period"}]
        for qid, entity in self.wikidata.entities(eras).items():
            for part in entity.ids("P527", preferred_only=False):
                if part in deck and qid in self.catalog.entities[part].ids("P361", preferred_only=False):
                    through[part] = min(levels[qid], through.get(part, levels[qid]))
        peoples_listed = self.wikidata.entities([qid for qid, found in listed_kinds.items() if "people" in found])
        named_for = {frozenset(stems(name)): qid for qid, entity in peoples_listed.items()
                     for name in (entity.label, entity.enwiki) if name and stems(name)}
        for polity in polities:
            for whole in self.catalog.entities[polity.id].ids("P361", preferred_only=False):
                if whole in topics:
                    through[polity.id] = min(levels[whole], through.get(polity.id, levels[whole]))
        houses = {qid: (words, lifespan(entity)) for qid, entity in listed.items()
                  if (words := name_words(entity.label) - HOUSE_WORDS)}
        kinds = self.wikidata.kinds(qids, TIER_KINDS | {SOVEREIGN_STATE: "sovereign", PERIOD: "period"})
        colonies = self.wikidata.kinds(qids, KINDS)
        lesser = []
        for polity in polities:
            entity = self.catalog.entities[polity.id]
            named = [levels[qid] for qid in entity.ids("P138", preferred_only=False) if qid in listed]
            named += [levels[qid] for name in (polity.name, entity.enwiki)
                      if name and (qid := named_for.get(frozenset(stems(name))))]
            named += [levels[qid] for qid, states in self.catalog.peoples.items() if polity.id in states and qid in levels]
            named += [levels[qid] for qid, (words, span) in houses.items()
                      if words <= name_words(polity.name) | name_words(entity.enwiki) and overlap(span, lifespan(entity))]
            level = min([levels.get(polity.id, 9), through.get(polity.id, 9), *named])
            rows = self.cliopatria.rows(polity.name)
            if level == MAX_VITAL_LEVEL + 1 and area_years(rows) >= CORE_AREA_YEARS \
                    and not colonial(colonies.get(polity.id, set()), rows):
                level = MAX_VITAL_LEVEL
            found = kinds.get(polity.id, set())
            era = "period" in found and "state" not in found
            ruled = bool(polity.capitals or polity.period.confirmed or (polity.key_figures and not era))
            people = not ruled and (not polity.borders or found - {"sovereign"} in ({"people"}, {"period"}, {"people", "period"}))
            present = "sovereign" in found and not entity.years("P576") + entity.years("P582")
            if level > MAX_VITAL_LEVEL or people or present:
                polity.tier = Tier.EXTENDED
                if not (people or present):
                    lesser.append(polity)
            else:
                polity.tier = Tier.ESSENTIAL if level < MAX_VITAL_LEVEL else Tier.CORE
        events = self.wikidata.events([polity.id for polity in lesser], EVENT_KINDS)
        editions = self.wikidata.language_editions(list(set().union(*events.values())))
        for polity in lesser:
            if sum(editions.get(event, 0) >= EVENT_LANGUAGES for event in events[polity.id]) >= CORE_EVENTS:
                polity.tier = Tier.CORE
        self.promote_sibling_phases(polities, {polity.id for polity in polities if polity.tier is Tier.EXTENDED and polity not in lesser})

    def promote_sibling_phases(self, polities: list[Polity], excluded: set[str]) -> None:
        by_id = {polity.id: polity for polity in polities}
        neighbours: dict[str, set[str]] = {qid: set() for qid in by_id}
        for polity in polities:
            for prop in ("P155", "P156", "P1365", "P1366"):
                for other in self.catalog.entities[polity.id].ids(prop, preferred_only=False):
                    if other in by_id:
                        neighbours[polity.id].add(other)
                        neighbours[other].add(polity.id)
        core = {qid for qid, polity in by_id.items() if polity.tier is not Tier.EXTENDED}
        for qid in neighbours.keys() - core - excluded:
            if any(stems(by_id[qid].name) == stems(by_id[other].name) for other in neighbours[qid] & core):
                by_id[qid].tier = Tier.CORE

    def choose_key_figures(self, polities: list[Polity], figures: list, scores: PowerScores) -> None:
        """A civilization's key figures, once its figures are known: up to four people, in order of their time.

        Candidates: those Wikidata records as its head of state (P35, within its period), holders of its own
        head-of-state office, members of the dynasty it is (P53) who took an office before 1945, figures whose role
        says they ruled it (see _ruled), the people its infobox lists as leaders, deputies or representatives, and the
        deck's figures filed first under it. A state with no head of state or head office of its own takes the holders of
        those of its parts named for it (the Kingdom of Prussia for Prussia) and, for a colony or colonial empire, of the
        state it belongs to or replaced (Castile and Spain for the Spanish Empire). Its years are its confirmed period,
        else the core its sources agree on, else the years it is drawn. Infobox rows titled for another state are passed
        over (a Seljuk sultan for the Zengids), as are the other rows when one row's title is the state's own office
        (shoguns, not emperors, for a shogunate), and people born after a row's years (Mahmud II for Karaman).

        Each is read by a model weighing ruler, consort, servant, ruler of another state and private person (see
        PowerModel). Rulers come first: those tied to its head office, its head of state or its infobox leaders, and
        those the model reads as its ruler; its own rulers before those who ruled it in union from a larger realm
        (James I before Charles V for Aragon), told apart by their title and the years of their head offices. Someone
        listed only by its infobox whom the model reads as a private person or another state's ruler is left out
        (Casimir III for Galicia–Volhynia), as is the spouse of one of its rulers who is not tied to it (Teresa
        Cristina). Then up to two others: deputies, and its own figures the model reads as rulers or servants
        (Pericles, Hannibal), not private people. Within each group by fame (Wikipedia languages), lowered for a union
        ruler by the share of their rule spent elsewhere and for a reign under three years by its length; anyone known
        in under a third as many languages as its best-known ruler is left out, and the rulers are spread over its
        history when the best known cluster in one part of it (see _spread).

        Also candidates: the office a state's P35 names (the Doge of Venice), the deck figures filed first under a
        neighbouring state whose role names a ruler, statesman or general (Zhuge Liang for Shu Han, filed under Han), the
        heads of its country for a sovereign state with none of its own (the presidents of France for the Third
        Republic), and, for a state with hardly any candidates, its citizens (P27) who held a head office. A person
        whose rule Wikidata or the infobox dates is placed by when it began, else by when they first took an office of a
        head-of-state class, else a ruler by thirty-five and anyone else by fifty years from birth, so the list keeps
        the order of their time; two beginning in one year go by which term ended first (Frederick III before
        Wilhelm II)."""
        spans = {polity.id: span for polity in polities if (span := self._ruled_years(polity))}
        ruled = self._ruled(polities, figures, spans)
        spans = {polity.id: span for polity in polities if (span := self._ruled_years(polity, core=True))}
        names = {polity.name: polity.id for polity in polities}
        self.deck = {polity.id: polity.name for polity in polities}
        self.spans = spans
        self.roles = {figure.id: figure.role or "" for figure in figures}
        self.neighbours = {polity.id: {edge["id"] for side in (polity.before, polity.after) for edge in side} for polity in polities}
        self.stand_ins: dict[str, list[str]] = {}
        self.row_titles: dict[str, dict[str, set[str]]] = {}
        homes: dict[str, set[str]] = {}
        alsos: dict[str, set[str]] = {}
        lifetimes = {figure.id: figure.lifetime for figure in figures}
        for figure in figures:
            if figure.civilizations and figure.civilizations[0] in names:
                first = names[figure.civilizations[0]]
                homes.setdefault(first, set()).add(figure.id)
                for name in figure.civilizations[1:]:
                    if name in names and ALSO_ROLE.search(figure.role or "") \
                            and (first in self.neighbours[names[name]] or names[name] in self.neighbours[first]):
                        alsos.setdefault(names[name], set()).add(figure.id)
        held: dict[str, dict[str, int]] = {}
        sources: dict[str, dict[str, set[str]]] = {}
        began_at: dict[str, dict[str, int]] = {}
        reigned: dict[str, dict[str, int]] = {}
        datings: dict[str, set[str]] = {}
        ends: dict[str, dict[str, int]] = {}
        terms: dict[str, dict[str, tuple[int, int]]] = {}
        named: dict[str, set[str]] = {}
        for polity in polities:
            if polity.id not in spans:
                continue
            start, end = spans[polity.id]
            entity = self.catalog.entities[polity.id]
            found: dict[str, int] = {}
            source: dict[str, set[str]] = {}
            reigns: dict[str, int] = {}

            def add(person: str, began: int, kind: str, dated: bool = False) -> None:
                found[person] = min(began, found.get(person, began))
                if dated:
                    reigns[person] = min(began, reigns.get(person, began))
                source.setdefault(person, set()).add(kind)

            for claim in entity.statements("P35", preferred_only=False):
                person = claim["mainsnak"].get("datavalue", {}).get("value", {}).get("id")
                began, ended = qualifier_year(claim, "P580"), qualifier_year(claim, "P582")
                began, ended = began if began is not None else ended, ended if ended is not None else began
                if person and began is not None and began <= end and ended >= start:
                    add(person, began, "head", True)
                    named.setdefault(polity.id, set()).add(person)
            for person, (began, years) in self._office_terms(polity.id, start, end).items():
                add(person, began, "office", True)
                held.setdefault(person, {})[polity.id] = years
            for person, began in self._dynasty_members(polity.id, start, end).items():
                add(person, began, "dynasty")
            for person, (began, dated) in ruled.get(polity.id, {}).items():
                add(person, began, "ruled", dated)
            for person, (kind, began, term) in self._row_people(polity, start, end).items():
                add(person, began, kind, bool(term))
                if term:
                    terms.setdefault(polity.id, {})[person] = term
            if not any(kinds & {"head", "office"} for kinds in source.values()):
                self.stand_ins[polity.id] = self._stand_ins(entity)
                for other in self.stand_ins[polity.id]:
                    for person, (began, years) in self._office_terms(other, start, end).items():
                        add(person, began, "office", True)
                        held.setdefault(person, {})[polity.id] = max(held.get(person, {}).get(polity.id, 0), years)
                        self.office_spans[person].setdefault(polity.id, []).extend(self.office_spans[person][other])
            if self._sole_state_of_country(polity, entity, source):
                for country in entity.ids("P17", preferred_only=False):
                    for person, (began, years) in self._office_terms(country, start, end).items():
                        add(person, began, "office", True)
                        held.setdefault(person, {})[polity.id] = years
                        self.office_spans[person].setdefault(polity.id, []).extend(self.office_spans[person][country])
            for kind, people in (("home", homes), ("also", alsos)):
                for person in people.get(polity.id, ()):
                    life = lifetimes[person]
                    if life[0] + ADULT_YEARS <= end and life[1] >= start and life[0] + 2 * ADULT_YEARS < LATEST_START:
                        add(person, life[0] + ADULT_YEARS, kind)
            if sum(bool(kinds & {"head", "office", "leader", "dynasty", "ruled"}) for kinds in source.values()) < FEW_CANDIDATES \
                    and sum(bool(kinds & {"home", "also"}) for kinds in source.values()) < FEW_CANDIDATES:
                for person, began in self._citizen_heads(polity.id, start, end).items():
                    add(person, began, "citizen")
            began_at[polity.id], sources[polity.id], datings[polity.id] = found, source, set(reigns)
            ends[polity.id] = {}
            reigned[polity.id] = {person: reigns[person] if person in reigns else self._prime(lifetimes.get(person), source[person]) or began
                                  for person, began in found.items()}
        people = self.wikidata.entities([qid for found in began_at.values() for qid in found if qid not in self.names])
        self.names.update(people)
        pages = {qid: self.names[qid].enwiki for found in began_at.values() for qid in found
                 if qid in self.names and self.names[qid].enwiki}
        texts = self.wikipedia.wikitext(sorted(set(pages.values())))
        first_offices = self._first_offices([person for polity_id, found in began_at.items() for person in found
                                             if person not in datings[polity_id]])
        for polity in polities:
            if polity.id in began_at:
                start, end = spans[polity.id]
                for person in began_at[polity.id]:
                    if person not in datings[polity.id] and start <= first_offices.get(person, start - 1) <= end:
                        reigned[polity.id][person] = first_offices[person]
                        datings[polity.id].add(person)
                    mine = self.office_spans.get(person, {}).get(polity.id)
                    if mine:
                        ends[polity.id][person] = min(mine)[1]
            found = {person: began for person, began in began_at.get(polity.id, {}).items()
                     if person in self.names and HUMAN in self.names[person].ids("P31", preferred_only=False)}
            if not found:
                continue
            start, end = spans[polity.id]
            source = sources[polity.id]
            editions = self.wikidata.language_editions(list(found))
            standing = {}
            for person in found:
                entity = self.names[person]
                text = ". ".join(part for part in (self._name(person), entity.description or "",
                                                   "; ".join(person_titles(texts.get(pages.get(person, ""), "")))) if part)
                shares = scores.shares(polity.name, self._name(person), text)
                standing[person] = self._standing(polity, person, source[person], shares, terms.get(polity.id, {}).get(person),
                                                  (start, end), lifetimes.get(person), editions.get(person, 0), text)
            tied = {person for person in found if standing[person] and source[person] & {"head", "office", "leader", "citizen"}}
            for person in found:
                years = held.get(person, {}).get(polity.id)
                if not standing[person] or source[person] & {"head", "office", "leader", "citizen"}:
                    if standing[person] and years is not None and not source[person] & {"head", "leader"} \
                            and years < MIN_REIGN_YEARS:
                        standing[person] = (standing[person][0], standing[person][1] * years / MIN_REIGN_YEARS)
                elif set(self.names[person].ids("P26", preferred_only=False)) & tied:
                    standing[person] = None
            ranked = sorted((person for person in found if standing[person]), key=lambda person: (
                -standing[person][0], -standing[person][1], person))
            if not ranked:
                polity.key_figures = []
                continue
            rulers = [person for person in ranked if standing[person][0] >= RULER_RANK]
            floor = max(standing[person][1] for person in (rulers or ranked)) / RULER_FAME_SHARE
            ranked = [person for person in ranked if standing[person][1] >= floor]
            others = [person for person in ranked if standing[person][0] < RULER_RANK][:OTHER_FIGURES if rulers else SHOWN_RULERS]
            top = [person for person in ranked if standing[person][0] == standing[ranked[0]][0]]
            best = self._spread(top, found, {person: standing[person][1] for person in ranked}, (start, end))
            best += [person for person in ranked if person not in best and (standing[person][0] >= RULER_RANK
                                                                             or person in others)][:SHOWN_RULERS - len(best)]
            order = lambda person: (reigned[polity.id][person], ends[polity.id].get(person, reigned[polity.id][person]))
            polity.key_figures = [{"id": qid, "name": self._name(qid)} for qid in sorted(best, key=order)]

    def _standing(self, polity: Polity, person: str, source: set[str], shares: list[float], term: tuple | None,
                  years: tuple[int, int], lifetime, editions: int, text: str) -> tuple[int, float] | None:
        """Where a candidate ranks: a native ruler (3), a ruler in union (2), another key figure (1), or not at all;
        and their weight within the rank: fame, lowered for a union ruler by the share of their rule spent elsewhere."""
        reading = max(range(len(shares)), key=shares.__getitem__)
        if "foreign" in source and not source & {"head", "office", "ruled", "dynasty"}:
            return None
        if not source & {"head", "office", "ruled", "dynasty", "home", "also", "citizen"} and self._misplaced_row(polity, person, reading, text):
            return None
        tied = bool(source & {"head", "office", "leader", "citizen"}) and reading != CONSORT
        own_only = source <= {"home", "also", "deputy", "representative"}
        if own_only and source & {"home", "also"}:
            if self._rules_elsewhere(polity, person, [self.roles.get(person, "")]):
                return None
            ended = lifetime[1] if lifetime else None
            ruler = "home" in source and shares[RULER] >= RULER_SCORE and ended is not None and ended <= years[1] + ADULT_YEARS \
                and bool(HOME_RULER_ROLE.search(self.roles.get(person, "")))
        else:
            ruler = tied or (shares[RULER] >= RULER_SCORE and not own_only)
        if ruler:
            share = self._own_share(polity, person, source, term, years)
            return (RULER_RANK + 1 if share > 0.5 else RULER_RANK), editions * min(1.0, 2 * share)
        if source & {"deputy", "representative"} and not tied:
            return 1, editions
        if source & {"home", "also"}:
            return (1, editions * (shares[RULER] + shares[SERVANT])) if reading in (RULER, SERVANT) else None
        return 1, editions

    def _own_share(self, polity: Polity, person: str, source: set[str], term: tuple | None, years: tuple[int, int]) -> float:
        """How much of a ruler's rule was over this civilization rather than an unrelated realm held at the same time:
        1 when their title names it (Philip II of Navarre) or it names them as head of state, 0 when their title names
        another deck state they headed or one of their time that is neither its predecessor nor its successor
        (Christian I of Denmark for Sweden), 1 when their role names it (Otto the Great, Holy Roman Emperor);
        otherwise the share of their head-office years that were its own."""
        if "head" in source:
            return 1.0
        title = re.search(r"(?: of |, )(.+)$", self._name(person))
        words = stems(title.group(1)) if title else set()
        own = stems(polity.name)
        if words & own:
            return 1.0
        offices = self.office_spans.get(person, {})
        if any(stems(self._name(state)) and stems(self._name(state)) <= words and not self._related(polity.id, state)
               for state in offices) or any(stems(name) == words and self._other_realm(polity.id, state, person)
                                            for state, name in self.deck.items()):
            return 0.0
        role = self.roles.get(person, "")
        if RulingRole.read(role) and stems(re.split(r"\s+from\b|[,;]", role)[0]) & own:
            return 1.0
        elsewhere = [spans for state, spans in offices.items() if not self._related(polity.id, state)]
        mine = offices.get(polity.id) or ([term] if term else None)
        if mine:
            own_years = {year for began, ended in mine for year in range(began, ended + 1)}
            other_years = {year for spans in elsewhere for began, ended in spans for year in range(began, ended + 1)}
            return len(own_years) / (len(own_years) + len(own_years & other_years))
        if "ruled" in source:
            return 1.0
        return 0.0 if any(began <= years[1] and ended >= years[0] for spans in elsewhere for began, ended in spans) else 1.0

    def _other_realm(self, polity: str, state: str, person: str) -> bool:
        """Another deck state of the person's time (within a lifetime of its years), unrelated to the civilization and
        neither its predecessor nor its successor."""
        if state == polity or state not in self.spans or state in self.neighbours.get(polity, ()) \
                or polity in self.neighbours.get(state, ()) or self._related(polity, state):
            return False
        first, last = self.spans[state]
        return lived_during(self.names[person], first - ASSUMED_LIFESPAN, last + ASSUMED_LIFESPAN)

    def _rules_elsewhere(self, polity: Polity, person: str, roles: list[str]) -> bool:
        """Someone one of whose roles says they ruled another deck state of their lifetime (Alauddin Khalji, Sultan of
        Delhi)."""
        places = [place for role in roles if (found := RulingRole.read(role)) for place in found.places]
        return any(place == stems(name) and qid in self.spans and lived_during(self.names[person], *self.spans[qid])
                   and not self._related(polity.id, qid)
                   for place in places for qid, name in self.deck.items() if qid != polity.id)

    def _misplaced_row(self, polity: Polity, person: str, reading: int, text: str) -> bool:
        """Someone only its infobox lists whom the sources place elsewhere: read as another state's ruler while their
        description or titles say they ruled another deck state of their time and none says they ruled this one
        (Casimir III, King of Poland, for Galicia–Volhynia; not Roman the Great, also Prince of Volhynia), or read as
        a private person while nothing said of them names the civilization or the row's office (Theodore Abu Qurrah,
        a theologian linked among the Avar khagans; not the Mahdi)."""
        if reading == OTHER_STATE:
            roles = re.split(r"[.;]\s+", text)
            own = stems(polity.name)
            return self._rules_elsewhere(polity, person, roles) and not any(
                place <= own for role in roles if (found := RulingRole.read(role)) for place in found.places)
        if reading == PRIVATE:
            return not stems(text) & (stems(polity.name) | self.row_titles.get(polity.id, {}).get(person, set()))
        return False

    def _related(self, first: str, second: str) -> bool:
        """The two are one state or a part and whole (Bohemia and the Holy Roman Empire), one stands in for the
        other's heads (Spain for the Spanish Empire), or they are named alike."""
        if first == second:
            return True
        if second in self.stand_ins.get(first, ()) \
                or stems(self._name(second)) in [stems(self._name(qid)) for qid in self.stand_ins.get(first, ())]:
            return True
        links = lambda qid: set(self.catalog.entities[qid].ids("P361", "P527", preferred_only=False)) \
            if qid in self.catalog.entities else set()
        return second in links(first) or first in links(second) or same_name(self._name(first), self._name(second))

    def _stand_ins(self, entity: Entity) -> list[str]:
        """States whose heads stand in for a state with none of its own: its parts named for it and, for a colony or
        colonial empire, the state it belongs to (P17) or replaced (P1365)."""
        own = stems(self._name(entity.id))
        parts = entity.ids("P527", preferred_only=False)
        self.names.update(self.wikidata.entities([qid for qid in parts if qid not in self.names]))
        found = [qid for qid in parts if own and own <= stems(self._name(qid))]
        if set(entity.ids("P31", preferred_only=False)) & COLONY_KINDS:
            found += [qid for qid in entity.ids("P17", "P1365", preferred_only=False) if qid != entity.id]
        return list(dict.fromkeys(found))

    def _row_people(self, polity: Polity, start: int, end: int) -> dict[str, tuple[str, int, tuple | None]]:
        """The people its infobox rows list, with the row's kind (leader, deputy, representative, or foreign for a row
        titled for another state or, when one row's title is the state's own office, for the other rows), when their
        rule or adult life began, and the years the row gives them."""
        rows = self.infoboxes[polity.id].rows if polity.id in self.infoboxes else []
        own = stems(fold(polity.name)) | set().union(set(), *(stems(country) for country in polity.modern_countries))
        echoed = [row for row in rows if stems(fold(row.title + " " + " ".join(row.title_links))) & stems(fold(polity.name))]
        found = {}
        qids = {title: qid for title, qid in zip(
            (title for row in rows for title in row.people), self._qids([title for row in rows for title in row.people]))}
        people = self.wikidata.entities([qid for qid in self._qids([title for row in rows for title in row.people])
                                         if qid not in self.names])
        self.names.update(people)
        for row in rows:
            words = stems(row.title + " " + " ".join(row.title_links))
            foreign = (not words & own and any(stems(name) and stems(name) <= words and not self._related(polity.id, qid)
                                               for qid, name in self.deck.items() if qid != polity.id)) \
                or (echoed and row not in echoed)
            term = row_years(row.years)
            for qid in self._qids(row.people):
                person = self.names.get(qid)
                if not person:
                    continue
                self.row_titles.setdefault(polity.id, {}).setdefault(qid, words)
                births = person.years("P569")
                began = term[0] if term else (min(year.earliest for year in births) + ADULT_YEARS if births else start)
                if term and not (term[0] <= end and term[1] >= start):
                    continue
                if term and births and not lived_during(person, term[0] - ADULT_YEARS, term[1] + ADULT_YEARS):
                    continue
                if not term and not lived_during(person, start, end):
                    continue
                if began < LATEST_START:
                    found.setdefault(qid, ("foreign" if foreign else row.kind, began, term))
        return found

    @staticmethod
    def _spread(ranked: list[str], began: dict[str, int], editions: dict[str, int], years: tuple[int, int]) -> list[str]:
        """The four best known, unless their rule began within under half the civilization's years: then the best
        known whose rule began in each quarter of them, if known in at least half as many languages as the best known,
        then the best known of the rest (Stephen I and Matthias Corvinus for Hungary, not only its Habsburgs). A rule
        that began before the civilization counts in its first quarter."""
        start, end = years
        best = ranked[:SHOWN_RULERS]
        starts = [began[qid] for qid in best]
        if not best or max(starts) - min(starts) >= SPREAD_SHARE * (end - start):
            return best
        floor = editions.get(ranked[0], 0) * SPREAD_SHARE
        step = (end - start + 1) / SHOWN_RULERS
        chosen = []
        for quarter in range(SHOWN_RULERS):
            first, last = start + quarter * step, start + (quarter + 1) * step
            found = next((qid for qid in ranked if first <= min(max(began[qid], start), end) < last
                          and editions.get(qid, 0) >= floor), None)
            if found and found not in chosen:
                chosen.append(found)
        return chosen + [qid for qid in ranked if qid not in chosen][:SHOWN_RULERS - len(chosen)]

    def _ruled_years(self, polity: Polity, core: bool = False) -> tuple[int, int] | None:
        """The years a civilization's rulers must fall in: its confirmed period, else (when asked) the core its sources
        agree on, else the years it is drawn."""
        first, last = self.catalog.years(polity.id) if polity.id in self.catalog.by_qid else (None, None)
        start = polity.period.start.earliest if polity.period.confirmed else first
        end = polity.period.end.latest if polity.period.confirmed else last
        if core and not polity.period.confirmed and polity.period.core:
            start, end = polity.period.core
        return None if start is None or end is None else (start, min(end, LATEST_START - 1))

    def _ruled(self, polities: list[Polity], figures: list, spans: dict[str, tuple[int, int]]) -> dict[str, dict[str, tuple[int, bool]]]:
        """Figures whose role says they ruled one of their civilizations, with when their rule began. The role must
        open with a ruling title, not as a consort, regent or rebel. A place it names ('Sultan of Egypt and Syria')
        must be the state, a country of its territory or its capital; a place that is another state counts only
        when Wikidata's offices tie the person to both (the tlatoani of Tenochtitlan for the Aztec Empire), and
        an unknown one (Asante for the Ashanti Empire) only with an office there. A ruler, leader or founder needs
        the place itself ('founder of psychoanalysis' is not a rule). Without a place, the role must name the state
        or its main country, or the person must hold an office of it. A dated reign must overlap the state's years,
        and goes to the state it names, then the one it overlaps most (Baybars to the Mamluks, not the Ayyubids). Each is
        returned with when their rule began and whether the role gives its years."""
        by_name = {polity.name: polity for polity in polities}
        lands = {polity.name: stems(polity.name) | set().union(*(stems(country) for country in polity.modern_countries))
                 | set().union(*(stems(capital["name"]) for capital in polity.capitals)) for polity in polities}
        states = {name: found for name in set(self.catalog.identities) | set(by_name) if (found := stems(name))}
        countries = {country: found for polity in polities for country in polity.modern_countries if (found := stems(country))}
        ruled: dict[str, dict[str, tuple[int, bool]]] = {}
        for figure in figures:
            role = RulingRole.read(figure.role or "")
            year = ROLE_YEAR.search(figure.role or "")
            homes = [by_name[name] for name in figure.civilizations if name in by_name and by_name[name].id in spans]
            if not role or not homes or (year and int(year.group(1)) >= LATEST_START) \
                    or (role.reign and role.reign[0] >= LATEST_START):
                continue
            offices = set((getattr(figure, "checks", {}) or {}).get("civilization_votes", {}).get("office", []))

            def names_it(polity: Polity) -> bool:
                return any(place <= lands[polity.name] for place in role.places)

            def fits(polity: Polity) -> bool:
                if not role.fits_office(polity.name):
                    return False
                if role.places and not names_it(polity):
                    others = {name for name, found in states.items() if any(place <= found or found <= place for place in role.places)} \
                             | {country for country, found in countries.items() if found in role.places}
                    return not role.generic and polity.name in offices and (not others or bool(others & offices))
                if role.generic and not role.places:
                    return polity.name in offices
                main = stems(polity.name) | stems(polity.modern_countries[0] if polity.modern_countries else "")
                return names_it(polity) or polity.name in offices or bool(main & stems(figure.role))

            if role.reign:
                overlapping = [(names_it(polity), min(spans[polity.id][1], role.reign[1]) - max(spans[polity.id][0], role.reign[0]),
                                -order, polity) for order, polity in enumerate(homes)]
                overlapping = [choice for choice in overlapping if choice[1] >= 0 and fits(choice[3])]
                if not overlapping:
                    continue
                polity = max(overlapping, key=lambda choice: choice[:3])[3]
                began = max(role.reign[0], spans[polity.id][0])
            else:
                polity = next((polity for polity in homes if fits(polity)), None)
                if polity is None:
                    continue
                start, end = spans[polity.id]
                began = figure.lifetime[0] + ADULT_YEARS
                if not (began <= end and figure.lifetime[1] >= start):
                    continue
            ruled.setdefault(polity.id, {}).setdefault(figure.id, (began, bool(role.reign)))
        return ruled

    def _first_offices(self, people: list[str]) -> dict[str, int]:
        """When each person first took an office of a head-of-state class (monarch, emperor, king, president...) with a
        dated start."""
        generic = " ".join(f"wd:{kind}" for kind in sorted(HEAD_OF_STATE_CLASSES))
        first: dict[str, int] = {}
        for at in range(0, len(people), 200):
            rows = self.wikidata.query(f"""SELECT ?person ?start WHERE {{
              VALUES ?person {{ {" ".join(f"wd:{qid}" for qid in people[at:at + 200])} }}
              ?person p:P39 ?held . ?held ps:P39 ?office ; pq:P580 ?start .
              ?office wdt:P279* ?kind . VALUES ?kind {{ {generic} }} }}""")
            for row in rows:
                if (year := sparql_year(row.get("start"))) is not None:
                    person = entity_id(row["person"])
                    first[person] = min(year, first.get(person, year))
        return first

    def _office_terms(self, polity_qid: str, start: int, end: int) -> dict[str, tuple[int, int]]:
        """Holders of the state's own head-of-state office, or of an office Wikidata classes as head of state, monarch,
        emperor, king or president whose jurisdiction is the state, with a term within the years: when the first
        began, and how many years they held it (each term is also kept, to tell a ruler's own realm from those held in
        union). A state whose own office is one of those classes itself (Judah's is "king") would take every king, so such
        an office does not count."""
        generic = ", ".join(f"wd:{kind}" for kind in sorted(HEAD_OF_STATE_CLASSES))
        rows = self.wikidata.query(f"""SELECT DISTINCT ?person ?start ?end WHERE {{
          {{ wd:{polity_qid} wdt:P1906 ?office FILTER(?office NOT IN ({generic})) }}
          UNION {{ wd:{polity_qid} wdt:P35 ?office FILTER(?office NOT IN ({generic})) }} UNION {{ ?office wdt:P1001 wd:{polity_qid} .
             ?office wdt:P279 ?kind . VALUES ?kind {{ {" ".join(f"wd:{kind}" for kind in sorted(HEAD_OF_STATE_CLASSES))} }} }}
          ?person p:P39 ?held . ?held ps:P39 ?office .
          OPTIONAL {{ ?held pq:P580 ?start }} OPTIONAL {{ ?held pq:P582 ?end }} }}""")
        terms: dict[str, tuple[int, int]] = {}
        for row in rows:
            began, ended = sparql_year(row.get("start")), sparql_year(row.get("end"))
            began, ended = began if began is not None else ended, ended if ended is not None else began
            if began is not None and began <= end and ended >= start:
                person = entity_id(row["person"])
                first, years = terms.get(person, (began, 0))
                terms[person] = (min(first, began), years + ended - began + 1)
                self.office_spans.setdefault(person, {}).setdefault(polity_qid, []).append((began, ended))
        return terms

    def _sole_state_of_country(self, polity: Polity, entity: Entity, source: dict[str, set[str]]) -> bool:
        """A sovereign state with no head of its own, one country today and under three leaders (the French Third
        Republic), which takes the heads of that country in its years."""
        return SOVEREIGN_STATE in entity.ids("P31", preferred_only=False) and len(polity.modern_countries) == 1 \
            and not any(kinds & {"head", "office"} for kinds in source.values()) \
            and sum(bool(kinds & {"leader"}) for kinds in source.values()) < SHOWN_RULERS - 1

    def _citizen_heads(self, polity_qid: str, start: int, end: int) -> dict[str, int]:
        """Citizens (P27) of the state who held an office Wikidata classes as head of state, monarch, emperor, king or
        president: when the office began, else when their adult life did."""
        kinds = ", ".join(f"wd:{kind}" for kind in sorted(HEAD_OF_STATE_CLASSES))
        try:
            rows = self.wikidata.query(f"""SELECT DISTINCT ?person ?born ?began ?ended WHERE {{
              ?person wdt:P27 wd:{polity_qid} ; p:P39 ?held . ?held ps:P39 ?office . ?office wdt:P279 ?kind . FILTER(?kind IN ({kinds}))
              OPTIONAL {{ ?held pq:P580 ?began }} OPTIONAL {{ ?held pq:P582 ?ended }} OPTIONAL {{ ?person wdt:P569 ?born }} }}""")
        except ValueError:
            return {}
        heads: dict[str, int] = {}
        for row in rows:
            born, began, ended = (sparql_year(row.get(key)) for key in ("born", "began", "ended"))
            first, last = (began if began is not None else ended), (ended if ended is not None else began)
            if first is None and born is not None:
                first = last = born + ADULT_YEARS
                if born + ASSUMED_LIFESPAN < start:
                    continue
            if first is not None and first <= end and last >= start and first < LATEST_START:
                person = entity_id(row["person"])
                heads[person] = min(first, heads.get(person, first))
        return heads

    @staticmethod
    def _prime(lifetime, source: set[str]) -> int | None:
        """When someone with no dated term is placed among rulers: a minister or general counts from the year they reached
        fifty (or died), so Tojo follows Hirohito, who came to the throne young; a ruler from thirty-five, a usual age
        at accession."""
        if not lifetime:
            return None
        if source <= {"home", "also", "deputy", "representative"}:
            return min(lifetime[0] + PRIME_YEARS, lifetime[1])
        return lifetime[0] + ACCESSION_YEARS

    def _dynasty_members(self, polity_qid: str, start: int, end: int) -> dict[str, int]:
        """Members of the family the item is (a dynasty such as the Zengids) who took an office before 1945 and lived
        as adults within the years: when their adult life began."""
        rows = self.wikidata.query(f"""SELECT DISTINCT ?person ?born ?began WHERE {{
          ?person wdt:P53 wd:{polity_qid} ; p:P39 ?held . OPTIONAL {{ ?held pq:P580 ?began }}
          OPTIONAL {{ ?person wdt:P569 ?born }} }}""")
        members: dict[str, int] = {}
        for row in rows:
            born, began = sparql_year(row.get("born")), sparql_year(row.get("began"))
            if (began is None or began < LATEST_START) and born is not None and born + ADULT_YEARS <= end and born + ASSUMED_LIFESPAN >= start:
                members[entity_id(row["person"])] = born + ADULT_YEARS
        return members

    def _rulers(self, polity_qid: str, leaders: list[str]) -> list[dict]:
        """Infobox leaders that Wikidata also records as holding an office of the polity, or files as people who lived
        during its years (the Askias of Songhai, whose offices Wikidata ties to no state)."""
        if not leaders:
            return []
        query = f"""SELECT DISTINCT ?person WHERE {{
          {{ ?office wdt:P1001 wd:{polity_qid} }} UNION {{ wd:{polity_qid} wdt:P1906 ?office }}
          UNION {{ ?office wdt:P17 wd:{polity_qid} }}
          ?person wdt:P39 ?office .
        }}"""
        holders = {entity_id(row["person"]) for row in self.wikidata.query(query)}
        first, last = self.catalog.years(polity_qid)
        people = self.wikidata.entities([qid for qid in leaders if qid not in holders])
        lived = {qid for qid, person in people.items() if lived_during(person, first, last)}
        return [{"id": qid, "name": self._name(qid)} for qid in leaders if qid in holders] + \
            [{"id": qid, "name": self._name(qid)} for qid in leaders if qid in lived]

    def _country_at(self, points: list) -> list[str]:
        """The countries holding the places Wikidata gives for a civilization the map does not draw: its capitals, or
        else its location (Greece for Sparta)."""
        held = [country.listed_as for point in points if point for country in
                self.countries[self.countries.listed & self.countries.intersects(
                    equal_area_shape(Point(point), self.cliopatria.frame.crs))].itertuples()]
        return list(dict.fromkeys(held))

    def _modern_countries(self, geometry, capitals: list[dict], location) -> tuple[list[str], list[str]]:
        """Today's countries in the territory, largest share first: those in the region around the capital (land
        within 800 km of it, as the map groups it; its Wikidata location when it has no capital, as for Phoenicia),
        then those only overseas, such as the protectorates Cliopatria draws as the United Kingdom. A dependency
        counts as its sovereign at home but keeps its own name overseas (Greenland, Macao, New Caledonia). An empire
        drawn without its homeland, its capital outside the territory (the French colonial empire), has no home to split
        off."""
        shape = equal_area_shape(geometry, self.cliopatria.frame.crs)
        found: dict[str, float] = {}
        units: dict[str, dict[str, float]] = {}
        for country in self.countries[self.countries.listed & self.countries.intersects(shape)].itertuples():
            overlap = country.geometry.intersection(shape).area
            if overlap / shape.area >= MODERN_SHARE_OF_POLITY or overlap / country.geometry.area >= MODERN_SHARE_OF_COUNTRY:
                found[country.listed_as] = found.get(country.listed_as, 0) + overlap
                units.setdefault(country.listed_as, {})[country.name] = overlap
        ranked = sorted(found, key=found.get, reverse=True)
        points = [capital["location"] for capital in capitals if capital["location"]] or ([location] if location else [])
        found = regions(geometry)
        if points and not any(region.geometry.buffer(CAPITAL_TOLERANCE_DEGREES).contains(Point(point))
                              for region in found for point in points):
            return ranked, []
        home_region = equal_area_shape(home(found, points).geometry, self.cliopatria.frame.crs)
        nearby = self.countries[self.countries.listed & self.countries.intersects(home_region)]
        at_home = [country for country in nearby.itertuples() if country.geometry.intersection(home_region).area > 0]
        sovereigns, names = {country.listed_as for country in at_home}, {country.name for country in at_home}
        overseas = [unit for name in ranked if name not in sovereigns
                    for unit in sorted(units[name], key=units[name].get, reverse=True) if unit not in names]
        return [name for name in ranked if name in sovereigns], list(dict.fromkeys(overseas))
