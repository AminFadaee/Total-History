import datetime
import logging
import pathlib
import tomllib
from dataclasses import dataclass

from page_in_history.legacy import unended
from page_in_history.polities import Polity

REMOVABLE_FIELDS = ("before", "after", "key_figures", "capitals", "modern_countries")
LEGACY = "legacy"
FIGURE_FIELDS = ("civilizations", "contribution", LEGACY)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Erratum:
    field: str
    value: str
    reason: str
    civilization: str = ""
    figure: str = ""
    source: str = ""
    quote: str = ""
    retrieved: datetime.date | None = None
    needs_verification: bool = False

    @property
    def subject(self) -> str:
        return self.civilization or self.figure

    def to_json(self) -> dict:
        return {"civilization": self.civilization, "figure": self.figure, "field": self.field, "value": self.value}


def load_errata(path: pathlib.Path) -> list[Erratum]:
    entries = tomllib.loads(path.read_text()).get("remove", []) if path.exists() else []
    errata = [Erratum(**entry) for entry in entries]
    for erratum in errata:
        if bool(erratum.civilization) == bool(erratum.figure):
            raise ValueError(f"{erratum.subject}: an erratum names either a civilization or a figure")
        if erratum.field not in (FIGURE_FIELDS if erratum.figure else (*REMOVABLE_FIELDS, LEGACY)):
            raise ValueError(f"{erratum.subject}: {erratum.field} is not a field that errata may remove from")
    return errata


def apply_errata(polities: list[Polity], errata: list[Erratum]) -> list[Erratum]:
    """Drops each listed answer from its civilization and returns the entries that matched something; legacy lines are
    passed over when they are chosen (see legacy_rejections)."""
    by_name = {polity.name: polity for polity in polities}
    dropped = []
    for erratum in (erratum for erratum in errata if erratum.civilization and erratum.field != LEGACY):
        items = getattr(by_name[erratum.civilization], erratum.field) if erratum.civilization in by_name else []
        kept = [item for item in items if (item["name"] if isinstance(item, dict) else item) != erratum.value]
        if len(kept) < len(items):
            items[:] = kept
            dropped.append(erratum)
        else:
            logger.warning("Errata entry matches nothing: %s, %s, %s", erratum.civilization, erratum.field, erratum.value)
    return dropped


def legacy_rejections(errata: list[Erratum]) -> set[tuple[str, str]]:
    """The (civilization or figure, line) pairs a review rejected."""
    return {(erratum.subject, unended(erratum.value)) for erratum in errata if erratum.field == LEGACY}


def passed_legacy(errata: list[Erratum], passed: set[tuple[str, str]]) -> list[Erratum]:
    """The legacy errata whose line came up and was passed over, warning about the rest."""
    matched = []
    for erratum in (erratum for erratum in errata if erratum.field == LEGACY):
        if (erratum.subject, unended(erratum.value)) in passed:
            matched.append(erratum)
        else:
            logger.warning("Errata entry matches nothing: %s, legacy, %s", erratum.subject, erratum.value)
    return matched


def apply_figure_errata(figures: list, errata: list[Erratum]) -> list[Erratum]:
    """Drops a state a figure was wrongly filed under (from their states and their related ones), or a Known for line
    a review found wrong, leaving it empty; returns the entries that matched something."""
    by_name = {figure.name: figure for figure in figures}
    dropped = []
    for erratum in (erratum for erratum in errata if erratum.figure and erratum.field != LEGACY):
        figure = by_name.get(erratum.figure)
        if figure and erratum.field == "contribution" and figure.contribution == erratum.value:
            figure.contribution = ""
        elif figure and erratum.field == "civilizations" and erratum.value in figure.civilizations + figure.related:
            figure.civilizations = [name for name in figure.civilizations if name != erratum.value]
            figure.related = [name for name in figure.related if name != erratum.value]
        else:
            logger.warning("Errata entry matches nothing: %s, %s, %s", erratum.figure, erratum.field, erratum.value)
            continue
        dropped.append(erratum)
    return dropped
