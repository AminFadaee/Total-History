import hashlib
import html
import logging
import pathlib
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from urllib.parse import urlparse

import genanki

from page_in_history.dates import Span, centuries, period_label
from page_in_history.photos import PictureKind

DECK_NAME = "Page in History"
CIVILIZATION_MODEL_ID = 1_812_604_221
FIGURE_MODEL_ID = 1_812_604_222
TAG_PREFIX = "PIH"
MEDIA_PREFIX = "pih"
TAG_UNSAFE = re.compile(r"[^\w-]+")
MAX_MODERN_COUNTRIES = 10
MAX_COUNTRY_TAGS = 3
GENERIC_PLACE_WORDS = {"city", "new", "old", "great", "san", "of", "the", "al", "el", "de"}
ASKED_KINDS = (PictureKind.PHOTOGRAPH, PictureKind.FROM_LIFE)
SOURCE_NAMES = {"wikipedia.org": "Wikipedia", "wikidata.org": "Wikidata",
                "worldhistory.org": "World History Encyclopedia", "britannica.com": "Britannica",
                "iranicaonline.org": "Encyclopaedia Iranica", "treccani.it": "Treccani", "larousse.fr": "Larousse",
                "encyclopediaofukraine.com": "Encyclopedia of Ukraine"}

logger = logging.getLogger(__name__)


class Subdeck(StrEnum):
    CIVILIZATIONS = "Civilizations"
    SUCCESSION = "Succession"
    PERIODS = "Periods"
    CAPITALS = "Capitals"
    LOCATIONS = "Locations"
    FIGURES = "Figures"
    PHOTOS = "Photos"

    @property
    def full_name(self) -> str:
        return f"{DECK_NAME}::{self.value}"

    @property
    def deck_id(self) -> int:
        return stable_id(self.full_name)


@dataclass(frozen=True)
class Template:
    name: str
    subdeck: Subdeck
    requires: tuple[str, ...]
    front: str
    back: str

    def as_genanki(self, script: str = "") -> dict:
        return {"name": self.name, "qfmt": self.front + script, "afmt": self.back + script}


class RoutedCard(genanki.Card):
    def __init__(self, ord: int, deck_id: int):
        super().__init__(ord)
        self.deck_id = deck_id

    def write_to_db(self, cursor, timestamp: float, deck_id, note_id, id_gen, due=0):
        super().write_to_db(cursor, timestamp, self.deck_id, note_id, id_gen, due)


class RoutedNote(genanki.Note):
    def __init__(self, routed_cards: list[RoutedCard], **kwargs):
        super().__init__(**kwargs)
        self.routed_cards = routed_cards

    @property
    def cards(self) -> list[RoutedCard]:
        return self.routed_cards


def stable_id(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest()[:12], 16) % (1 << 40) + (1 << 30)


UNKNOWN_ENTITY = '<div class="entity unknown">?</div>'
UNKNOWN_VALUE = '<div class="value unknown">?</div>'


def asked_picture(document: dict) -> tuple[str, str] | None:
    """The picture the Portrait card asks about and its credit: the person's photograph, or else their Who card picture
    when it is a photograph or a portrait made in their lifetime. Later depictions are never asked."""
    if document["photo"]:
        return document["photo"], document["photo_credit"]
    if document["image"] and document.get("image_kind") in ASKED_KINDS:
        credit = document["image_credit"] if document["image_kind"] == PictureKind.PHOTOGRAPH \
            else f'{document["image_kind"]} · {document["image_credit"]}'
        return document["image"], credit
    return None


def asks_capital(document: dict) -> bool:
    """Whether the Capital card is made: the civilization has a capital and none of them is in its name (Venice for
    the Republic of Venice), which would give the answer away."""
    words = set(re.findall(r"\w+", document["name"].lower()))
    named = {word for capital in document["capitals"] for word in re.findall(r"\w+", capital["name"].lower())}
    return bool(document["capitals"]) and not (named - GENERIC_PLACE_WORDS) & words


def period_text(period: dict) -> str:
    """The confirmed period, the approximate years Wikidata gives, marked as such, or the start of a state that
    still exists ("since 1867")."""
    if period["confirmed"]:
        return period_label(Span(**period["start"]), Span(**period["end"]))
    if period.get("core"):
        first, last = period["core"]
        return period_label(Span(first, first, True), Span(last, last, True))
    if period.get("since"):
        return f"since {Span(**period['since']).label()}"
    return ""


