import hashlib
import json
import logging
import os
import pathlib
import re
import unicodedata
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

from total_history.sentences import LEANING, sentences, words

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA = OLLAMA if OLLAMA.startswith("http") else f"http://{OLLAMA}"
MODEL = "qwen3.5:4b"
MODEL_DIGEST = "d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04"
WORKERS = 8
MAX_SHARED_WORDS = 0.6
MAX_MENTIONS = 2
DERIVED = re.compile(r"(?:ian|ians|ean|eans|ist|ists|ism|isms|ismo|ista|istas|ismus|ic|ics|ite|ites|esque)$")
TAGGED = re.compile(r"citation needed|reference demanded|clarification needed|according to whom|by whom|dubious|"
                    r"pronunciation|\[|[^\x00-\u024f\u1e00-\u1eff\u2000-\u206f]", re.IGNORECASE)
NAME_LIST = re.compile(r"this person\W+(?:\w+\W+){0,2}this person|\bas this person\b|"
                       r"this person,? (?:or|also known as|known as|born|né|née) [A-Z]|this person of [A-Z]|"
                       r"pen.?name|referred to as|also called|nicknamed", re.IGNORECASE)
MIN_WORDS = 6
POINTING_BACK = re.compile(r"^(?:Thus|Hence|So|Then|Afterwards|After that|Later|Henceforth|Shortly afterwards|Subsequently|"
                           r"Thereafter|In this position|Indeed|Prior to this|This began|During this period)\b|"
                           r"^The (?:event|result|baptism|war|treaty|law|site|regency|flight|reforms|inscriptions|"
                           r"constitution|city|town|meetings?|effort)\b|"
                           r"^(?:Two|Three|Four|Several|A few) (?:days|weeks|months|years) later\b|"
                           r"\b(?:the latter|the former|this same|that same|the result|as a result|these meetings|this effort|"
                           r"during this period)\b", re.IGNORECASE)
UNNAMED_PERSON = re.compile(r"^(?:He|She|His|Her)\b")
STEM_LETTERS = 5
PROGRESS_EVERY = 500
SECTIONS = re.compile(r"legacy|assessment|reputation|historiography|achievements|significance|influence|evaluation|"
                      r"historical (?:view|perception|image)|memory|commemoration", re.IGNORECASE)
TITLES = {"of", "the", "de", "da", "di", "del", "du", "dos", "das", "von", "van", "der", "al", "el", "ibn", "bin", "bint", "ben", "and", "le", "la",
          "emperor", "empress", "king", "queen", "saint", "st", "pope", "sultan", "khan", "prince", "princess", "duke",
          "duchess", "count", "lord", "sir", "lady", "mansa", "shah", "tsar", "caliph", "pharaoh", "great", "elder",
          "younger"}
EPITHETS = {"great", "elder", "younger"}
BROKEN = re.compile(r"(?<!\.[A-Z])(?<!\b[a-z])(?<!\be\.g)(?<!\bi\.e)(?<!\bc)(?<!\bca)(?<!\bvs)\. [a-z]")
HONOUR = re.compile(r"\bin (?:his|her) hono(?:u)?r\b|\bnamed (?:after|for) (?:him|her)\b|\bafter whom\b", re.IGNORECASE)
DETERMINERS = {"a", "an", "his", "her", "its", "their", "our", "own"}
PATRONYMICS = {"ibn", "bin", "ben", "bint", "ap"}
PARTICLES = {"y", "i", "e", "or", "zur", "zum", "dei", "degli", "bat", "ap", "zu", "ter", "ten", "op", "dan", "bin", "binti"}
MAX_NAME_WORDS = 20
NAME_LINKS = {"also", "known", "as", "and", "alias", "born", "in", "called"}
NAMING = {"numbers", "sequence", "syndrome", "wheel", "scale", "effect", "law", "laws", "theorem", "principle", "equation",
          "disease", "prize", "award", "medal", "crater", "method", "doctrine", "school", "era", "dynasty", "style",
          "conjecture", "constant", "paradox", "cycle", "code", "order"}
NICKNAME = re.compile(r"\b(?:nicknamed?|sobriquet|courtesy name|personal name|temple name|posthumous name|regnal name|pen name|"
                      r"poetic title|pseudonym|also spelled|transliterated|romani[sz]ed as|latini[sz]ed as|usually known by|"
                      r"popularly known as|commonly known as|better known as|known by the|(?:sometimes|often|also) (?:styled|titled))\b|^Known as\b", re.IGNORECASE)
