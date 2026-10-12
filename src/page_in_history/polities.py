import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import StrEnum

import geopandas as gpd
import shapely
from shapely.geometry import Point

from page_in_history.dates import Span
from page_in_history.naming import slugify
from page_in_history.sources import vital
from page_in_history.sources.cliopatria import CAPITAL_TOLERANCE_DEGREES, EQUAL_AREA, Cliopatria
from page_in_history.sources.wikidata import Entity, Wikidata, entity_id, qualifier_year
from page_in_history.sources.wikipedia import Infobox, Page, Wikipedia, country_infobox, display_name, place_name
from page_in_history.regions import home, regions
from page_in_history.sentences import contribution, first_paragraph
from page_in_history.succession import RegionalSuccession

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
LARGE_PEAK_KM2 = 2_000_000
SHARED_TOLERANCE_YEARS = 25
# head of state, monarch, emperor, king, president (two items)
HEAD_OF_STATE_CLASSES = {"Q48352", "Q116", "Q39018", "Q12097", "Q248577", "Q30461", "Q111840950"}
SHOWN_RULERS, RULER_FAME_SHARE, ADULT_YEARS = 4, 3, 20
SPREAD_SHARE = 0.5
KNOWN_NEIGHBOUR_LANGUAGES = 40
MIRROR_GAP_YEARS = 100
SAME_PLACE_KM = 5
STEM_LETTERS = 5
CORE_LANGUAGES, CORE_VITAL_LANGUAGES, CORE_FIGURES = 70, 60, 3
TIER_KINDS = {"Q41710": "people", "Q4204501": "people", "Q8432": "state", "Q2718886": "state", "Q3024240": "state",
              "Q7275": "state", "Q417175": "state", "Q48349": "state"}  # peoples; civilizations and states
CENTURY_PRECISION, DECADE_PRECISION = 7, 8
SAME_NAME_WORDS = {"dynasty", "of", "the", "kingdom", "crown", "electorate", "principality", "duchy", "grand", "emirate",
                   "khanate", "caliphate", "sultanate"}
RULING_TITLES = (r"king|queen|emperor|empress|sultan|sultana|shah|shahanshah|caliph|khan|khagan|qaghan|pharaoh|tsar|tsarina|czar"
                 r"|president|shogun|shōgun|chanyu|mansa|emir|amir|negus|tlatoani|huey tlatoani|sapa inca|doge|grand duke"
                 r"|grand prince|monarch|dictator|raja|maharaja|nizam|peshwa|khedive|basileus|chhatrapati"
                 r"|duke of|prince of|archduke|elector|consul|ruler|leader|founder")
GENERIC_TITLES = {"ruler", "leader", "founder"}
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
LIVING_ON_MAP_BEFORE = 1930
PRESENT_DESCRIPTION_FROM = 1900
MID_THOUGHT = re.compile(r"(?:It|Its|They|Their|The dynasty|Under)\b")
PRESENT_OR_PAST = re.compile(r"\b(is|are|was|were)\b")
HUMAN = "Q5"
ASSUMED_LIFESPAN = 70
LIVING_PEAK_KM2 = 2_000_000
STATE_KINDS = {"state", "historical country", "city-state", "empire", "kingdom"}
MAPLESS_KINDS = {
    "Q7275": "state", "Q3024240": "historical country", "Q133442": "city-state", "Q48349": "empire",
    "Q417175": "kingdom", "Q486972": "settlement", "Q10864048": "subdivision", "Q11514315": "period",
    "Q164950": "dynasty", "Q41710": "ethnic group", "Q8432": "civilization",
}
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
CAPITAL_MATCH_KM = 25
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
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    rulers: list[dict] = field(default_factory=list)
    modern_countries: list[str] = field(default_factory=list)
    overseas_countries: list[str] = field(default_factory=list)
    borders: bool = True
    summary: str = ""
    tier: str = ""
    map: str = ""
    map_asked: bool = True
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