def source_name(url: str) -> str:
    if not url.startswith("https://"):
        return url.split(",")[0]
    host = urlparse(url).hostname or ""
    return next((name for domain, name in SOURCE_NAMES.items() if host == domain or host.endswith(f".{domain}")),
                host.removeprefix("www."))


def dispute_text(document: dict, disputed: str) -> str:
    """'Sources differ on the end: AD 550 (Wikipedia) · AD 600 (World History Encyclopedia)', or for a side the
    civilizations each source names."""
    dispute = next((item for item in document.get("disputes", []) if item["field"] == disputed), None)
    if dispute is None:
        return ""
    values = [Span(item["value"], item["value"], item["circa"]).label() if disputed in ("start", "end")
              else item["value"] for item in dispute["values"]]
    cited = [f"{value} ({source_name(item['source'])})" if item["source"] else value
             for value, item in zip(values, dispute["values"])]
    subject = f" on the {disputed}" if disputed in ("start", "end") else ""
    return f"Sources differ{subject}: {' · '.join(cited)}"


def disputed_names(document: dict, side: str) -> list[str]:
    dispute = next((item for item in document.get("disputes", []) if item["field"] == side), None)
    return [item["value"] for item in dispute["values"]] if dispute else []


def conditional(field: str, content: str) -> str:
    return f"{{{{#{field}}}}}{content}{{{{/{field}}}}}"


def guarded(fields: tuple[str, ...], content: str) -> str:
    for field in reversed(fields):
        content = conditional(field, content)
    return content


def entity(content: str, answered: bool = False) -> str:
    return f'<div class="entity answer">{content}</div>' if answered else f'<div class="entity">{content}</div>'


def value(content: str, answered: bool = False) -> str:
    return f'<div class="value answer">{content}</div>' if answered else f'<div class="value">{content}</div>'


def face(entity_html: str, label: str, value_html: str) -> str:
    return f'{entity_html}<hr class="divider"><div class="label">{label}</div>{value_html}'


def info(*lines: tuple[str, str]) -> str:
    rows = "".join(conditional(field, f'<div class="line"><span class="key">{key}</span> {{{{{field}}}}}</div>')
                   for key, field in lines)
    return f'<div class="info">{rows}</div>'


CIVILIZATION_TITLE = '{{Name}}<div class="subtitle">{{Period}}</div>'
CIVILIZATION_INFO = info(("What", "Summary"), ("Capital", "Capital"), ("Key figures", "Rulers"),
                         ("Before", "BeforeList"), ("After", "AfterList"), ("Today", "ModernCountries"))
SUCCESSION_SLOTS = 3
MAP_IMAGE = '<div class="image map">{{Map}}</div>'
MAP_THUMBNAIL = '<div class="thumbnail map">{{Map}}</div>'
NOTE = '<div class="note">{{{{{}}}}}</div>'


def slot(side: str, number: int) -> str:
    """The field for a side's numbered succession card: Before, Before2, Before3."""
    return side if number == 1 else f"{side}{number}"


def neighbour_template(side: str, number: int = 1) -> Template:
    """Who ruled a region right before or after the civilization, e.g. 'Before it in Iraq'. The first card is its
    home region's; the others are other regions it took over or handed on."""
    field = slot(side, number)
    label = f"{side} it in {{{{{field}Region}}}}"
    note = conditional(f"{field}Note", NOTE.format(f"{field}Note")) if number == 1 else ""
    return Template(field, Subdeck.SUCCESSION, (field,),
                    face(entity(CIVILIZATION_TITLE), label, UNKNOWN_VALUE),
                    face(entity(CIVILIZATION_TITLE), label, value(f"{{{{{field}}}}}", answered=True)) + note
                    + MAP_THUMBNAIL)


