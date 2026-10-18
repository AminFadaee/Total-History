import contextlib
import hashlib
import json
import logging
import pathlib
import re
import unicodedata
from dataclasses import dataclass

import mwparserfromhell as mw
import numpy as np

from page_in_history.http import Http

logger = logging.getLogger(__name__)

REVISION = "80a99030ce45a69a39ea2a6f50756d03859ff521"
REPOSITORY = f"https://huggingface.co/Xenova/nli-deberta-v3-base/resolve/{REVISION}/{{}}"
FILES = {
    "model.onnx": ("onnx/model.onnx", "2c342d0a5c945a9ce3710a69f8150d8f575893ebcfe5c5a4c3ca8fbf31fddd06"),
    "tokenizer.json": ("tokenizer.json", "a86f883318afa11c8c10466f1bf4efaeb6ded28a52cbe57217a8fa0d0a2a87df"),
    "config.json": ("config.json", "cedce9043e58e52f0f6fc31e8c87ec518092a742851e80eddbb651d36a0c754f"),
}
MAX_TOKENS = 256
BATCH_PAIRS = 16
PROGRESS_EVERY = 500
PREMISES_PER_SOURCE = 2
CHUNK_WORDS, WINDOW_WORDS = 160, 90
MIN_SENTENCE, MIN_SECTION, MIN_NAME = 20, 40, 3
STRIPPED_NAME = 4
SUCCESSION, PREDECESSION = 0, 1
HYPOTHESES = {
    "after": ("{x} was succeeded by {y}.", "{y} conquered {x}.", "{y} came after {x} and took over its place.",
              "{y} existed before {x} and was replaced by {x}.",
              "{y} and {x} existed at the same time, as neighbours, rivals, allies or parts of one another."),
    "before": ("{x} succeeded {y}.", "{x} was formed from {y}.", "{y} came after {x} and took over its place.",
               "{y} existed before {x} and was replaced by {x}.",
               "{y} and {x} existed at the same time, as neighbours, rivals, allies or parts of one another."),
}
DROPPED_SECTIONS = re.compile(r"^(see also|references|notes|footnotes|external links|further reading|bibliography|sources"
                              r"|citations|gallery|works cited)$", re.IGNORECASE)
DROPPED_TAGS = {"ref", "gallery", "table", "math", "imagemap", "timeline"}
DROPPED_LINKS = ("file:", "image:", "category:")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“\[(])")
DISAMBIGUATOR = re.compile(r"\s*\([^)]*\)\s*$")
KIND_WORDS = re.compile(r"\b(kingdom|empire|dynasty|khaganate|khanate|sultanate|state|states|confederation|republic|caliphate"
                        r"|principality|duchy|county|period|the|of|and|early|late|great|new|old|first|second|third|era|rule"
                        r"|province|governorate)\b", re.IGNORECASE)
LATER_SECTIONS = re.compile(r"declin|fall|end|aftermath|legacy|collaps|dissol|conquest|success|disintegr|demise|decay|fragment"
                            r"|destruction|downfall|later|post|partition|annex|absorb|abolition|overthrow|invasion|defeat"
                            r"|division|break|final", re.IGNORECASE)
EARLIER_SECTIONS = re.compile(r"origin|background|rise|found|format|establish|early|emerg|predecess|prehist|beginning"
                              r"|creation|unif|independen|history|antecedent|prelude|genesis|conquest|ancestry|before"
                              r"|foundation", re.IGNORECASE)
CUES = re.compile(r"succeed|follow|replac|conquer|fell|fall|annex|absorb|overthr|collaps|destroy|found|establish|emerg|split"
                  r"|inherit|took over|take over|after|declin|defeat|captur|ended|successor|predecessor|heir|vassal|dissol"
                  r"|unif|formed|rose|rise|began|begin|ruled|rule|control|invad|overran|subjug|incorporat|abolish|ceased"
                  r"|former|previous", re.IGNORECASE)


def softmax(values) -> np.ndarray:
    exponentials = np.exp(np.asarray(values) - np.max(values))
    return exponentials / exponentials.sum()


