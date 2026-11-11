"""Figure overrides: each one cited, and loaded only when well formed."""
import datetime
import pathlib

import pytest

from total_history.figure_overrides import load_figure_overrides

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
ENTRY = '''[[figure]]
figure = "J. R. R. Tolkien"
figure_id = "Q892"
field = "civilization"
value = "United Kingdom"
value_id = "Q145"
source = "https://example.org/tolkien"
quote = "an English writer"
retrieved = 2026-11-08
'''


def test_the_deck_overrides_load():
    for override in load_figure_overrides(DATA / "figure_overrides.toml"):
        assert override.note.strip() if override.field == "exclude" else isinstance(override.retrieved, datetime.date)


def test_leaving_someone_out_needs_a_reason(tmp_path):
    path = tmp_path / "figure_overrides.toml"
    path.write_text('[[figure]]\nfigure = "Longinus"\nfigure_id = "Q1"\nfield = "exclude"\nvalue = "legend"\n')
    with pytest.raises(ValueError):
        load_figure_overrides(path)
    path.write_text(path.read_text() + 'note = "a figure of legend"\n')
    assert [override.field for override in load_figure_overrides(path)] == ["exclude"]


def test_a_well_formed_override_loads(tmp_path):
    path = tmp_path / "figure_overrides.toml"
    path.write_text(ENTRY)
    assert [override.value for override in load_figure_overrides(path)] == ["United Kingdom"]


@pytest.mark.parametrize("change", [
    ('quote = "an English writer"', 'quote = ""'),
    ('source = "https://example.org/tolkien"', 'source = "example.org"'),
    ('value_id = "Q145"', 'value_id = ""'),
    ('field = "civilization"', 'field = "birthplace"'),
])
def test_an_override_without_its_citation_or_with_an_unknown_field_is_refused(tmp_path, change):
    path = tmp_path / "figure_overrides.toml"
    path.write_text(ENTRY.replace(*change))
    with pytest.raises(ValueError):
        load_figure_overrides(path)


def test_one_override_per_figure_and_field(tmp_path):
    path = tmp_path / "figure_overrides.toml"
    path.write_text(ENTRY + "\n" + ENTRY)
    with pytest.raises(ValueError):
        load_figure_overrides(path)


def test_a_further_source_says_what_it_supports_and_how(tmp_path):
    path = tmp_path / "figure_overrides.toml"
    further = 'sources = [\n  { fact = "Britannica confirms it", source = "https://example.org/b", quote = "a writer" },\n]\n'
    path.write_text(ENTRY + further)
    with pytest.raises(ValueError):
        load_figure_overrides(path)
    path.write_text(ENTRY + further + 'derivation = "the value is the first quote; the second confirms it"\n')
    assert load_figure_overrides(path)[0].sources[0]["quote"] == "a writer"
