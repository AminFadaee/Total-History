import logging

import pandas as pd

from total_history.http import Http

URL = "https://data.sciencespo.fr/api/access/datafile/4432"
FILE = "bhht-cross-verified-database.csv.gz"
COLUMNS = ["wikidata_code", "birth", "birth_estimation", "un_subregion", "level1_main_occ", "level2_main_occ", "sum_visib_ln_5criteria",
           "bplo1", "bpla1"]
PERIODS = [(500, "before 500"), (1500, "500–1500"), (1750, "1500–1750"), (1900, "1750–1900"), (10_000, "after 1900")]
EXCLUDED_DOMAINS = {"Sports/Games", "Missing"}
EXCLUDED_FIELDS = {"Sports/Games", "Corporate/Executive/Business (large)", "Worker/Business (small)"}
UNKNOWN_REGION = "Unknown"
SUBREGIONS = {  # Natural Earth's UN subregions under the database's names
    "Northern Africa": "North Africa", "Western Africa": "West Africa", "Eastern Africa": "East Africa",
    "Middle Africa": "Central Africa", "Southern Africa": "Southern Africa", "Western Asia": "Western Asia (Middle East Caucasus)",
    "Central Asia": "Central Asia", "Southern Asia": "South Asia incl. Indian Peninsula", "South-Eastern Asia": "SouthEast Asia",
    "Eastern Asia": "Eastern Asia", "Northern Europe": "Northern Europe", "Western Europe": "Western Europe",
    "Southern Europe": "Southern Europe", "Eastern Europe": "Eastern Europe", "Northern America": "Northern America",
    "Central America": "Central America", "Caribbean": "Caribbean", "South America": "South America",
    "Australia and New Zealand": "Oceania Western World", "Melanesia": "Oceania not Aus Nze",
    "Micronesia": "Oceania not Aus Nze", "Polynesia": "Oceania not Aus Nze"}
REGION_NAMES = {"Oceania Western World": "Oceania", "Oceania not Aus Nze": "Oceania", "SouthEast Asia": "Southeast Asia",
                "South Asia incl. Indian Peninsula": "South Asia", "Western Asia (Middle East Caucasus)": "Western Asia",
                UNKNOWN_REGION: ""}

logger = logging.getLogger(__name__)


def period(birth: float) -> str:
    return next(name for end, name in PERIODS if birth <= end)


def load(http: Http) -> pd.DataFrame:
    """The cross-verified database of notable people (Laouenan et al., Scientific Data 2022): 2.29 million people
    with a Wikidata id, cross-checked across seven Wikipedia editions and Wikidata, each with a visibility score that
    combines language editions, page length, page views, external links and biographical completeness. Indexed by
    Wikidata id, with each person's region and period and the longitude and latitude of their birthplace. When only
    the century of birth is known, as for Nefertiti, the database's estimate stands in."""
    frame = pd.read_csv(http.download(URL, FILE), usecols=COLUMNS, encoding="utf-8", encoding_errors="replace",
                        low_memory=False)
    frame["birth"] = pd.to_numeric(frame.birth, errors="coerce").fillna(pd.to_numeric(frame.birth_estimation, errors="coerce"))
    frame = frame.dropna(subset=["birth", "sum_visib_ln_5criteria"]).drop_duplicates("wikidata_code")
    frame["bplo1"] = pd.to_numeric(frame.bplo1, errors="coerce")
    frame["bpla1"] = pd.to_numeric(frame.bpla1, errors="coerce")
    frame["period"] = frame.birth.map(period)
    frame["region"] = frame.un_subregion.fillna(UNKNOWN_REGION)
    frame = frame.rename(columns={"sum_visib_ln_5criteria": "score"})
    logger.info("Read %d notable people", len(frame))
    return frame.set_index("wikidata_code")


def pool(frame: pd.DataFrame, adult_before: int, adult_age: int, per_cell: int) -> pd.DataFrame:
    """The candidates: people who were adults before the year, outside sport and business, ranked
    within their region and period, and among the first of their cell. Every part of the world and of history brings
    the same number of people, so the database's lean towards Europe and North America does not carry into the deck,
    and comparing people only with their own region and period keeps the many recent figures from crowding out the
    few ancient ones. Each keeps its rank in the cell."""
    eligible = adults(frame, adult_before, adult_age).copy()
    eligible["cell_rank"] = eligible.groupby(["region", "period"]).score.rank(ascending=False, method="first")
    return eligible[eligible.cell_rank <= per_cell]


def adults(frame: pd.DataFrame, adult_before: int, adult_age: int) -> pd.DataFrame:
    """People who were adults before the year, outside sport and business."""
    return frame[(frame.birth + adult_age < adult_before) & ~frame.level1_main_occ.isin(EXCLUDED_DOMAINS)
                 & ~frame.level2_main_occ.isin(EXCLUDED_FIELDS)]


def region_name(region: str) -> str:
    """The database's region as a reader would say it: 'Western Asia' for 'Western Asia (Middle East Caucasus)'."""
    return REGION_NAMES.get(region, region)
