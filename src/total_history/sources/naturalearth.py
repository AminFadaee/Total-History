import geopandas as gpd

from total_history.http import Http

COUNTRIES_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip"
DISPUTED_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_breakaway_disputed_areas.zip"
RIVERS_URL = "https://naciscdn.org/naturalearth/50m/physical/ne_50m_rivers_lake_centerlines.zip"
MAX_RIVER_RANK = 5
RECOGNISED_HOLDER = {"Crimea": "Ukraine"}
NO_CODE = "-99"
DEPENDENT_TYPES = {"Dependency", "Country"}


def countries(http: Http) -> gpd.GeoDataFrame:
    """Today's countries, each under the shorter of Natural Earth's two names (Serbia, not Republic of Serbia; Russia,
    not Russian Federation). Those without an ISO 3166 code, such as the Siachen Glacier or Northern Cyprus, are drawn
    on maps but not listed as modern countries, and a dependency (Hong Kong, Greenland, the Faroe Islands) is listed
    as its sovereign country. Borders follow international recognition, not control: Crimea, which Natural Earth
    draws inside Russia, belongs to Ukraine."""
    path = http.download(COUNTRIES_URL, "ne_50m_admin_0_countries.zip")
    frame = gpd.read_file(f"zip://{path}")
    frame["geometry"] = frame.geometry.buffer(0)
    frame["geometry"] = recognised_borders(frame, http)
    frame["name"] = [min(admin, long, key=len) for admin, long in zip(frame.ADMIN, frame.NAME_LONG)]
    short = dict(zip(frame.ADMIN, frame.name))
    frame["listed"] = frame.ISO_A2_EH != NO_CODE
    frame["listed_as"] = [short.get(sovereign, name) if kind in DEPENDENT_TYPES and sovereign != admin else name
                          for name, admin, sovereign, kind in zip(frame.name, frame.ADMIN, frame.SOVEREIGNT, frame.TYPE)]
    return frame[["name", "WIKIDATAID", "listed", "listed_as", "SUBREGION", "geometry"]].rename(
        columns={"WIKIDATAID": "qid", "SUBREGION": "subregion"})


def recognised_borders(frame: gpd.GeoDataFrame, http: Http) -> gpd.GeoSeries:
    """The shapes with each area of Natural Earth's disputed-areas layer in RECOGNISED_HOLDER taken from whichever
    country draws it and given to the country that holds it by international recognition (Crimea from Russia to
    Ukraine)."""
    path = http.download(DISPUTED_URL, "ne_50m_admin_0_breakaway_disputed_areas.zip")
    disputed = gpd.read_file(f"zip://{path}")
    shapes = frame.geometry
    for area, holder in RECOGNISED_HOLDER.items():
        land = disputed.geometry[disputed.BRK_NAME == area].union_all()
        shapes = shapes.difference(land)
        shapes[frame.ADMIN == holder] = shapes[frame.ADMIN == holder].union(land)
    return shapes


def rivers(http: Http) -> gpd.GeoDataFrame:
    """Major rivers only: the Nile, Tigris, Euphrates, Indus, Oxus and the like."""
    path = http.download(RIVERS_URL, "ne_50m_rivers_lake_centerlines.zip")
    frame = gpd.read_file(f"zip://{path}")
    return frame[frame.scalerank <= MAX_RIVER_RANK][["name", "scalerank", "geometry"]]
