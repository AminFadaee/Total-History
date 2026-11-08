import datetime
import pathlib
import tomllib
from dataclasses import dataclass

from page_in_history.overrides import cites

FIELDS = ("civilization", "contribution", "role")


@dataclass(frozen=True)
class FigureOverride:
    figure: str
    figure_id: str
    field: str
    value: str
    source: str
    quote: str
    retrieved: datetime.date
    value_id: str = ""
    page: str = ""
    note: str = ""

    def to_json(self) -> dict:
        return {"figure": self.figure, "field": self.field, "value": self.value}


def load_figure_overrides(path: pathlib.Path) -> list[FigureOverride]:
    entries = tomllib.loads(path.read_text()).get("figure", []) if path.exists() else []
    overrides = [FigureOverride(**entry) for entry in entries]
    seen = set()
    for override in overrides:
        who = f"{override.figure}, {override.field}"
        if override.field not in FIELDS:
            raise ValueError(f"{who}: a figure override sets one of {', '.join(FIELDS)}")
        if not cites(override.source, override.page) or not override.quote.strip():
            raise ValueError(f"{who}: an override cites an https page, or a book with its page, and quotes it")
        if not isinstance(override.retrieved, datetime.date):
            raise ValueError(f"{who}: an override gives the date its source was read")
        if override.field == "civilization" and not override.value_id:
            raise ValueError(f"{who}: a civilization override gives the state's Wikidata id")
        if not override.value.strip():
            raise ValueError(f"{who}: an override gives a value")
        if (override.figure_id, override.field) in seen:
            raise ValueError(f"{who}: one override per figure and field")
        seen.add((override.figure_id, override.field))
    return overrides

