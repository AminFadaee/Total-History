"""Properties every civilization in data/ must have, whatever the sources say."""
import json
import pathlib
import re
import tomllib

import pytest

from page_in_history.deck import period_text
from page_in_history.figures import MAX_LIFE_YEARS
from page_in_history.polities import same_name

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
ACCEPTED = tomllib.loads((pathlib.Path(__file__).with_name("accepted.toml")).read_text())
SIDES = ("before", "after")
PRESENT = 2026
CENTURY = 100
MAP_YEAR_SLACK = 25
MID_THOUGHT = re.compile(r"(?:It|Its|They|Their|The dynasty|Under)\b")


def test_periods_run_forwards(civilizations):
    for name, civilization in civilizations.items():
        period = civilization["period"]
        if period["confirmed"]:
            assert period["start"]["earliest"] <= period["end"]["latest"], name
        if period.get("core"):
            assert period["core"][0] <= period["core"][1], name


def test_a_period_is_one_kind(civilizations):
    for name, civilization in civilizations.items():
        period = civilization["period"]
        kinds = [bool(period["confirmed"]), bool(period.get("core")), bool(period.get("since"))]
        assert sum(kinds) <= 1, name


def test_period_text_matches_period(civilizations):
    for name, civilization in civilizations.items():
        period = civilization["period"]
        has_period = period["confirmed"] or period.get("core") or period.get("since")
        assert bool(period_text(period)) == bool(has_period), name


def test_successions_have_at_most_one_home_and_three_a_side(civilizations):
    for name, civilization in civilizations.items():
        for side in SIDES:
            edges = civilization[side]
            assert len(edges) <= 3, (name, side)
            assert sum(edge.get("home", False) for edge in edges) <= 1, (name, side)
            if edges and any(edge.get("home") for edge in edges):
                assert edges[0].get("home"), (name, side, "home answer first")


def test_successions_are_named_regions_without_repeats(civilizations):
    for name, civilization in civilizations.items():
        for side in SIDES:
            edges = civilization[side]
            assert all(edge["region"] for edge in edges), (name, side)
            assert len({edge["region"] for edge in edges}) == len(edges), (name, side, "one answer per region")
            assert len({edge["id"] for edge in edges}) == len(edges), (name, side, "one region per answer")
            assert all(edge["id"] != civilization["id"] for edge in edges), (name, side, "not itself")


def test_key_figures_are_at_most_four_distinct_people(civilizations):
    for name, civilization in civilizations.items():
        people = [figure["id"] for figure in civilization["key_figures"]]
        assert len(people) <= 4, name
        assert len(set(people)) == len(people), name


def test_capitals_are_named_once(civilizations):
    for name, civilization in civilizations.items():
        capitals = [capital["name"] for capital in civilization["capitals"]]
        assert all(capitals), name
        assert len(set(capitals)) == len(capitals), name


def test_tiers_are_known(civilizations):
    assert {civilization["tier"] for civilization in civilizations.values()} <= {"Essential", "Core", "Extended"}


def test_what_lines_do_not_begin_mid_thought(civilizations):
    for name, civilization in civilizations.items():
        assert not MID_THOUGHT.match(civilization.get("summary") or ""), name


def test_figures_list_each_civilization_once(figures):
    for figure in figures.values():
        assert len(set(figure["civilizations"])) == len(figure["civilizations"]), figure["name"]


def test_ids_slugs_and_names_are_unique(civilizations):
    documents = list(civilizations.values())
    for key in ("id", "slug", "name"):
        values = [document[key] for document in documents]
        assert len(set(values)) == len(values), key


def test_slugs_match_file_names():
    for path in (DATA / "civilizations").glob("*.json"):
        assert json.loads(path.read_text())["slug"] == path.stem, path.name


def test_maps_exist_and_show_a_year_cliopatria_draws(civilizations):
    for name, civilization in civilizations.items():
        assert (DATA / civilization["map"]).exists(), name
        if years := civilization["checks"]["cliopatria_years"]:
            assert years[0] <= civilization["map_year"] <= years[1], name


