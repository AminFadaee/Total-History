import math
from dataclasses import dataclass

PLACE_AGREEMENT_KM = 100
YEAR_AGREEMENT = 2
EARTH_RADIUS_KM = 6371

Location = tuple[float, float]


@dataclass(frozen=True)
class Birth:
    location: Location
    year: int


def distance_km(first: Location, second: Location) -> float:
    """Great-circle distance between two (longitude, latitude) points."""
    (lon1, lat1), (lon2, lat2) = [(math.radians(lon), math.radians(lat)) for lon, lat in (first, second)]
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def agreed_location(locations: list[Location | None]) -> Location | None:
    """The first location, when at least two sources give one and all of them lie within 100 km of each other. One
    dissenting source is enough to leave the birthplace out: the sources often copy each other, so two agreeing on a
    wrong place (Mir Taqi Mir in England rather than Agra) is no proof."""
    found = [location for location in locations if location]
    agreed = len(found) >= 2 and all(distance_km(first, second) <= PLACE_AGREEMENT_KM
                                     for index, first in enumerate(found) for second in found[index + 1:])
    return found[0] if agreed else None


def agreed_year(years: list[int | None]) -> int | None:
    """The first year, when at least two sources give one and all lie within two years of each other."""
    found = [year for year in years if year is not None]
    return found[0] if len(found) >= 2 and max(found) - min(found) <= YEAR_AGREEMENT else None


def agreed_birth(locations: list[Location | None], years: list[int | None]) -> Birth | None:
    """A birth on whose place and year every source that gives them agrees, at least two of them, or none."""
    location, year = agreed_location(locations), agreed_year(years)
    return Birth(location, year) if location and year is not None else None
