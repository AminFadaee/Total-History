import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

import mwparserfromhell as mw

from total_history.conflict_overrides import ConflictOverride
from total_history.dates import Span, period_label
from total_history.http import Http
from total_history.naming import slugify
from total_history.sources import vital
from total_history.sources.wikidata import Entity, Wikidata, Year
from total_history.sources.wikipedia import Wikipedia, lead_links, link_targets, plain_text

CONFLICT_ROOTS = ["Q198", "Q178561", "Q831663", "Q188055", "Q645883", "Q124734", "Q6107280"]
FIGHT_ROOTS = ["Q178561", "Q188055"]
UNREAL_ROOTS = ["Q24336466", "Q14897293"]
ALLIANCE_ROOTS = ["Q1127126"]
ALLIANCE_DEPTH = 2
CONFLICT_INFOBOX = "Infobox military conflict"
MIN_EDITIONS = 5
DECADE_PRECISION = 8
BIG_LEVEL_ALONE, BIG_LEVEL = 3, 4
BIG_BELLIGERENTS_ALONE, BIG_BELLIGERENTS = 2, 5
YEARS_BEFORE_END, YEARS_AFTER_END = 5, 1
YEARS_BEFORE_START = 1
SOVEREIGN_PROPERTIES = ["P17", "P1365"]
LINEAGE_YEARS = 10
MAX_ENDING_SPAN = 50
SECOND_TIERS = {"Essential", "Core"}
SECOND_CANDIDATES = 20
SECOND_EDITIONS = 15
COMBATANT = re.compile(r"combatant(\d+)[a-z]?$")
RESULT = re.compile(r"(result|territory)\d*$")
LINE_BREAK = re.compile(r"\n|<br\s*/?>", re.IGNORECASE)
ENDING_WORDS = re.compile(r"\b(fall|end|ended|collapse|destruction|destroyed|annex\w*|conquest|conquered|dissolution|dissolved|"
                          r"demise|extinction|overthrow\w*|abolish\w*|absorbed|incorporated)\b", re.IGNORECASE)
SUPPORTERS = re.compile(r"\bsupport(?:ed)?\s+by\b|\bsupport\s*:", re.IGNORECASE)
FOUNDING_WORDS = re.compile(r"\b(independence|independent|establish\w*|founded|founding|foundation|creation|created|"
                            r"formation|formed|unification|unified|proclaim\w*)\b", re.IGNORECASE)
VICTORY = re.compile(r"\bvictor(y|ious)\b", re.IGNORECASE)

logger = logging.getLogger(__name__)


def precise(years: list[Year]) -> list[Year]:
    """The dates given to the decade or better, or all of them when none is."""
    return [year for year in years if year.precision >= DECADE_PRECISION] or years


class Evidence(StrEnum):
    WIKIDATA = "wikidata"
    INFOBOX = "infobox"
    MUTUAL = "mutual"
    OVERRIDE = "override"


class Role(StrEnum):
    BIG = "big"
    CENTRAL = "central"
    ENDING = "ending"
    FOUNDING = "founding"
    SECOND = "second"


@dataclass
class Infobox:
    sides: dict[str, set[str]] = field(default_factory=dict)
    results: list[tuple[set[str], str]] = field(default_factory=list)


@dataclass
class Conflict:
    id: str
    name: str
    wikipedia: str | None
    start: int | None
    end: int | None
    editions: int
    infobox: Infobox
    lead: set[str]
    participants: set[str]
    fight: bool

    @property
    def belligerents(self) -> set[str]:
        return self.participants | set(self.infobox.sides)

    @property
    def span(self) -> int:
        return (self.end - self.start) if self.start is not None and self.end is not None else 0


@dataclass
class Candidate:
    conflict: Conflict
    evidence: set[Evidence]
    in_lead: bool
    in_article: bool
    during: bool

    @property
    def weak(self) -> bool:
        return self.evidence == {Evidence.MUTUAL}


