"""Facts the outside reviews checked, kept in review_cases.toml. A case still open is marked as an expected failure (strict,
so fixing it fails the run until its mark is removed)."""
import pathlib
import tomllib

import pytest

CASES = tomllib.loads((pathlib.Path(__file__).with_name("review_cases.toml")).read_text())
FIELDS = {"figure_absent": ("civilization", "people"), "figure_present": ("civilization", "people"),
          "succession_absent": ("civilization", "side", "other"), "succession_present": ("civilization", "side", "other"),
          "succession_answered": ("civilization", "side"), "home_region": ("civilization", "region"),
          "capital_absent": ("civilization", "capital"), "capital_present": ("civilization", "capitals"),
          "in_deck": ("civilization",), "tier": ("civilization", "tier"), "period_ends_by": ("civilization", "year"),
          "period_present": ("civilization",), "what_present": ("civilization",),
          "map_paints": ("civilization", "states"), "map_leaves_out": ("civilization", "states"),
          "map_year_within": ("civilization", "years")}


def label(value) -> str:
    if isinstance(value, dict):
        return "|".join(value.values())
    return "|".join(map(str, value)) if isinstance(value, list) else str(value)


def cases(kind: str) -> list:
    fields = FIELDS[kind]
    return [pytest.param(*(case[field] for field in fields), id=f"{kind}:{'/'.join(label(case[field]) for field in fields)}",
                         marks=pytest.mark.xfail(strict=True, raises=AssertionError) if case.get("open") else ())
            for case in CASES[kind]]


def figure_ids(civilizations, civilization) -> set[str]:
    return {figure["id"] for figure in civilizations[civilization]["key_figures"]}


def answers(civilizations, civilization, side) -> list[str]:
    return [edge["name"] for edge in civilizations[civilization][side]]


def test_cases_are_known_kinds_and_fields():
    assert set(CASES) == set(FIELDS)
    for kind, fields in FIELDS.items():
        for case in CASES[kind]:
            assert set(case) <= {*fields, "open"}, (kind, case)


def test_cases_name_civilizations_in_the_deck(civilizations):
    missing = {case["civilization"] for kind, items in CASES.items() if kind != "in_deck" for case in items} - set(civilizations)
    assert not missing


def claims(kind: str, key: str) -> set[tuple]:
    return {(case["civilization"], case.get("side"), value) for case in CASES[kind]
            for value in (case[key] if isinstance(case[key], (list, dict)) else [case[key]])}


def test_no_case_is_both_present_and_absent():
    for present, absent in (("figure_present", "people"), ("figure_absent", "people")), \
                           (("succession_present", "other"), ("succession_absent", "other")), \
                           (("capital_present", "capitals"), ("capital_absent", "capital")):
        assert not claims(*present) & claims(*absent), present[0]


@pytest.mark.parametrize("civilization, people", cases("figure_absent"))
def test_key_figure_absent(civilizations, civilization, people):
    assert not set(people) & figure_ids(civilizations, civilization)


@pytest.mark.parametrize("civilization, people", cases("figure_present"))
def test_key_figure_present(civilizations, civilization, people):
    assert set(people) & figure_ids(civilizations, civilization)


@pytest.mark.parametrize("civilization, side, other", cases("succession_absent"))
def test_succession_absent(civilizations, civilization, side, other):
    assert other not in answers(civilizations, civilization, side)


@pytest.mark.parametrize("civilization, side, other", cases("succession_present"))
def test_succession_present(civilizations, civilization, side, other):
    assert other in answers(civilizations, civilization, side)


@pytest.mark.parametrize("civilization, side", cases("succession_answered"))
def test_succession_answered(civilizations, civilization, side):
    assert answers(civilizations, civilization, side)


@pytest.mark.parametrize("civilization, region", cases("home_region"))
def test_home_region(civilizations, civilization, region):
    homes = [edge["region"] for side in ("before", "after") for edge in civilizations[civilization][side] if edge.get("home")]
    assert homes and all(home == region for home in homes)


@pytest.mark.parametrize("civilization, capital", cases("capital_absent"))
def test_capital_absent(civilizations, civilization, capital):
    assert capital not in [found["name"] for found in civilizations[civilization]["capitals"]]


@pytest.mark.parametrize("civilization, capitals", cases("capital_present"))
def test_capital_present(civilizations, civilization, capitals):
    assert set(capitals) & {found["name"] for found in civilizations[civilization]["capitals"]}


@pytest.mark.parametrize("civilization", cases("in_deck"))
def test_in_deck(civilizations, civilization):
    assert civilization in civilizations


@pytest.mark.parametrize("civilization, tier", cases("tier"))
def test_tier(civilizations, civilization, tier):
    assert civilizations[civilization]["tier"] == tier


@pytest.mark.parametrize("civilization, year", cases("period_ends_by"))
def test_period_ends_by(civilizations, civilization, year):
    period = civilizations[civilization]["period"]
    assert not period["confirmed"] or period["end"]["latest"] <= year


@pytest.mark.parametrize("civilization", cases("period_present"))
def test_period_present(civilizations, civilization):
    period = civilizations[civilization]["period"]
    assert period["confirmed"] or period.get("core") or period.get("since")


@pytest.mark.parametrize("civilization", cases("what_present"))
def test_what_line_present(civilizations, civilization):
    assert civilizations[civilization].get("summary")


@pytest.mark.parametrize("civilization, states", cases("map_paints"))
def test_map_paints(civilizations, civilization, states):
    assert set(states) <= set(civilizations[civilization]["parts"])


@pytest.mark.parametrize("civilization, states", cases("map_leaves_out"))
def test_map_leaves_out(civilizations, civilization, states):
    assert not set(states) & set(civilizations[civilization]["parts"])


@pytest.mark.parametrize("civilization, years", cases("map_year_within"))
def test_map_year_within(civilizations, civilization, years):
    assert years[0] <= civilizations[civilization]["map_year"] <= years[1]