def sparql_year(value: str | None) -> int | None:
    """The year of a SPARQL date such as '1453-05-29T00:00:00Z' or '-0549-01-01T00:00:00Z'."""
    match = re.match(r"(-?)(\d+)-", value or "")
    return (-1 if match.group(1) else 1) * int(match.group(2)) if match else None


def stems(name: str) -> set[str]:
    """The first five letters of each distinctive word of a name, without the kind of state and joining words, so
    'Babylon' meets 'Babylonia' and 'Egyptian' meets 'Egypt': {'ottom'} for the Ottoman Empire."""
    words = set(re.findall(r"\w+", name.lower())) - SAME_NAME_WORDS - {"empire", "republic", "state", "dynasty", "and", "from", "to"}
    return {word[:STEM_LETTERS] for word in words if not word.isdigit()}


def same_name(first: str, second: str) -> bool:
    """The two names are the same once "dynasty", a monarch's rank (kingdom, crown, electorate, principality, duchy,
    emirate, khanate, caliphate, sultanate) and brackets are set aside, as Qin and the Qin dynasty, the Kingdom and
    the Crown of Castile, or the Kingdom of Naples and the Kingdom of Naples (Napoleonic), so asking one for the other
    gives the answer away. A change of form stays, since it tells the Roman Republic from the Roman Empire."""
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
            self.cliopatria = Cliopatria(frame.assign(Name=frame.Name.replace(renamed)), self.cliopatria.spikes)

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
        Britain. Then its own Wikidata id, or a Wikidata state named exactly like the Cliopatria polity (by label or
        alias), stands in when its dates overlap and it is the only such state."""
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
            self.cliopatria = Cliopatria(frame, self.cliopatria.spikes)
            self.qids = {renamed.get(name, name): qid for name, qid in self.qids.items()}
        return kept

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
        self.countries = equal_area(countries)
        self.present = int(self.cliopatria.frame.ToYear.max())
        self.succession = RegionalSuccession(self.cliopatria.frame, catalog.qids, countries, self._stated, self._implied,
                                             equal_area)
        self.founded_early: set[str] = set()
        self.parents: dict[str, set[str]] = {}

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
        qids = [qid for qid in levels if qid not in self.catalog.by_qid]
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
        Wikidata's colony class counts only from 1500, since it also marks the Teutonic Order and the Ostrogoths. A sovereign state that still exists
        qualifies only if it was on the maps before 1930 and reached 2 million km², as the United States did. Any
        other polity without an end date must be filed as historical, since Cliopatria sometimes links an old polity
        to today's place, such as the island of Rhodes, and today's provinces and towns never qualify."""
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
        colony = "colony" in kinds and rows.FromYear.min() >= COLONIAL_ERA
        return (level <= MAX_VITAL_LEVEL or languages >= MIN_LANGUAGE_EDITIONS
                or (level == MAX_VITAL_LEVEL + 1 and (languages >= LEVEL_5_LANGUAGES or peak >= LEVEL_5_PEAK_KM2))
                or (peak >= LARGE_PEAK_KM2 and not colony)
                or (peak >= MIN_PEAK_AREA_KM2 and languages >= LASTING_LANGUAGES and not colony
                    and float((rows.Area * (rows.ToYear - rows.FromYear + 1)).sum()) >= LASTING_AREA_YEARS))

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
        texts = self.wikipedia.wikitext([identity.title for identity in known])
        self.infoboxes = {identity.qid: country_infobox(texts.get(identity.title, "")) or Infobox() for identity in known}
        linked = [title for box in self.infoboxes.values()
                  for title in box.capital + box.predecessors + box.successors + box.leaders]
        self.pages = self.wikipedia.resolve(linked)
        referenced = [self.catalog.entities[identity.qid].ids("P36") for identity in known]
        self.names = self.wikidata.entities([qid for ids in referenced for qid in ids] +
                                            [page.qid for page in self.pages.values() if page.qid])
        roots = {SETTLEMENT: "settlement", HERITAGE: "heritage", SITE: "site", STATE: "state", FORMER_OR_CURRENT_STATE: "state"}
        capitals = sorted({qid for ids in referenced for qid in ids} |
                          {qid for box in self.infoboxes.values() for qid in self._qids(box.capital)})
        self.capital_kinds = self.wikidata.kinds(capitals, roots)
        self.settlements = {qid for qid, found in self.capital_kinds.items() if found - {"state"} == {"settlement"}}
        self.located = {qid: self.names[qid].ids("P131") for qid, found in self.capital_kinds.items()
                        if "settlement" not in found and qid in self.names}
        towns = sorted({town for ids in self.located.values() for town in ids})
        self.names.update(self.wikidata.entities(towns))
        self.towns = {qid for qid, found in self.wikidata.kinds(towns, roots).items() if "settlement" in found}
        self.introductions = self.wikipedia.introductions([identity.title for identity in identities])
        return [self._polity(identity, self.infoboxes[identity.qid]) for identity in identities]

    def _summary(self, identity: Identity, entity: Entity, last_drawn: int | None) -> str:
        """What the civilization was: the opening sentence of its article without its subject, by the rule used for
        what a figure is known for ("The third caliphate to succeed the Islamic prophet Muhammad."), or the article's
        short description when that sentence carries on from an opening the rule cannot read ("It began with …"). None when the
        item still exists and its article speaks in the present tense, but the map stops drawing it long before
        today, since the article then describes the present (medieval Norway as "a Nordic country comprising …
        Svalbard")."""
        extract, description = self.introductions.get(identity.title, ("", None))
        tense = PRESENT_OR_PAST.search(first_paragraph(extract))
        if last_drawn is not None and last_drawn < PRESENT_DESCRIPTION_FROM and tense and tense.group(1) in ("is", "are") \
                and not entity.years("P576") + entity.years("P582"):
            return ""
        summary = contribution(first_paragraph(extract), identity.name, description)
        if MID_THOUGHT.match(summary):
            summary = contribution("", identity.name, description)
        return summary

    def _capital_edges(self, entity: Entity) -> tuple:
        """Where its first and last capitals stood, when Wikidata dates at least two of them."""
        dated = []
        for claim in entity.statements("P36", preferred_only=False):
            value = claim["mainsnak"].get("datavalue", {}).get("value", {})
            place = self.names.get(value.get("id")) if isinstance(value, dict) else None
            start, end = qualifier_year(claim, "P580"), qualifier_year(claim, "P582")
            if place and place.coordinates() and (start is not None or end is not None):
                dated.append((start if start is not None else end, end if end is not None else start, place.coordinates()))
        if len(dated) < 2:
            return None, None
        return min(dated)[2], max(dated, key=lambda found: (found[1], found[0]))[2]

    def restrict_neighbours(self, polities: list[Polity]) -> None:
        """Once the deck is known, a civilization before or after one must be in it or be well known (an article in
        at least 40 Wikipedia languages, as the Median Kingdom is), so no answer is obscure: otherwise the nearest
        confirmed holder that is takes its place."""
        deck = {polity.id for polity in polities}
        editions = self.wikidata.language_editions(list(self.catalog.by_qid))
        known = lambda other: other in deck or editions.get(other, 0) >= KNOWN_NEIGHBOUR_LANGUAGES
        for polity in polities:
            if (polity.before and not known(polity.before["id"])) or (polity.after and not known(polity.after["id"])):
                entity = self.catalog.entities[polity.id]
                before, after, _ = self.succession.neighbours(
                    polity.id, polity.capitals, self._capital_edges(entity),
                    lambda other: known(other) and not same_name(polity.name, self._name(other)))
                polity.before, polity.after = self._named(before), self._named(after)
        self._mirror_neighbours(polities)

    def _mirror_neighbours(self, polities: list[Polity]) -> None:
        """A civilization the deck names as followed by another, which has no civilization before it, is that one's
        predecessor in the same region (Achaemenid before the Macedonian Empire), and the other way round, when the
        later one began after the earlier began, within a century of its end, and ended after it (not the
        Confederacy before the USA). Of several, the one whose end and start lie closest together."""
        by_id = {polity.id: polity for polity in polities}

        def later(first: Polity, second: Polity) -> bool:
            earlier, after = self._ruled_years(first), self._ruled_years(second)
            return bool(earlier and after) and earlier[0] < after[0] and earlier[1] < after[1] \
                and abs(after[0] - earlier[1]) <= MIRROR_GAP_YEARS
        gap = lambda first, second: abs(self._ruled_years(second)[0] - self._ruled_years(first)[1])
        befores: dict[str, list[Polity]] = {}
        for polity in polities:
            following = by_id.get((polity.after or {}).get("id"))
            if following and not following.before and later(polity, following):
                befores.setdefault(following.id, []).append(polity)
        afters: dict[str, list[Polity]] = {}
        for polity in polities:
            preceding = by_id.get((polity.before or {}).get("id"))
            if preceding and not preceding.after and later(preceding, polity):
                afters.setdefault(preceding.id, []).append(polity)
        for qid, candidates in befores.items():
            following = by_id[qid]
            polity = min(candidates, key=lambda candidate: (gap(candidate, following), candidate.name))
            following.before = {"id": polity.id, "region": polity.after["region"], "name": polity.name}
        for qid, candidates in afters.items():
            preceding = by_id[qid]
            polity = min(candidates, key=lambda candidate: (gap(preceding, candidate), candidate.name))
            preceding.after = {"id": polity.id, "region": polity.before["region"], "name": polity.name}

    def _stated(self, first: str, last: str) -> bool:
        """Wikidata or a Wikipedia infobox, on either civilization's side, says that the second followed the first."""
        earlier, later = self.catalog.entities.get(first), self.catalog.entities.get(last)
        return bool(
            (earlier and last in earlier.ids("P1366", "P156"))
            or (later and first in later.ids("P1365", "P155"))
            or (first in self.infoboxes and last in self._qids(self.infoboxes[first].successors))
            or (last in self.infoboxes and first in self._qids(self.infoboxes[last].predecessors))
        )

    def _implied(self, first: str, last: str) -> bool:
        """The sources name a part of the second as following the first, or a part of the first as preceding the
        second, by Wikidata's part of or country: Carthage was followed by the province of Africa, part of the Roman
        Republic; the Aztecs by New Spain, part of the Spanish Empire. Never between a state and a part of itself
        (the French Consulate in the First Republic)."""
        if first in self._parents(last) or last in self._parents(first):
            return False
        return any(last in self._parents(qid) for qid in self._followers(first, later=True)) \
            or any(first in self._parents(qid) for qid in self._followers(last, later=False))

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

    def _named(self, neighbour: dict) -> dict:
        return {**neighbour, "name": self._name(neighbour["id"])} if neighbour else {}

    def _capitals(self, entity: Entity, infobox: Infobox, rows, period: Period) -> list[dict]:
        """Capitals that Wikidata and the infobox both name: the same item, or two items for the same place within
        25 km, as when Wikidata names the prefecture Shuntian Fu and the infobox the city of Beijing. They are shown
        under the name of the place Wikidata lists, once per place (Bursa rather than the infobox's Prusa, Ayutthaya rather
        than its historical park); capitals 5 km or more apart are separate places, as Venice and Malamocco are, or the infobox's name when Wikidata's item is wider than a place. An item that
        is not a settlement, such as a historical park or an archaeological site (Ayutthaya Historical Park, Abbasid
        Samarra), is shown as the town it lies in when that town is within 25 km. A state is never a capital, though a city that Wikidata also files as an administrative
        unit, like Paris, Berlin or Bucharest, is. When the two sources share none, a capital either names counts if it lies in the
        civilization's own territory on the map (Hattusa, Susa, Gordion). Wikidata's capitals of every rank count, but
        one it dates only outside the confirmed period (Cairo, seat of the Abbasid caliphs after the fall of
        Baghdad) or only from 1945 on (Brasília) is left out."""
        outside = self._capitals_outside(entity, period)
        listed = [qid for qid in entity.ids("P36", preferred_only=False) if qid not in outside]
        places = [(qid, found.coordinates()) for qid, found in ((qid, self.names.get(qid)) for qid in listed)
                  if found and found.coordinates()]
        capitals = []
        for qid in self._qids(infobox.capital):
            if not self._can_be_capital(qid) or qid in outside:
                continue
            capital = self._capital(qid)
            location = capital["location"]
            near = [place for place, coordinates in places if location and kilometres(location, coordinates) <= CAPITAL_MATCH_KM]
            nearby = bool(near)
            source = qid
            if near and qid not in listed and near[0] in self.settlements:
                capital, source = {**self._capital(near[0]), "location": location}, near[0]
            town = next((town for town in self.located.get(source, []) if town in self.towns and location
                         and self.names[town].coordinates()
                         and kilometres(location, self.names[town].coordinates()) <= CAPITAL_MATCH_KM), None)
            if town:
                capital = {**self._capital(town), "location": location}
            if not (qid in listed or nearby) or capital["name"] in {found["name"] for found in capitals}:
                continue
            if not any(location and found["location"] and kilometres(location, found["location"]) <= SAME_PLACE_KM
                       for found in capitals):
                capitals.append(capital)
        if capitals or rows.empty:
            return capitals
        territory = shapely.union_all(rows.geometry.values).buffer(CAPITAL_TOLERANCE_DEGREES)
        for qid in dict.fromkeys(listed + self._qids(infobox.capital)):
            location = self._capital(qid)["location"]
            if self._can_be_capital(qid) and qid not in outside and location and territory.contains(Point(location)):
                capital = self._shown(qid, location)
                if capital["name"] not in {found["name"] for found in capitals}:
                    capitals.append(capital)
        return capitals

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
        """Any place but a state: a municipality like Bucharest is one, a kingdom named as a capital is not."""
        kinds = self.capital_kinds.get(qid, set())
        return qid not in self.catalog.by_qid and ("state" not in kinds or "settlement" in kinds)

    def _shown(self, qid: str, location) -> dict:
        """A capital under its own name, or the town it lies in when it is a site rather than a settlement."""
        town = next((town for town in self.located.get(qid, []) if town in self.towns
                     and self.names[town].coordinates()
                     and kilometres(location, self.names[town].coordinates()) <= CAPITAL_MATCH_KM), None)
        return {**self._capital(town), "location": location} if town else self._capital(qid)

    def _capital(self, qid: str) -> dict:
        entity = self.names.get(qid)
        location = entity.coordinates() if entity else None
        founded = min((year.earliest for year in entity.years("P571")), default=None) if entity else None
        return {"name": place_name(self._name(qid)), "location": list(location) if location else None, "founded": founded}

    def _name(self, qid: str) -> str:
        if qid in self.catalog.by_qid:
            return self.catalog.by_qid[qid].name
        entity = self.names.get(qid)
        return display_name(entity.enwiki) if entity and entity.enwiki else (entity.label if entity and entity.label else qid)

    def _polity(self, identity: Identity, infobox: Infobox) -> Polity:
        entity = self.catalog.entities[identity.qid]
        rows = self.cliopatria.rows(identity.name)
        drawn = not rows.empty
        first, last = self.catalog.years(identity.qid)
        period = self._period(entity, infobox, first if drawn else None, last if drawn else None)
        capitals = self._capitals(entity, infobox, rows, period)
        parts = [self.catalog.by_qid[qid].name for qid in entity.ids("P527", preferred_only=False)
                 if qid in self.catalog.by_qid and qid != identity.qid]
        map_year, extent = self._peak(identity.name, parts, rows, period, capitals) if drawn else (first, None)
        before, after, home_place = self.succession.neighbours(
            identity.qid, capitals, self._capital_edges(entity), lambda other: not same_name(identity.name, self._name(other)))
        modern = self._modern_countries(extent, capitals, entity.coordinates()) if drawn else ([], [])
        polity = Polity(
            id=identity.qid,
            slug=slugify(identity.name),
            name=identity.name,
            wikipedia=identity.title,
            map_year=map_year,
            period=period,
            capitals=capitals,
            parts=parts,
            before=self._named(before),
            after=self._named(after),
            rulers=self._rulers(identity.qid, self._qids(infobox.leaders))[:SHOWN_RULERS],
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
            "home": home_place or "the spot held longest",
        }
        return polity

    def _peak(self, name: str, parts: list[str], rows: gpd.GeoDataFrame, period: Period, capitals: list[dict]):
        """The year of the largest extent, looking only inside the confirmed period when there is one (a map that begins
        before it is shown from its first year) and up to 1945 where the civilization was drawn by then, counted with
        the parts Wikidata lists (Spain for the Spanish Empire) and preferring years when the civilization holds a
        capital, so that a year of foreign occupation is not shown; with that year's territory."""
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
            territory = self.cliopatria.at(year)
            territory = territory[territory.Name.isin([name, *parts])]
            shape = territory.geometry.union_all()
            holds = any(shape.buffer(CAPITAL_TOLERANCE_DEGREES).contains(point) for point in points)
            snapshots.append((holds, float(territory.Area.sum()), year, shape))
        _, _, year, shape = max(snapshots, key=lambda snapshot: snapshot[:3])
        return year, shape

    def _period(self, entity: Entity, infobox: Infobox, clio_start: int | None, clio_end: int | None) -> Period:
        """Each end needs two sources to agree. Wikidata's inception or start time and dissolution or end time all
        count. The infobox and Wikidata agree within a year for modern dates and within about 1% of a date's age for
        older ones, since ancient chronologies differ by decades: 27 years at 700 BC, 47 at 2700 BC."""
        inception = min(entity.years("P571") + entity.years("P580"), key=lambda year: year.earliest, default=None)
        dissolution = max(entity.years("P576") + entity.years("P582"), key=lambda year: year.latest, default=None)
        start = vote({"infobox": infobox.start, "wikidata": inception.as_span() if inception else None,
                      "cliopatria": Span(clio_start, clio_start) if clio_start is not None else None},
                     tolerances(reference(clio_start, inception, infobox.start)))
        end = vote({"infobox": infobox.end, "wikidata": dissolution.as_span() if dissolution else None,
                    "cliopatria": Span(clio_end, clio_end) if clio_end is not None else None},
                   tolerances(reference(clio_end, dissolution, infobox.end)))
        confirmed = start is not None and end is not None
        return Period(start, end, confirmed, None if confirmed else self._agreed_core(entity, infobox),
                      None if confirmed else self._since(entity, start, clio_end))

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
        or the infobox's where Wikidata gives none, within the infobox's, rounded out to whole decades, or centuries
        when Wikidata knows one only to the century: "c. 60 BC – AD 900" for Silla, "c. 510–320 BC" for Classical
        Athens. Cliopatria's drawn years do not narrow it, since the map often stops drawing a state before it ended.
        Nothing when an end is missing or the two share no years."""
        def date(found: list, fallback: Span | None) -> tuple[int, int] | None:
            if len({year.value for year in found}) == 1 and min(year.precision for year in found) >= CENTURY_PRECISION:
                return found[0].value, min(year.precision for year in found)
            return (fallback.earliest, DECADE_PRECISION) if not found and fallback else None

        start = date(entity.years("P571") + entity.years("P580"), infobox.start)
        end = date(entity.years("P576") + entity.years("P582"), infobox.end and Span(infobox.end.latest, infobox.end.latest))
        if not start or not end:
            return None
        first, last = start[0], end[0]
        if infobox.start and infobox.end:
            first, last = max(first, infobox.start.earliest), min(last, infobox.end.latest)
        if first > last:
            return None
        step = 10 if min(start[1], end[1]) >= DECADE_PRECISION else 100
        return [math.floor(first / step) * step, math.ceil(last / step) * step]

    def assign_tiers(self, polities: list[Polity], figures: list) -> None:
        """Core for what every learner should meet: listed as vital at level 4 or above, or at level 5 with an article
        in at least 60 Wikipedia languages; an article in at least 70 languages, though a people (the Goths, the
        Saxons) only with a capital, rulers or a confirmed period (the Huns); or home to at least three of the deck's
        figures. Extended for the rest (Chen, Shirvan, the Kingdom of Fiji), which a learner may suspend, and for a
        people or dynasty the deck also has as a state: the people a deck state names as its own (the Vandals of the
        Vandal Kingdom), or a dynasty whose jurisdiction is one (the Seljuk dynasty of the Great Seljuk Empire)."""
        names = {polity.name for polity in polities}
        deck = {polity.id for polity in polities}
        peoples = {qid for polity in polities for qid in self.catalog.entities[polity.id].ids("P172", preferred_only=False)}
        doubled = {polity.id for polity in polities if polity.id in peoples or
                   set(self.catalog.entities[polity.id].ids("P1001", preferred_only=False)) & (deck - {polity.id})}
        homes = Counter(next((name for name in figure.civilizations if name in names), None) for figure in figures)
        qids = [polity.id for polity in polities]
        editions = self.wikidata.language_editions(qids)
        kinds = self.wikidata.kinds(qids, TIER_KINDS)
        for polity in polities:
            level = self.catalog.vital_levels.get(polity.id)
            languages = editions.get(polity.id, 0)
            only_people = kinds.get(polity.id, set()) == {"people"}
            known = bool(polity.capitals or polity.rulers or polity.period.confirmed)
            vital = level is not None and (level <= MAX_VITAL_LEVEL
                                           or (level == MAX_VITAL_LEVEL + 1 and languages >= CORE_VITAL_LANGUAGES))
            core = vital or homes[polity.name] >= CORE_FIGURES or (languages >= CORE_LANGUAGES and (known or not only_people))
            polity.tier = Tier.CORE if core and polity.id not in doubled else Tier.EXTENDED

    def choose_rulers(self, polities: list[Polity], figures: list) -> None:
        """A civilization's notable rulers, once its figures are known: the best-known people (by the number of Wikipedia
        languages) among those Wikidata records as its head of state (P35, within its period), members of the
        dynasty it is (P53) who took an office before 1945, holders of its own head-of-state office, and the figures
        whose role says they ruled it (see _ruled). All must have ruled or lived within its period and before 1945.
        Up to four, in order of their time, dropping any known in fewer than a third as many languages as the best
        known. Someone holding the head office of several states counts for the one they held it longest (Philip II
        for Spain, not Portugal) unless the state itself names them as its head of state. The infobox's leaders stay
        when none of these is found, except emperors listed for a shogunate."""
        spans = {polity.id: span for polity in polities if (span := self._ruled_years(polity))}
        ruled = self._ruled(polities, figures, spans)
        held: dict[str, dict[str, int]] = {}
        gathered: dict[str, dict[str, int]] = {}
        named: dict[str, set[str]] = {}
        for polity in polities:
            if polity.id not in spans:
                continue
            start, end = spans[polity.id]
            entity = self.catalog.entities[polity.id]
            found: dict[str, int] = {}
            for claim in entity.statements("P35", preferred_only=False):
                person = claim["mainsnak"].get("datavalue", {}).get("value", {}).get("id")
                began, ended = qualifier_year(claim, "P580"), qualifier_year(claim, "P582")
                began, ended = began if began is not None else ended, ended if ended is not None else began
                if person and began is not None and began <= end and ended >= start:
                    found[person] = min(began, found.get(person, began))
                    named.setdefault(polity.id, set()).add(person)
            for person, (began, years) in self._office_terms(polity.id, start, end).items():
                found[person] = min(began, found.get(person, began))
                held.setdefault(person, {})[polity.id] = max(held.get(person, {}).get(polity.id, 0), years)
            for person, began in self._dynasty_members(polity.id, start, end).items():
                found.setdefault(person, began)
            for person, began in ruled.get(polity.id, {}).items():
                found.setdefault(person, began)
            gathered[polity.id] = found
        for polity in polities:
            found = {person: began for person, began in gathered.get(polity.id, {}).items()
                     if person in named.get(polity.id, ()) or person not in held or polity.id not in held[person]
                     or held[person][polity.id] >= max(held[person].values())}
            people = self.wikidata.entities([qid for qid in found if qid not in self.names])
            self.names.update(people)
            found = {qid: year for qid, year in found.items()
                     if qid in self.names and HUMAN in self.names[qid].ids("P31", preferred_only=False)}
            if not found:
                if "shogunate" in polity.name.lower():
                    leaders = self.wikidata.entities([ruler["id"] for ruler in polity.rulers])
                    polity.rulers = [ruler for ruler in polity.rulers if not re.search(
                        r"\b(?:emperor|empress)\b", leaders[ruler["id"]].description or "" if ruler["id"] in leaders else "",
                        re.IGNORECASE)]
                continue
            editions = self.wikidata.language_editions(list(found))
            ranked = sorted(found, key=lambda qid: (-editions.get(qid, 0), qid))
            floor = editions.get(ranked[0], 0) / RULER_FAME_SHARE
            ranked = [qid for qid in ranked if editions.get(qid, 0) >= floor]
            best = self._spread(ranked, found, editions, spans[polity.id])
            polity.rulers = [{"id": qid, "name": self._name(qid)} for qid in sorted(best, key=lambda qid: found[qid])]

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

    def _ruled_years(self, polity: Polity) -> tuple[int, int] | None:
        """The years a civilization's rulers must fall in: its confirmed period, else the years it is drawn."""
        first, last = self.catalog.years(polity.id) if polity.id in self.catalog.by_qid else (None, None)
        start = polity.period.start.earliest if polity.period.confirmed else first
        end = polity.period.end.latest if polity.period.confirmed else last
        return None if start is None or end is None else (start, min(end, LATEST_START - 1))

    def _ruled(self, polities: list[Polity], figures: list, spans: dict[str, tuple[int, int]]) -> dict[str, dict[str, int]]:
        """Figures whose role says they ruled one of their civilizations, with when their rule began. The role must
        open with a ruling title, not as a consort, regent or rebel. A place it names ('Sultan of Egypt and Syria')
        must be the state, a country of its territory or its capital; a place that is another state counts only
        when Wikidata's offices tie the person to both (the tlatoani of Tenochtitlan for the Aztec Empire), and
        an unknown one (Asante for the Ashanti Empire) only with an office there. A ruler, leader or founder needs
        the place itself ('founder of psychoanalysis' is not a rule). Without a place, the role must name the state
        or its main country, or the person must hold an office of it. A dated reign must overlap the state's years,
        and goes to the state it names, then the one it overlaps most (Baybars to the Mamluks, not the Ayyubids)."""
        by_name = {polity.name: polity for polity in polities}
        lands = {polity.name: stems(polity.name) | set().union(*(stems(country) for country in polity.modern_countries))
                 | set().union(*(stems(capital["name"]) for capital in polity.capitals)) for polity in polities}
        states = {name: found for name in set(self.catalog.identities) | set(by_name) if (found := stems(name))}
        countries = {country: found for polity in polities for country in polity.modern_countries if (found := stems(country))}
        ruled: dict[str, dict[str, int]] = {}
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
            ruled.setdefault(polity.id, {}).setdefault(figure.id, began)
        return ruled

    def _office_terms(self, polity_qid: str, start: int, end: int) -> dict[str, tuple[int, int]]:
        """Holders of the state's own head-of-state office, or of an office Wikidata classes as head of state, monarch,
        emperor, king or president whose jurisdiction is the state, with a term within the years: when the first
        began, and how many years they held it."""
        rows = self.wikidata.query(f"""SELECT DISTINCT ?person ?start ?end WHERE {{
          {{ wd:{polity_qid} wdt:P1906 ?office }} UNION {{ ?office wdt:P1001 wd:{polity_qid} .
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
        return terms

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

    def _modern_countries(self, geometry, capitals: list[dict], location) -> tuple[list[str], list[str]]:
        """Today's countries in the territory, largest share first: those in the region around the capital (land
        within 800 km of it, as the map groups it; its Wikidata location when it has no capital, as for Phoenicia),
        then those only overseas, such as the protectorates Cliopatria draws as the United Kingdom. A dependency
        counts as its sovereign at home but keeps its own name overseas (Greenland, Macao, New Caledonia). An empire
        drawn without its homeland, its capital outside the territory (the British Empire), has no home to split off."""
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
