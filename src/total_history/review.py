import hashlib
import json
import pathlib
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from enum import StrEnum

FACT_DIRS = ("civilizations", "figures")
SHOWN_WIDTH = 160


@dataclass(frozen=True)
class Change:
    file: str
    name: str
    path: str
    old: object
    new: object

    def line(self) -> str:
        return f"  {self.path or '(file)'}: {shown(self.old)} -> {shown(self.new)}"


class Verdict(StrEnum):
    ACCEPT = "a"
    REJECT = "r"
    ACCEPT_ALL = "A"
    SKIP = "s"


def shown(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, list) and value and all(isinstance(item, dict) and "name" in item for item in value):
        value = [item["name"] for item in value]
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= SHOWN_WIDTH else text[:SHOWN_WIDTH - 1] + "…"


def git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check).stdout


def committed(path: str) -> dict | None:
    text = git("show", f"HEAD:{path}", check=False)
    return json.loads(text) if text else None


def differences(old, new, path: str = ""):
    """The changed fields of a fact file, by their dotted path; lists are compared whole."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            yield from differences(old.get(key), new.get(key), f"{path}.{key}" if path else key)
    elif old != new:
        yield path, old, new


def fact_files(data_dir: pathlib.Path) -> list[str]:
    tracked = git("ls-files", *(str(data_dir / folder) for folder in FACT_DIRS)).split()
    present = [str(path) for folder in FACT_DIRS for path in sorted((data_dir / folder).glob("*.json"))]
    return sorted(set(tracked) | set(present))


def changes(data_dir: pathlib.Path) -> list[Change]:
    found = []
    for path in fact_files(data_dir):
        file = pathlib.Path(path)
        new = json.loads(file.read_text()) if file.exists() else None
        old = committed(path)
        name = (new or old or {}).get("name", file.stem)
        if old is None or new is None:
            found.append(Change(path, name, "", "file" if old else None, "file" if new else None))
            continue
        found += [Change(path, name, field, before, after) for field, before, after in differences(old, new)]
    return found


def digest(data_dir: pathlib.Path) -> str:
    total = hashlib.sha256()
    for folder in FACT_DIRS:
        for path in sorted((data_dir / folder).glob("*.json")):
            total.update(path.name.encode() + b"\0" + path.read_bytes())
    return total.hexdigest()


def marker() -> pathlib.Path:
    return pathlib.Path(git("rev-parse", "--git-dir").strip()) / "total-history-approved"


def approved(data_dir: pathlib.Path) -> bool:
    return marker().exists() and marker().read_text().strip() == digest(data_dir)


def sources(data_dir: pathlib.Path) -> dict[tuple[str, str], list[str]]:
    """The cited sources of each override, by civilization or figure and top-level field of the fact file."""
    fields = {"capital": "capitals", "period": "period", "map_year": "map_year"}
    found: dict[tuple[str, str], list[str]] = {}
    for entry in tomllib.loads((data_dir / "overrides.toml").read_text()).get("override", []):
        cited = [cite["source"] for cite in entry.get("sources", [])] or [entry.get("source", "")]
        key = (entry["civilization"], fields.get(entry["field"], entry["field"]))
        found.setdefault(key, []).extend(source for source in cited if source)
    for entry in tomllib.loads(path.read_text()).get("figure", []) if (path := data_dir / "figure_overrides.toml").exists() else []:
        field = "civilizations" if entry["field"] == "civilization" else entry["field"]
        if entry.get("source"):
            found.setdefault((entry["figure"], field), []).append(entry["source"])
    return found


def grouped(found: list[Change]) -> dict[str, list[Change]]:
    groups: dict[str, list[Change]] = {}
    for change in found:
        groups.setdefault(change.name, []).append(change)
    return groups


def describe(name: str, items: list[Change], cited: dict[tuple[str, str], list[str]]) -> str:
    lines = [f"{name} ({items[0].file})"] + [change.line() for change in items]
    for field in sorted({change.path.split(".")[0] for change in items if change.path}):
        lines += [f"    source of {field}: {source}" for source in dict.fromkeys(cited.get((name, field), []))]
    return "\n".join(lines)


def ask(prompt: str) -> Verdict:
    while True:
        answer = input(prompt).strip()
        if answer in set(Verdict):
            return Verdict(answer)


def review(data_dir: pathlib.Path, interactive: bool, approve: bool) -> int:
    groups = grouped(changes(data_dir))
    if not groups:
        marker().write_text(digest(data_dir))
        print("No changes to the fact files.")
        return 0
    cited = sources(data_dir)
    print(f"{sum(len(items) for items in groups.values())} changes in {len(groups)} files.\n")
    rejected, accept_rest = [], approve
    for name, items in groups.items():
        print(describe(name, items, cited))
        if accept_rest or not interactive:
            print()
            continue
        verdict = ask("[a]ccept, [r]eject, [A]ccept all the rest, [s]kip the review: ")
        print()
        if verdict is Verdict.SKIP:
            print("Review skipped; the data is not approved.")
            return 1
        if verdict is Verdict.REJECT:
            rejected.append(name)
        accept_rest = verdict is Verdict.ACCEPT_ALL
    if rejected:
        print("Rejected, so the data is not approved:\n" + "\n".join(f"  {name}" for name in rejected))
        return 1
    if interactive or approve:
        marker().write_text(digest(data_dir))
        print("Approved.")
        return 0
    print("Not approved: run the review in a terminal, or with --approve.")
    return 1


def required() -> bool:
    """Whether the pre-commit hook enforces the review: `[tool.total-history] review = false` in pyproject.toml turns
    it off."""
    pyproject = pathlib.Path(git("rev-parse", "--show-toplevel").strip()) / "pyproject.toml"
    settings = tomllib.loads(pyproject.read_text()).get("tool", {}).get("total-history", {})
    return settings.get("review", True)


def check(data_dir: pathlib.Path) -> int:
    """For the pre-commit hook: staged fact files must be exactly the approved ones."""
    if not required():
        return 0
    folders = [str(data_dir / folder) for folder in FACT_DIRS]
    if not git("diff", "--cached", "--name-only", "--", *folders).strip():
        return 0
    if git("diff", "--name-only", "--", *folders).strip():
        print("Fact files have unstaged changes; stage all of them or none.", file=sys.stderr)
        return 1
    if not approved(data_dir):
        print("The fact files have changes nobody approved: run `uv run total-history review`.", file=sys.stderr)
        return 1
    return 0
