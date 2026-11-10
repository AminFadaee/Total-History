import datetime
import logging
import pathlib
import tomllib
from dataclasses import dataclass

from page_in_history.polities import Polity

REMOVABLE_FIELDS = ("before", "after", "key_figures", "capitals")
LEGACY = "legacy"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Erratum:
    civilization: str
    field: str
    value: str
    reason: str
    source: str = ""
    quote: str = ""
    retrieved: datetime.date | None = None
    needs_verification: bool = False

    def to_json(self) -> dict:
        return {"civilization": self.civilization, "field": self.field, "value": self.value}


def load_errata(path: pathlib.Path) -> list[Erratum]:
    entries = tomllib.loads(path.read_text()).get("remove", []) if path.exists() else []
    errata = [Erratum(**entry) for entry in entries]
    for erratum in errata:
        if erratum.field not in (*REMOVABLE_FIELDS, LEGACY):
            raise ValueError(f"{erratum.civilization}: {erratum.field} is not a list or a legacy line that errata may remove")
    return errata


def apply_errata(polities: list[Polity], errata: list[Erratum]) -> list[Erratum]:
    """Drops each listed answer from its civilization and returns the entries that matched something; legacy lines are
    passed over when they are chosen (see legacy_rejections)."""
    by_name = {polity.name: polity for polity in polities}
    dropped = []
    for erratum in (erratum for erratum in errata if erratum.field != LEGACY):
        items = getattr(by_name[erratum.civilization], erratum.field) if erratum.civilization in by_name else []
        kept = [item for item in items if item["name"] != erratum.value]
        if len(kept) < len(items):
            items[:] = kept
            dropped.append(erratum)
        else:
            logger.warning("Errata entry matches nothing: %s, %s, %s", erratum.civilization, erratum.field, erratum.value)
    return dropped


def legacy_rejections(errata: list[Erratum]) -> set[tuple[str, str]]:
    return {(erratum.civilization, erratum.value) for erratum in errata if erratum.field == LEGACY}


def passed_legacy(errata: list[Erratum], passed: set[tuple[str, str]]) -> list[Erratum]:
    """The legacy errata whose line came up and was passed over, warning about the rest."""
    matched = []
    for erratum in (erratum for erratum in errata if erratum.field == LEGACY):
        if (erratum.civilization, erratum.value) in passed:
            matched.append(erratum)
        else:
            logger.warning("Errata entry matches nothing: %s, legacy, %s", erratum.civilization, erratum.value)
    return matched
