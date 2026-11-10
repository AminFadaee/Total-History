import hashlib
import json
import logging
import os
import pathlib
import re
import unicodedata

import requests

from page_in_history.sentences import sentences

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA = OLLAMA if OLLAMA.startswith("http") else f"http://{OLLAMA}"
MODEL = "qwen3.5:4b"
MODEL_DIGEST = "d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04"
MIN_SCORE = 8
MAX_MENTIONS = 2
DERIVED = re.compile(r"(?:ian|ians|ean|eans|ist|ists|ism|isms|ismo|ista|istas|ismus|ic|ics|ite|ites|esque)$")
TAGGED = re.compile(r"citation needed|reference demanded|clarification needed|according to whom|by whom|dubious|"
                    r"pronunciation|\[|[^\x00-\u024f\u1e00-\u1eff\u2010-\u206f]", re.IGNORECASE)
NAME_LIST = re.compile(r"this person\W+(?:\w+\W+){0,2}this person|\bas this person\b|"
                       r"this person,? (?:or|also known as|known as|born|né|née) [A-Z]|this person of [A-Z]|"
                       r"pen.?name|referred to as|also called|nicknamed", re.IGNORECASE)
MIN_WORDS = 6
STEM_LETTERS = 5
PROGRESS_EVERY = 500
SECTIONS = re.compile(r"legacy|assessment|reputation|historiography|achievements|significance|influence|evaluation|"
                      r"historical (?:view|perception|image)|memory|commemoration", re.IGNORECASE)
TITLES = {"of", "the", "de", "da", "di", "von", "van", "der", "al", "el", "ibn", "bin", "bint", "ben", "and", "le", "la",
          "emperor", "empress", "king", "queen", "saint", "st", "pope", "sultan", "khan", "prince", "princess", "duke",
          "duchess", "count", "lord", "sir", "lady", "mansa", "shah", "tsar", "caliph", "pharaoh", "great", "elder",
          "younger"}
