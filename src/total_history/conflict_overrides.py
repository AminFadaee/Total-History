import datetime
import pathlib
import tomllib
from dataclasses import dataclass

from total_history.overrides import cites

ROLES = ("big", "central", "ending", "founding", "second")
ACTIONS = ("set", "remove")


@dataclass(frozen=True)
class ConflictOverride:
    conflict: str
    conflict_id: str
    role: str
    action: str
    civilization: str = ""
    civilization_id: str = ""
    source: str = ""
    quote: str = ""
    page: str = ""
    retrieved: datetime.date | None = None
    note: str = ""

    @property
    def who(self) -> str:
        return f"{self.civilization or 'big wars'}, {self.role}, {self.conflict}"


def load_conflict_overrides(path: pathlib.Path) -> list[ConflictOverride]:
    entries = tomllib.loads(path.read_text()).get("conflict", []) if path.exists() else []
    overrides = [ConflictOverride(**entry) for entry in entries]
    seen = set()
    for override in overrides:
        who = override.who
        if override.role not in ROLES or override.action not in ACTIONS:
            raise ValueError(f"{who}: a conflict override sets or removes one of {', '.join(ROLES)}")
        if override.role != "big" and not override.civilization_id:
            raise ValueError(f"{who}: only a big war can be set or removed without a civilization")
        if override.action == "remove" and not override.note.strip():
            raise ValueError(f"{who}: removing a pick is an editorial choice and gives its reason in the note")
        if override.action == "set" and override.civilization_id:
            if not cites(override.source, override.page) or not override.quote.strip():
                raise ValueError(f"{who}: an override cites an https page, or a book with its page, and quotes it")
            if not isinstance(override.retrieved, datetime.date):
                raise ValueError(f"{who}: an override gives the date its source was read")
        if override.action == "set" and not override.civilization_id and not override.note.strip():
            raise ValueError(f"{who}: adding a big war is an editorial choice and gives its reason in the note")
        key = (override.civilization_id, override.role, override.conflict_id)
        if key in seen:
            raise ValueError(f"{who}: one override per civilization, role and conflict")
        seen.add(key)
    return overrides