CIVILIZATION_TEMPLATES = (
    Template("Map", Subdeck.CIVILIZATIONS, ("AskMap",),
             face(UNKNOWN_ENTITY, "Map", MAP_IMAGE),
             face(entity(CIVILIZATION_TITLE, answered=True), "Map", MAP_IMAGE) + CIVILIZATION_INFO),
    neighbour_template("Before"),
    neighbour_template("After"),
    Template("Period", Subdeck.PERIODS, ("AskPeriod",),
             face(entity("{{Name}}"), "When", UNKNOWN_VALUE),
             face(entity("{{Name}}"), "When", value("{{Period}}", answered=True))
             + conditional("PeriodNote", NOTE.format("PeriodNote")) + CIVILIZATION_INFO + MAP_THUMBNAIL),
    *(neighbour_template(side, number) for side in ("Before", "After") for number in range(2, SUCCESSION_SLOTS + 1)),
    Template("Capital", Subdeck.CAPITALS, ("AskCapital",),
             face(entity(CIVILIZATION_TITLE), "Capital", UNKNOWN_VALUE),
             face(entity(CIVILIZATION_TITLE), "Capital", value("{{Capital}}", answered=True)) + MAP_THUMBNAIL),
    Template("Where", Subdeck.LOCATIONS, ("AskMap",),
             face(entity(CIVILIZATION_TITLE), "Where", UNKNOWN_VALUE),
             face(entity(CIVILIZATION_TITLE), "Where", MAP_IMAGE) + CIVILIZATION_INFO),
)

CIVILIZATION_FIELDS = ("Id", "Name", "Map", "AskMap", "Period", "AskPeriod", "Capital", "Before", "BeforeRegion",
                       "After", "AfterRegion", "Rulers", "ModernCountries", "Summary", "BeforeList", "AfterList",
                       *(f"{slot(side, number)}{part}" for side in ("Before", "After")
                         for number in range(2, SUCCESSION_SLOTS + 1) for part in ("", "Region")),
                       "PeriodNote", "BeforeNote", "AfterNote", "AskCapital")

FIGURE_TITLE = '{{Name}}<div class="subtitle">{{Life}}</div>'
PORTRAIT = conditional("Image", '<div class="portrait">{{Image}}<div class="credit">{{ImageKind}} · {{ImageCredit}}</div></div>')
PHOTO = '<div class="value image photo">{{Photo}}</div>'
PHOTO_CREDITED = '<div class="value image photo">{{Photo}}<div class="credit">{{PhotoCredit}}</div></div>'
FIGURE_INFO = info(("Civilization", "Civilization"), ("Setting", "Setting"), ("Known for", "Contribution"),
                   ("Related", "Related"))

FIGURE_TEMPLATES = (
    Template("Who", Subdeck.FIGURES, ("Role",),
             face(entity("{{Name}}"), "Who", UNKNOWN_VALUE),
             face(entity(FIGURE_TITLE), "Who", value("{{Role}}", answered=True)) + FIGURE_INFO + PORTRAIT
             + conditional("CivilizationMap", '<div class="thumbnail map">{{CivilizationMap}}</div>')),
    Template("Photo", Subdeck.PHOTOS, ("Photo",),
             face(UNKNOWN_ENTITY, "Portrait", PHOTO),
             face(entity(FIGURE_TITLE, answered=True), "Portrait", PHOTO_CREDITED) + FIGURE_INFO),
)

FIGURE_FIELDS = ("Id", "Name", "Role", "Life", "Civilization", "CivilizationMap", "Contribution", "Image", "ImageCredit",
                 "ImageKind", "Photo", "PhotoCredit", "Related", "Setting")

CSS = """
.card {
  --text: #1d1d1f; --muted: #8a8a8e; --faint: #c2c2c8; --accent: #0a66c2;
  --line: #d8d8dd; --info: #505055; --background: #fdfdfd;
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif; text-align: center;
  color: var(--text); background: var(--background); padding: 12px 8px;
}
.nightMode.card, .night_mode .card, .card.night_mode {
  --text: #e8e8ea; --muted: #9a9aa0; --faint: #5c5c62; --accent: #6cb2ff;
  --line: #3a3a3f; --info: #bdbdc2; --background: #1e1e20;
}
.entity { font-size: 30px; font-weight: 600; }
.subtitle { font-size: 18px; font-weight: 400; color: var(--muted); margin-top: 2px; }
.divider { border: none; border-top: 1px solid var(--line); width: min(60%, 360px); margin: 14px auto; }
.label { font-size: 14px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }
.value { font-size: 24px; line-height: 1.35; max-width: 32em; margin: 0 auto; }
.unknown { color: var(--faint); font-weight: 600; }
.answer { color: var(--accent); font-weight: 700; }
.image img { width: 100%; max-width: 900px; height: auto; max-height: 60vh; object-fit: contain; }
.thumbnail { margin-top: 14px; }
.thumbnail img { width: 100%; max-width: 360px; height: auto; }
.info { font-size: 16px; line-height: 1.5; color: var(--info); margin: 14px auto 0; max-width: 40em; text-align: left; }
.info .line { margin-top: 4px; }
.info .key { color: var(--muted); font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; margin-right: 0.4em; }
.portrait { margin-top: 18px; }
.portrait img { max-height: 260px; max-width: 100%; border-radius: 6px; }
.photo img { max-height: 55vh; max-width: 100%; border-radius: 6px; }
.credit { font-size: 12px; color: var(--muted); margin-top: 4px; }
.note { font-size: 14px; color: var(--muted); margin: 8px auto 0; max-width: 32em; }
"""


