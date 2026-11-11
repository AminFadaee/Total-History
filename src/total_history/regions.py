from dataclasses import dataclass

import geopandas as gpd
from shapely.geometry import Point

from total_history.sources.cliopatria import CAPITAL_TOLERANCE_DEGREES, EQUAL_AREA

REGION_GAP_METERS = 800_000


@dataclass(frozen=True)
class Region:
    geometry: object
    share: float


def regions(territory) -> list[Region]:
    """The civilization's pieces grouped into regions of land lying within 800 km of each other, largest first, each
    with its share of the territory."""
    pieces = gpd.GeoSeries(list(getattr(territory, "geoms", [territory])), crs="EPSG:4326")
    projected = pieces.to_crs(EQUAL_AREA)
    clusters = projected.buffer(REGION_GAP_METERS / 2).union_all()
    total = projected.area.sum()
    found = []
    for cluster in getattr(clusters, "geoms", [clusters]):
        inside = projected.intersects(cluster)
        found.append(Region(pieces[inside].union_all(), float(projected[inside].area.sum() / total)))
    return sorted(found, key=lambda region: -region.share)


def home(found: list[Region], capitals: list[tuple[float, float]]) -> Region:
    """The region holding a capital, or the largest when none does: Cliopatria draws some colonial empires without
    their homeland, which is a state of its own."""
    return next((region for region in found for capital in capitals
                 if region.geometry.buffer(CAPITAL_TOLERANCE_DEGREES).contains(Point(capital))), found[0])
