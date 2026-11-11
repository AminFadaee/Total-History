import argparse
import logging
import pathlib
import sys

from total_history import quality
from total_history.collect import Collector, DataPaths, Slice, load_documents
from total_history.deck import build_deck
from total_history.photos import PhotoMode
from total_history.review import check, review
from total_history.samples import render_samples

DATA_DIR = pathlib.Path("data")
CACHE_DIR = pathlib.Path("cache")
BUILD_DIR = pathlib.Path("build")
DECK_FILE = BUILD_DIR / "total_history.apkg"
SAMPLES_DIR = pathlib.Path("docs/samples")
ERROR_LOG = pathlib.Path("errors.log")


def configure_logging() -> None:
    console = logging.StreamHandler()
    errors = logging.FileHandler(ERROR_LOG, mode="w")
    errors.setLevel(logging.ERROR)
    logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[console, errors])
    logging.getLogger("total_history.sources.cliopatria").setLevel(logging.ERROR)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect civilizations and historical figures and build the Anki deck.")
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect", help="select civilizations and figures for a region and period")
    collect.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    collect.add_argument("--region", help="limit to a modern country, as named by Natural Earth; the whole world if left out")
    collect.add_argument("--start", type=int, default=-3400, help="first year, negative for BC")
    collect.add_argument("--end", type=int, default=2024, help="last year")
    collect.add_argument("--photos", type=PhotoMode, choices=list(PhotoMode), default=PhotoMode.MODEL,
                         help="how photographs are recognised: an image model and Commons metadata (needs "
                              "'uv sync --extra ml'), or Commons metadata only")

    check = commands.add_parser("check", help="report what was confirmed, left out and why")
    check.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)

    deck = commands.add_parser("deck", help="build the Anki deck from the collected data")
    deck.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    deck.add_argument("--output", type=pathlib.Path, default=DECK_FILE)

    changes = commands.add_parser("review", help="review the changes a collect made to the fact files and approve them")
    changes.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    changes.add_argument("--approve", action="store_true", help="approve every change without asking")
    changes.add_argument("--check", action="store_true", help="fail if staged fact files were not approved (for the hook)")

    samples = commands.add_parser("samples", help="render sample cards from the built deck")
    samples.add_argument("--deck", type=pathlib.Path, default=DECK_FILE)
    samples.add_argument("--output", type=pathlib.Path, default=SAMPLES_DIR)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()

    match args.command:
        case "collect":
            Collector(DataPaths(args.data_dir), CACHE_DIR, args.photos).run(Slice(args.region, args.start, args.end))
        case "check":
            print(quality.report(DataPaths(args.data_dir)))
        case "deck":
            paths = DataPaths(args.data_dir)
            build_deck(load_documents(paths.civilizations), load_documents(paths.figures), args.data_dir, BUILD_DIR,
                       args.output)
        case "review":
            raise SystemExit(check(args.data_dir) if args.check else
                             review(args.data_dir, sys.stdin.isatty() and not args.approve, args.approve))
        case "samples":
            render_samples(args.deck, args.output)


if __name__ == "__main__":
    main()
