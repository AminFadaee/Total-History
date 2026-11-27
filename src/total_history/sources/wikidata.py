from dataclasses import dataclass

from total_history.dates import Span
from total_history.http import Http

API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
BATCH = 20
ENTITY_CHUNK = 500
KIND_BATCH = 150
EVENT_BATCH = 10
INSTANCE_BATCH = 200
CIRCA = "Q5727902"
SOURCING_CIRCUMSTANCES = "P1480"
YEAR_PRECISION = 9
NOT_WIKIPEDIAS = {"commonswiki", "specieswiki", "metawiki", "wikidatawiki", "mediawikiwiki", "sourceswiki"}
QUALITY_BADGES = {"Q17437796", "Q17437798", "Q17559452", "Q17506997", "Q51759403"}


@dataclass(frozen=True)
class Year:
    value: int
    precision: int
    circa: bool

    @property
    def approximate(self) -> bool:
        return self.circa or self.precision < YEAR_PRECISION

    @property
    def span(self) -> int:
        """Years covered: 1 for a year, 10 for a decade, 100 for a century, 1000 for a millennium."""
        return 10 ** max(YEAR_PRECISION - self.precision, 0)

    @property
    def earliest(self) -> int:
        """Wikidata stores the 1180s as 1180 and the 190s BC as -190; a century is the one holding the year, which
        Wikidata often gives as the century's last (300 for the 3rd century, -600 for the 6th century BC) but not
        always (1240 for Fibonacci's death in the 13th century); a millennium BC is the one holding the year, so -500
        is the 1st millennium BC."""
        if self.span == 1:
            return self.value
        if self.span == 10:
            return self.value if self.value > 0 else self.value - 9
        if self.span == 100:
            return (self.value - 1) // 100 * 100 + 1 if self.value > 0 else -((-self.value - 1) // 100 + 1) * 100
        if self.span == 1000 and self.value < 0:
            return -((-self.value - 1) // 1000 + 1) * 1000
        return self.value - self.span + 1 if self.value > 0 else self.value

    @property
    def latest(self) -> int:
        return self.earliest + self.span - 1

    def as_span(self) -> Span:
        return Span(self.earliest, self.latest, self.circa)


def slim(data: dict) -> dict:
    """The parts of an item the pipeline reads: English (or multilingual) label and English description, the English
    Wikipedia title, and each statement's rank, value and qualifier values. References and hashes make up most of an
    item and are dropped."""
    def value(snak: dict) -> dict:
        return {"datavalue": snak["datavalue"]} if "datavalue" in snak else {}
    return {
        "id": data["id"],
        "labels": data.get("labels", {}),
        "descriptions": data.get("descriptions", {}),
        "sitelinks": {site: {"title": link["title"]} for site, link in data.get("sitelinks", {}).items()},
        "claims": {
            prop: [{"rank": claim["rank"], "mainsnak": value(claim["mainsnak"]),
                    "qualifiers": {name: [value(snak) for snak in snaks]
                                   for name, snaks in claim.get("qualifiers", {}).items()}}
                   for claim in claims]
            for prop, claims in data.get("claims", {}).items()
        },
    }


class Entity:
    def __init__(self, data: dict):
        self.data = data
        self.id: str = data["id"]

    @property
    def label(self) -> str | None:
        """The English label, or the multilingual one Wikidata keeps instead when it is the same in every language
        (Antonio López de Santa Anna)."""
        labels = self.data.get("labels", {})
        return (labels.get("en") or labels.get("mul") or {}).get("value")

    @property
    def description(self) -> str | None:
        return self.data.get("descriptions", {}).get("en", {}).get("value")

    @property
    def enwiki(self) -> str | None:
        return self.data.get("sitelinks", {}).get("enwiki", {}).get("title")

    def statements(self, prop: str, preferred_only: bool = True) -> list[dict]:
        """Non-deprecated statements, by default keeping only preferred ones when any exist."""
        found = [claim for claim in self.data.get("claims", {}).get(prop, [])
                 if claim["rank"] != "deprecated" and claim["mainsnak"].get("datavalue")]
        preferred = [claim for claim in found if claim["rank"] == "preferred"] if preferred_only else []
        return preferred or found

    def ids(self, *props: str, preferred_only: bool = True) -> list[str]:
        return list(dict.fromkeys(
            claim["mainsnak"]["datavalue"]["value"]["id"]
            for prop in props
            for claim in self.statements(prop, preferred_only)
            if isinstance(claim["mainsnak"]["datavalue"]["value"], dict) and "id" in claim["mainsnak"]["datavalue"]["value"]
        ))

    def qualifier_ids(self, prop: str, qualifiers: set[str]) -> list[str]:
        """Items named by the given qualifiers on any of the property's statements."""
        return list(dict.fromkeys(
            qualifier["datavalue"]["value"]["id"]
            for claim in self.statements(prop, preferred_only=False)
            for name, values in claim.get("qualifiers", {}).items() if name in qualifiers
            for qualifier in values
            if isinstance(qualifier.get("datavalue", {}).get("value"), dict) and "id" in qualifier["datavalue"]["value"]
        ))

    def coordinates(self) -> tuple[float, float] | None:
        """Longitude and latitude of the item's first coordinate location (P625)."""
        for claim in self.statements("P625"):
            value = claim["mainsnak"]["datavalue"]["value"]
            return value["longitude"], value["latitude"]
        return None

    def years(self, prop: str) -> list[Year]:
        found = []
        for claim in self.statements(prop):
            value = claim["mainsnak"]["datavalue"]["value"]
            if not isinstance(value, dict) or "time" not in value:
                continue
            circumstances = [
                qualifier["datavalue"]["value"]["id"]
                for qualifier in claim.get("qualifiers", {}).get(SOURCING_CIRCUMSTANCES, [])
                if qualifier.get("datavalue")
            ]
            found.append(Year(parse_year(value["time"]), value["precision"], CIRCA in circumstances))
        return found


def qualifier_year(claim: dict, prop: str) -> int | None:
    for qualifier in claim.get("qualifiers", {}).get(prop, []):
        value = qualifier.get("datavalue", {}).get("value")
        if isinstance(value, dict) and "time" in value:
            return parse_year(value["time"])
    return None


def parse_year(time: str) -> int:
    sign = -1 if time.startswith("-") else 1
    return sign * int(time[1:].split("-", 1)[0])



class Wikidata:
    def __init__(self, http: Http):
        self.http = http

    def entities(self, ids: list[str]) -> dict[str, Entity]:
        """Wikidata items, cached per item and slimmed when fetched, since whole items take several gigabytes for the
        deck's people and reading them back dominated a rerun. They are read a few hundred at a time."""
        unique = list(dict.fromkeys(qid for qid in ids if qid))
        found: dict[str, Entity] = {}
        for start in range(0, len(unique), ENTITY_CHUNK):
            chunk = self.http.per_key("entity-by-id", unique[start : start + ENTITY_CHUNK], self._fetch_entities)
            found.update((qid, Entity(data)) for qid, data in chunk.items())
        return found

    def _fetch_entities(self, ids: list[str]) -> dict[str, dict]:
        unique = sorted(set(ids))
        found: dict[str, dict] = {}
        for start in range(0, len(unique), BATCH):
            batch = unique[start : start + BATCH]
            params = {
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": "labels|descriptions|claims|sitelinks",
                "languages": "en|mul",
                "sitefilter": "enwiki",
                "format": "json",
            }
            data = self.http.json(API, params, namespace="wikidata")
            for qid, entity in data.get("entities", {}).items():
                if "missing" not in entity:
                    found[qid] = slim(entity)
        return found

    def exact_matches(self, name: str) -> list[str]:
        """Items whose English label or alias is exactly the name, ignoring case."""
        params = {"action": "wbsearchentities", "search": name, "language": "en", "type": "item", "limit": 20,
                  "format": "json"}
        results = self.http.json(API, params, namespace="wikidata").get("search", [])
        return [result["id"] for result in results
                if result.get("match", {}).get("text", "").casefold() == name.casefold()]

    def language_editions(self, ids: list[str]) -> dict[str, int]:
        """How many Wikipedia language editions have an article on each item."""
        unique = sorted(set(ids))
        found: dict[str, int] = {}
        for start in range(0, len(unique), 50):
            params = {"action": "wbgetentities", "ids": "|".join(unique[start : start + 50]), "props": "sitelinks",
                      "format": "json"}
            for qid, entity in self.http.json(API, params, namespace="sitelinks").get("entities", {}).items():
                found[qid] = sum(1 for site in entity.get("sitelinks", {}) if site.endswith("wiki")
                                 and site not in NOT_WIKIPEDIAS)
        return found

    def quality_articles(self, ids: list[str]) -> dict[str, dict[str, str]]:
        """The Wikipedia articles on each item that carry a quality badge (featured, good or recommended article, featured
        or good list), as the title in each language edition, cached per item."""
        return self.http.per_key("quality-articles", ids, self._fetch_quality_articles)

    def _fetch_quality_articles(self, ids: list[str]) -> dict[str, dict[str, str]]:
        unique = sorted(set(ids))
        found: dict[str, dict[str, str]] = {}
        for start in range(0, len(unique), 50):
            params = {"action": "wbgetentities", "ids": "|".join(unique[start : start + 50]), "props": "sitelinks",
                      "format": "json"}
            for qid, entity in self.http.json(API, params, namespace="sitelinks").get("entities", {}).items():
                found[qid] = {site.removesuffix("wiki"): link["title"] for site, link in entity.get("sitelinks", {}).items()
                              if site.endswith("wiki") and site not in NOT_WIKIPEDIAS and QUALITY_BADGES & set(link.get("badges", []))}
        return found

    def subclass_of(self, classes: list[str], roots: set[str], depth: int = 12) -> set[str]:
        """The classes whose superclass chain reaches one of the roots."""
        matching = set()
        for start in classes:
            seen, frontier = {start}, [start]
            for _ in range(depth):
                if seen & roots or not frontier:
                    break
                parents = self.entities(frontier)
                frontier = [parent for qid in frontier if qid in parents
                            for parent in parents[qid].ids("P279", preferred_only=False) if parent not in seen]
                seen.update(frontier)
            if seen & roots:
                matching.add(start)
        return matching

    def kinds(self, ids: list[str], roots: dict[str, str]) -> dict[str, set[str]]:
        """Which of the named root classes each item is an instance of, through any chain of subclasses."""
        unique = sorted(set(ids))
        found: dict[str, set[str]] = {qid: set() for qid in unique}
        values = " ".join(f"wd:{root}" for root in sorted(roots))
        for start in range(0, len(unique), KIND_BATCH):
            items = " ".join(f"wd:{qid}" for qid in unique[start : start + KIND_BATCH])
            rows = self.query(f"SELECT DISTINCT ?item ?root WHERE {{ VALUES ?item {{ {items} }} VALUES ?root {{ {values} }} "
                              "?item wdt:P31/wdt:P279* ?root }")
            for row in rows:
                found[entity_id(row["item"])].add(roots[entity_id(row["root"])])
        return found

    def events(self, ids: list[str], roots: set[str]) -> dict[str, set[str]]:
        """The events of the root kinds, through any chain of subclasses, that each item took part in (P710, P1344) or
        was the country of (P17)."""
        unique = sorted(set(ids))
        found: dict[str, set[str]] = {qid: set() for qid in unique}
        values = " ".join(f"wd:{root}" for root in sorted(roots))
        for start in range(0, len(unique), EVENT_BATCH):
            items = " ".join(f"wd:{qid}" for qid in unique[start : start + EVENT_BATCH])
            rows = self.query(f"SELECT DISTINCT ?item ?event WHERE {{ VALUES ?item {{ {items} }} "
                              f"VALUES ?root {{ {values} }} {{ ?event wdt:P710 ?item }} UNION {{ ?item wdt:P1344 ?event }} "
                              "UNION { ?event wdt:P17 ?item } ?event wdt:P31/wdt:P279* ?root }")
            for row in rows:
                found[entity_id(row["item"])].add(entity_id(row["event"]))
        return found

    def subjects(self, prop: str, ids: list[str]) -> dict[str, list[str]]:
        """The items whose property names each of the ids, as the cities Wikidata files as capital of (P1376) a state."""
        unique = sorted(set(ids))
        found: dict[str, list[str]] = {qid: [] for qid in unique}
        for start in range(0, len(unique), KIND_BATCH):
            items = " ".join(f"wd:{qid}" for qid in unique[start : start + KIND_BATCH])
            for row in self.query(f"SELECT ?item ?subject WHERE {{ VALUES ?item {{ {items} }} ?subject wdt:{prop} ?item }}"):
                found[entity_id(row["item"])].append(entity_id(row["subject"]))
        return found

    def subclasses(self, roots: list[str]) -> set[str]:
        """The roots and every class below them."""
        found = set(roots)
        for root in roots:
            found |= {entity_id(row["c"]) for row in self.query(f"SELECT ?c WHERE {{ ?c wdt:P279+ wd:{root} }}")}
        return found

    def instance_classes(self, ids: list[str]) -> dict[str, set[str]]:
        """The classes each item is directly an instance of (P31)."""
        unique = sorted(set(ids))
        found: dict[str, set[str]] = {qid: set() for qid in unique}
        for start in range(0, len(unique), INSTANCE_BATCH):
            items = " ".join(f"wd:{qid}" for qid in unique[start : start + INSTANCE_BATCH])
            for row in self.query(f"SELECT ?e ?c WHERE {{ VALUES ?e {{ {items} }} ?e wdt:P31 ?c }}"):
                found[entity_id(row["e"])].add(entity_id(row["c"]))
        return found

    def participations(self, ids: list[str]) -> dict[str, dict[str, set[str]]]:
        """The events each item took part in (P710, P1344) or is a facet of (P1269), with the classes of each event."""
        unique = sorted(set(ids))
        found: dict[str, dict[str, set[str]]] = {qid: {} for qid in unique}
        for start in range(0, len(unique), EVENT_BATCH):
            items = " ".join(f"wd:{qid}" for qid in unique[start : start + EVENT_BATCH])
            rows = self.query(f"SELECT DISTINCT ?item ?event ?cls WHERE {{ VALUES ?item {{ {items} }} "
                              "{ ?event wdt:P710 ?item } UNION { ?item wdt:P1344 ?event } UNION { ?event wdt:P1269 ?item } "
                              "?event wdt:P31 ?cls }")
            for row in rows:
                found[entity_id(row["item"])].setdefault(entity_id(row["event"]), set()).add(entity_id(row["cls"]))
        return found

    def same_as(self, ids: list[str]) -> dict[str, set[str]]:
        """The items each item is said to be the same as (P460), in either direction."""
        found: dict[str, set[str]] = {qid: set() for qid in ids}
        values = " ".join(f"wd:{qid}" for qid in sorted(set(ids)))
        for row in self.query(f"SELECT ?a ?b WHERE {{ VALUES ?a {{ {values} }} {{ ?a wdt:P460 ?b }} UNION {{ ?b wdt:P460 ?a }} }}"):
            found[entity_id(row["a"])].add(entity_id(row["b"]))
        return found

    def members(self, ids: list[str]) -> dict[str, set[str]]:
        """The items that are members of (P463) each item."""
        found: dict[str, set[str]] = {qid: set() for qid in ids}
        values = " ".join(f"wd:{qid}" for qid in sorted(set(ids)))
        for row in self.query(f"SELECT ?a ?m WHERE {{ VALUES ?a {{ {values} }} ?m wdt:P463 ?a }}"):
            found[entity_id(row["a"])].add(entity_id(row["m"]))
        return found

    def values(self, ids: list[str], properties: list[str]) -> dict[str, set[str]]:
        """The items each item names under any of the properties."""
        unique = sorted(set(ids))
        found: dict[str, set[str]] = {qid: set() for qid in unique}
        predicates = " ".join(f"wdt:{prop}" for prop in properties)
        for start in range(0, len(unique), INSTANCE_BATCH):
            values = " ".join(f"wd:{qid}" for qid in unique[start : start + INSTANCE_BATCH])
            for row in self.query(f"SELECT ?a ?b WHERE {{ VALUES ?a {{ {values} }} VALUES ?p {{ {predicates} }} ?a ?p ?b . "
                                  f"FILTER(isIRI(?b)) }}"):
                found[entity_id(row["a"])].add(entity_id(row["b"]))
        return found

    def ancestors(self, ids: list[str]) -> dict[str, set[str]]:
        """Everything each item is part of (P361), through any chain."""
        unique = sorted(set(ids))
        found: dict[str, set[str]] = {qid: set() for qid in unique}
        for start in range(0, len(unique), INSTANCE_BATCH):
            values = " ".join(f"wd:{qid}" for qid in unique[start : start + INSTANCE_BATCH])
            for row in self.query(f"SELECT ?a ?b WHERE {{ VALUES ?a {{ {values} }} ?a wdt:P361+ ?b }}"):
                found[entity_id(row["a"])].add(entity_id(row["b"]))
        return found

    def nested(self, ids: list[str]) -> set[str]:
        """The items that are part of (P361), through any chain, another of the items."""
        values = " ".join(f"wd:{qid}" for qid in sorted(set(ids)))
        rows = self.query(f"SELECT DISTINCT ?a WHERE {{ VALUES ?a {{ {values} }} VALUES ?b {{ {values} }} ?a wdt:P361+ ?b }}")
        return {entity_id(row["a"]) for row in rows}

    def query(self, sparql: str) -> list[dict]:
        data = self.http.json(SPARQL, {"query": sparql, "format": "json"}, namespace="sparql")
        return [{key: value["value"] for key, value in row.items()} for row in data["results"]["bindings"]]


def entity_id(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]
