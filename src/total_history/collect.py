import json
import logging
import os
import pathlib
import shutil
from dataclasses import asdict, dataclass
from urllib.parse import quote

from PIL import Image

from total_history.errata import apply_errata, apply_figure_errata, legacy_rejections, load_errata, passed_legacy
from total_history.figure_overrides import load_figure_overrides
from total_history.figures import Figure, FigureBuilder
from total_history.http import Http
from total_history.legacy import LegacyScores, add_civilization_legacy, add_legacy
from total_history.lineages import Lineages
from total_history.maps import MapJob, MapStyle, render_all
from total_history.overrides import apply_overrides, apply_tiers, check_against_errata, load_overrides
from total_history.photos import COMMONS_FILE, PhotoClassifier, PhotoMode, PortraitFinder, digest
from total_history.power import PowerScores
from total_history.polities import Polity, PolityBuilder, PolityCatalog
from total_history.sources import fonts, naturalearth, pantheon
from total_history.sources.cliopatria import Cliopatria
from total_history.sources.wikidata import Wikidata
from total_history.sources.wikipedia import Wikipedia
from total_history.succession_text import SuccessionScores

OUTPUT_DIRS = ("civilizations", "figures", "maps", "images")
OUTPUT_FILES = ("extended.json", "report.json")
IMAGE_WIDTH = 480
IMAGE_QUALITY = 85
MAP_WORKERS = max(1, min(4, (os.cpu_count() or 2) // 2))

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Slice:
    """A region and period, or the whole world (no region) with civilizations chosen by importance."""

    region: str | None
    start: int
    end: int


@dataclass(frozen=True)
class DataPaths:
    root: pathlib.Path

    @property
    def civilizations(self) -> pathlib.Path:
        return self.root / "civilizations"

    @property
    def figures(self) -> pathlib.Path:
        return self.root / "figures"

    @property
    def maps(self) -> pathlib.Path:
        return self.root / "maps"

    @property
    def images(self) -> pathlib.Path:
        return self.root / "images"

    @property
    def errata(self) -> pathlib.Path:
        return self.root / "errata.toml"

    @property
    def overrides(self) -> pathlib.Path:
        return self.root / "overrides.toml"

    @property
    def figure_overrides(self) -> pathlib.Path:
        return self.root / "figure_overrides.toml"

    @property
    def report(self) -> pathlib.Path:
        return self.root / "report.json"

    @property
    def staging(self) -> "DataPaths":
        """Where a collect writes, beside the data, until it has finished."""
        return DataPaths(self.root.with_name(f"{self.root.name}.partial"))


class Collector:
    def __init__(self, paths: DataPaths, cache_dir: pathlib.Path, photos: PhotoMode = PhotoMode.MODEL):
        self.paths = paths
        self.cache_dir = cache_dir
        self.http = Http(cache_dir)
        self.photos = photos

    def run(self, scope: Slice) -> None:
        """Collects into a staging folder and replaces the data's output only once everything is written, so a run
        that fails leaves the data as it was. Overrides, errata and the saved model readings are read from the data."""
        self.output = self.paths.staging
        shutil.rmtree(self.output.root, ignore_errors=True)
        for directory in OUTPUT_DIRS:
            (self.output.root / directory).mkdir(parents=True)
        self._collect(scope)
        self._publish()

    def _publish(self) -> None:
        for directory in OUTPUT_DIRS:
            shutil.rmtree(self.paths.root / directory, ignore_errors=True)
            (self.output.root / directory).rename(self.paths.root / directory)
        for name in OUTPUT_FILES:
            (self.output.root / name).replace(self.paths.root / name)
        self.output.root.rmdir()

    def _collect(self, scope: Slice) -> None:
        cliopatria = Cliopatria.load(self.http)
        wikipedia, wikidata = Wikipedia(self.http), Wikidata(self.http)
        countries = naturalearth.countries(self.http)
        catalog = PolityCatalog(cliopatria, wikipedia, wikidata, scope.start, scope.end)
        lineages = Lineages(catalog)
        lineages.apply()
        cliopatria = catalog.cliopatria
        builder = PolityBuilder(catalog, countries)
        selected = (builder.select(scope.region, scope.start, scope.end) if scope.region
                    else builder.select_notable())
        polities = builder.build(selected)
        logger.info("Selected %d civilizations: %s", len(polities), ", ".join(polity.name for polity in polities))
        classifier = PhotoClassifier(self.http) if self.photos is PhotoMode.MODEL else None
        figure_overrides = load_figure_overrides(self.paths.figure_overrides)
        figure_builder = FigureBuilder(catalog, pantheon.load(self.http), self.http, PortraitFinder(self.http, classifier),
                                       figure_overrides)
        figures = figure_builder.build({polity.name for polity in polities}, scope.start, scope.end)
        polities += builder.build([catalog.identities[name] for name in figure_builder.joined])
        builder.mark_repeated_maps(polities)
        texts = SuccessionScores(self.paths.root / "succession_scores.json", self.http)
        builder.link_successions(polities, figures, texts)
        texts.save()
        scores = PowerScores(self.paths.root / "ruler_scores.json", self.http)
        builder.choose_key_figures(polities, figures, scores)
        scores.save()
        errata, overrides = load_errata(self.paths.errata), load_overrides(self.paths.overrides)
        check_against_errata(overrides, errata)
        dropped = apply_errata(polities, errata) + apply_figure_errata(figures, errata)
        applied = apply_overrides(polities, overrides, wikidata)
        builder.follow_map_years(polities, applied)
        builder.assign_tiers(polities)
        apply_tiers(polities, overrides)
        logger.info("Their people brought in %d more civilizations: %s", len(figure_builder.joined),
                    ", ".join(figure_builder.joined))
        legacy_scores = LegacyScores(self.paths.root / "legacy_scores.json")
        rejected = legacy_rejections(errata)
        passed = add_legacy(figures, wikipedia, legacy_scores, rejected)
        passed |= add_civilization_legacy(polities, wikipedia, legacy_scores, rejected)
        dropped += passed_legacy(errata, passed)
        legacy_scores.save()
        self.failures: list[str] = []
        self._draw([polity for polity in polities if polity.borders], cliopatria, countries, builder)
        for figure in figures:
            try:
                figure.image = self._image(figure.image, figure.slug)
                figure.photo = self._image(figure.photo, f"{figure.slug}-photo")
            except Exception:
                logger.exception("Could not fetch the images of %s", figure.name)
                self.failures.append(f"images of {figure.name}")
                figure.image, figure.photo = "", ""
        self._write(polities, figures)
        write_json(self.output.root / "extended.json", figure_builder.extended)
        self._report(scope, catalog, cliopatria, lineages, polities, figures, dropped, applied,
                     figure_overrides, figure_builder.unused_overrides(figures))

    def _draw(self, polities: list[Polity], cliopatria: Cliopatria, countries, builder: PolityBuilder) -> None:
        """Each civilization's map, and for one whose capitals are placed its Capital card's maps: the capitals
        dotted, then named."""
        jobs = [MapJob(polity.name, polity.map_year, cliopatria.at(polity.map_year),
                       [capital["location"] for capital in polity.capitals if capital["location"]
                        and (capital.get("founded") is None or capital["founded"] <= polity.map_year)],
                       self.output.maps / f"{polity.slug}.png", polity.parts, builder.colonial_power(polity.id))
                for polity in polities]
        located = [polity for polity in polities if any(capital["location"] for capital in polity.capitals)]
        capital_jobs = [MapJob(polity.name, polity.map_year, cliopatria.at(polity.map_year),
                               [tuple(capital["location"]) for capital in polity.capitals if capital["location"]],
                               self.output.maps / f"{polity.slug}-{style.value}.png", polity.parts,
                               builder.colonial_power(polity.id), style,
                               tuple(capital["name"] for capital in polity.capitals if capital["location"]))
                        for polity in located for style in (MapStyle.CAPITAL, MapStyle.CAPITAL_ANSWER)]
        results = render_all(jobs + capital_jobs, countries, naturalearth.rivers(self.http), fonts.inter(self.http),
                             MAP_WORKERS, self.cache_dir / "maps")
        for polity, result in zip(polities, results):
            if isinstance(result, Exception):
                logger.error("Could not draw %s", polity.name, exc_info=result)
                self.failures.append(f"drawing {polity.name}")
                polity.map = ""
            else:
                polity.map = f"maps/{polity.slug}.png"
                polity.checks["labelled"] = result
        drawn = results[len(jobs):]
        for index, polity in enumerate(located):
            asked, answered = drawn[2 * index], drawn[2 * index + 1]
            if isinstance(asked, Exception) or isinstance(answered, Exception):
                logger.error("Could not draw the capital of %s", polity.name,
                             exc_info=asked if isinstance(asked, Exception) else answered)
                self.failures.append(f"drawing the capital of {polity.name}")
                continue
            polity.capital_map = f"maps/{polity.slug}-{MapStyle.CAPITAL.value}.png"
            polity.capital_map_answer = f"maps/{polity.slug}-{MapStyle.CAPITAL_ANSWER.value}.png"

    def _image(self, file: str, name: str) -> str:
        if not file:
            return ""
        url = COMMONS_FILE.format(quote(file), IMAGE_WIDTH)
        source = self.http.download(url, f"commons-{digest(file)}-{IMAGE_WIDTH}")
        target = self.output.images / f"{name}.jpg"
        Image.open(source).convert("RGB").save(target, quality=IMAGE_QUALITY)
        return f"images/{target.name}"

    def _write(self, polities: list[Polity], figures: list[Figure]) -> None:
        for polity in polities:
            write_json(self.output.civilizations / f"{polity.slug}.json", polity.to_json())
        for figure in figures:
            write_json(self.output.figures / f"{figure.slug}.json", figure.to_json())
        logger.info("Wrote %d civilizations and %d figures", len(polities), len(figures))

    def _report(self, scope: Slice, catalog: PolityCatalog, cliopatria: Cliopatria, lineages: Lineages, polities,
                figures, dropped, applied, figure_overrides, unused) -> None:
        write_json(self.output.report, {
            "slice": asdict(scope),
            "civilizations": len(polities),
            "figures": len(figures),
            "failures": self.failures,
            "errata_dropped": [erratum.to_json() for erratum in dropped],
            "overrides_applied": [override.to_json() for override in applied],
            "needs_verification": [override.to_json() for override in applied if override.needs_verification],
            "figure_overrides_applied": [override.to_json() for override in figure_overrides if override not in unused],
            "figure_overrides_unused": [override.to_json() for override in unused],
            "unresolved_polities": dict(sorted(catalog.unresolved.items())),
            "ignored_cliopatria_steps": [asdict(spike) for spike in cliopatria.spikes],
            "lineage_handovers": [handover.to_json() for handover in lineages.handovers],
            "names_not_matching_their_state": lineages.mismatched,
            "names_dropped_for_their_wrong_state": lineages.dropped,
        })


def write_json(path: pathlib.Path, document: dict) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def load_documents(directory: pathlib.Path) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))]