def test_successions_use_deck_names(civilizations):
    names = {civilization["id"]: name for name, civilization in civilizations.items()}
    for name, civilization in civilizations.items():
        for side in SIDES:
            for edge in civilization[side]:
                assert names.get(edge["id"], edge["name"]) == edge["name"], (name, side, edge["name"])
                assert not same_name(name, edge["name"]), (name, side, edge["name"])


def test_home_region_is_the_same_on_both_sides(civilizations):
    for name, civilization in civilizations.items():
        homes = {edge["region"] for side in SIDES for edge in civilization[side] if edge.get("home")}
        assert len(homes) <= 1, name


def test_extended_figures_belong_to_deck_civilizations(civilizations):
    for figure in json.loads((DATA / "extended.json").read_text()):
        assert figure["civilization"] in civilizations, figure["name"]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="the data predates the cap; holds after the next collect")
def test_lifetimes_are_plausible(figures):
    for figure in figures.values():
        if figure.get("lifetime"):
            born, died = figure["lifetime"]
            assert 0 <= died - born <= MAX_LIFE_YEARS, figure["name"]


def span(civilization: dict, cliopatria: bool = True) -> tuple[int, int] | None:
    period = civilization["period"]
    if period["confirmed"]:
        return period["start"]["earliest"], period["end"]["latest"]
    if period.get("core"):
        return tuple(period["core"])
    if period.get("since"):
        return period["since"]["earliest"], PRESENT
    years = civilization["checks"]["cliopatria_years"]
    return tuple(years) if cliopatria and years else None


def accepted(rule: str, found: set[tuple]) -> None:
    listed = {tuple(item) for item in ACCEPTED[rule]}
    assert not found - listed, f"{rule}: new {sorted(found - listed)}"
    assert not listed - found, f"{rule}: no longer found, remove from accepted.toml {sorted(listed - found)}"


def successions(civilizations, side):
    by_id = {civilization["id"]: civilization for civilization in civilizations.values()}
    for name, civilization in civilizations.items():
        for edge in civilization[side]:
            if edge["id"] in by_id and (own := span(civilization)) and (theirs := span(by_id[edge["id"]])):
                yield name, edge["name"], own, theirs


def test_successors_end_after_the_civilization(civilizations):
    accepted("successor_ends_first", {(name, other) for name, other, own, theirs in successions(civilizations, "after")
                                      if theirs[1] <= own[1]})


def test_predecessors_begin_before_the_civilization(civilizations):
    accepted("predecessor_begins_later", {(name, other) for name, other, own, theirs in successions(civilizations, "before")
                                          if theirs[0] >= own[0]})


def test_successions_follow_within_a_century(civilizations):
    found = {(name, other) for name, other, own, theirs in successions(civilizations, "after") if theirs[0] - own[1] > CENTURY}
    found |= {(other, name) for name, other, own, theirs in successions(civilizations, "before") if own[0] - theirs[1] > CENTURY}
    accepted("succession_gap", found)


def test_no_civilization_both_precedes_and_follows(civilizations):
    accepted("before_and_after", {(name, edge["name"]) for name, civilization in civilizations.items() for edge in civilization["before"]
                                  if edge["id"] in {later["id"] for later in civilization["after"]}})


def test_key_figures_lived_in_the_period(civilizations, figures):
    accepted("key_figure_outside_period", {
        (name, figure["name"]) for name, civilization in civilizations.items() for figure in civilization["key_figures"]
        if (life := figures.get(figure["id"], {}).get("lifetime")) and (years := span(civilization))
        and not (life[0] <= years[1] and life[1] >= years[0])})


def test_map_years_fall_in_the_period(civilizations):
    accepted("map_year_outside_period", {
        (name,) for name, civilization in civilizations.items() if (years := span(civilization, cliopatria=False))
        and not years[0] - MAP_YEAR_SLACK <= civilization["map_year"] <= years[1] + MAP_YEAR_SLACK})


def test_capitals_were_founded_before_the_end(civilizations):
    accepted("capital_founded_after_end", {
        (name, capital["name"]) for name, civilization in civilizations.items() for capital in civilization["capitals"]
        if capital.get("founded") is not None and (years := span(civilization)) and capital["founded"] > years[1]})


def test_what_lines_are_spaced_once(civilizations):
    accepted("what_line_spacing", {(name,) for name, civilization in civilizations.items() if "  " in (civilization.get("summary") or "")})