@dataclass
class Civilization:
    id: str
    name: str
    wikipedia: str
    article: str
    tier: str
    start: int | None
    end: int | None
    successors: set[str]
    identities: set[str]
    predecessors: set[str] = field(default_factory=set)
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def names(self) -> set[str]:
        return {name.casefold() for name in (self.name, self.wikipedia, self.article)}

    def named_in(self, links: set[str], words: str) -> bool:
        return bool(self.identities & links) or any(name in words.casefold() for name in self.names)

    def during(self, conflict: Conflict) -> bool:
        """The conflict began within the period or overlaps it by a year or more."""
        if conflict.start is None or self.start is None:
            return True
        end = self.end if self.end is not None else conflict.end if conflict.end is not None else conflict.start
        finish = conflict.end if conflict.end is not None else conflict.start
        return self.start <= conflict.start <= end or min(finish, end) - max(conflict.start, self.start) >= 1

    def founded_by(self, conflict: Conflict) -> bool:
        """The conflict began by the start and lasted until at least a year before it."""
        return (self.start is not None and conflict.start is not None
                and conflict.start <= self.start and conflict.end >= self.start - YEARS_BEFORE_START)


class ConflictCollector:
    """The wars, battles and campaigns of the deck's civilizations, in five sets.

    A civilization's candidates are the conflicts its English Wikipedia article links to, those Wikidata lists it in,
    and the articles with a military-conflict infobox that link to it; Wikidata must class each as a war, battle,
    campaign, siege, military operation, rebellion or revolt, and not as mythical or fictional. The civilization is
    known by its own item, its article's item and any item Wikidata says is the same as it. A candidate found outside
    the civilization's article must link back to it from its opening. It is kept when Wikidata names the civilization
    a participant, the infobox names it a combatant (not among those it was "supported by", and an alliance such as
    the Axis counting for its members), or, as weaker evidence, when the two articles link each other; and when it
    began within the civilization's period or overlaps it by a year, reading a date given only to the century when
    no closer one is given; one that began by the civilization's start and lasted until at least a year before it is
    kept only as a founding conflict.

    Big wars are wars on Vital Articles level 3 fought by at least two deck civilizations, or on level 4 by at least
    five, counting only Wikidata or infobox evidence, and not part of another big war.

    A civilization's central conflict is its best candidate that is not a big war and has at least five language
    editions: one linked from the article's opening first, then one backed by Wikidata or the infobox, then by
    language editions. It is not its ending or founding conflict, nor part of either, unless no other candidate is left.

    Its ending conflict, when it has ended, lasted at most 50 years and reaches from five years before the end to a
    year after, and one of these holds: a successor of the civilization, or that successor's own predecessor when it
    ended within ten years of the civilization, or the state a successor belongs to or replaced, fought it, on the other
    side when the infobox places both, or the result names it the victor; it is a war rather than a single battle, the
    civilization's article links it, and the side the result names as victor is not the civilization's; or a line of
    the result links or names the civilization with a word such as fall, destruction or annexed. The conflict spanning
    the end year comes first, then one that is not a big war, then by language editions.

    Its founding conflict, when the period has a start, lasted at most 50 years, began by the start and lasted until at
    least a year before it, has five language editions, and the civilization fought a predecessor on the other side,
    the result names the civilization the victor, or a line of the result links or names it with a word such as
    independence, establishment or unification. It is ranked as the ending conflict is, from the start year.

    An Essential or Core civilization, or one with twenty or more candidates, also has a second war: a candidate war,
    not a single battle, with at least fifteen language editions, that Wikidata or the infobox say it fought and that
    is not a big war, its central, ending or founding conflict, or part of one; one linked from the article's opening
    first, then by language editions.

    Reviewed overrides then set or remove a pick, a big war or a civilization's place in one, and a conflict an override
    takes out of a role is not used in another of the civilization's; an override that no longer changes anything
    fails the run. A conflict whose name another chosen conflict shares is titled by its article or its years."""

    def __init__(self, http: Http):
        self.http = http
        self.wikipedia = Wikipedia(http)
        self.wikidata = Wikidata(http)

    def collect(self, documents: list[dict], overrides: list[ConflictOverride] = ()) -> list[dict]:
        civilizations = self._civilizations(documents)
        conflicts = self._candidates(civilizations)
        by_id = {civilization.id: civilization for civilization in civilizations}
        if unknown := [override.who for override in overrides if override.civilization_id
                       and override.civilization_id not in by_id]:
            raise ValueError(f"Conflict overrides name civilizations not in the deck: {'; '.join(unknown)}")
        if missing := sorted({override.conflict_id for override in overrides} - set(conflicts)):
            conflicts |= self._conflicts(self.wikidata.entities(missing))
        used: set[ConflictOverride] = set()
        big = self._big(civilizations, conflicts)
        self._override_big(big, civilizations, conflicts, by_id, overrides, used)
        picks: dict[tuple[str, str], list[ConflictOverride]] = {}
        for override in overrides:
            if override.role != Role.BIG:
                picks.setdefault((override.civilization_id, override.role), []).append(override)

        rejected: dict[str, set[str]] = {}

        def overridden(civilization: Civilization, role: Role, pick: Candidate | None) -> Candidate | None:
            for override in picks.get((civilization.id, role), []):
                if override.action == "remove" and pick and pick.conflict.id == override.conflict_id:
                    rejected.setdefault(civilization.id, set()).add(pick.conflict.id)
                    pick = None
                    used.add(override)
                elif override.action == "set" and not (pick and pick.conflict.id == override.conflict_id):
                    if pick:
                        rejected.setdefault(civilization.id, set()).add(pick.conflict.id)
                    pick = self._overriding(civilization, conflicts[override.conflict_id])
                    used.add(override)
            return pick

        roles: dict[str, dict[str, list[dict]]] = {}

        def assign(conflict: Conflict, civilization: Civilization, role: Role, evidence: set[Evidence]) -> None:
            roles.setdefault(conflict.id, {}).setdefault(role, []).append(
                {"id": civilization.id, "name": civilization.name, "evidence": sorted(evidence)})

        for conflict_id, members in big.items():
            for civilization, candidate in members:
                assign(candidate.conflict, civilization, Role.BIG, candidate.evidence)
        ancestors = self.wikidata.ancestors(sorted({candidate.conflict.id for civilization in civilizations
                                                    for candidate in civilization.candidates}))
        for civilization in civilizations:
            ending = overridden(civilization, Role.ENDING, self._ending(civilization, big))
            founding = overridden(civilization, Role.FOUNDING, self._founding(civilization, big))
            taken = {candidate.conflict.id for candidate in (ending, founding) if candidate}
            barred = rejected.get(civilization.id, set())
            central = overridden(civilization, Role.CENTRAL, self._central(civilization, big, taken, ancestors, barred))
            second = overridden(civilization, Role.SECOND, self._second(civilization, big, [central, ending, founding],
                                                                        ancestors, rejected.get(civilization.id, set())))
            for role, candidate in ((Role.CENTRAL, central), (Role.ENDING, ending), (Role.FOUNDING, founding),
                                    (Role.SECOND, second)):
                if candidate:
                    assign(candidate.conflict, civilization, role, candidate.evidence)
        if stale := [override.who for override in overrides if override not in used]:
            raise ValueError(f"Conflict overrides that no longer change anything: {'; '.join(stale)}")
        logger.info("Chose %d big wars and %s", len(big), ", ".join(
            f"{sum(role in r for r in roles.values())} {role}" for role in (Role.CENTRAL, Role.ENDING, Role.FOUNDING, Role.SECOND)))
        order = sorted(roles, key=lambda qid: (conflicts[qid].start is None, conflicts[qid].start or 0, conflicts[qid].name))
        titles = self._titles([conflicts[qid] for qid in order])
        return [self._document(conflicts[qid], titles[qid], {role: roles[qid][role] for role in Role if role in roles[qid]})
                for qid in order]

    @staticmethod
    def _titles(chosen: list[Conflict]) -> dict[str, str]:
        """Each conflict's name, or, when another chosen conflict shares it, its Wikipedia title if that tells them
        apart and otherwise the name with its years."""
        names = Counter(conflict.name for conflict in chosen)
        articles = Counter(conflict.wikipedia for conflict in chosen if names[conflict.name] > 1)
        titles = {}
        for conflict in chosen:
            if names[conflict.name] == 1:
                titles[conflict.id] = conflict.name
            elif conflict.wikipedia and conflict.wikipedia != conflict.name and articles[conflict.wikipedia] == 1:
                titles[conflict.id] = conflict.wikipedia
            elif conflict.start is not None:
                start, end = Span(conflict.start, conflict.start), Span(conflict.end, conflict.end)
                titles[conflict.id] = f"{conflict.name} ({start.label() if start == end else period_label(start, end)})"
            else:
                titles[conflict.id] = conflict.name
        return titles

    @staticmethod
    def _overriding(civilization: Civilization, conflict: Conflict) -> Candidate:
        found = next((candidate for candidate in civilization.candidates if candidate.conflict.id == conflict.id), None)
        if found:
            return Candidate(conflict, found.evidence | {Evidence.OVERRIDE}, found.in_lead, found.in_article, found.during)
        return Candidate(conflict, {Evidence.OVERRIDE}, False, False, True)

    def _override_big(self, big: dict, civilizations: list[Civilization], conflicts: dict[str, Conflict],
                      by_id: dict[str, Civilization], overrides: list[ConflictOverride], used: set) -> None:
        for override in overrides:
            if override.role != Role.BIG:
                continue
            qid, members = override.conflict_id, big.get(override.conflict_id)
            if not override.civilization_id:
                if override.action == "remove" and members is not None:
                    del big[qid]
                    used.add(override)
                elif override.action == "set" and members is None:
                    big[qid] = [(civilization, candidate) for civilization in civilizations
                                for candidate in civilization.candidates
                                if candidate.conflict.id == qid and candidate.during and not candidate.weak]
                    used.add(override)
                continue
            if members is None:
                raise ValueError(f"{override.who}: not a big war")
            civilization = by_id[override.civilization_id]
            present = any(member.id == civilization.id for member, _ in members)
            if override.action == "set" and not present:
                members.append((civilization, self._overriding(civilization, conflicts[qid])))
                used.add(override)
            elif override.action == "remove" and present:
                big[qid] = [(member, candidate) for member, candidate in members if member.id != civilization.id]
                used.add(override)

    def _civilizations(self, documents: list[dict]) -> list[Civilization]:
        pages = self.wikipedia.resolve([document["wikipedia"] for document in documents])
        same = self.wikidata.same_as([document["id"] for document in documents])
        civilizations = []
        for document in documents:
            page = pages.get(document["wikipedia"])
            period = document["period"]
            civilizations.append(Civilization(
                document["id"], document["name"], document["wikipedia"], page.title if page else document["wikipedia"],
                document["tier"],
                (period.get("start") or {}).get("earliest"), (period.get("end") or {}).get("latest"),
                {successor["id"] for successor in document["after"]},
                {document["id"]} | ({page.qid} if page and page.qid else set()) | same[document["id"]]))
        by_id = {civilization.id: civilization for civilization in civilizations}
        befores = {document["id"]: [before["id"] for before in document["before"]] for document in documents}
        for civilization in civilizations:
            civilization.predecessors = set().union(*(by_id[q].identities if q in by_id else {q}
                                                      for q in befores[civilization.id])) - civilization.identities
        sovereigns = self.wikidata.values(sorted({q for c in civilizations if c.end is not None for q in c.successors}),
                                          SOVEREIGN_PROPERTIES)
        for civilization in civilizations:
            if civilization.end is None:
                continue
            lineage = {predecessor for successor in civilization.successors for predecessor in befores.get(successor, [])
                       if predecessor != civilization.id and predecessor in by_id and by_id[predecessor].end is not None
                       and abs(by_id[predecessor].end - civilization.end) <= LINEAGE_YEARS}
            successors = civilization.successors
            civilization.successors = set().union(*(by_id[q].identities if q in by_id else {q}
                                                    for q in successors | lineage),
                                                  *(sovereigns.get(q, set()) for q in successors)) - civilization.identities
        return civilizations

    def _candidates(self, civilizations: list[Civilization]) -> dict[str, Conflict]:
        kinds = self.wikidata.subclasses(CONFLICT_ROOTS)
        unreal = self.wikidata.subclasses(UNREAL_ROOTS)
        articles = self.wikipedia.wikitext([civilization.wikipedia for civilization in civilizations], follow=True)
        leads = {civilization.id: lead_links(articles.get(civilization.wikipedia, "")) for civilization in civilizations}
        linked = {civilization.id: leads[civilization.id] + link_targets(mw.parse(articles.get(civilization.wikipedia, "")))
                  for civilization in civilizations}
        searched = {civilization.id: self.wikipedia.linking_articles(civilization.article, CONFLICT_INFOBOX)
                    for civilization in civilizations}
        titles = set().union(*linked.values(), *searched.values())
        items = {title: page.qid for title, page in self.wikipedia.resolve(sorted(titles)).items() if page.qid}
        classes = self.wikidata.instance_classes(sorted(set(items.values())))
        is_conflict = {qid for qid, found in classes.items() if found & kinds}
        owners = {identity: civilization for civilization in civilizations for identity in civilization.identities}
        taking_part: dict[str, set[str]] = {civilization.id: set() for civilization in civilizations}
        for identity, events in self.wikidata.participations(sorted(owners)).items():
            taking_part[owners[identity].id] |= {event for event, found in events.items() if found & kinds}
        found_ids = is_conflict | set().union(*taking_part.values())
        entities = self.wikidata.entities(sorted(found_ids))
        entities = {qid: entity for qid, entity in entities.items() if not set(entity.ids("P31", preferred_only=False)) & unreal}
        conflicts = self._conflicts(entities)
        for civilization in civilizations:
            from_article = [items.get(title) for title in dict.fromkeys(linked[civilization.id])]
            in_lead = {items.get(title) for title in leads[civilization.id]}
            reached = [items.get(title) for title in searched[civilization.id]]
            for qid in dict.fromkeys(from_article + sorted(taking_part[civilization.id]) + sorted(q for q in reached if q)):
                if qid not in conflicts:
                    continue
                conflict = conflicts[qid]
                linked_back = bool(civilization.identities & conflict.lead)
                if qid not in from_article and not linked_back:
                    continue
                evidence = set()
                if qid in taking_part[civilization.id] or civilization.identities & conflict.participants:
                    evidence.add(Evidence.WIKIDATA)
                if civilization.identities & set(conflict.infobox.sides):
                    evidence.add(Evidence.INFOBOX)
                if qid in from_article and linked_back:
                    evidence.add(Evidence.MUTUAL)
                during = civilization.during(conflict)
                if evidence and (during or civilization.founded_by(conflict)):
                    civilization.candidates.append(Candidate(conflict, evidence, qid in in_lead, qid in from_article, during))
            civilization.candidates.sort(key=lambda candidate: (not candidate.in_lead, -candidate.conflict.editions))
        return conflicts

    def _conflicts(self, entities: dict[str, Entity]) -> dict[str, Conflict]:
        editions = self.wikidata.language_editions(sorted(entities))
        texts = self.wikipedia.wikitext([entity.enwiki for entity in entities.values() if entity.enwiki], follow=True)
        leads = {qid: lead_links(texts.get(entity.enwiki or "", "")) for qid, entity in entities.items()}
        boxes = {qid: self._infobox_links(texts.get(entity.enwiki or "", "")) for qid, entity in entities.items()}
        titles = set().union(*leads.values(), *(set().union(*sides.values(), *(links for links, _ in results))
                                                for sides, results in boxes.values()))
        items = {title: page.qid for title, page in self.wikipedia.resolve(sorted(titles)).items() if page.qid}

        def resolved(titles: list[str]) -> set[str]:
            return {items[title] for title in titles if title in items}

        participants = {qid: set(entity.ids("P710", preferred_only=False)) | set(entity.ids("P1269", preferred_only=False))
                        for qid, entity in entities.items()}
        combatants = {item for sides, _ in boxes.values() for links in sides.values() for item in resolved(links)}
        members = self._alliance_members(set().union(*participants.values()) | combatants)
        fights = self.wikidata.subclasses(FIGHT_ROOTS)
        conflicts = {}
        for qid, entity in entities.items():
            sides, results = boxes[qid]
            infobox = Infobox({}, [(resolved(links), words) for links, words in results])
            for side, links in sides.items():
                for item in resolved(links):
                    for member in {item} | members.get(item, set()):
                        infobox.sides.setdefault(member, set()).add(side)
            starts = [year.earliest for year in precise(entity.years("P580") + entity.years("P585"))]
            ends = [year.latest for year in precise(entity.years("P582") + entity.years("P585"))]
            start = min(starts) if starts else None
            conflicts[qid] = Conflict(qid, entity.label or qid, entity.enwiki, start, max(ends) if ends else start,
                                      editions.get(qid, 0), infobox, resolved(leads[qid]),
                                      participants[qid].union(*(members.get(item, set()) for item in participants[qid])),
                                      bool(set(entity.ids("P31")) & fights))
        return conflicts

    def _alliance_members(self, items: set[str]) -> dict[str, set[str]]:
        """The members of each item that Wikidata classes as a military alliance: its parts (P527) and the items that
        are members of it (P463), with the members of an alliance among them (the Triple Entente in the Allies of
        World War I)."""
        alliances = self.wikidata.subclasses(ALLIANCE_ROOTS)
        members: dict[str, set[str]] = {}
        frontier = sorted(items)
        for _ in range(ALLIANCE_DEPTH):
            classes = self.wikidata.instance_classes(frontier)
            found = [qid for qid, kinds in classes.items() if kinds & alliances and qid not in members]
            if not found:
                break
            joined = self.wikidata.members(found)
            entities = self.wikidata.entities(found)
            for alliance in found:
                members[alliance] = joined[alliance] | set(entities[alliance].ids("P527", preferred_only=False))
            frontier = sorted(set().union(*(members[alliance] for alliance in found)))
        for alliance in list(members):
            for member in list(members[alliance]):
                members[alliance] |= members.get(member, set())
        return members

    @staticmethod
    def _infobox_links(wikitext: str) -> tuple[dict[str, list[str]], list[tuple[list[str], str]]]:
        """The articles each numbered combatant column links, and each line of the result and territory fields with its
        links and its text."""
        sides: dict[str, list[str]] = {}
        results: list[tuple[list[str], str]] = []
        for template in mw.parse(wikitext).filter_templates():
            if not str(template.name).strip().lower().startswith(CONFLICT_INFOBOX.lower()):
                continue
            for param in template.params:
                name = str(param.name).strip()
                if side := COMBATANT.match(name):
                    fighting = mw.parse(SUPPORTERS.split(str(param.value), maxsplit=1)[0])
                    flagged = [str(flag.params[0].value).strip() for flag in fighting.filter_templates()
                               if str(flag.name).strip().lower() == "flagicon" and flag.params]
                    sides.setdefault(side.group(1), []).extend(link_targets(fighting) + flagged)
                elif RESULT.match(name):
                    for line in LINE_BREAK.split(str(param.value)):
                        piece = mw.parse(line)
                        results.append((link_targets(piece), plain_text(piece)))
            break
        return sides, results

    def _big(self, civilizations: list[Civilization],
             conflicts: dict[str, Conflict]) -> dict[str, list[tuple[Civilization, Candidate]]]:
        members: dict[str, list[tuple[Civilization, Candidate]]] = {}
        for civilization in civilizations:
            for candidate in civilization.candidates:
                if candidate.during and not candidate.weak:
                    members.setdefault(candidate.conflict.id, []).append((civilization, candidate))
        levels = vital.article_levels(self.http, self.wikipedia)
        shared = {candidate.conflict.id for civilization in civilizations for candidate in civilization.candidates}

        def qualifies(qid: str) -> bool:
            level = levels.get(conflicts[qid].wikipedia)
            count = len(members.get(qid, []))
            if not level or conflicts[qid].fight or count < BIG_BELLIGERENTS_ALONE:
                return False
            return level <= BIG_LEVEL_ALONE or (level <= BIG_LEVEL and count >= BIG_BELLIGERENTS)

        big = {qid for qid in shared if qualifies(qid)}
        big -= self.wikidata.nested(sorted(big))
        return {qid: members[qid] for qid in big}

    @staticmethod
    def _central(civilization: Civilization, big: dict, taken: set[str], ancestors: dict[str, set[str]],
                 barred: set[str] = frozenset()) -> Candidate | None:
        kept = [candidate for candidate in civilization.candidates
                if candidate.during and candidate.conflict.id not in big and candidate.conflict.id not in barred
                and candidate.conflict.editions >= MIN_EDITIONS]
        kept.sort(key=lambda candidate: (not candidate.in_lead, not candidate.in_lead and candidate.weak,
                                         -candidate.conflict.editions))
        distinct = [candidate for candidate in kept if candidate.conflict.id not in taken
                    and not ancestors.get(candidate.conflict.id, set()) & taken]
        return (distinct or kept or [None])[0]

    @staticmethod
    def _ending(civilization: Civilization, big: dict) -> Candidate | None:
        if civilization.end is None:
            return None
        end = civilization.end
        found = []
        for candidate in civilization.candidates:
            conflict = candidate.conflict
            if conflict.start is None or conflict.span > MAX_ENDING_SPAN or conflict.editions < MIN_EDITIONS:
                continue
            if not conflict.start <= end + YEARS_AFTER_END or not conflict.end >= end - YEARS_BEFORE_END:
                continue
            sides = conflict.infobox.sides
            mine = set().union(*(sides.get(q, set()) for q in civilization.identities))
            theirs = set().union(*(sides.get(q, set()) for q in civilization.successors))
            opposed = bool(theirs - mine) if mine and theirs else not theirs
            victors = {q for links, words in conflict.infobox.results if VICTORY.search(words) for q in links}
            winners = set().union(*(sides.get(q, set()) for q in victors))
            by_successor = (bool(civilization.successors & conflict.belligerents) and opposed
                            or bool(civilization.successors & victors - civilization.identities))
            by_defeat = candidate.in_article and not conflict.fight and bool(mine and winners and not winners & mine)
            by_result = any(civilization.named_in(links, words) and ENDING_WORDS.search(words)
                            for links, words in conflict.infobox.results)
            if by_successor or by_defeat or by_result:
                found.append(candidate)

        def distance(candidate: Candidate) -> int:
            conflict = candidate.conflict
            return 0 if conflict.start <= end <= conflict.end else min(abs(end - conflict.start), abs(end - conflict.end))

        found.sort(key=lambda candidate: (distance(candidate), candidate.conflict.id in big, -candidate.conflict.editions))
        return found[0] if found else None

    @staticmethod
    def _founding(civilization: Civilization, big: dict) -> Candidate | None:
        start = civilization.start
        found = []
        for candidate in civilization.candidates:
            conflict = candidate.conflict
            if not civilization.founded_by(conflict) or conflict.span > MAX_ENDING_SPAN or conflict.editions < MIN_EDITIONS:
                continue
            sides = conflict.infobox.sides
            mine = set().union(*(sides.get(q, set()) for q in civilization.identities))
            theirs = set().union(*(sides.get(q, set()) for q in civilization.predecessors))
            victors = {q for links, words in conflict.infobox.results if VICTORY.search(words) for q in links}
            by_predecessor = bool(mine and theirs - mine)
            by_victory = bool(civilization.identities & victors)
            by_result = any(civilization.named_in(links, words) and FOUNDING_WORDS.search(words)
                            for links, words in conflict.infobox.results)
            if by_predecessor or by_victory or by_result:
                found.append(candidate)

        def distance(candidate: Candidate) -> int:
            conflict = candidate.conflict
            return 0 if conflict.start <= start <= conflict.end else min(abs(start - conflict.start), abs(start - conflict.end))

        found.sort(key=lambda candidate: (distance(candidate), candidate.conflict.id in big, -candidate.conflict.editions))
        return found[0] if found else None

    @staticmethod
    def _second_pool(civilization: Civilization, big: dict) -> list[Candidate]:
        if civilization.tier not in SECOND_TIERS and len(civilization.candidates) < SECOND_CANDIDATES:
            return []
        return [candidate for candidate in civilization.candidates
                if candidate.during and not candidate.conflict.fight and candidate.conflict.editions >= SECOND_EDITIONS
                and candidate.conflict.id not in big and candidate.evidence & {Evidence.WIKIDATA, Evidence.INFOBOX}]

    @classmethod
    def _second(cls, civilization: Civilization, big: dict, chosen: list[Candidate | None],
                ancestors: dict[str, set[str]], barred: set[str] = frozenset()) -> Candidate | None:
        taken = {candidate.conflict.id for candidate in chosen if candidate} | barred
        covering = taken | {candidate.conflict.id for candidate in civilization.candidates if candidate.conflict.id in big}
        pool = [candidate for candidate in cls._second_pool(civilization, big)
                if candidate.conflict.id not in taken and not ancestors.get(candidate.conflict.id, set()) & covering]
        return min(pool, key=lambda candidate: (not candidate.in_lead, -candidate.conflict.editions), default=None)

    @staticmethod
    def _document(conflict: Conflict, title: str, roles: dict[str, list[dict]]) -> dict:
        return {"id": conflict.id, "slug": slugify(conflict.wikipedia or conflict.name), "name": conflict.name, "title": title,
                "wikipedia": conflict.wikipedia, "years": [conflict.start, conflict.end], "editions": conflict.editions,
                "roles": roles}