COMMON = {"as", "is", "an", "in", "on", "at", "to", "by", "or", "he", "she", "it", "we", "us", "no", "so", "do", "up"}
NUMERAL = re.compile(r"^(?:[IVXL]+|\d+(?:st|nd|rd|th)?)$")
WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|['’-][^\W\d_])*'?s?|[^\W\d_]+", re.UNICODE)
OPENING_NAME = re.compile(r"^(.+?)\s*(?:\(|,| was | is | were )")
ALSO_KNOWN = re.compile(r"(?:also known as|known as|born|or)\s+([A-Z][^,;()]+)")
PROMPT = """Rate how well this sentence from a Wikipedia introduction says what the person is remembered for: their lasting contribution or historical significance (works, discoveries, founding, conquests, reforms, firsts, movements).
Low scores (0-3): identity only ("A Swedish writer"), offices and dates, family, birth or death circumstances, trivia.
High scores (8-10): the achievement or significance itself.

Person: {name} ({role})
Sentence: {sentence}

Answer with a single integer from 0 to 10 and nothing else."""

logger = logging.getLogger(__name__)


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def stem(word: str) -> str:
    return fold(re.sub(r"['’]s?$", "", word))[:STEM_LETTERS]


def name_forms(names: list[str], opening: str) -> list[str]:
    """The person's names: those given, the name the article opens with, and what its first sentence calls them besides
    ('also known as Emperor Wu'). A territorial 'of …' after a regnal number or title is not part of the name (Catherine
    I of Russia), but it is in Joan of Arc."""
    forms = list(names)
    first = sentences(opening)[0] if opening else ""
    if found := OPENING_NAME.match(first):
        forms.append(found.group(1))
    forms += [part.strip() for match in ALSO_KNOWN.finditer(first.split(" was ")[0])
              for part in re.split(r"\s+and\s+(?:as\s+)?|\s+or\s+|,", match.group(1))]
    trimmed = []
    for form in forms:
        form = re.sub(r"\s*\(.*?\)", "", form)
        head, _, tail = form.partition(" of ")
        words = head.split()
        trimmed.append(head if tail and (NUMERAL.match(words[-1]) or fold(words[0]) in TITLES) else form)
    return list(dict.fromkeys(form for form in trimmed if form.strip()))


def hide(sentence: str, names: list[str]) -> str | None:
    """The sentence with the person replaced by 'this person', or None when any part of their name would remain. A run
    of their name words (with joining words and a regnal number inside it) becomes one mention; title words alone, such
    as 'Emperor' in another ruler's title, are left as they are, and a title just before or after the name goes with it
    ('King Sweyn's', 'Zahir Shah'). A name that names something else (Rákóczi's War of Independence, the Tokugawa
    shogunate), a word made from it (Zoroastrian) or holding it (Bâkî in Abdülbâkî), a sentence that lists the person's
    names or gives a pronunciation, and one Wikipedia tags as unsourced are not used."""
    words = {fold(part) for form in names for word in WORD.findall(form) for part in [word, *re.split(r"[-’']", word)]
             if fold(part) not in TITLES | COMMON and len(part) > 1}
    stems = {word[:STEM_LETTERS] for word in words}
    for token in WORD.findall(sentence):
        bare = fold(re.sub(r"['’]s?$", "", token))
        if bare[:STEM_LETTERS] in stems and bare not in words and DERIVED.search(bare):
            return None
    tokens = list(re.finditer(r"\S+", sentence))
    out, index = [], 0
    while index < len(tokens):
        word = re.sub(r"^\W+|\W+$", "", tokens[index].group())
        if word and stem(word) in stems:
            end, possessive = index, False
            while end + 1 < len(tokens):
                following = re.sub(r"^\W+|\W+$", "", tokens[end + 1].group())
                if following and (stem(following) in stems or NUMERAL.match(following)
                                  or (fold(re.sub(r"['’]s?$", "", following)) in TITLES and following[:1].isupper())):
                    end += 1
                else:
                    break
            last = tokens[end].group()
            possessive = bool(re.search(r"['’]s?\W*$", last))
            if possessive and end + 1 < len(tokens) and tokens[end + 1].group()[:1].isupper():
                return None
            trailing = re.search(r"[^\w'’]*$", last).group()
            if out and fold(out[-1]) == "the":
                return None
            if out and fold(re.sub(r"\W", "", out[-1])) in TITLES and out[-1][:1].isupper():
                out.pop()
            out.append(("this person's" if possessive else "this person") + trailing)
            index = end + 1
        else:
            out.append(tokens[index].group())
            index += 1
    text = " ".join(out)
    if text.count("this person") > MAX_MENTIONS or NAME_LIST.search(text) or TAGGED.search(text):
        return None
    if any(word in bare for word in words if len(word) > 3 for bare in (fold(token) for token in WORD.findall(text))
           if word != bare and bare not in ("this", "person")):
        return None
    if any(stem(word) in stems for word in WORD.findall(text) if fold(word) not in ("this", "person")):
        return None
    text = text[0].upper() + text[1:]
    return re.sub(r"(?<=[.!?] )this person", "This person", text)


class LegacyScores:
    """The model's scores of how well each sentence says what a person is remembered for, kept with the data so a build
    gives the same legacy lines on any machine and the model reads only sentences it has not read before. The model is
    qwen3.5:4b through Ollama, pinned to one digest, at temperature 0; OLLAMA_HOST points to another machine's Ollama."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.saved: dict[str, int] = json.loads(path.read_text()) if path.exists() else {}
        self.used: dict[str, int] = {}
        self.checked = False
        self.read = 0

    def score(self, name: str, role: str, sentence: str) -> int:
        key = hashlib.sha1(f"{name}\n{role}\n{sentence}".encode()).hexdigest()
        if key not in self.saved:
            self._check_model()
            self.saved[key] = self._ask(name, role, sentence)
            self.read += 1
            if self.read % PROGRESS_EVERY == 0:
                logger.info("Scored %d new sentences for legacy", self.read)
        self.used[key] = self.saved[key]
        return self.used[key]

    def _check_model(self) -> None:
        if self.checked:
            return
        models = requests.get(f"{OLLAMA}/api/tags", timeout=30).json()["models"]
        if not any(model["name"] == MODEL and model["digest"] == MODEL_DIGEST for model in models):
            raise RuntimeError(f"Ollama needs {MODEL} at digest {MODEL_DIGEST[:12]} to score legacy lines")
        self.checked = True

    @staticmethod
    def _ask(name: str, role: str, sentence: str) -> int:
        response = requests.post(f"{OLLAMA}/api/chat", timeout=300, json={
            "model": MODEL, "stream": False, "think": False, "keep_alive": "5m",
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 6},
            "messages": [{"role": "user", "content": PROMPT.format(name=name, role=role, sentence=sentence)}]})
        found = re.search(r"\d+", response.json()["message"]["content"])
        return min(int(found.group()), 10) if found else 0

    def save(self) -> None:
        self.path.write_text(json.dumps(dict(sorted(self.used.items())), indent=0) + "\n")
        logger.info("Kept the legacy scores of %d sentences", len(self.used))


def best_line(scores: LegacyScores, name: str, role: str, names: list[str], texts: list[str]) -> str:
    """The best-scored sentence that says what the person is remembered for, from the first text that has one (the
    article's introduction, then its legacy sections), with the person hidden; empty when none scores at least 5 or
    every good one would give the name away."""
    for text in texts:
        candidates = [sentence for sentence in sentences(re.sub(r"\s*\n+\s*", " ", text)) if len(sentence.split()) >= MIN_WORDS]
        ranked = sorted(((scores.score(name, role, sentence), -index, sentence) for index, sentence in enumerate(candidates)),
                        reverse=True)
        for score, _, sentence in ranked:
            if score < MIN_SCORE:
                break
            if hidden := hide(sentence, names):
                return hidden
    return ""


def legacy_sections(text: str) -> str:
    """The sections of a plain-text article whose heading speaks of legacy, assessment, reputation or the like."""
    parts = re.split(r"^(=+)\s*(.*?)\s*=+\s*$", text, flags=re.MULTILINE)
    return "\n".join(parts[index + 2] for index in range(1, len(parts) - 2, 3) if SECTIONS.search(parts[index + 1]))


def add_legacy(figures: list, wikipedia, scores: LegacyScores) -> None:
    """Each figure's legacy line: the best sentence of their article's introduction, or else of its legacy sections,
    with the person hidden."""
    introductions = wikipedia.introductions([figure.wikipedia for figure in figures])
    unfound = []
    for figure in figures:
        introduction = introductions.get(figure.wikipedia, ("", None))[0]
        names = name_forms([figure.name, figure.wikipedia], introduction)
        figure.legacy = best_line(scores, figure.name, figure.role, names, [introduction])
        if not figure.legacy:
            unfound.append((figure, names))
    articles = wikipedia.article_texts([figure.wikipedia for figure, _ in unfound])
    for figure, names in unfound:
        figure.legacy = best_line(scores, figure.name, figure.role, names, [legacy_sections(articles.get(figure.wikipedia, ""))])
    logger.info("Found a legacy line for %d of %d figures", sum(bool(figure.legacy) for figure in figures), len(figures))
