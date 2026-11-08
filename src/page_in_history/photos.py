import hashlib
import html as markup
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

import numpy as np
from PIL import Image

from page_in_history.http import Http
from page_in_history.sources.wikidata import Entity

logger = logging.getLogger(__name__)
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
COMMONS_FILE = "https://commons.wikimedia.org/wiki/Special:FilePath/{}?width={}"
PHOTO_ERA = 1860
PHOTOGRAPHY_INVENTED = 1839
PHOTOGRAPH = "Q125191"
MAX_CANDIDATES = 10
BATCH = 50
MAX_ENCODED_TITLES = 6000
SCORE_WIDTH = 480
ALONE = 0.9
WITH_EVIDENCE = 0.7
PORTRAIT = 0.5
CLEAR_PORTRAIT = 0.9
IMAGE_FILE = re.compile(r"\.(jpe?g|png|tiff?)$", re.IGNORECASE)
NOT_A_PORTRAIT = re.compile(r"\bcoins?\b|coinage|\bdinars?\b|\baure(?:us|i)\b|siliqua|calligraph|\bstamps?\b|postage|"
                            r"\bplates\b|burial suits?|\btomb of\b|\bstatues?\b|\bbusts?\b(?! length)|sculptures?\b|\breliefs?\b|"
                            r"\beffig(?:y|ies)\b", re.IGNORECASE)
FROM_LIFE_YEARS = 5
MODERN_ERA = 1900
PHOTOGRAPHED_AFTER = 1950
EARLY_YEARS = 50
ASSUMED_LIFESPAN = 70
WORN = re.compile(r"\bcrowns\b|\bflags?\b", re.IGNORECASE)
PORTRAYED = re.compile(r"portrait|\bin art\b", re.IGNORECASE)
PHOTOGRAPHIC = re.compile(r"photograph|daguerreotype|albumen|gelatin silver|cartes? de visite|cabinet card|ambrotype|"
                          r"tintype|collodion", re.IGNORECASE)
NOT_PHOTOGRAPHIC = re.compile(r"painting|drawing|lithograph|engraving|etching|woodcut|illustration|sculpture|statue|"
                              r"\bbusts?\b|caricature|miniature|fresco|mosaic|\bcoins?\b|\bstamps?\b|calligraph",
                              re.IGNORECASE)
PORTRAIT_CATEGORY = re.compile(r"portraits? of|paintings of|photographs of|depictions of|drawings of|engravings of", re.IGNORECASE)
MACHINE_DATE = re.compile(r"QS:P\d*,\+(\d{3,4})-")
HUMAN_YEAR = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")
FILE_NAME = re.compile(r"(?:^[^:*]*?\.(?:jpe?g|png|tiff?|gif|svg|webp):\s*|(?<=\s)\*?\S+?\.(?:jpe?g|png|tiff?|gif|svg|webp):\s*)+",
                       re.IGNORECASE)