MAP_ZOOM_CSS = """
.map img { cursor: zoom-in; }
.zoom-overlay { position: fixed; inset: 0; z-index: 1000; overflow: auto; background: rgba(0, 0, 0, .88); cursor: grab;
  -webkit-overflow-scrolling: touch; }
.zoom-overlay.dragging { cursor: grabbing; }
.zoom-stage { display: flex; min-width: 100%; min-height: 100%; width: max-content; height: max-content; }
.zoom-stage img { margin: auto; max-width: none; max-height: none; }
"""

MAP_ZOOM_SCRIPT = """
<script>
(function () {
  var ZOOM = 1.25;
  var root = document.getElementById("qa") || document.body;

  function open(image, event) {
    var bounds = image.getBoundingClientRect();
    var focusX = (event.clientX - bounds.left) / bounds.width;
    var focusY = (event.clientY - bounds.top) / bounds.height;
    var fit = Math.min(window.innerWidth / image.naturalWidth, window.innerHeight / image.naturalHeight);
    var overlay = document.createElement("div");
    var stage = document.createElement("div");
    var large = document.createElement("img");
    overlay.className = "zoom-overlay";
    stage.className = "zoom-stage";
    large.src = image.src;
    large.style.width = image.naturalWidth * fit * ZOOM + "px";
    large.style.height = image.naturalHeight * fit * ZOOM + "px";
    stage.appendChild(large);
    overlay.appendChild(stage);
    root.appendChild(overlay);
    overlay.scrollLeft = large.offsetLeft + focusX * large.offsetWidth - overlay.clientWidth / 2;
    overlay.scrollTop = large.offsetTop + focusY * large.offsetHeight - overlay.clientHeight / 2;
    pannable(overlay);
  }

  function pannable(overlay) {
    var drag = null;
    overlay.addEventListener("pointerdown", function (event) {
      if (event.pointerType !== "mouse") return;
      drag = { x: event.clientX, y: event.clientY, left: overlay.scrollLeft, top: overlay.scrollTop, moved: false };
      overlay.classList.add("dragging");
    });
    overlay.addEventListener("pointermove", function (event) {
      if (!drag) return;
      var dx = event.clientX - drag.x;
      var dy = event.clientY - drag.y;
      drag.moved = drag.moved || Math.abs(dx) + Math.abs(dy) > 4;
      overlay.scrollLeft = drag.left - dx;
      overlay.scrollTop = drag.top - dy;
    });
    overlay.addEventListener("pointerup", function () {
      overlay.classList.remove("dragging");
      setTimeout(function () { drag = null; }, 0);
    });
    overlay.addEventListener("click", function (event) {
      event.stopPropagation();
      if (!(drag && drag.moved)) overlay.remove();
    });
  }

  root.querySelectorAll(".map img").forEach(function (image) {
    image.addEventListener("click", function (event) {
      event.stopPropagation();
      open(image, event);
    });
  });
})();
</script>
"""


def description() -> str:
    return f"""
<p><b>{DECK_NAME}</b>: civilizations on the map and the people who shaped them.</p>
<p>Each question type lives in its own subdeck. To skip a type, <b>suspend</b> its subdeck rather than deleting it,
because deleted cards come back when you import an update.</p>
<p>Borders come from Cliopatria (Seshat Global History Databank, CC BY 4.0); facts from Wikidata (CC0) and Wikipedia
(CC BY-SA); people lists from Wikipedia's Vital Articles and Pantheon (CC BY); modern borders from Natural Earth
(public domain); portraits from Wikimedia Commons under the licence credited on each card. Built {date.today().isoformat()}.</p>
"""


def century_tag(number: int) -> str:
    """'PIH::Century::BC::06th', 'PIH::Century::AD::03rd': padded so the tag browser sorts them in order."""
    suffix = "th" if 10 <= abs(number) % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(abs(number) % 10, "th")
    return tag("Century", "BC" if number < 0 else "AD", f"{abs(number):02d}{suffix}")


