from collections.abc import Callable
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import Point

from page_in_history.sources.cliopatria import CAPITAL_TOLERANCE_DEGREES

WINDOW_YEARS = 30
MIN_HOLD_YEARS = 6
HOLDING_GAP_YEARS = 5
HEARTLAND_GRID = 60
PARTITION_SHARE = 0.15
PARTITION_HOLDERS = 3
HOMELAND_DEGREES = 10


@dataclass(frozen=True)
class Holding:
    qid: str
    start: int
    end: int

    @property
    def years(self) -> int:
        return self.end - self.start + 1


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
    it (Carthage was followed by the Roman province of Africa, part of the Roman Republic). Holders that stayed five
    years or less, like a wartime occupation, are passed over, and an empire partitioned among three or more states
    has no single successor."""

    def __init__(self, frame: gpd.GeoDataFrame, qids: dict[str, str], countries: gpd.GeoDataFrame,
                 stated: Callable[[str, str], bool], implied: Callable[[str, str], bool], equal_area: Callable):
        self.frame = frame.assign(qid=frame.Name.map(qids)).dropna(subset=["qid"])
        self.countries = countries.to_crs(frame.crs)
        self.stated = stated
        self.implied = implied
        self.equal_area = equal_area

    def neighbours(self, qid: str, capitals: list[dict], edges: tuple = (None, None),
                   fits: Callable[[str], bool] = lambda neighbour: True, years: tuple | None = None,
                   places: tuple = ()) -> tuple[list[dict], list[dict], str]:
        """Before and after, the main answer judged at the civilization's home and named by its country. When Wikidata
        dates the capitals, a side is judged at the capital held at that end if it lies in the same country (Ming's
        successor at Beijing, not at its first capital Nanjing); one in another country gives an answer for that
        country beside the main one (the Roman Empire's at Constantinople). Only neighbours that fit (in the deck, not
        named like the civilization) are taken. An empire drawn far from its capital, as Cliopatria draws the British
        and Spanish Empires without Britain and Spain, has none: its home would be a colony."""
        rows = self.frame[self.frame.qid == qid]
        if rows.empty:
            return [], [], ""
        anchor = self._anchor(rows, capitals, years, places)
        located = [Point(capital["location"]) for capital in capitals if capital["location"]]
        if not anchor.place and located and anchor.point.distance(located[0]) > HOMELAND_DEGREES:
            return [], [], ""
        before = self._sides(qid, rows, edges[0], anchor, True, fits)
        after = [] if self._partitioned(rows) else self._sides(qid, rows, edges[1], anchor, False, fits)
        return before, after, anchor.place

    def home(self, qid: str, capitals: list[dict], years: tuple | None = None, places: tuple = ()) -> Anchor | None:
        """The civilization's home, as its before and after are judged: its point and today's country there."""
        rows = self.frame[self.frame.qid == qid]
        return None if rows.empty else self._anchor(rows, capitals, years, places)

    def held(self, qid: str, point: Point) -> bool:
        """Whether the civilization ever held the point on the maps, or land within reach of it."""
        return any(holding.qid == qid for holding in self._holdings(point, CAPITAL_TOLERANCE_DEGREES))

    def _sides(self, qid: str, rows: gpd.GeoDataFrame, edge, anchor: Anchor, before: bool, fits) -> list[dict]:
        """The main answer, and one at an end capital in another country. An answer at the end capital that repeats
        the main one is not asked again but keeps its country, so no other answer is asked there."""
        edge_rows = rows[rows.FromYear == rows.FromYear.min()] if before else rows[rows.ToYear == rows.ToYear.max()]
        near = edge and (shapely.distance(edge_rows.geometry.values, Point(edge)) <= CAPITAL_TOLERANCE_DEGREES).any()
        if not near or self._country(Point(edge)) == anchor.region:
            found = self._side(qid, rows, edge if near else None, anchor, before, fits)
            return [{**found, "main": True}] if found else []
        main = self._side(qid, rows, None, anchor, before, fits)
        at_edge = self._side(qid, rows, edge, anchor, before, fits)
        sides = [{**main, "main": True}] if main else []
        if at_edge:
            sides.append({**at_edge, "reserved": bool(main) and at_edge["id"] == main["id"]})
        return sides

    def _side(self, qid: str, rows: gpd.GeoDataFrame, edge, anchor: Anchor, before: bool, fits) -> dict:
        point = Point(edge) if edge else anchor.point
        at = anchor if point is anchor.point else Anchor(point, anchor.place, self._country(point))
        holdings = self._holdings(point, CAPITAL_TOLERANCE_DEGREES if edge else 0)
        own = [holding for holding in holdings if holding.qid == qid]
        if not own:
            return {}
        others = [holding for holding in holdings if holding.qid != qid and holding.years >= MIN_HOLD_YEARS
                  and fits(holding.qid)]
        if before:
            return self._neighbour(others, own[0].start, lambda holding: holding.end, (None, qid), at)
        return self._neighbour(others, own[-1].end, lambda holding: holding.start, (qid, None), at)

    def _neighbour(self, others: list[Holding], moment: int, edge, pair, anchor: Anchor) -> dict:
        nearby = sorted((abs(edge(holding) - moment), holding.qid) for holding in others
                        if abs(edge(holding) - moment) <= WINDOW_YEARS)
        for confirmed in (self.stated, self.implied):
            for _, neighbour in nearby:
                first, last = (neighbour, pair[1]) if pair[0] is None else (pair[0], neighbour)
                if confirmed(first, last):
                    return {"id": neighbour, "region": anchor.region}
        return {}

    def _anchor(self, rows: gpd.GeoDataFrame, capitals: list[dict], years: tuple | None = None,
                places: tuple = ()) -> Anchor:
        """The capital it held longest within its years: one Wikidata dates by the years of its terms (Székesfehérvár for
        Hungary, not Timișoara, capital for eight years), one it does not by the years the maps show it held, but no more
        than the years the dated ones leave (Vienna for Austria-Hungary). Without a capital held, the first place
        Wikidata gives as its capital within reach of its territory (Frankfurt for the German Confederation), else the
        spot held for the most years; an empire Cliopatria draws without its homeland is still named by its capital's
        country ("in Spain", not "in Cuba")."""
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
