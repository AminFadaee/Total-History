from page_in_history.review import Change, describe, differences, grouped, shown


def test_only_changed_fields_are_listed_by_their_path():
    old = {"name": "Wu", "period": {"start": {"earliest": -1200}, "end": {"earliest": -473}}, "capitals": ["Gusu"]}
    new = {"name": "Wu", "period": {"start": {"earliest": -585}, "end": {"earliest": -473}}, "capitals": ["Gusu", "Wu"]}
    assert list(differences(old, new)) == [("capitals", ["Gusu"], ["Gusu", "Wu"]),
                                           ("period.start.earliest", -1200, -585)]


def test_lists_of_people_are_shown_by_name_and_long_values_are_cut():
    assert shown([{"id": "Q1", "name": "Shou-meng"}, {"id": "Q2", "name": "Fu-ch'ai"}]) == '["Shou-meng", "Fu-ch\'ai"]'
    assert shown(None) == "-"
    assert shown("x" * 300).endswith("…") and len(shown("x" * 300)) == 160


def test_changes_are_grouped_by_civilization_with_the_sources_behind_them():
    found = [Change("data/civilizations/wu.json", "Wu", "period.start.earliest", -1200, -585),
             Change("data/civilizations/wu.json", "Wu", "period.start.latest", -1200, -585)]
    groups = grouped(found)
    text = describe("Wu", groups["Wu"], {("Wu", "period"): ["The Grand Scribe's Records, Vol. V.1"]})
    assert text.splitlines() == ["Wu (data/civilizations/wu.json)",
                                 "  period.start.earliest: -1200 -> -585",
                                 "  period.start.latest: -1200 -> -585",
                                 "    source of period: The Grand Scribe's Records, Vol. V.1"]


def test_a_throttled_request_waits_as_long_as_the_server_asks():
    from types import SimpleNamespace

    from page_in_history.http import LONGEST_WAIT_SECONDS, wait_before_retry
    assert wait_before_retry(SimpleNamespace(headers={"Retry-After": "30"}), 0) == 30
    assert wait_before_retry(SimpleNamespace(headers={"Retry-After": "9999"}), 0) == LONGEST_WAIT_SECONDS
    assert wait_before_retry(None, 2) == 15