def civilization_tags(document: dict) -> list[str]:
    tiers = [document["tier"]] + (["Core"] if document.get("tier") == "Essential" else []) if document.get("tier") else []
    tags = [tag("Civilization", document["name"])] + [tag("Tier", tier) for tier in tiers]
    period = document["period"]
    if period["confirmed"]:
        tags += [century_tag(number) for number in centuries(period["start"]["earliest"], period["end"]["latest"])]
    countries = document["modern_countries"][:MAX_COUNTRY_TAGS]
    countries += [edge.get("region", "") for side in ("before", "after") for edge in document[side]]
    return tags + [tag("Country", country) for country in countries if country]


def succession_fields(document: dict) -> dict[str, str]:
    """A card per region before and after, the first for its home region, and the back's list of them: 'Abbasid
    Caliphate (Iraq) · Kara-Khitans (Kyrgyzstan)'."""
    fields = {}
    for side in ("Before", "After"):
        names = disputed_names(document, side.lower())
        edges = [{**edge, "name": " or ".join(names)} if edge.get("home") and names else edge
                 for edge in document[side.lower()]]
        home = [edge for edge in edges if edge.get("home")][:1]
        slots = (home or [{}]) + [edge for edge in edges if not edge.get("home")]
        for number in range(1, SUCCESSION_SLOTS + 1):
            edge = slots[number - 1] if number <= len(slots) else {}
            fields[slot(side, number)] = escaped(edge.get("name"))
            fields[f"{slot(side, number)}Region"] = escaped(edge.get("region"))
        fields[f"{side}List"] = joined([f"{edge['name']} ({edge['region']})" if edge.get("region") else edge["name"]
                                        for edge in edges], " · ")
        same = "The same state under another name." if any(edge.get("home") and edge.get("same_state") for edge in edges) else ""
        fields[f"{side}Note"] = "<br>".join(escaped(text) for text in (same, dispute_text(document, side.lower())) if text)
    return fields


def figure_tags(document: dict, civilizations: list[str]) -> list[str]:
    tags = [tag("Civilization", name) for name in civilizations]
    tags += [century_tag(number) for number in centuries(*document["lifetime"])]
    tags += [tag("Country", document["birth_country"])] if document["birth_country"] else []
    tags += [tag("Occupation", document["occupation"])] if document["occupation"] else []
    return tags


def tag(*parts: str) -> str:
    return "::".join([TAG_PREFIX, *(TAG_UNSAFE.sub("_", part).strip("_") for part in parts)])


def escaped(text: str | None) -> str:
    return html.escape(text or "")


def joined(items: list[str], separator: str = ", ") -> str:
    return separator.join(escaped(item) for item in items)


def countries(names: list[str]) -> str:
    shown = joined(names[:MAX_MODERN_COUNTRIES])
    hidden = len(names) - MAX_MODERN_COUNTRIES
    return f"{shown} and {hidden} more" if hidden > 0 else shown


class MediaLibrary:
    def __init__(self, data_dir: pathlib.Path, build_dir: pathlib.Path):
        self.data_dir = data_dir
        self.build_dir = build_dir
        self.files: set[str] = set()
        self.missing: list[pathlib.Path] = []

    def image(self, relative: str) -> str:
        if not relative:
            return ""
        source = self.data_dir / relative
        if not source.exists():
            self.missing.append(source)
            return ""
        target = self.build_dir / f"{MEDIA_PREFIX}-{source.parent.name}-{source.name}"
        if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        self.files.add(str(target))
        return f'<img src="{target.name}">'


