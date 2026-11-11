import json
import pathlib
import re
import sqlite3
import tempfile
import zipfile

import pytest

from total_history.deck import (CIVILIZATION_TEMPLATES, FIGURE_TEMPLATES, LINKS, capital_asked, figure_tier, known_for,
                                named_after, study_order, told_apart)

DECK = pathlib.Path(__file__).resolve().parents[1] / "build" / "total_history.apkg"
MEDIA_REFERENCE = re.compile(r'src="([^"]+)"')
FIELD_SEPARATOR = "\x1f"


def civilization(name: str, start: int, tier: str = "Core") -> dict:
    return {"id": name, "name": name, "period": {"start": {"earliest": start}}, "map_year": start, "tier": tier}


def figure(name: str, born: int, civilizations: list[str], role: str = "King", contribution: str = "", legacy: str = "",
           vital_level: int | None = 4, languages: int = 60) -> dict:
    return {"id": name, "name": name, "lifetime": [born, born + 60], "civilizations": civilizations, "role": role,
            "contribution": contribution, "legacy": legacy, "vital_level": vital_level, "languages": languages}


@pytest.mark.parametrize("name, capital", [("Republic of Genoa", "Genoa"), ("Caliphate of Córdoba", "Córdoba"),
                                           ("Mexico", "Mexico City"), ("Sparta", "Sparta")])
def test_a_capital_in_the_civilizations_name_gives_it_away(name, capital):
    assert named_after(name, capital)


@pytest.mark.parametrize("name, capital", [("Parthian Empire", "Ctesiphon"), ("Babylonia", "Babylon"),
                                           ("East Franks", "Frankfurt")])
def test_a_capital_that_only_resembles_the_name_is_still_asked(name, capital):
    assert not named_after(name, capital)


def test_a_capital_card_is_made_unless_every_capital_gives_itself_away():
    def asked(name: str, *capitals: str) -> bool:
        return capital_asked({"name": name, "capitals": [{"name": capital} for capital in capitals]})
    assert asked("Delhi Sultanate", "Lahore", "Delhi", "Agra")
    assert not asked("Republic of Genoa", "Genoa")
    assert not asked("Brythons")


def test_known_for_is_left_out_when_the_legacy_line_repeats_it():
    line = "The founder of the first empire of the steppe and its lawgiver."
    assert known_for(figure("A", 1, [], contribution=line, legacy=line)) == ""
    assert known_for(figure("A", 1, [], contribution=line, legacy="He built the great canal.")) == line


def test_the_reverse_card_needs_more_than_a_role_and_words_no_one_shares():
    alone = figure("Ousas", 300, [], role="King of Aksum")
    twins = [figure(name, 300, [], role="King of Aksum", contribution="He minted coins.") for name in ("Ebana", "Nezool")]
    told = figure("Ezana", 320, [], role="King of Aksum", contribution="The first king of Aksum to embrace Christianity.")
    assert told_apart([alone, *twins, told]) == {"Ezana"}


def test_new_cards_follow_each_civilization_with_its_people():
    rome, han = civilization("Rome", -27), civilization("Han", -202)
    people = [figure("Trajan", 53, ["Rome"]), figure("Augustus", -63, ["Elsewhere", "Rome"]), figure("Wu", -156, ["Han"]),
              figure("Stranger", -100, ["Elsewhere"])]
    order = study_order([rome, han], people)
    assert sorted(order, key=order.get) == ["Han", "Wu", "Stranger", "Rome", "Augustus", "Trajan"]


def test_new_cards_come_a_tier_at_a_time():
    rome, han = civilization("Rome", -27, "Essential"), civilization("Han", -202)
    people = [figure("Augustus", -63, ["Rome"], languages=200), figure("Trajan", 53, ["Rome"]),
              figure("Wu", -156, ["Han"], languages=150), figure("Nerva", 30, ["Rome"], vital_level=5, languages=200)]
    order = study_order([rome, han], people)
    assert sorted(order, key=order.get) == ["Wu", "Rome", "Augustus", "Han", "Trajan", "Nerva"]
    assert order["Trajan"] > order["Han"] and order["Nerva"] == len(order)


@pytest.mark.parametrize("vital_level, languages, tier", [(4, 150, "Essential"), (4, 149, "Core"), (5, 250, "Extended"),
                                                           (None, 115, "Extended")])
def test_a_figures_tier_follows_the_vital_articles_and_its_languages(vital_level, languages, tier):
    assert figure_tier(figure("A", 1, [], vital_level=vital_level, languages=languages)) == tier


@pytest.mark.parametrize("template", CIVILIZATION_TEMPLATES + FIGURE_TEMPLATES, ids=lambda template: template.name)
def test_no_question_shows_the_links(template):
    assert "links" not in template.front and LINKS not in template.back


@pytest.fixture(scope="module")
def package() -> dict:
    if not DECK.exists():
        pytest.skip("build the deck first")
    with zipfile.ZipFile(DECK) as archive, tempfile.TemporaryDirectory() as folder:
        archive.extract("collection.anki2", folder)
        media = set(json.loads(archive.read("media")).values())
        with sqlite3.connect(pathlib.Path(folder) / "collection.anki2") as collection:
            models = json.loads(collection.execute("SELECT models FROM col").fetchone()[0])
            notes = collection.execute("SELECT id, guid, mid, flds FROM notes").fetchall()
            cards = collection.execute("SELECT nid, ord, due FROM cards").fetchall()
    return {"media": media, "models": models, "notes": notes, "cards": cards}


def test_the_deck_holds_every_civilization_and_figure_once(package, civilizations, figures):
    guids = [guid for _, guid, _, _ in package["notes"]]
    assert len(guids) == len(set(guids)) == len(civilizations) + len(figures)


def test_every_picture_on_a_card_is_in_the_package(package):
    used = {name for *_, fields in package["notes"] for name in MEDIA_REFERENCE.findall(fields)}
    assert used <= package["media"]


def test_every_answer_ends_with_its_links_and_no_question_has_them(package):
    for model in package["models"].values():
        for template in model["tmpls"]:
            assert LINKS in template["afmt"] and LINKS not in template["qfmt"], template["name"]


def test_every_note_has_its_own_place_among_the_new_cards(package):
    places = {note: due for note, _, due in package["cards"]}
    assert all(places[note] == due for note, _, due in package["cards"])
    assert sorted(places.values()) == list(range(1, len(package["notes"]) + 1))


def test_no_card_asks_for_a_legacy_line(package):
    names = {template["name"] for model in package["models"].values() for template in model["tmpls"]}
    assert not {name for name in names if "Legacy" in name}
