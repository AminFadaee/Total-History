from collections.abc import Callable
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import Point

from total_history.sources.cliopatria import CAPITAL_TOLERANCE_DEGREES, HOLDING_GAP_YEARS

WINDOW_YEARS = 30
MIN_HOLD_YEARS = 6
HEARTLAND_GRID = 60
PARTITION_SHARE = 0.15
PARTITION_HOLDERS = 3
HOMELAND_DEGREES = 10
WIDE_YEARS = 100
NEAR_DEGREES = 2
BACKING_FULL = 2.25


@dataclass(frozen=True)
class Holding:
    qid: str
    start: int
    end: int

    @property
    def years(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class Judge:
    """What the deck knows of the states around a civilization: which may be asked as its neighbour (in the deck or well
    known, not named like it), which is less known but may stand in when the sources and the maps agree, which is named
    like it and so leaves the side unasked, whether one can have followed the other, their years (and core years), the
    deck, the states the sources say came after (or before) one, and the country of a deck state's home."""
    asked: Callable[[str], bool]
    less_known: Callable[[str], bool]
    blocking: Callable[[str], bool]
    follows: Callable[[str, str, bool], bool]
    years: Callable[[str], tuple[int, int] | None]
    spans: Callable[[str], list[tuple[int, int]]]
    in_deck: Callable[[str], bool]
    followers: Callable[[str, bool], set[str]]
    home: Callable[[str], str | None]


@dataclass(frozen=True)
class Anchor:
    point: Point
    place: str
    region: str


class RegionalSuccession:
    """Who ruled a civilization's home region right before and right after it. The home is the capital it held
    longest, or without a confirmed capital the spot it held for the most years; the region is the modern country
    around it. The neighbour is the nearest holder on Cliopatria's maps within 30 years that Wikidata or the Wikipedia
    infobox also says came before or after; failing that, the nearest whose succession they imply through a part of
    it (Carthage was followed by the Roman province of Africa, whose country Ancient Rome lists the Roman Republic). The 30 years run from the
    end the maps draw or the end of its period, whichever is nearer. Failing those, a less known holder the sources
    name, when it also took the largest share of the civilization's last (first) land (Northern Zhou before Sui); then a
    deck state both sides name as each other's neighbour, within 30 years and drawn within 2 degrees of the home, that
    held the home on the maps or has its own home in the same country (not Sweden-Norway after Denmark-Norway, drawn
    across the strait from Copenhagen); then a deck state the sources name that held the
    home up to a century away with no other deck state holding it in between. Holders that stayed five
    years or less, like a wartime occupation, are passed over unless Wikidata and the infobox both name them, they
    lasted longer, and nothing longer-held answers (Cao Wei after Shu Han); so are those that cannot have followed it,
    and an empire partitioned among three or more states has no single successor, unless it outlasted its last map by
    over a century (the Zhou). When the nearest confirmed holder is named like
    the civilization (Qin and the Qin dynasty), the side is left unasked, unless the two are consecutive states (the
    Kingdom and the Crown of Castile)."""

    def __init__(self, frame: gpd.GeoDataFrame, qids: dict[str, str], countries: gpd.GeoDataFrame,
                 stated: Callable[[str, str], bool], corroborated: Callable[[str, str], bool],
                 implied: Callable[[str, str], bool], equal_area: Callable):
        self.frame = frame.assign(qid=frame.Name.map(qids)).dropna(subset=["qid"])
        self.countries = countries.to_crs(frame.crs)
        self.stated = stated
        self.corroborated = corroborated
        self.implied = implied
        self.equal_area = equal_area

    def neighbours(self, qid: str, capitals: list[dict], edges: tuple, judge: Judge, years: tuple | None = None,
                   places: tuple = (), unplaced: bool = False) -> tuple[list[dict], list[dict], str]:
        """Before and after, the main answer judged at the civilization's home and named by its country. When Wikidata
        dates the capitals, a side is judged at the capital held at that end if it lies in the same country (Ming's
        successor at Beijing, not at its first capital Nanjing); one in another country gives an answer for that
        country beside the main one (the Roman Empire's at Constantinople). A side whose nearest confirmed holder is
        named like the civilization comes back as blocked. Each answer says how it was found: on the map, less known,
        mutually stated, or across a wide window. An empire drawn far from its capital, as Cliopatria draws the
        British and Spanish Empires without Britain and Spain, has none: its home would be a colony."""
        rows = self.frame[self.frame.qid == qid]
        if rows.empty:
            return [], [], ""
        anchor = self._anchor(rows, capitals, years, places, unplaced)
        located = [Point(capital["location"]) for capital in capitals if capital["location"]]
        if not anchor.place and located and anchor.region == self._country(located[0]) \
                and anchor.point.distance(located[0]) > HOMELAND_DEGREES:
            return [], [], ""
        before = self._sides(qid, rows, edges[0], anchor, True, judge)
        after = [] if self.partitioned(qid, judge.years(qid)) else self._sides(qid, rows, edges[1], anchor, False, judge)
        return before, after, anchor.place

    def home(self, qid: str, capitals: list[dict], years: tuple | None = None, places: tuple = (),
             unplaced: bool = False) -> Anchor | None:
        """The civilization's home, as its before and after are judged: its point and today's country there."""
        rows = self.frame[self.frame.qid == qid]
        return None if rows.empty else self._anchor(rows, capitals, years, places, unplaced)

    def partitioned(self, qid: str, span: tuple | None) -> bool:
        """Whether the year after its last map its land was held in large parts by three or more states, unless it
        lasted over a century longer than that map (the Zhou, drawn until 751 BC, reigned to 256 BC)."""
        rows = self.frame[self.frame.qid == qid]
        return not rows.empty and not (span and span[1] - int(rows.ToYear.max()) > WIDE_YEARS) and self._partitioned(rows)

    def country(self, location) -> str:
        return self._country(Point(location))

    def held(self, qid: str, point: Point, tolerance: float = CAPITAL_TOLERANCE_DEGREES) -> bool:
        """Whether the civilization ever held the point on the maps, or land within reach of it."""
        return any(holding.qid == qid for holding in self._holdings(point, tolerance))

    def near(self, qid: str, moment: int, point: Point) -> bool:
        """Whether the civilization held land within 2 degrees of the point within 30 years of the moment."""
        rows = self.frame[(self.frame.qid == qid) & (self.frame.ToYear >= moment - WINDOW_YEARS)
                          & (self.frame.FromYear <= moment + WINDOW_YEARS)]
        return not rows.empty and shapely.distance(rows.geometry.values, point).min() <= NEAR_DEGREES

    def drawn(self, qid: str) -> bool:
        return bool((self.frame.qid == qid).any())

    def backing(self, qid: str, others: list[str], before: bool, anchor: Anchor, span: tuple | None) -> dict[str, float]:
        """How far the maps back each state as the one that came before (after) the civilization at its home, up to 1:
        the share of the civilization's first (last) shape it held within 30 years of the change, plus one if it held
        the home point within 30 years of the change or half of one if it held land within 2 degrees of the home, plus
        a quarter if it was ever drawn within 2 degrees of it, out of 2.25. A state not on the maps has none."""
        rows = self.frame[self.frame.qid == qid]
        edge = rows.loc[rows.FromYear.idxmin()] if before else rows.loc[rows.ToYear.idxmax()]
        change = int(edge.FromYear if before else edge.ToYear)
        moments = [change] + ([span[0] if before else span[1]] if span else [])
        shape = self.equal_area(gpd.GeoSeries([edge.geometry], crs=rows.crs)).iloc[0]
        held = self._holdings(anchor.point)
        year = change - 1 if before else change + 1
        found = {}
        for other in others:
            theirs = self.frame[self.frame.qid == other]
            if theirs.empty or other == qid:
                found[other] = 0.0
                continue
            gaps = [min(abs((holding.end if before else holding.start) - moment) for moment in moments)
                    for holding in held if holding.qid == other]
            around = theirs[(theirs.FromYear <= year + WINDOW_YEARS) & (theirs.ToYear >= year - WINDOW_YEARS)]
            around = around[around.geometry.intersects(edge.geometry)]
            share = max(geometry.intersection(shape).area for geometry in self.equal_area(around).geometry) / shape.area \
                if not around.empty else 0.0
            holds = 1.0 if gaps and min(gaps) <= WINDOW_YEARS else 0.5 * self.near(other, change, anchor.point)
            nearest = shapely.distance(theirs.geometry.values, anchor.point).min() <= NEAR_DEGREES
            found[other] = (share + holds + 0.25 * nearest) / BACKING_FULL
        return found

    def _sides(self, qid: str, rows: gpd.GeoDataFrame, edge, anchor: Anchor, before: bool, judge: Judge) -> list[dict]:
        """The main answer, and one at an end capital in another country. An answer at the end capital that repeats
        the main one is not asked again but keeps its country, so no other answer is asked there."""
        edge_rows = rows[rows.FromYear == rows.FromYear.min()] if before else rows[rows.ToYear == rows.ToYear.max()]
        near = edge and (shapely.distance(edge_rows.geometry.values, Point(edge)) <= CAPITAL_TOLERANCE_DEGREES).any()
        if not near or self._country(Point(edge)) == anchor.region:
            found = self._side(qid, edge if near else None, anchor, before, judge)
            return [found if found.get("blocked") else {**found, "main": True}] if found else []
        main = self._side(qid, None, anchor, before, judge)
        at_edge = self._side(qid, edge, anchor, before, judge)
        sides = [main if main.get("blocked") else {**main, "main": True}] if main else []
        if at_edge and not at_edge.get("blocked"):
            sides.append({**at_edge, "reserved": bool(main) and at_edge["id"] == main.get("id")})
        return sides

    def _side(self, qid: str, edge, anchor: Anchor, before: bool, judge: Judge) -> dict:
        point = Point(edge) if edge else anchor.point
        at = anchor if point is anchor.point else Anchor(point, anchor.place, self._country(point))
        holdings = self._holdings(point, CAPITAL_TOLERANCE_DEGREES if edge else 0)
        own = [holding for holding in holdings if holding.qid == qid]
        if not own:
            return {}
        span = judge.years(qid)
        drawn_end = own[0].start if before else own[-1].end
        moments = [drawn_end] + ([span[0] if before else span[1]] if span else [])
        pair = (lambda other: (other, qid)) if before else (lambda other: (qid, other))
        their_end = (lambda holding: holding.end) if before else (lambda holding: holding.start)
        distance = lambda holding: min(abs(their_end(holding) - moment) for moment in moments)
        others = [holding for holding in holdings if holding.qid != qid and holding.years >= MIN_HOLD_YEARS
                  and judge.follows(*pair(holding.qid), before)]
        nearby = sorted((holding for holding in others if distance(holding) <= WINDOW_YEARS),
                        key=lambda holding: (abs(their_end(holding) - drawn_end), holding.qid))
        for confirmed in (self.stated, self.implied):
            for holding in nearby:
                if confirmed(*pair(holding.qid)):
                    if judge.asked(holding.qid):
                        return {"id": holding.qid, "region": at.region, "how": "map"}
                    if judge.blocking(holding.qid):
                        return {"blocked": True}
        brief = sorted((holding for holding in holdings if holding.qid != qid and holding.years < MIN_HOLD_YEARS
                        and distance(holding) <= WINDOW_YEARS and judge.asked(holding.qid) and self._lasted(judge, holding.qid)
                        and judge.follows(*pair(holding.qid), before) and self.corroborated(*pair(holding.qid))),
                       key=lambda holding: (abs(their_end(holding) - drawn_end), holding.qid))
        if brief:
            return {"id": brief[0].qid, "region": at.region, "how": "map"}
        less_known = {holding.qid for holding in nearby if judge.less_known(holding.qid) and self.stated(*pair(holding.qid))}
        if less_known and (heir := self._heir(qid, before)) in less_known:
            return {"id": heir, "region": at.region, "how": "less known"}
        if span:
            moment = span[0] if before else span[1]
            for other in sorted(judge.followers(qid, not before)):
                theirs = judge.years(other)
                if judge.in_deck(other) and judge.asked(other) and judge.follows(*pair(other), before) \
                        and qid in judge.followers(other, before) and theirs \
                        and abs((theirs[1] if before else theirs[0]) - moment) <= WINDOW_YEARS and self.near(other, moment, point) \
                        and (judge.home(other) == at.region or self.held(other, point, 0)):
                    return {"id": other, "region": at.region, "how": "mutual"}
        wide = sorted((holding for holding in others if WINDOW_YEARS < distance(holding) <= WIDE_YEARS
                       and judge.in_deck(holding.qid) and (holding.start < own[0].start if before else holding.end > own[-1].end)),
                      key=lambda holding: (distance(holding), holding.qid))
        for holding in wide:
            if not self.stated(*pair(holding.qid)) or not judge.asked(holding.qid):
                continue
            low, high = sorted((holding.end, own[0].start) if before else (own[-1].end, holding.start))
            gaps = [(mine[0] - theirs[1]) if before else (theirs[0] - mine[1])
                    for mine in judge.spans(qid) for theirs in judge.spans(holding.qid)]
            between = [other for other in holdings if judge.in_deck(other.qid) and other.qid not in (holding.qid, qid)
                       and other.years >= MIN_HOLD_YEARS and other.start < high and other.end > low]
            if all(gap <= WIDE_YEARS for gap in gaps) and not between:
                return {"id": holding.qid, "region": at.region, "how": "wide"}
        return {}

    @staticmethod
    def _lasted(judge: Judge, qid: str) -> bool:
        years = judge.years(qid)
        return bool(years) and years[1] - years[0] + 1 >= MIN_HOLD_YEARS

    def _anchor(self, rows: gpd.GeoDataFrame, capitals: list[dict], years: tuple | None = None,
                places: tuple = (), unplaced: bool = False) -> Anchor:
        """The capital it held longest within its years: one Wikidata dates by the years of its terms (Székesfehérvár for
        Hungary, not Timișoara, capital for eight years), one it does not by the years the maps show it held, but no more
        than the years the dated ones leave (Vienna for Austria-Hungary). Without a capital held, the first place
        Wikidata gives as its capital within reach of its territory (Frankfurt for the German Confederation), else the
        spot held for the most years; an empire Cliopatria draws without its homeland is still named by its capital's
        country ("in Spain", not "in Cuba"). When no capital is dated and the infobox names first one that no source
        places, the one held is a later seat: if it lies in another country than the spot held for the most years and
        over 10 degrees from it, the home is that spot (the Göktürk Khaganate in Mongolia, not at Suyab, seat of its
        western half)."""
        first, last = years if years else (int(rows.FromYear.min()), int(rows.ToYear.max()))
        held = []
        for capital in capitals:
            if capital["location"]:
                point = Point(capital["location"])
                near = rows[shapely.distance(rows.geometry.values, point) <= CAPITAL_TOLERANCE_DEGREES]
                if not near.empty:
                    within = near[(near.ToYear >= first) & (near.FromYear <= last)]
                    drawn = int((within.ToYear.clip(upper=last) - within.FromYear.clip(lower=first) + 1).clip(lower=0).sum())
                    dated = capital.get("years", 0) if capital.get("dated") else min(drawn, max(0, last - first - capital.get("covered", 0)))
                    held.append(((dated, drawn), capital["name"], point, near))
        if held:
            _, place, point, near = max(held, key=lambda found: found[0])
            point = self._on_land(point, near)
            if unplaced and not any(capital.get("dated") for capital in capitals):
                heartland = self._heartland(rows)
                if point.distance(heartland) > HOMELAND_DEGREES and self._country(point) != self._country(heartland):
                    return Anchor(heartland, "", self._country(heartland))
            return Anchor(point, place, self._country(point))
        located = [Point(capital["location"]) for capital in capitals if capital["location"]]
        if located:
            return Anchor(self._heartland(rows), "", self._country(located[0]))
        for point in places:
            near = rows[shapely.distance(rows.geometry.values, point) <= CAPITAL_TOLERANCE_DEGREES]
            if not near.empty:
                point = self._on_land(point, near)
                return Anchor(point, "", self._country(point))
        point = self._heartland(rows)
        return Anchor(point, "", self._country(point))

    @staticmethod
    def _on_land(point: Point, rows: gpd.GeoDataFrame) -> Point:
        """A capital on the water's edge, like Venice in its lagoon, moved onto the nearest land the maps give it."""
        if rows.geometry.contains(point).any():
            return point
        longest = rows.loc[(rows.ToYear - rows.FromYear).idxmax()]
        return longest.geometry.intersection(point.buffer(CAPITAL_TOLERANCE_DEGREES)).representative_point()

    @staticmethod
    def _heartland(rows: gpd.GeoDataFrame) -> Point:
        """The spot held for the most years; among ties, the one nearest their middle."""
        left, bottom, right, top = rows.total_bounds
        xs, ys = np.meshgrid(np.linspace(left, right, HEARTLAND_GRID), np.linspace(bottom, top, HEARTLAND_GRID))
        xs, ys = xs.ravel(), ys.ravel()
        years = np.zeros(xs.size)
        for geometry, start, end in zip(rows.geometry, rows.FromYear, rows.ToYear):
            years += shapely.contains_xy(geometry, xs, ys) * (end - start + 1)
        best = np.flatnonzero(years == years.max())
        middle_x, middle_y = xs[best].mean(), ys[best].mean()
        pick = best[np.argmin((xs[best] - middle_x) ** 2 + (ys[best] - middle_y) ** 2)]
        return Point(xs[pick], ys[pick])

    def _holdings(self, point: Point, tolerance: float = 0) -> list[Holding]:
        """Who held the point (or land within the tolerance of it, for a capital on a coast the maps draw roughly) and
        when, with each holder's consecutive map snapshots joined into one stretch."""
        rows = self.frame[shapely.distance(self.frame.geometry.values, point) <= tolerance] if tolerance \
            else self.frame[self.frame.geometry.contains(point)]
        holdings = []
        for qid, group in rows.groupby("qid"):
            stretches: list[list[int]] = []
            for start, end in sorted(zip(group.FromYear, group.ToYear)):
                if stretches and start <= stretches[-1][1] + HOLDING_GAP_YEARS:
                    stretches[-1][1] = max(stretches[-1][1], end)
                else:
                    stretches.append([start, end])
            holdings += [Holding(qid, int(start), int(end)) for start, end in stretches]
        return sorted(holdings, key=lambda holding: holding.start)

    def _heir(self, qid: str, before: bool) -> str | None:
        """The state that held the largest share of the civilization's first land the year before it, or of its last
        land the year after."""
        rows = self.frame[self.frame.qid == qid]
        edge = rows.loc[rows.FromYear.idxmin()] if before else rows.loc[rows.ToYear.idxmax()]
        shape = self.equal_area(gpd.GeoSeries([edge.geometry], crs=rows.crs)).iloc[0]
        year = edge.FromYear - 1 if before else edge.ToYear + 1
        others = self.frame[(self.frame.FromYear <= year) & (self.frame.ToYear >= year)
                            & (self.frame.qid != qid) & self.frame.geometry.intersects(edge.geometry)]
        shares = {other: max(geometry.intersection(shape).area for geometry in group.geometry)
                  for other, group in self.equal_area(others).groupby("qid")}
        return max(shares, key=shares.get) if shares else None

    def _partitioned(self, rows: gpd.GeoDataFrame) -> bool:
        """The year after it ended, its final territory was held in large parts by three or more states, as
        Poland-Lithuania's was in 1795."""
        last = rows.loc[rows.ToYear.idxmax()]
        shape = self.equal_area(gpd.GeoSeries([last.geometry], crs=rows.crs)).iloc[0]
        year = last.ToYear + 1
        later = self.frame[(self.frame.FromYear <= year) & (self.frame.ToYear >= year)
                           & (self.frame.qid != last.qid) & self.frame.geometry.intersects(last.geometry)]
        shares = {}
        for qid, group in self.equal_area(later).groupby("qid"):
            shares[qid] = max(geometry.intersection(shape).area for geometry in group.geometry) / shape.area
        return sum(share >= PARTITION_SHARE for share in shares.values()) >= PARTITION_HOLDERS

    def _country(self, point: Point) -> str:
        listed = self.countries[self.countries.listed]
        inside = listed[listed.geometry.contains(point)]
        if not inside.empty:
            return inside.iloc[0]["listed_as"]
        return listed.iloc[int(np.argmin(shapely.distance(listed.geometry.values, point)))]["listed_as"]
