import logging
import zipfile
from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
from shapely.validation import make_valid

from total_history.http import Http

URL = "https://github.com/Seshat-Global-History-Databank/cliopatria/raw/main/cliopatria.geojson.zip"
EQUAL_AREA = "EPSG:6933"
CAPITAL_TOLERANCE_DEGREES = 0.1  # how far outside a drawn border a capital can lie, as on a coast or lagoon
SPIKE_RATIO = 2.5
OCCUPATION_YEARS = 5
HOLDING_GAP_YEARS = 5

logger = logging.getLogger(__name__)


def holding(steps: pd.DataFrame, year: int, bridged: bool = True) -> range:
    """The years the time steps hold without a break, around the year (empty when they do not hold it); five years or
    less is an occupation. Bridged, a hold of more than five years goes on when it resumes within five years, so a
    short loss does not cut it (the United States in the Philippines, 1898–1946, though Japan held them in 1942–44),
    while two short holds do not add up to a lasting one (the American occupations of Cuba, 1898–1902 and 1906–09).
    Unbridged, any break ends the hold: a state's last year on land it had lost is not the state of someone born
    there (the Ottoman Empire in Cilicia in 1922, after the French occupation)."""
    runs: list[list[int]] = []
    for first, last in sorted(zip(steps.FromYear, steps.ToYear)):
        gap = HOLDING_GAP_YEARS if bridged and runs and runs[-1][1] - runs[-1][0] + 1 > OCCUPATION_YEARS else 1
        if runs and first <= runs[-1][1] + gap:
            runs[-1][1] = max(runs[-1][1], last)
        else:
            runs.append([first, last])
    return next((range(int(first), int(last) + 1) for first, last in runs if first <= year <= last), range(0))


def occupation(held: range, home: range) -> bool:
    """Whether land held for these years, away from a state's home held for those, was an occupation: held for five
    years or less, taken after the state held its home and given up while it still held it (Greenland, held by the
    United States in 1941–45). Land held until the state lost its home is its own, however briefly it stood."""
    return len(held) <= OCCUPATION_YEARS and home.start < held.start and held.stop < home.stop


@dataclass(frozen=True)
class Spike:
    name: str
    from_year: int
    to_year: int
    area_km2: float
    neighbour_area_km2: float


class Cliopatria:
    """Polity shapes by time step. Parenthesised groupings and alliances are left out as shapes; the groupings are kept
    by name with the Wikidata item each stands for, so their members can be found ("(British Empire)": Q8680)."""

    def __init__(self, frame: gpd.GeoDataFrame, spikes: list[Spike] | None = None, groups: dict[str, str] | None = None):
        self.frame = frame
        self.spikes = self._drop_spikes() if spikes is None else spikes
        self.groups = groups or {}
        self.by_name = dict(tuple(self.frame.groupby("Name", sort=False)))

    @classmethod
    def load(cls, http: Http) -> "Cliopatria":
        archive = http.download(URL, "cliopatria.geojson.zip")
        with zipfile.ZipFile(archive) as zipped:
            member = next(name for name in zipped.namelist() if name.endswith(".geojson"))
        frame = gpd.read_file(f"zip://{archive}!{member}")
        grouping = (frame.Type == "POLITY") & frame.Name.str.startswith("(")
        groups = dict(zip(frame.Name[grouping], frame.Wikidata[grouping]))
        frame = frame[(frame.Type == "POLITY") & ~grouping].copy()
        frame["geometry"] = frame.geometry.apply(make_valid).buffer(0)
        frame["FromYear"] = frame.FromYear.astype(int)
        frame["ToYear"] = frame.ToYear.astype(int)
        return cls(frame.sort_values(["Name", "FromYear"]).reset_index(drop=True), groups=groups)

    def with_frame(self, frame: gpd.GeoDataFrame) -> "Cliopatria":
        """The same source over a changed frame, such as one with polities renamed."""
        return Cliopatria(frame, self.spikes, self.groups)

    def active(self, start: int, end: int) -> gpd.GeoDataFrame:
        return self.frame[(self.frame.ToYear >= start) & (self.frame.FromYear <= end)]

    def at(self, year: int) -> gpd.GeoDataFrame:
        return self.frame[(self.frame.FromYear <= year) & (self.frame.ToYear >= year)]

    def rows(self, name: str) -> gpd.GeoDataFrame:
        return self.by_name.get(name, self.frame.iloc[:0])

    def members(self, qid: str, year: int) -> list[str]:
        """The polities Cliopatria files that year under a grouping it links to the item, such as the British Raj,
        British Africa and Britain itself under its British Empire."""
        groups = {name for name, item in self.groups.items() if item == qid}
        shown = self.at(year)
        within = shown.MemberOf.fillna("").str.split(";").map(lambda found: bool(groups.intersection(found)))
        return sorted(set(shown.Name[within]))

    def _drop_spikes(self) -> list[Spike]:
        """A single time step far larger than both neighbours is a mislabelled shape, not a conquest."""
        spikes: list[Spike] = []
        drop = []
        for name, rows in self.frame.groupby("Name", sort=False):
            areas = rows.Area.tolist()
            for position in range(1, len(areas) - 1):
                neighbour = max(areas[position - 1], areas[position + 1])
                if areas[position] > SPIKE_RATIO * neighbour:
                    row = rows.iloc[position]
                    drop.append(rows.index[position])
                    spikes.append(Spike(name, int(row.FromYear), int(row.ToYear), float(row.Area), float(neighbour)))
        for spike in spikes:
            logger.warning("Ignoring %s %d..%d: %.0f km² against %.0f km² either side",
                           spike.name, spike.from_year, spike.to_year, spike.area_km2, spike.neighbour_area_km2)
        self.frame = self.frame.drop(index=drop)
        return spikes