class DeckBuilder:
    def __init__(self, data_dir: pathlib.Path, build_dir: pathlib.Path):
        self.media = MediaLibrary(data_dir, build_dir / "media")
        self.civilization_model = self._model(CIVILIZATION_MODEL_ID, f"{DECK_NAME} civilization",
                                              CIVILIZATION_FIELDS, CIVILIZATION_TEMPLATES)
        self.figure_model = self._model(FIGURE_MODEL_ID, f"{DECK_NAME} figure", FIGURE_FIELDS, FIGURE_TEMPLATES)
        self.root = genanki.Deck(stable_id(DECK_NAME), DECK_NAME, description())
        self.subdecks = [genanki.Deck(subdeck.deck_id, subdeck.full_name) for subdeck in Subdeck]
        self.notes = 0

    def add_civilization(self, document: dict) -> None:
        period = document["period"]
        values = {
            "Id": document["id"],
            "Name": escaped(document["name"]),
            "Map": self.media.image(document["map"]),
            "AskMap": "yes" if document["map"] and document.get("map_asked", True) else "",
            "Period": escaped(period_text(period)),
            "AskPeriod": "yes" if period_text(period) else "",
            "PeriodNote": "<br>".join(escaped(text) for end in ("start", "end") if (text := dispute_text(document, end))),
            "Capital": joined([capital["name"] for capital in document["capitals"]]),
            "AskCapital": "yes" if asks_capital(document) else "",
            **succession_fields(document),
            "Rulers": joined([figure["name"] for figure in document["key_figures"]], " · "),
            "Summary": escaped(document.get("summary", "")),
            "ModernCountries": countries(document["modern_countries"])
                               + (f" · overseas: {countries(document['overseas_countries'])}" if document.get("overseas_countries") else ""),
        }
        self._add(self.civilization_model, CIVILIZATION_FIELDS, CIVILIZATION_TEMPLATES, values,
                  civilization_tags(document), document["id"])

    def add_figure(self, document: dict, maps: dict[str, str]) -> None:
        """Only civilizations in the deck are shown, so each one named has its own map and cards."""
        civilizations = [name for name in document["civilizations"] if name in maps]
        asked = asked_picture(document)
        values = {
            "Id": document["id"],
            "Name": escaped(document["name"]),
            "Role": escaped(document["role"]),
            "Life": escaped(document["life"]),
            "Civilization": joined(civilizations),
            "Setting": "" if civilizations else escaped(document.get("setting", "")),
            "CivilizationMap": self.media.image(maps[civilizations[0]]) if civilizations else "",
            "Contribution": escaped(document["contribution"]),
            "Image": self.media.image(document["image"]),
            "ImageCredit": escaped(document["image_credit"]),
            "ImageKind": document.get("image_kind") or "Depiction",
            "Photo": self.media.image(asked[0]) if asked else "",
            "PhotoCredit": escaped(asked[1]) if asked else "",
            "Related": joined([name for name in document["related"] if name in maps]),
        }
        tags = figure_tags(document, civilizations)
        self._add(self.figure_model, FIGURE_FIELDS, FIGURE_TEMPLATES, values, tags, document["id"])

    def write(self, output: pathlib.Path) -> None:
        if self.media.missing:
            raise FileNotFoundError(f"{len(self.media.missing)} media files are missing, e.g. {self.media.missing[0]}")
        output.parent.mkdir(parents=True, exist_ok=True)
        package = genanki.Package([self.root, *self.subdecks], media_files=sorted(self.media.files))
        package.write_to_file(str(output))
        logger.info("Wrote %d notes and %d media files to %s", self.notes, len(self.media.files), output)

    def _add(self, model, fields, templates, values, tags, identity: str) -> None:
        cards = [RoutedCard(ord, template.subdeck.deck_id) for ord, template in enumerate(templates)
                 if all(values[field] for field in template.requires)]
        if not cards:
            return
        note = RoutedNote(cards, model=model, fields=[values[name] for name in fields], tags=sorted(set(tags)),
                          guid=genanki.guid_for(identity))
        self.root.add_note(note)
        self.notes += 1

    @staticmethod
    def _model(model_id: int, name: str, fields: tuple[str, ...], templates: tuple[Template, ...]) -> genanki.Model:
        return genanki.Model(
            model_id,
            name,
            fields=[{"name": field} for field in fields],
            templates=[Template(t.name, t.subdeck, t.requires, guarded(t.requires, t.front), t.back)
                       .as_genanki(MAP_ZOOM_SCRIPT) for t in templates],
            css=CSS + MAP_ZOOM_CSS,
            sort_field_index=1,
        )


def start_year(document: dict) -> int:
    start = document["period"]["start"]
    return start["earliest"] if start else document["map_year"]


def build_deck(civilizations: list[dict], figures: list[dict], data_dir: pathlib.Path, build_dir: pathlib.Path,
               output: pathlib.Path) -> None:
    builder = DeckBuilder(data_dir, build_dir)
    for document in sorted(civilizations, key=start_year):
        builder.add_civilization(document)
    maps = {document["name"]: document["map"] for document in civilizations}
    for document in sorted(figures, key=lambda document: document["name"]):
        builder.add_figure(document, maps)
    builder.write(output)