FOOTNOTE = re.compile(r"\s*\[(?:\d+|r[ée]f[^\]]*|citation needed)\]", re.IGNORECASE)
HIDDEN = re.compile(r"<(\w+)[^>]*display:\s*none[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)



def url_batches(titles: list[str]) -> list[list[str]]:
    """Batches of up to 50 titles, split further when the encoded titles would make the request URL too long for
    the server, as long non-Latin file names do."""
    batches = []
    for start in range(0, len(titles), BATCH):
        batch, size = [], 0
        for title in titles[start:start + BATCH]:
            length = len(quote(title)) + 3
            if batch and size + length > MAX_ENCODED_TITLES:
                batches.append(batch)
                batch, size = [], 0
            batch.append(title)
            size += length
        batches.append(batch)
    return batches

class PictureKind(StrEnum):
    PHOTOGRAPH = "Photograph"
    FROM_LIFE = "Portrait from life"
    LATER = "Later depiction"
    UNDATED = "Depiction"




class PhotoMode(StrEnum):
    MODEL = "model"
    METADATA = "metadata"


@dataclass(frozen=True)
class CommonsFile:
    title: str
    page_id: int | None
    categories: list[str]
    captured: str
    credit: str
    usage: int

    @property
    def name(self) -> str:
        return self.title.removeprefix("File:")

    @property
    def portrait(self) -> bool:
        """Not filed as an object: a coin, stamp, plate, burial suit or tomb, or a crown or flag on its own. A crown
        or flag in a picture filed as a portrait or as art, like a monarch painted wearing one, is still a portrait."""
        if any(NOT_A_PORTRAIT.search(category) for category in self.categories):
            return False
        return not any(WORN.search(category) for category in self.categories) or \
            any(PORTRAYED.search(category) for category in self.categories)


@dataclass(frozen=True)
class Photo:
    file: str
    credit: str
    check: str


class PhotoClassifier:
    """A zero-shot image model (OpenAI's CLIP ViT-L/14, 8-bit, run with onnxruntime on the CPU) that scores how much a
    picture shows one person rather than a statue, bust, relief, coin, medal, group, scene, building or text, and how
    much it looks like a photograph rather than a painting, drawing or print. Each picture's embedding is cached, so
    the descriptions can change without running the model again."""

    REPOSITORY = "https://huggingface.co/Xenova/clip-vit-large-patch14/resolve/main/{}"
    FILES = {"vision": "onnx/vision_model_quantized.onnx", "text": "onnx/text_model.onnx",
             "tokenizer": "tokenizer.json"}
    PHOTO_PROMPTS = ["a photograph of a person", "a black and white photograph of a person",
                     "an old sepia studio photograph of a person"]
    OTHER_PROMPTS = ["a painting of a person", "an oil painting portrait", "an engraving of a person",
                     "a lithograph illustration of a person", "a drawing of a person",
                     "a photograph of a statue of a person", "a photograph of a marble bust", "a coin with a portrait"]
    ONE_PERSON_PROMPTS = [
        "a portrait photograph of one person", "a black and white photograph of one man", "a painted portrait of one person",
        "an oil painting of a king", "an engraving of one man", "a pencil drawing of one person", "a lithograph portrait",
        "a medieval illustration of a single figure", "a miniature painting of one person", "a portrait of a woman",
        "a woodcut of one man", "an icon of a saint", "a drawing of a seated ruler"]
    OTHER_SUBJECT_PROMPTS = [
        "a marble statue", "a marble bust", "a bronze statue on a pedestal", "a stone head of a statue", "a stone relief",
        "a carved relief on a wall", "a coin", "a medal", "arabic calligraphy", "a page of text", "a building",
        "a window", "a landscape", "a map", "a group of people", "a crowd of people", "a battle scene",
        "a painting of many people", "a skeleton", "a tomb", "a monument", "a ruler surrounded by courtiers",
        "a court scene with many figures", "a rider on horseback", "an equestrian painting", "two people",
        "a group photograph of several men", "a silver vase", "an engraved metal plate", "a decorated object",
        "a signature", "handwriting", "a wax seal", "a coat of arms", "a book cover", "the title page of a book",
        "an animal", "a bird", "two men side by side", "two people talking", "a theatre scene"]
    SIZE = 224
    MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
    STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
    CONTEXT = 77
    PAD = 49407

    def __init__(self, http: Http):
        try:
            import onnxruntime
            from tokenizers import Tokenizer
        except ImportError as error:
            raise SystemExit("The photo model needs the optional ml extra: run 'uv sync --extra ml', or collect with "
                             "'--photos metadata' to use Commons metadata only") from error
        self.http = http
        paths = {part: http.download(self.REPOSITORY.format(file), f"clip-l14-{file.replace('/', '-')}")
                 for part, file in self.FILES.items()}
        self.vision = onnxruntime.InferenceSession(str(paths["vision"]), providers=["CPUExecutionProvider"])
        text = onnxruntime.InferenceSession(str(paths["text"]), providers=["CPUExecutionProvider"])
        tokenizer = Tokenizer.from_file(str(paths["tokenizer"]))
        tokenizer.enable_padding(length=self.CONTEXT, pad_id=self.PAD)
        tokenizer.enable_truncation(self.CONTEXT)
        def encode(prompts: list[str]) -> np.ndarray:
            ids = np.array([encoding.ids for encoding in tokenizer.encode_batch(prompts)], dtype=np.int64)
            return normalised(output(text, text.run(None, {"input_ids": ids}), "text_embeds"))
        self.medium = encode(self.PHOTO_PROMPTS + self.OTHER_PROMPTS)
        self.subject = encode(self.ONE_PERSON_PROMPTS + self.OTHER_SUBJECT_PROMPTS)
        self.embeddings = http.cache_dir / "photo-embeddings"

    def score(self, file: str) -> tuple[float, float]:
        """How much the picture looks like a photograph rather than art, and like one person rather than a statue, an
        object, a group, a scene, a building or text: each the share of the matching descriptions. An unreadable
        picture scores 0 on both."""
        image = self.embedding(file)
        if image is None:
            return 0.0, 0.0
        return share(image, self.medium, len(self.PHOTO_PROMPTS)), share(image, self.subject, len(self.ONE_PERSON_PROMPTS))

    def embedding(self, file: str) -> np.ndarray | None:
        """The picture's normalised embedding, computed once and cached per file."""
        path = self.embeddings / digest(file)[:2] / f"{digest(file)}.npy"
        if not path.exists():
            picture = self._picture(file)
            vector = np.zeros((1, 0), dtype=np.float32) if picture is None else normalised(
                output(self.vision, self.vision.run(None, {"pixel_values": self._pixels(picture)[None]}), "image_embeds"))
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, vector.astype(np.float32))
        vector = np.load(path)
        return vector if vector.size else None

    def _picture(self, file: str) -> Image.Image | None:
        """The file at scoring size, downloaded again once if the cached copy is unreadable, as a truncated transfer
        leaves it; None when it still cannot be read, which counts as neither a photograph nor a portrait. Pillow
        reports some files it refuses, such as a PNG with oversized embedded text, as a ValueError."""
        url, name = COMMONS_FILE.format(quote(file), SCORE_WIDTH), f"commons-{digest(file)}-{SCORE_WIDTH}"
        for attempt in range(2):
            path = self.http.download(url, name)
            try:
                return Image.open(path).convert("RGB")
            except (OSError, ValueError):
                logger.warning("Unreadable image %s (attempt %d)", file, attempt + 1)
                path.unlink(missing_ok=True)
        return None

    def _pixels(self, image: Image.Image) -> np.ndarray:
        scale = self.SIZE / min(image.size)
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.BICUBIC)
        left, top = (image.width - self.SIZE) // 2, (image.height - self.SIZE) // 2
        image = image.crop((left, top, left + self.SIZE, top + self.SIZE))
        array = (np.asarray(image, dtype=np.float32) / 255 - self.MEAN) / self.STD
        return array.transpose(2, 0, 1)


