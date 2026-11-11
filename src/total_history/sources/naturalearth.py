import geopandas as gpd

from total_history.http import Http

COUNTRIES_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip"
RIVERS_URL = "https://naciscdn.org/naturalearth/50m/physical/ne_50m_rivers_lake_centerlines.zip"
MAX_RIVER_RANK = 5
NO_CODE = "-99"
DEPENDENT_TYPES = {"Dependency", "Country"}


def countries(http: Http) -> gpd.GeoDataFrame:
    """Today's countries, each under the shorter of Natural Earth's two names (Serbia, not Republic of Serbia; Russia,
    not Russian Federation). Those without an ISO 3166 code, such as the Siachen Glacier or Northern Cyprus, are drawn
    on maps but not listed as modern countries, and a dependency (Hong Kong, Greenland, the Faroe Islands) is listed
    as its sovereign country."""
    path = http.download(COUNTRIES_URL, "ne_50m_admin_0_countries.zip")
    frame = gpd.read_file(f"zip://{path}")
    frame["geometry"] = frame.geometry.buffer(0)
    frame["name"] = [min(admin, long, key=len) for admin, long in zip(frame.ADMIN, frame.NAME_LONG)]
    short = dict(zip(frame.ADMIN, frame.name))
    frame["listed"] = frame.ISO_A2_EH != NO_CODE
    frame["listed_as"] = [short.get(sovereign, name) if kind in DEPENDENT_TYPES and sovereign != admin else name
                          for name, admin, sovereign, kind in zip(frame.name, frame.ADMIN, frame.SOVEREIGNT, frame.TYPE)]
    return frame[["name", "WIKIDATAID", "listed", "listed_as", "SUBREGION", "geometry"]].rename(
        columns={"WIKIDATAID": "qid", "SUBREGION": "subregion"})


def rivers(http: Http) -> gpd.GeoDataFrame:
    """Major rivers only: the Nile, Tigris, Euphrates, Indus, Oxus and the like."""
    path = http.download(RIVERS_URL, "ne_50m_rivers_lake_centerlines.zip")
    frame = gpd.read_file(f"zip://{path}")
    return frame[frame.scalerank <= MAX_RIVER_RANK][["name", "scalerank", "geometry"]]