class SuccessionModel:
    """A natural-language-inference model (DeBERTa v3 base trained on MultiNLI and SNLI; the fp32 ONNX export pinned
    to one revision and checked against its hash, run with onnxruntime on the CPU) that gives the logits of
    entailment, neutral and contradiction for a premise and a hypothesis."""

    def __init__(self, http: Http):
        import onnxruntime
        from tokenizers import Tokenizer

        files = {name: http.download(REPOSITORY.format(path), f"nli-deberta-v3-base-{REVISION[:12]}-{name}", sha256)
                 for name, (path, sha256) in FILES.items()}
        self.tokenizer = Tokenizer.from_file(str(files["tokenizer.json"]))
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.tokenizer.enable_truncation(MAX_TOKENS)
        labels = {label.lower(): int(index) for index, label in json.loads(files["config.json"].read_text())["id2label"].items()}
        self.order = [labels["entailment"], labels["neutral"], labels["contradiction"]]
        self.session = onnxruntime.InferenceSession(str(files["model.onnx"]), providers=["CPUExecutionProvider"])
        self.inputs = {node.name for node in self.session.get_inputs()}

    def logits(self, pairs: list[tuple[str, str]]) -> list[list[float]]:
        """Entailment, neutral and contradiction logits of each premise and hypothesis, the shorter pairs batched together."""
        found: list[list[float]] = [[] for _ in pairs]
        by_length = sorted(range(len(pairs)), key=lambda index: len(pairs[index][0]) + len(pairs[index][1]))
        for start in range(0, len(by_length), BATCH_PAIRS):
            batch = by_length[start : start + BATCH_PAIRS]
            encoded = self.tokenizer.encode_batch([pairs[index] for index in batch])
            feed = {"input_ids": np.array([item.ids for item in encoded], dtype=np.int64),
                    "attention_mask": np.array([item.attention_mask for item in encoded], dtype=np.int64)}
            output = self.session.run(None, {key: value for key, value in feed.items() if key in self.inputs})[0]
            for index, row in zip(batch, output[:, self.order]):
                found[index] = [round(float(logit), 4) for logit in row]
        return found


class SuccessionScores:
    """The model's logits kept with the data, one per civilization, candidate, text read and hypothesis, so a build
    gives the same answers on any machine and the model runs only for texts it has not read before."""

    def __init__(self, path: pathlib.Path, http: Http):
        self.path = path
        self.http = http
        self.saved: dict[str, list[float]] = json.loads(path.read_text()) if path.exists() else {}
        self.used: dict[str, list[float]] = {}
        self.model: SuccessionModel | None = None
        self.read = 0

    def logits(self, civilization: str, candidate: str, pairs: list[tuple[str, str]]) -> list[list[float]]:
        keys = [hashlib.sha1(f"{REVISION}\n{premise}\n{civilization}\n{candidate}\n{hypothesis}".encode()).hexdigest()
                for premise, hypothesis in pairs]
        missing = [(key, pair) for key, pair in zip(keys, pairs) if not isinstance(self.saved.get(key), list)]
        if missing:
            self.model = self.model or SuccessionModel(self.http)
            for (key, _), logits in zip(missing, self.model.logits([pair for _, pair in missing])):
                self.saved[key] = logits
            self.read += len(missing)
            if self.read // PROGRESS_EVERY != (self.read - len(missing)) // PROGRESS_EVERY:
                logger.info("Read %d new texts", self.read)
        self.used.update((key, self.saved[key]) for key in keys)
        return [self.saved[key] for key in keys]

    def save(self) -> None:
        self.path.write_text(json.dumps(dict(sorted(self.used.items())), indent=0) + "\n")
        logger.info("Kept the model's reading of %d texts", len(self.used))


def plain_sections(wikitext: str) -> list[tuple[str, str]]:
    """The article's sections as heading and plain text, the lead under an empty heading, without its reference sections."""
    code = mw.parse(re.sub(r"<!--.*?-->", "", wikitext, flags=re.DOTALL))
    sections = []
    for section in code.get_sections(flat=True, include_lead=True, include_headings=True):
        headings = section.filter_headings(recursive=False)
        heading = str(headings[0].title).strip() if headings and str(section).lstrip().startswith("=") else ""
        if heading:
            section = mw.parse(str(section).split("\n", 1)[1] if "\n" in str(section) else "")
        text = plain_text(section)
        if not DROPPED_SECTIONS.match(heading) and len(text) > MIN_SECTION:
            sections.append((heading, text))
    return sections


def plain_text(code) -> str:
    dropped = [*code.filter_templates(recursive=False), *(tag for tag in code.filter_tags() if str(tag.tag).lower() in DROPPED_TAGS),
               *(link for link in code.filter_wikilinks() if str(link.title).strip().lower().startswith(DROPPED_LINKS))]
    for node in dropped:
        with contextlib.suppress(ValueError):
            code.remove(node)
    text = re.sub(r"\[\[|\]\]|'''?|\{\|.*?\|\}", "", code.strip_code(normalize=True, collapse=True), flags=re.DOTALL)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*[*#:;]\s*", "\n", text)).strip()


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()).strip()


def chunks(sentences: list[str]) -> list[str]:
    """Runs of consecutive sentences of about 160 words."""
    found, run, words = [], [], 0
    for sentence in sentences:
        if run and words + len(sentence.split()) > CHUNK_WORDS:
            found.append(" ".join(run))
            run, words = [], 0
        run.append(sentence)
        words += len(sentence.split())
    return found + [" ".join(run)] if run else found


def cues(text: str) -> int:
    return len(CUES.findall(text))


def pattern(names: set[str]) -> re.Pattern | None:
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)) + r")(?!\w)",
                      re.IGNORECASE) if names else None