class PortraitFinder:
    """Pictures of a person from Wikimedia Commons: an illustration for the Who card and a photograph for the Photo
    card, which can be different files."""

    def __init__(self, http: Http, classifier: PhotoClassifier | None):
        self.http = http
        self.classifier = classifier

    def illustration(self, person: Entity) -> tuple[str, str, PictureKind | None, str]:
        """A picture of the person for the Who card, labelled a photograph, a portrait made in their lifetime, or a
        later depiction, which is shown as context only. Candidates are the person's main Wikidata image and the files
        in their own Commons category, except those Commons files as objects (coins, stamps, plates, statues, busts,
        reliefs, effigies, tombs; a crown or flag outside a portrait) or calligraphy. The main image is kept when the
        image model sees one person in it, not a statue, object, group, scene, signature, building or text, unless it
        is a later depiction and another file is a portrait from the person's lifetime that the model sees clearly.
        Any other file must be filed on Commons as a portrait, painting, drawing, engraving or photograph of the person
        by name, or be a photograph named after them in full, since their category also holds their works, their
        relatives and scenes they appear in, and the model must see one person in it clearly. Otherwise those files are
        tried best first by what Commons says before anything is downloaded: likely
        photographs, then pictures made in the person's lifetime, then within 50 years of their death, then the rest,
        the most used first, stopping once nothing later could do better. Without the model, the main image or a
        portrait category named after the person stands in. When nothing will do, the English Wikipedia article's lead
        image, if the model sees one person in it clearly. Returns the file name, its credit, its kind, and why there
        is none when there is not."""
        born, died = life_span(person)
        main_titles = {"File:" + claim["mainsnak"]["datavalue"]["value"].replace("_", " ") for claim in person.statements("P18")}
        files = self._candidates(person)[:MAX_CANDIDATES]
        if not files:
            return self._lead_picture(person, born, died, "no candidate image")
        expected = {file.title: self._expected(file, person, born, died, file.title in main_titles) for file in files}
        main = next((file for file in files if file.title in main_titles), None)
        if main and self._shows_one_person(main, person, PORTRAIT):
            rank = self._rank(main, person, born, died, True)
            if rank >= 2:
                from_life = next((file for file in files if file is not main and expected[file.title] == 1
                                  and self._about(file, person) and self._shows_one_person(file, person, CLEAR_PORTRAIT)), None)
                if from_life:
                    return from_life.name, from_life.credit, PictureKind.FROM_LIFE, ""
            return main.name, main.credit, kind(main, rank, made_year(main, died) is not None), ""
        others = sorted((file for file in files if file is not main and self._about(file, person)),
                        key=lambda file: (expected[file.title], -file.usage))
        best = None
        for position, file in enumerate(others):
            if self._shows_one_person(file, person, CLEAR_PORTRAIT):
                rank = self._rank(file, person, born, died, False)
                if best is None or rank < best[0]:
                    best = (rank, file)
            remaining = [expected[later.title] for later in others[position + 1:]]
            if best is not None and (not remaining or best[0] <= min(remaining)):
                break
        if best is None:
            return self._lead_picture(person, born, died, "no candidate shows one person")
        return best[1].name, best[1].credit, kind(best[1], best[0], made_year(best[1], died) is not None), ""

    def _lead_picture(self, person: Entity, born: int, died: int, why: str) -> tuple[str, str, PictureKind | None, str]:
        """The English Wikipedia article's lead image, which editors choose to show the person, when nothing else
        will do: not filed as an object, and seen clearly as one person by the image model."""
        if not self.classifier or not person.enwiki:
            return "", "", None, why
        data = self.http.json(WIKIPEDIA_API, {"action": "query", "prop": "pageimages", "piprop": "name", "pilicense": "any",
                                              "titles": person.enwiki, "redirects": 1, "format": "json", "formatversion": 2},
                              namespace="wikipedia")
        name = next((page["pageimage"] for page in data.get("query", {}).get("pages", []) if page.get("pageimage")), None)
        file = self._files(["File:" + name]).get("File:" + name.replace("_", " ")) if name else None
        if not file or not file.portrait or not IMAGE_FILE.search(file.title) \
                or not self._shows_one_person(file, person, CLEAR_PORTRAIT):
            return "", "", None, why
        rank = self._rank(file, person, born, died, False)
        return file.name, file.credit, kind(file, rank, made_year(file, died) is not None), ""

    def _shows_one_person(self, file: CommonsFile, person: Entity, bar: float) -> bool:
        """One person to the image model; without the model, the main image or a portrait or painting category named
        after the person."""
        if self.classifier:
            return self.classifier.score(file.name)[1] >= bar
        return about_by_category(file, person) or "File:" + file.name in {
            "File:" + claim["mainsnak"]["datavalue"]["value"].replace("_", " ") for claim in person.statements("P18")}

    def _about(self, file: CommonsFile, person: Entity) -> bool:
        """About the person by a category, or by a full-name file name when the model sees a photograph, since works of
        art carry their artist's name."""
        if about_by_category(file, person):
            return True
        return named_after(file, person) and self.classifier is not None and self.classifier.score(file.name)[0] >= WITH_EVIDENCE

    def _rank(self, file: CommonsFile, person: Entity, born: int, died: int, main: bool) -> int:
        """What the file is: with the model, as _actual; without it, what Commons says (_expected)."""
        if not self.classifier:
            return self._expected(file, person, born, died, main)
        return self._actual(file, person, born, died, main, self.classifier.score(file.name)[0])

    def _expected(self, file: CommonsFile, person: Entity, born: int, died: int, main: bool) -> int:
        """The best a file can be, from Commons alone: 0 a photograph, 1 made in the person's lifetime, 2 within 50
        years of their death, 3 anything else."""
        made = made_year(file, died)
        if died >= PHOTO_ERA and (self._photographic_category(file, person, main)
                                  or (made is not None and max(born, PHOTOGRAPHY_INVENTED) <= made <= died)):
            return 0
        if made is not None and born <= made <= died + FROM_LIFE_YEARS:
            return 1
        if made is not None and born <= made <= died + EARLY_YEARS:
            return 2
        return 3

    def _actual(self, file: CommonsFile, person: Entity, born: int, died: int, main: bool, photo: float) -> int:
        """What the file is once the model has seen it: a photograph only when the model is sure on its own, or fairly
        sure and Commons agrees, as for the Photo card; otherwise its date decides."""
        made = made_year(file, died)
        evidence = self._photographic_category(file, person, main) or (
            made is not None and max(born, PHOTOGRAPHY_INVENTED) <= made <= died)
        if died >= PHOTO_ERA and (photo >= ALONE or (photo >= WITH_EVIDENCE and evidence)):
            return 0
        return max(1, self._expected(file, person, born, died, main))

    def photograph(self, person: Entity) -> tuple[Photo | None, str]:
        """A photograph of the person for the Photo card. Candidates are the person's Wikidata images and the files
        in their own Commons category, tried in order: Wikidata's first, then the files most used across Wikipedia's
        articles. A candidate is a photograph when the model is sure on its own (0.9), or fairly sure (0.7) and
        Commons agrees; without the model, a photographic Commons category must say so."""
        births, deaths = person.years("P569"), person.years("P570")
        if not deaths or deaths[0].value < PHOTO_ERA:
            return None, f"no death year in or after {PHOTO_ERA}"
        lifetime = (births[0].earliest if births else deaths[0].latest - 100, deaths[0].latest)
        main = {"File:" + claim["mainsnak"]["datavalue"]["value"].replace("_", " ") for claim in person.statements("P18")}
        checked = []
        for file in self._candidates(person)[:MAX_CANDIDATES]:
            strong = self._photographic_category(file, person, file.title in main)
            weak = self._supporting(file, lifetime)
            if self.classifier:
                score, one_person = self.classifier.score(file.name)
                evidence = strong or weak
                found = f"model {score:.2f}, one person {one_person:.2f}" + (f", {evidence}" if evidence else "")
                if one_person >= PORTRAIT and (score >= ALONE or (score >= WITH_EVIDENCE and evidence)):
                    return Photo(file.name, file.credit, found), ""
                checked.append(f"{file.name}: {found}")
            elif strong:
                return Photo(file.name, file.credit, strong), ""
            else:
                checked.append(f"{file.name}: {weak or 'no photographic category'}")
        return None, "; ".join(checked) or "no candidate image"

    def _candidates(self, person: Entity) -> list[CommonsFile]:
        main = [claim["mainsnak"]["datavalue"]["value"] for claim in person.statements("P18", preferred_only=False)]
        category = [claim["mainsnak"]["datavalue"]["value"] for claim in person.statements("P373")][:1]
        own = self._category_files(category[0]) if category else []
        files = self._files(["File:" + name for name in main] + own)
        main_titles = {"File:" + name.replace("_", " ") for name in main}
        ranked = sorted(files.values(), key=lambda file: (file.title not in main_titles, -file.usage))
        return [file for file in ranked if file.portrait and IMAGE_FILE.search(file.title)]

    def _category_files(self, category: str) -> list[str]:
        params = {"action": "query", "list": "categorymembers", "cmtitle": f"Category:{category}", "cmtype": "file",
                  "cmlimit": "max", "format": "json", "formatversion": 2}
        members = self.http.json(COMMONS_API, params, namespace="commons").get("query", {}).get("categorymembers", [])
        return [member["title"] for member in members]

    def _files(self, titles: list[str]) -> dict[str, CommonsFile]:
        """Metadata, visible categories and the number of Wikipedia articles using each file."""
        found: dict[str, dict] = {}
        unique = list(dict.fromkeys(title.replace("_", " ") for title in titles))
        for batch in url_batches(unique):
            params = {"action": "query", "titles": "|".join(batch),
                      "prop": "imageinfo|categories|globalusage", "iiprop": "extmetadata", "clshow": "!hidden",
                      "cllimit": "max", "gunamespace": 0, "gulimit": "max", "format": "json", "formatversion": 2}
            while True:
                data = self.http.json(COMMONS_API, params, namespace="commons")
                for page in data.get("query", {}).get("pages", []):
                    if "missing" in page:
                        continue
                    entry = found.setdefault(page["title"], {"page": page, "categories": [], "usage": 0})
                    entry["categories"] += [category["title"] for category in page.get("categories", [])]
                    entry["usage"] += len(page.get("globalusage", []))
                    if "imageinfo" in page:
                        entry["page"] = page
                if "continue" not in data:
                    break
                params = {**params, **data["continue"]}
        files = {}
        for title, entry in found.items():
            metadata = entry["page"].get("imageinfo", [{}])[0].get("extmetadata", {})
            artist = visible(metadata.get("Artist", {}).get("value", ""))
            licence = metadata.get("LicenseShortName", {}).get("value", "")
            files[title] = CommonsFile(title, entry["page"].get("pageid"), entry["categories"],
                                       plain(metadata.get("DateTimeOriginal", {}).get("value", "")),
                                       " · ".join(part for part in (artist, licence) if part), entry["usage"])
        return files

    @staticmethod
    def _photographic_category(file: CommonsFile, person: Entity, main: bool) -> str:
        """A Commons category that names a photographic process, when none names a painting, print or sculpture. For
        files other than the person's main Wikidata image it must also be about portraits or about the person, since
        their Commons category also holds photographs of their house, grave or writings."""
        if any(NOT_PHOTOGRAPHIC.search(category) for category in file.categories):
            return ""
        name = (person.label or "").casefold()
        for category in file.categories:
            if PHOTOGRAPHIC.search(category) and (main or "portrait" in category.casefold() or name in category.casefold()):
                return f"in {category}"
        return ""

    def _supporting(self, file: CommonsFile, lifetime: tuple[int, int]) -> str:
        """Weaker signs, which only back the model: Commons classifies the file as a photograph, which it also does
        for photographs of paintings, or it was captured after 1839 during the person's lifetime, which also holds for
        prints of the time."""
        if any(NOT_PHOTOGRAPHIC.search(category) for category in file.categories):
            return ""
        if self._classified(file):
            return "classified as a photograph on Commons"
        year = capture_year(file.captured)
        if year is not None and max(lifetime[0], PHOTOGRAPHY_INVENTED) <= year <= lifetime[1]:
            return f"captured {year}, during their lifetime"
        return ""

    def _classified(self, file: CommonsFile) -> bool:
        if not file.page_id:
            return False
        media = self.http.json(COMMONS_API, {"action": "wbgetentities", "ids": f"M{file.page_id}", "format": "json"},
                               namespace="commons")
        statements = media.get("entities", {}).get(f"M{file.page_id}", {}).get("statements") or {}
        kinds = [claim["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for claim in statements.get("P31", [])]
        return PHOTOGRAPH in kinds


def share(image: np.ndarray, prompts: np.ndarray, first: int) -> float:
    """The softmax weight of the first descriptions among all of them, at CLIP's usual scale of 100."""
    logits = 100 * image @ prompts.T
    weights = np.exp(logits - logits.max())
    return float(weights[0, :first].sum() / weights.sum())


def output(session, outputs: list, name: str) -> np.ndarray:
    return outputs[[meta.name for meta in session.get_outputs()].index(name)]


def normalised(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def digest(file: str) -> str:
    return hashlib.sha1(file.encode()).hexdigest()[:16]


def capture_year(captured: str) -> int | None:
    """The year from a Commons capture date: its machine-readable form ('QS:P571,+1863-11-08…') when present,
    otherwise the first three- or four-digit number ('8 November 1863', 'circa 1890', '1922年11月15日')."""
    match = MACHINE_DATE.search(captured) or HUMAN_YEAR.search(captured)
    return int(match.group(1)) if match else None


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def visible(html: str) -> str:
    """The text a reader sees: hidden copies ('Unknown author' twice), footnote marks ('[1]') with a later clause citing
    them ('unknown; according to [1] …'), links to a user's talk page and the file names of derivative works
    ('Russell.jpg: …') dropped, and entities decoded."""
    first, *rest = markup.unescape(plain(HIDDEN.sub("", html))).split(";")
    text = ";".join([first, *(clause for clause in rest if not FOOTNOTE.search(clause))])
    text = FILE_NAME.sub("", re.sub(r"\(\s*(?:talk|discuss)?\s*\)", "", FOOTNOTE.sub("", text)))
    text = re.sub(r"([^\s()][^()]*?) \(\s*\1\s*\)", r"\1", re.sub(r"\bUser:", "", text))
    return re.sub(r"\s+", " ", text).strip()


def life_span(person: Entity) -> tuple[int, int]:
    """The person's first and last possible year, assuming a 70-year life where one end is unknown."""
    births, deaths = person.years("P569"), person.years("P570")
    born = min((year.earliest for year in births), default=None)
    died = max((year.latest for year in deaths), default=None)
    if born is None and died is None:
        return 0, 0
    return (born if born is not None else died - ASSUMED_LIFESPAN), (died if died is not None else born + ASSUMED_LIFESPAN)


def kind(file: CommonsFile, rank: int, file_dated: bool) -> PictureKind:
    """A photograph, a portrait from life, or a later depiction when its date says so; undated art is a depiction."""
    if rank == 0:
        return PictureKind.PHOTOGRAPH
    if rank == 1:
        return PictureKind.FROM_LIFE
    return PictureKind.LATER if file_dated else PictureKind.UNDATED


def names(person: Entity) -> set[str]:
    categories = [claim["mainsnak"]["datavalue"]["value"] for claim in person.statements("P373")]
    return {name.casefold() for name in [person.label or "", *categories] if name}


def about_by_category(file: CommonsFile, person: Entity) -> bool:
    """Filed on Commons as a portrait, painting, drawing, engraving or photograph of the person, by their name or their
    own Commons category's name."""
    return any(PORTRAIT_CATEGORY.search(category) and any(name in category.casefold() for name in names(person))
               for category in file.categories)


def named_after(file: CommonsFile, person: Entity) -> bool:
    """The file name holds the person's full name ('Victor Hugo by Étienne Carjat')."""
    title = file.name.replace("_", " ").casefold()
    return any(name in title for name in names(person))


def made_year(file: CommonsFile, died: int) -> int | None:
    """When the picture was made, by its Commons date; for someone who died before 1900, a date from 1950 on is when
    the artwork was photographed, not made, so it is unknown."""
    year = capture_year(file.captured)
    if year is not None and died < MODERN_ERA and year >= PHOTOGRAPHED_AFTER:
        return None
    return year
