"""A collect replaces the data's output only when it finishes."""
import pytest

from page_in_history.collect import OUTPUT_DIRS, OUTPUT_FILES, Collector, DataPaths, Slice


def fill(root, text):
    for directory in OUTPUT_DIRS:
        (root / directory).mkdir(parents=True, exist_ok=True)
        (root / directory / "item.json").write_text(text)
    for name in OUTPUT_FILES:
        (root / name).write_text(text)


@pytest.fixture
def collector(tmp_path):
    paths = DataPaths(tmp_path / "data")
    fill(paths.root, "old")
    (paths.root / "overrides.toml").write_text("kept")
    return Collector(paths, tmp_path / "cache")


def test_a_failed_collect_leaves_the_data_as_it_was(collector, monkeypatch):
    def fail(scope):
        fill(collector.output.root, "half")
        raise RuntimeError("an override names a civilization that is not in the deck")

    monkeypatch.setattr(collector, "_collect", fail)
    with pytest.raises(RuntimeError):
        collector.run(Slice(None, -3400, 2024))
    root = collector.paths.root
    assert all((root / directory / "item.json").read_text() == "old" for directory in OUTPUT_DIRS)
    assert all((root / name).read_text() == "old" for name in OUTPUT_FILES)


def test_a_finished_collect_replaces_the_output_and_keeps_the_inputs(collector, monkeypatch):
    monkeypatch.setattr(collector, "_collect", lambda scope: fill(collector.output.root, "new"))
    collector.run(Slice(None, -3400, 2024))
    root = collector.paths.root
    assert all((root / directory / "item.json").read_text() == "new" for directory in OUTPUT_DIRS)
    assert all((root / name).read_text() == "new" for name in OUTPUT_FILES)
    assert (root / "overrides.toml").read_text() == "kept"
    assert not collector.paths.staging.root.exists()