APPOSITIVE = re.compile(r"^this person, (?:[A-Z][\w’'.-]*\s?)+,? (?:was|is|were)\b")
ALIAS = re.compile(r"\((?:[^()]*?)\b(?:Latini[sz]ed|romani[sz]ed|anglici[sz]ed|transliterated|also known|known)(?: as)? "
                   r"([A-Z][\w’'-]+(?: [A-Z][\w’'-]+)*)")
NEAR_LETTERS = 5
COMMON = {"as", "is", "an", "in", "on", "at", "to", "by", "or", "he", "she", "it", "we", "us", "no", "so", "do", "up"}
NUMERAL = re.compile(r"^(?:[IVXL]+|\d+(?:st|nd|rd|th)?)$")
WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|['’-][^\W\d_])*'?s?|[^\W\d_]+", re.UNICODE)
OPENING_NAME = re.compile(r"^(.+?)\s*(?:\(|,| was | is | were )")
ALSO_KNOWN = re.compile(r"(?:also known as|known as|born|or)\s+([A-Z][^,;()]+)")
FIGURE_PROMPT = """Rate how well this sentence from a Wikipedia introduction says what the person is remembered for: their lasting contribution or historical significance (works, discoveries, founding, conquests, reforms, firsts, movements).
Low scores (0-3): identity only ("A Swedish writer"), offices and dates, family, birth or death circumstances, trivia.
High scores (8-10): the achievement or significance itself.

Person: {name} ({role})
Sentence: {sentence}

Answer with a single integer from 0 to 10 and nothing else."""
CIVILIZATION_PROMPT = """Rate how well this sentence from a Wikipedia article says what the civilization is remembered for: its lasting achievements or historical significance (inventions, law, institutions, religion, art and monuments, writing and language, science, trade, influence on later peoples, being the largest or first of its kind).
Low scores (0-3): identity only ("A kingdom in West Africa"), founding and end dates, territorial extent, succession of rulers or dynasties, a single event (a coronation, a declaration, a battle, a treaty), one ruler's deeds, wars without lasting result, trivia.
High scores (8-10): the achievement or significance itself, or what later ages inherited from it.

Civilization: {name} ({role})
Sentence: {sentence}

Answer with a single integer from 0 to 10 and nothing else."""


@dataclass(frozen=True)
class Subject:
    prompt: str
    min_score: int
    person: bool


FIGURE = Subject(FIGURE_PROMPT, 8, person=True)
CIVILIZATION = Subject(CIVILIZATION_PROMPT, 7, person=False)
CIVILIZATION_ROLE = "civilization"

logger = logging.getLogger(__name__)


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def stem(word: str) -> str:
    return fold(re.sub(r"['’]s?$", "", word))[:STEM_LETTERS]


def name_forms(names: list[str], opening: str, realms: tuple[str, ...] = ()) -> list[str]:
    """The person's names: those given, the name the article opens with, and what its first sentence calls them besides
    ('also known as Emperor Wu', '(Latinized as Alhazen; …)'). A territorial 'of …' after a regnal number or title, or naming one of the person's own
    realms, is not part of the name (Catherine I of Russia, Taejo of Joseon), but it is in Joan of Arc."""
    forms = list(names)
    first = sentences(opening)[0] if opening else ""
    if found := OPENING_NAME.match(first):
        forms.append(found.group(1))
    forms += [part.strip() for match in ALSO_KNOWN.finditer(first.split(" was ")[0])
              for part in re.split(r"\s+and\s+(?:as\s+)?|\s+or\s+|,", match.group(1))]
    if bracket := re.match(r"[^(.]*\([^()]*\)", opening):
        forms += [match.group(1) for match in ALIAS.finditer(bracket.group())]
    realm = {fold(word)[:STEM_LETTERS] for name in realms for word in WORD.findall(name or "") if fold(word) not in TITLES}
    trimmed = []
    for form in forms:
        form = re.sub(r"\s*\(.*?\)|\s*IPA:\s*\[[^\]]*\]", "", form)
        head, _, tail = form.partition(" of ")
        words = head.split()
        place = {fold(word)[:STEM_LETTERS] for word in WORD.findall(tail) if fold(word) not in TITLES}
        stripped = tail and (NUMERAL.match(words[-1]) or fold(words[0]) in TITLES or (place and place <= realm))
        trimmed.append(head.split(",")[0] if stripped else form)
    return list(dict.fromkeys(form for form in trimmed if form.strip()))


def near(word: str, other: str) -> bool:
    """One letter apart from the same first letter: a spelling of the same name (Ahmed for Ahmad), not another name
    (Janga beside Ganga)."""
    if word == other or word[:1] != other[:1] or abs(len(word) - len(other)) > 1:
        return False
    if len(word) == len(other):
        return sum(a != b for a, b in zip(word, other)) == 1
    short, long = sorted((word, other), key=len)
    return any(long[:index] + long[index + 1:] == short for index in range(len(long)))


def name_words(forms: list[str], whole: bool = False) -> tuple[set[str], set[str], set[str]]:
    """The capitalised words of the names' short forms (before any comma, unless whole), split into the person's own,
    those after a patronymic particle not opening the name, which name a parent (Ali in Hasan ibn Ali, not Hajar in Ibn
    Hajar), and those after 'of', which name a place (Arc, Malacca)."""
    own, parents, places = set(), set(), set()
    for form in forms:
        tokens = WORD.findall(form if whole else form.split(",")[0])
        kind = own
        for index, token in enumerate(tokens):
            if fold(token) == "of":
                kind = places
            elif any(part[:1].isupper() for part in re.split(r"[-’']", token)):
                parts = {fold(token), *(fold(part) for part in re.split(r"[-’']", token) if part[:1].isupper())}
                (parents if kind is own and index > 1 and fold(tokens[index - 1]) in PATRONYMICS else kind).update(parts)
    return own, parents - own, places - own


def name_like(form: str) -> bool:
    """Whether a form reads as a name: capitalised words, with particles, regnal numbers and the words that join other
    names between them ('Joan of Arc', 'Lakshmibai, Rani of Jhansi', 'Raden Wijaya alias Nararya Sangramawijaya'), not
    a phrase the opening sentence was cut into."""
    words = form.replace(",", " ").split()
    return bool(words) and words[0][:1].isupper() and len(words) <= MAX_NAME_WORDS and all(
        any(part[:1].isupper() for part in re.split(r"[-’']", word)) or NUMERAL.match(word)
        or fold(word) in TITLES | PARTICLES | NAME_LINKS for word in words)


def hide(sentence: str, names: list[str], own: list[str] | None = None) -> str | None:
    """The sentence with the person replaced by 'this person', or None when any part of their name would remain. Only
    the person's own short name is replaced (own, else names): a run of its words (with joining words and their own
    regnal number and their other names inside it, as in Alan Mathison Turing) becomes one mention, and a full form
    with 'of' is taken whole (Joan of Arc); title words
    alone, such as 'Emperor' in another ruler's title, are left as they are, and a title just before or after the name
    goes with it ('King Sweyn's', 'Zahir Shah'). A sentence is not used when it still holds another of their names or a
    name inside theirs that names someone or somewhere else (Augustus in Tiberius's full name, his father Ali in Hasan
    ibn Ali, Malacca in Parameswara of Malacca), another regnal number (Edward II beside Edward III), a nickname or
    another name ('nicknamed Scarface', 'courtesy name Xuande', 'This person, Rajasa, was'), a thing named after them
    (Rákóczi's War of Independence, the Tokugawa shogunate, the Fibonacci numbers, Sun Liang for Sun Quan, Burkhard
    Christoph von Münnich for Anna Leopoldovna), a word made from the name (Zoroastrian) or holding it (Bâkî in
    Abdülbâkî), a title that ends their name left on its own ('Khan overthrew the monarchy' for Mohammad Daoud Khan),
    a list of their names, a pronunciation, or a Wikipedia tag of an unsourced claim."""
    if HONOUR.search(sentence) or NICKNAME.search(sentence):
        return None
    own = own if own is not None else names
    mine, parents, places = name_words(own)
    named, named_parents, named_places = name_words([form for form in names if name_like(form)], whole=True)
    every = mine | parents | named | named_parents
    words = {word for word in mine if word not in TITLES | COMMON and len(word) > 1}
    foreign = {word for word in every - mine - places - named_places if word not in TITLES | COMMON and len(word) > 1}
    numerals = {token for form in names + own for token in form.split() if NUMERAL.match(token)}
    joiners = {fold(word) for form in names + own for word in form.split()[1:-1] if word.islower()}
    for form in sorted({part for form in names + own for part in (form, form.split(",")[0].strip()) if name_like(part)},
                       key=len, reverse=True):
        sentence = re.sub(rf"(?<![\w-]){re.escape(form)}(?![\w-])", "Personmark", sentence)
    words.add("personmark")
    stems = {word[:STEM_LETTERS] for word in words}
    for token in WORD.findall(sentence):
        bare = fold(re.sub(r"['’]s?$", "", token))
        if bare[:STEM_LETTERS] in stems and bare not in words | foreign and DERIVED.search(bare):
            return None
    tokens = list(re.finditer(r"\S+", sentence))
    out, index = [], 0
    while index < len(tokens):
        word = re.sub(r"^\W+|\W+$", "", tokens[index].group())
        if word and word[:1].isupper() | any(part[:1].isupper() for part in re.split(r"[-’']", word)) \
                and fold(re.sub(r"['’]s?$", "", word)) in words | foreign:
            end = index
            while end + 1 < len(tokens):
                following = re.sub(r"^\W+|\W+$", "", tokens[end + 1].group())
                after = re.sub(r"^\W+|\W+$", "", tokens[end + 2].group()) if end + 2 < len(tokens) else ""
                if following and (fold(re.sub(r"['’]s?$", "", following)) in words | foreign or NUMERAL.match(following)
                                  or (following.islower() and fold(following) in TITLES | joiners
                                      and fold(re.sub(r"['’]s?$", "", after)) in words | foreign)
                                  or (fold(re.sub(r"['’]s?$", "", following)) in TITLES and following[:1].isupper())
                                  or (following == "the" and fold(re.sub(r"['’]s?$", "", after)) in EPITHETS
                                      and after[:1].isupper())):
                    end += 1
                else:
                    break
            run = [re.sub(r"^\W+|\W+$", "", token.group()) for token in tokens[index:end + 1]]
            if not any(fold(re.sub(r"['’]s?$", "", part)) in words for part in run):
                out.append(tokens[index].group())
                index += 1
                continue
            if any(NUMERAL.match(part) and part not in numerals for part in run):
                return None
            last = tokens[end].group()
            possessive = bool(re.search(r"['’]s?\W*$", last))
            if end + 1 < len(tokens) and tokens[end + 1].group()[:1].isupper() and (possessive or last[-1:].isalpha()):
                return None
            if end + 1 < len(tokens) and fold(re.sub(r"\W+$", "", tokens[end + 1].group())) in NAMING and last[-1:].isalpha():
                return None
            trailing = re.search(r"[^\w'’]*$", last).group()
            if trailing == "." and end + 1 < len(tokens) and tokens[end + 1].group()[:1].islower():
                trailing = ""
            while out and out[-1][:1].isupper() and fold(re.sub(r"\W", "", out[-1])) in TITLES | every:
                out.pop()
            if out and fold(out[-1]) in ("the", *DETERMINERS):
                return None
            if len(out) > 2 and fold(out[-1]) == "of" and out[-2][:1].isupper() and not re.search(r"[.!?:]$", out[-3]):
                return None
            if out and out[-1][:1].isupper() and out[-1][-1:].isalpha() and len(out) > 1 and not re.search(r"[.!?:]$", out[-2]):
                return None
            out.append(("this person's" if possessive else "this person") + trailing)
            index = end + 1
        else:
            out.append(tokens[index].group())
            index += 1
    text = " ".join(out)
    if "Personmark" in text or text.count("this person") > MAX_MENTIONS or NAME_LIST.search(text) or TAGGED.search(text) \
            or APPOSITIVE.search(text):
        return None
    if any(fold(re.sub(r"['’]s?$", "", token)) in foreign for token in WORD.findall(text)
           if any(part[:1].isupper() for part in re.split(r"[-’']", token))):
        return None
    if any(word in bare for word in words if len(word) > 3 for bare in (fold(token) for token in WORD.findall(text))
           if word != bare and bare not in ("this", "person")):
        return None
    if any(stem(word) in stems for word in WORD.findall(text) if word[:1].isupper() and fold(word) not in ("this", "person")):
        return None
    if any(near(fold(re.sub(r"['’]s?$", "", token)), word) for token in WORD.findall(text) if len(token) >= NEAR_LETTERS
           for word in words if len(word) >= NEAR_LETTERS):
        return None
    endings = {fold(form.split()[-1]) for form in names if form.split()} & (TITLES - EPITHETS)
    if any(fold(re.sub(r"['’]s?$", "", word)) in endings for word in WORD.findall(text) if word[:1].isupper()):
        return None
    text = re.sub(r"^this person, (?=(?:was|is|were)\b)", "this person ", text)
    text = text[0].upper() + text[1:]
    return re.sub(r"(?<=[.!?] )this person", "This person", text)


class LegacyScores:
    """The model's scores of how well each sentence says what a person or civilization is remembered for, kept with the
    data so a build gives the same legacy lines on any machine and the model reads only sentences it has not read
    before. The model is qwen3.5:4b through Ollama, pinned to one digest, at temperature 0, asked several sentences at a
    time; OLLAMA_HOST points to another machine's Ollama."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.saved: dict[str, int] = json.loads(path.read_text()) if path.exists() else {}
        self.used: dict[str, int] = {}
        self.checked = False
        self.read = 0

    def scores(self, subject: Subject, name: str, role: str, sentences: list[str]) -> list[int]:
        keys = [hashlib.sha1(f"{name}\n{role}\n{sentence}".encode()).hexdigest() for sentence in sentences]
        missing = {key: sentence for key, sentence in zip(keys, sentences) if key not in self.saved}
        if missing:
            self._check_model()
            with ThreadPoolExecutor(WORKERS) as pool:
                found = pool.map(lambda sentence: self._ask(subject.prompt, name, role, sentence), missing.values())
                self.saved.update(zip(missing, found))
            if (self.read + len(missing)) // PROGRESS_EVERY > self.read // PROGRESS_EVERY:
                logger.info("Scored %d new sentences for legacy", self.read + len(missing))
            self.read += len(missing)
        self.used.update((key, self.saved[key]) for key in keys)
        return [self.saved[key] for key in keys]

    def _check_model(self) -> None:
        if self.checked:
            return
        models = requests.get(f"{OLLAMA}/api/tags", timeout=30).json()["models"]
        if not any(model["name"] == MODEL and model["digest"] == MODEL_DIGEST for model in models):
            raise RuntimeError(f"Ollama needs {MODEL} at digest {MODEL_DIGEST[:12]} to score legacy lines")
        self.checked = True

    @staticmethod
    def _ask(prompt: str, name: str, role: str, sentence: str) -> int:
        response = requests.post(f"{OLLAMA}/api/chat", timeout=300, json={
            "model": MODEL, "stream": False, "think": False, "keep_alive": "5m",
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 6},
            "messages": [{"role": "user", "content": prompt.format(name=name, role=role, sentence=sentence)}]})
        found = re.search(r"\d+", response.json()["message"]["content"])
        return min(int(found.group()), 10) if found else 0

    def save(self) -> None:
        self.path.write_text(json.dumps(dict(sorted(self.used.items())), indent=0) + "\n")
        logger.info("Kept the legacy scores of %d sentences", len(self.used))


def repeats(sentence: str, summary: str) -> bool:
    """Whether the sentence mostly says again what the summary says (the Mongol Empire 'was the largest contiguous
    empire in history')."""
    said, told = words(sentence), words(summary)
    shared = len(said & told)
    return bool(said and told) and max(shared / len(said), shared / len(told)) > MAX_SHARED_WORDS


def good_lines(scores: LegacyScores, subject: Subject, name: str, role: str, text: str, names: list[str] | None = None,
               summary: str = "", own: list[str] | None = None) -> Iterator[str]:
    """The sentences of the text that say well enough what the person or civilization is remembered for, best scored
    first, with the names hidden when given; none scores below the subject's minimum or would give the name away. A
    sentence that repeats the summary, that Wikipedia tags as unsourced or that gives a pronunciation is not used, nor,
    since a line must stand on its own, one that leans on the sentence before it ('However, …', '… the latter …',
    'Three months later …') or, for a civilization, speaks of an unnamed ruler ('He consolidated …')."""
    candidates = [sentence for sentence in sentences(re.sub(r"\s*\n+\s*", " ", text))
                  if len(sentence.split()) >= MIN_WORDS and not repeats(sentence, summary)
                  and not (LEANING.match(sentence) or POINTING_BACK.search(sentence)
                           or (not subject.person and UNNAMED_PERSON.match(sentence)))]
    ranked = sorted(zip(scores.scores(subject, name, role, candidates), range(0, -len(candidates), -1), candidates),
                    reverse=True)
    for score, _, sentence in ranked:
        if score < subject.min_score:
            return
        if (line := hide(sentence, names, own) if names else sentence) and not TAGGED.search(line) and not BROKEN.search(line):
            line = line if re.search(r"[.!?][\"”’)]*$", line) else line + "."
            yield re.sub(r"\s{2,}", " ", re.sub(r",\s*([,.])", r"\1", line))


def unended(line: str) -> str:
    """The line without its closing full stops, so a review of it still holds when its ending is tidied."""
    return line.rstrip(".")


def legacy_sections(text: str) -> str:
    """The sections of a plain-text article whose heading speaks of legacy, assessment, reputation or the like."""
    parts = re.split(r"^(=+)\s*(.*?)\s*=+\s*$", text, flags=re.MULTILINE)
    return "\n".join(parts[index + 2] for index in range(1, len(parts) - 2, 3) if SECTIONS.search(parts[index + 1]))


def legacy_lines(wikipedia, titles: list[str], pick: Callable[[int, str, str, bool], str]) -> list[str]:
    """Each article's legacy line, as pick(index, text, introduction, body) chooses it from the introduction, else from
    the legacy sections, else from the cited sentences of the whole article (body true), which stand further from what
    the article says first and so must stand on their own."""
    introductions = wikipedia.introductions(titles)
    openings = [introductions.get(title, ("", None))[0] for title in titles]
    lines = [pick(index, opening, opening, False) for index, opening in enumerate(openings)]
    unfound = [index for index, line in enumerate(lines) if not line]
    articles = wikipedia.article_texts([titles[index] for index in unfound])
    for index in unfound:
        lines[index] = pick(index, legacy_sections(articles.get(titles[index], "")), openings[index], False)
    unfound = [index for index in unfound if not lines[index]]
    cited = wikipedia.cited_sentences([titles[index] for index in unfound])
    for index in unfound:
        body = "\n".join(sentence if re.search(r"[.!?\"”]$", sentence) else sentence + "."
                          for sentence in cited.get(titles[index], []))
        lines[index] = pick(index, body, openings[index], True)
    return lines


def realms(figure) -> tuple[str, ...]:
    """The states and countries a figure belongs to, whose names can follow theirs ('Taejo of Joseon')."""
    return (*figure.civilizations, figure.setting, figure.modern_country, figure.birth_country)


def add_legacy(figures: list, wikipedia, scores: LegacyScores, rejected: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Each figure's legacy line, with the person hidden, unless an override set it, passing over the (figure, line) pairs a review rejected for
    the next best line; returns the rejected pairs it passed over."""
    passed = set()

    def pick(index: int, text: str, introduction: str, body: bool) -> str:
        figure = figures[index]
        names = name_forms([figure.name, figure.wikipedia], introduction, realms(figure))
        own = name_forms([figure.name, figure.wikipedia], "", realms(figure))
        for line in good_lines(scores, FIGURE, figure.name, figure.role, text, names, own=own):
            if (figure.name, unended(line)) not in rejected:
                return line
            passed.add((figure.name, unended(line)))
        return ""

    for figure, line in zip(figures, legacy_lines(wikipedia, [figure.wikipedia for figure in figures], pick)):
        figure.legacy = figure.legacy or line
    logger.info("Found a legacy line for %d of %d figures", sum(bool(figure.legacy) for figure in figures), len(figures))
    return passed


def add_civilization_legacy(polities: list, wikipedia, scores: LegacyScores,
                            rejected: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Each civilization's legacy line, unless an override set it, read from the article its title leads to (the Caliphate of Córdoba's is now
    the Umayyad state of Córdoba's), passing over the (civilization, line) pairs a review rejected for the next best
    line; returns the rejected pairs it passed over."""
    passed = set()
    pages = wikipedia.resolve([polity.wikipedia for polity in polities])
    titles = [pages[polity.wikipedia].title if polity.wikipedia in pages else polity.wikipedia for polity in polities]

    def pick(index: int, text: str, _: str, __: bool) -> str:
        polity = polities[index]
        for line in good_lines(scores, CIVILIZATION, polity.name, CIVILIZATION_ROLE, text, summary=polity.summary):
            if (polity.name, unended(line)) not in rejected:
                return line
            passed.add((polity.name, unended(line)))
        return ""

    for polity, line in zip(polities, legacy_lines(wikipedia, titles, pick)):
        polity.legacy = polity.legacy or line
    logger.info("Found a legacy line for %d of %d civilizations", sum(bool(polity.legacy) for polity in polities),
                len(polities))
    return passed