@dataclass(frozen=True)
class Article:
    """An English Wikipedia article read as sentences, each under the heading of its section."""
    title: str
    wikitext: str
    sentences: list[tuple[str, str]]

    @classmethod
    def read(cls, title: str, wikitext: str) -> "Article":
        sentences = [(heading, sentence.strip()) for heading, text in plain_sections(wikitext)
                     for line in text.split("\n") if len(line.strip()) >= MIN_SENTENCE
                     for sentence in SENTENCE_SPLIT.split(line.strip()) if len(sentence.strip()) > MIN_SENTENCE]
        return cls(title, wikitext, sentences)

    def names(self, label: str, other: "Article") -> tuple[set[str], set[str]]:
        """What the article is called: its label and title, without the disambiguator, and the words the other article
        links it by; and those names without the kind of state ("Songhai" for the Songhai Empire)."""
        linked = {display.strip() for target, display in re.findall(r"\[\[([^\]|#]+)(?:#[^\]|]*)?\|([^\]]*)\]\]", other.wikitext)
                  if fold(target) == fold(self.title) and len(display.strip()) > MIN_NAME - 1}
        names = {name for name in {label, self.title, DISAMBIGUATOR.sub("", self.title), DISAMBIGUATOR.sub("", label)} | linked
                 if len(name) >= MIN_NAME}
        stripped = {re.sub(r"\s+", " ", KIND_WORDS.sub("", name)).strip(" ,-–") for name in names}
        return names, {name for name in stripped if len(name) >= STRIPPED_NAME} - names


@dataclass(frozen=True)
class Premise:
    text: str
    civilization: str
    candidate: str


def premises(side: str, civilization: Article, civilization_name: str, candidate: Article,
             candidate_label: str) -> tuple[list[Premise], list[Premise]]:
    """Two kinds of passage from the civilization's article: where it mentions the candidate (the sentence with its
    neighbours, those naming both first, then those with most words of succession), and the passages of its history, or
    its lead, that name the candidate; two of each."""
    candidate_names, candidate_stripped = candidate.names(candidate_label, civilization)
    own_names, own_stripped = civilization.names(civilization_name, candidate)
    own, other, strict = pattern(own_names | own_stripped), pattern(candidate_names | candidate_stripped), pattern(candidate_names)
    own_display = DISAMBIGUATOR.sub("", civilization.title)
    own_display = own_display if len(own_display) > MIN_NAME else civilization_name
    other_display = DISAMBIGUATOR.sub("", candidate.title)
    first = lambda expression, text, default: match.group(0) if expression and (match := expression.search(text)) else default
    sentences = [sentence for _, sentence in civilization.sentences]
    mentions = sorted(((bool(own and own.search(sentence)), cues(sentence), index) for index, sentence in enumerate(sentences)
                       if other and other.search(sentence)), key=lambda mention: (-mention[0], -mention[1]))
    mentioned: dict[str, Premise] = {}
    for _, _, index in mentions:
        mentioned.setdefault(window(sentences, index), Premise(window(sentences, index), first(own, sentences[index], own_display),
                                                              first(other, sentences[index], other_display)))
    later = LATER_SECTIONS if side == "after" else EARLIER_SECTIONS
    history = chunks([sentence for heading, sentence in civilization.sentences if heading and later.search(heading)])
    lead = chunks([sentence for heading, sentence in civilization.sentences if not heading])
    passages = sorted((text for text in history + lead if strict and strict.search(text)), key=lambda text: -cues(text))
    return (list(mentioned.values())[:PREMISES_PER_SOURCE],
            [Premise(text, own_display, other_display) for text in passages[:PREMISES_PER_SOURCE]])


def window(sentences: list[str], index: int) -> str:
    """The sentence with the next, the previous and the one after, as many as fit in 90 words."""
    taken, words = {index}, len(sentences[index].split())
    for neighbour in (index + 1, index - 1, index + 2):
        if 0 <= neighbour < len(sentences) and words + len(sentences[neighbour].split()) <= WINDOW_WORDS:
            taken.add(neighbour)
            words += len(sentences[neighbour].split())
    return " ".join(sentences[position] for position in sorted(taken))


def text_score(side: str, scores: SuccessionScores, civilization: Article, civilization_name: str, candidate: Article,
               candidate_label: str) -> float:
    """How far the civilization's article says the candidate followed (preceded) it: for each passage, the mean of two
    readings, that the candidate is stated to follow it and the share of "came after" among the ways the two can be
    related; the best passage of each kind, the two kinds averaged (a kind with no passage reads zero)."""
    best = []
    for kind in premises(side, civilization, civilization_name, candidate, candidate_label):
        pairs = [(premise.text, hypothesis.format(x=premise.civilization, y=premise.candidate))
                 for premise in kind for hypothesis in HYPOTHESES[side]]
        logits = scores.logits(civilization_name, candidate_label, pairs)
        rows = [logits[start : start + len(HYPOTHESES[side])] for start in range(0, len(logits), len(HYPOTHESES[side]))]
        best.append(max((reading(side, row) for row in rows), default=0.0))
    return float(np.mean(best))


def reading(side: str, logits: list[list[float]]) -> float:
    stated = np.mean([softmax(row)[0] for row in logits[:2]])
    related = softmax([row[0] for row in logits[2:]])[SUCCESSION if side == "after" else PREDECESSION]
    return float((stated + related) / 2)
