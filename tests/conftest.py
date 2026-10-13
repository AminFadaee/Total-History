import json
import pathlib

import pytest

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"


def load(folder: str) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted((DATA / folder).glob("*.json"))]


@pytest.fixture(scope="session")
def civilizations() -> dict[str, dict]:
    return {document["name"]: document for document in load("civilizations")}


@pytest.fixture(scope="session")
def figures() -> dict[str, dict]:
    return {document["id"]: document for document in load("figures")}
