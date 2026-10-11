import logging
from dataclasses import dataclass

import pandas as pd
import shapely
from shapely.geometry import Point

from page_in_history.polities import POLITY_ROOTS, Identity, PolityCatalog
from page_in_history.sources.cliopatria import CAPITAL_TOLERANCE_DEGREES, Cliopatria
from page_in_history.sources.wikidata import Entity
from page_in_history.sources.wikipedia import country_infobox, display_name

OVERSHOOT_YEARS = 25
HANDOVER_TOLERANCE_YEARS = 25
UNCLAIMED_YEARS = 50
FORWARD = ("P1366", "P156")
BACKWARD = ("P1365", "P155")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Handover:
    name: str
    side: str
    start: int
    end: int
    to: str | None

    def to_json(self) -> dict:
        return {"name": self.name, "side": self.side, "years": [self.start, self.end], "to": self.to}


class Lineages:
    """Cliopatria sometimes keeps one name for a chain of states, such as the Kingdom of Great Britain to this day.
    Where its years run more than 25 years past the dates Wikidata gives the state, the years beyond go to the
    successor (or predecessor) that both Wikidata and the Wikipedia infobox name and whose capital lies inside the
    borders it takes over; the chain continues from there. The successor's own dates must meet the handover within
    25 years, or it must have begun while the state still stood and outlast the handover, as the Almohads did the
    Almoravids. A successor that itself ended at the handover, like the short Duchy of Florence, is passed through to
    its own successor, and one that is a period of a country rather than a state, like Sweden's Age of Liberty, hands
    the years to that country. Years without such a successor stay with the original name, unless there are more than
    50 of them: then the map has no state for them, as for Cliopatria's Tibet before 1912 or its Ngô dynasty after
    965. A name whose years never meet its state's dates points at the wrong state and is identified again by name, or
    leaves the catalogue."""

    def __init__(self, catalog: PolityCatalog):
        self.catalog = catalog
        self.wikidata = catalog.wikidata
        self.wikipedia = catalog.wikipedia
        self.frame = catalog.cliopatria.frame.copy()
        self.names = {identity.qid: name for name, identity in catalog.identities.items()}
        self.infoboxes: dict[str, tuple[set[str], set[str]]] = {}
        self.handovers: list[Handover] = []
        self.mismatched: list[str] = []
        self.dropped: list[str] = []

    def apply(self) -> None:
        spans = {}
        for name, identity in list(self.catalog.identities.items()):
            if identity.qid in self.catalog.mapless:
                continue
            rows = self.frame[self.frame.Name == name]
            first, last = int(rows.FromYear.min()), int(rows.ToYear.max())
            start, end = dates(self.catalog.entities[identity.qid])
            if (start is not None and start > last) or (end is not None and end < first):
                self.mismatched.append(name)
                spans[name] = (first, last)
                continue
            if end is not None and last - end > OVERSHOOT_YEARS:
                self._hand_over(name, identity.qid, end + 1, last, "after")
            if start is not None and start - first > OVERSHOOT_YEARS:
                self._hand_over(name, identity.qid, first, start - 1, "before")
        for handover in self.handovers:
            if handover.to is None and handover.end - handover.start + 1 > UNCLAIMED_YEARS:
                self._relabel(handover.name, None, handover.start, handover.end)
        self.dropped = self.catalog.reidentify(spans)
        frame = self.frame[self.frame.FromYear <= self.frame.ToYear].sort_values(["Name", "FromYear"])
        self.catalog.cliopatria = Cliopatria(frame.reset_index(drop=True), self.catalog.cliopatria.spikes)
        logger.info("Handed %d stretches of Cliopatria names to their successors; %d names do not match their state",
                    sum(handover.to is not None for handover in self.handovers), len(self.mismatched))

    def _hand_over(self, name: str, qid: str, first: int, last: int, side: str) -> None:
        current = qid
        while first <= last:
            edge = first if side == "after" else last
            found = self._successor(name, current, edge, first, last, side)
            if not found:
                self.handovers.append(Handover(name, side, first, last, None))
                return
            successor, start, end = found
            target = self._identity(successor)
            self._relabel(name, target, start, end)
            self.handovers.append(Handover(name, side, start, end, target))
            current = successor
            first, last = (end + 1, last) if side == "after" else (first, start - 1)

    def _successor(self, name: str, current: str, edge: int, first: int, last: int, side: str, through: bool = True):
        """The state that took over at the edge, with the years it holds of the remaining stretch."""
        forward = FORWARD if side == "after" else BACKWARD
        entity = self.wikidata.entities([current]).get(current)
        options = set(entity.ids(*forward, preferred_only=False)) if entity else set()
        options |= self._infobox(current)[side == "after"]
        own = dates(entity) if entity else (None, None)
        best, ended = None, []
        for qid, option in self.wikidata.entities(sorted(options)).items():
            if qid == current or not option.enwiki or not self._confirmed(current, qid, side):
                continue
            state = qid if self._is_polity(option) else self._country_of_period(option, edge)
            start, end = dates(option)
            meeting = start if side == "after" else end
            if state is None or meeting is None:
                continue
            if side == "after" and end is not None and edge - HANDOVER_TOLERANCE_YEARS <= end < edge:
                ended.append(qid)
            distance = abs(meeting - edge)
            if distance > HANDOVER_TOLERANCE_YEARS and not overlapping(own, (start, end), edge, side):
                continue
            if state != qid:
                held = (edge, last) if side == "after" else (first, edge)
            else:
                held = (edge, min(end, last) if end is not None else last) if side == "after" \
                    else (max(start, first) if start is not None else first, edge)
            state_entity = option if state == qid else self.wikidata.entities([state])[state]
            if held[0] > held[1] or not self._capital_inside(state_entity, name, *held):
                continue
            rank = (distance > HANDOVER_TOLERANCE_YEARS, distance)
            if best is None or rank < best[0]:
                best = (rank, state, *held)
        if best:
            return best[1:]
        if through:
            return next((found for qid in ended
                         if (found := self._successor(name, qid, edge, first, last, side, through=False))), None)
        return None

    def _country_of_period(self, period: Entity, edge: int) -> str | None:
        """The catalogue state whose history includes the period, directly or through what the period is part of, and
        which already stood at the edge: Sweden for its Age of Liberty, but not today's Egypt for the Second
        Intermediate Period."""
        histories = {period.id} | set(period.ids("P361", preferred_only=False))
        found = [qid for qid in self.catalog.by_qid
                 if histories & set(self.catalog.entities[qid].ids("P2184", preferred_only=False))]
        for history in self.wikidata.entities(sorted(histories - {period.id})).values():
            found += [state for state in history.ids("P1269", preferred_only=False) if state in self.catalog.by_qid]
        for state in found:
            start, end = dates(self.catalog.entities[state])
            if start is not None and start <= edge and (end is None or end >= edge):
                return state
        return None

    def _confirmed(self, current: str, other: str, side: str) -> bool:
        forward, backward = (FORWARD, BACKWARD) if side == "after" else (BACKWARD, FORWARD)
        entities = self.wikidata.entities([current, other])
        wikidata = (current in entities and other in entities[current].ids(*forward, preferred_only=False)) or \
                   (other in entities and current in entities[other].ids(*backward, preferred_only=False))
        mine, theirs = self._infobox(current), self._infobox(other)
        infobox = other in mine[side == "after"] or current in theirs[side != "after"]
        return wikidata and infobox

    def _infobox(self, qid: str) -> tuple[set[str], set[str]]:
        """The states the Wikipedia infobox names as predecessors and successors."""
        if qid not in self.infoboxes:
            entity = self.wikidata.entities([qid]).get(qid)
            title = entity.enwiki if entity else None
            box = country_infobox(self.wikipedia.wikitext([title]).get(title, "")) if title else None
            if box:
                pages = self.wikipedia.resolve(box.predecessors + box.successors)
                linked = lambda titles: {pages[title].qid for title in titles if title in pages and pages[title].qid}
                self.infoboxes[qid] = (linked(box.predecessors), linked(box.successors))
            else:
                self.infoboxes[qid] = (set(), set())
        return self.infoboxes[qid]

    def _is_polity(self, entity: Entity) -> bool:
        unknown = set(entity.ids("P31", preferred_only=False)) - self.catalog.polity_classes
        if unknown:
            self.catalog.polity_classes |= self.wikidata.subclass_of(sorted(unknown), POLITY_ROOTS)
        return self.catalog.is_polity(entity)

    def _capital_inside(self, entity: Entity, name: str, first: int, last: int) -> bool:
        rows = self._rows(name, first, last)
        if rows.empty:
            return False
        capitals = self.wikidata.entities(entity.ids("P36", preferred_only=False))
        points = [capital.coordinates() for capital in capitals.values() if capital.coordinates()]
        points = points or [location for location in [entity.coordinates()] if location]
        shape = shapely.union_all(rows.geometry.values)
        return any(shapely.distance(shape, Point(point)) <= CAPITAL_TOLERANCE_DEGREES for point in points)

    def _identity(self, qid: str) -> str:
        if qid in self.names:
            return self.names[qid]
        entity = self.wikidata.entities([qid])[qid]
        name = display_name(entity.enwiki)
        if name in self.catalog.identities:
            name = entity.enwiki
        self.catalog.add_drawn(Identity(name, entity.enwiki, qid, "successor on borders Cliopatria gives an earlier name"),
                               entity)
        self.names[qid] = name
        return name

    def _rows(self, name: str, first: int, last: int):
        return self.frame[(self.frame.Name == name) & (self.frame.ToYear >= first) & (self.frame.FromYear <= last)]

    def _relabel(self, name: str, target: str | None, first: int, last: int) -> None:
        """The name's years from first to last drawn as the target, or as no state when there is none."""
        rows = self._rows(name, first, last)
        self.frame = self.frame.drop(index=rows.index)
        inside = rows.assign(Name=target, FromYear=rows.FromYear.clip(lower=first), ToYear=rows.ToYear.clip(upper=last))
        inside = inside if target else inside.iloc[:0]
        before = rows[rows.FromYear < first].assign(ToYear=first - 1)
        after = rows[rows.ToYear > last].assign(FromYear=last + 1)
        self.frame = pd.concat([self.frame, inside, before, after], ignore_index=True)


def overlapping(own: tuple[int | None, int | None], other: tuple[int | None, int | None], edge: int, side: str) -> bool:
    """The other state stood at the edge and arose (after) or ended (before) within the state's own years."""
    start, end = other
    if start is None or start > edge or (end is not None and end < edge):
        return False
    if side == "after":
        return own[0] is None or start >= own[0]
    return end is not None and (own[1] is None or end <= own[1])


def dates(entity: Entity) -> tuple[int | None, int | None]:
    """A state's first and last year by Wikidata: inception or start time, dissolution or end time."""
    starts = [year.earliest for year in entity.years("P571") + entity.years("P580")]
    ends = [year.latest for year in entity.years("P576") + entity.years("P582")]
    return min(starts, default=None), max(ends, default=None)
