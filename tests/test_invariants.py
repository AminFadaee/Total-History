"""Properties every civilization in data/ must have, whatever the sources say."""
import re

from page_in_history.deck import period_text

SIDES = ("before", "after")
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
