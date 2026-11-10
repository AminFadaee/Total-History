import re
from dataclasses import dataclass, field

import mwparserfromhell as mw
from mwparserfromhell.wikicode import Wikicode

from page_in_history.dates import Span, parse, parse_range
from page_in_history.http import Http

LINE_BREAK = re.compile(r"\n|<br\s*/?>", re.IGNORECASE)
LIST_MARKUP = re.compile(r"^[*#:\s]+|\{\{\s*(?:plainlist|ubl|unbulleted list|flatlist)\s*\|?|\}\}", re.IGNORECASE)
OFFICE_ROW = re.compile(r"(leader|deputy|representative)(\d+)")
TITLE_FIELD = re.compile(r"(?:succession|title|office|order|reign-type)\d*")
PLACE_NAME = re.compile(r"[A-Z][\w'’.-]*(?: [A-Z][\w'’.-]*){0,3}")
WIKILINK = re.compile(r"(\[\[[^\]]*\]\])")
YEAR = re.compile(r"\b\d{1,4}\b")
MONTH = "January|February|March|April|May|June|July|August|September|October|November|December"
DAY = re.compile(rf"\b\d{{1,2}}\s+(?:{MONTH})\b|\b(?:{MONTH})\s+\d{{1,2}}\b")
BEFORE_CHRIST = re.compile(r"\bBCE?\b")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[])")
CAPITAL_WORD = re.compile(r"\bcapital\b", re.IGNORECASE)
API = "https://en.wikipedia.org/w/api.php"
EDITION_API = "https://{}.wikipedia.org/w/api.php"
EDITION_HOSTS = {"be_x_old": "be-tarask"}
BATCH = 50
EXTRACT_BATCH = 20
INFOBOXES = ("infobox country", "infobox former country")
NOTE_TEMPLATES = ("efn", "ref", "sfn", "refn", "notetag", "cn", "citation needed", "r", "nbsp")
FLAG_TEMPLATES = ("flag", "flagcountry", "flagu", "flag country", "flagdeco")
NON_ARTICLE = ("file:", "image:", "category:", "wikt:", ":category:")
SECTION = re.compile(r"\n==[^=]")
INFOBOX_START = re.compile(r"\{\{\s*infobox (?:former )?country\s*[|}]", re.IGNORECASE)
BRACES = re.compile(r"\{\{|\}\}")
REDIRECT = re.compile(r"\s*#REDIRECT\s*\[\[([^\]|#]+)\]\]", re.IGNORECASE)
DASH_TEMPLATES = ("snd", "ndash", "spaced ndash", "spnd", "sndash", "mdash", "spaced en dash", "en dash")
DISAMBIGUATOR = re.compile(r"\s*\([^)]*\)$")
SUCCESSION_FIELDS = (r"p\d+", r"s\d+")
EDITION_SUCCESSION_FIELDS = {
    "ar": (r"بلد سابق\d+", r"بلد لاحق\d+"),
    "arz": (r"بلد سابق\d+", r"بلد لاحق\d+"),
    "cs": (r"před \d+", r"po \d+"),
    "de": (r"vorgänger", r"nachfolger"),
    "eo": (r"antaŭ \d+", r"post \d+"),
    "et": (r"eelnes", r"järgnes"),
    "eu": (r"a\d+", r"o\d+"),
    "fi": (r"edeltäjät", r"seuraajat"),
    "hu": (r"előd ?\d+|elődállamok", r"utód ?\d+|utódállamok"),
    "hy": (r"նախորդ\d+", r"հաջորդ\d+"),
    "it": (r"stato precedente", r"stato successivo"),
    "ja": (r"先代\d+", r"次代\d+"),
    "nl": (r"voor\d+", r"na\d+"),
    "no": (r"f\d+|foregående", r"e\d+|etterfølgende"),
    "sk": (r"před \d+", r"po \d+"),
    "sr": (r"претходник\d+", r"следбеник\d+"),
    "sv": (r"tidigare\d+", r"senare\d+"),
    "zh": (r"前\d+", r"後\d+"),
}


@dataclass(frozen=True)
class Page:
    title: str
    qid: str | None


@dataclass
class OfficeRow:
    """One holder row of a state's infobox: leader, deputy or representative, with its title and years as written."""
    kind: str
    people: list[str]
    title: str
    title_links: list[str]
    years: str


@dataclass
class Infobox:
    start: Span | None = None
    end: Span | None = None
    capital: list[str] = field(default_factory=list)
    capital_years: dict[str, tuple[int, int]] = field(default_factory=dict)
    predecessors: list[str] = field(default_factory=list)
    successors: list[str] = field(default_factory=list)
    leaders: list[str] = field(default_factory=list)
    rows: list[OfficeRow] = field(default_factory=list)


def display_name(title: str) -> str:
    return DISAMBIGUATOR.sub("", title)


def place_name(title: str) -> str:
    """'Nisa, Turkmenistan' -> 'Nisa': Wikipedia disambiguates places with a comma; Wikidata labels some city items
    'Madrid city', but 'Mexico City' is a name."""
    return re.sub(r" city$", "", display_name(title).split(",")[0])


def edition_api(language: str) -> str:
    """The API of a language edition, from Wikidata's site code (zh_classical, be_x_old)."""
    return API if language == "en" else EDITION_API.format(EDITION_HOSTS.get(language, language.replace("_", "-")))


class Wikipedia:
    def __init__(self, http: Http):
        self.http = http

    def resolve(self, titles: list[str], language: str = "en") -> dict[str, Page]:
        """Follow normalisation and redirects; map each requested title to its article and Wikidata item. Cached per
        title, in a language edition if given."""
        wanted = [title for title in titles if title and "|" not in title]
        namespace = "page-by-title" if language == "en" else f"page-by-title-{language}"
        found = self.http.per_key(namespace, wanted, lambda missing: self._fetch_pages(missing, edition_api(language)))
        return {title: Page(*page) for title, page in found.items()}

    def _fetch_pages(self, titles: list[str], url: str = API) -> dict[str, list]:
        unique = sorted(set(titles))
        found: dict[str, list] = {}
        for start in range(0, len(unique), BATCH):
            batch = unique[start : start + BATCH]
            params = {"action": "query", "titles": "|".join(batch), "prop": "pageprops", "ppprop": "wikibase_item",
                      "redirects": 1, "format": "json", "formatversion": 2}
            query = self.http.json(url, params, namespace="wikipedia")["query"]
            normalized = {item["from"]: item["to"] for item in query.get("normalized", [])}
            redirects = {item["from"]: item["to"] for item in query.get("redirects", [])}
            pages = {page["title"]: page for page in query.get("pages", [])}
            for title in batch:
                target = normalized.get(title, title)
                target = redirects.get(target, target)
                page = pages.get(target, {})
                if not page.get("missing"):
                    found[title] = [target, page.get("pageprops", {}).get("wikibase_item")]
        return found

    def wikitext(self, titles: list[str], follow: bool = False) -> dict[str, str]:
        """The source text of each article, cached per title; with follow, a redirect to a whole article gives that
        article's text (the Caliphate of Córdoba, now part of the Umayyad state of Córdoba)."""
        texts = self.http.per_key("wikitext-by-title", titles, self._fetch_wikitext)
        if not follow:
            return texts
        targets = {title: match.group(1).strip() for title, text in texts.items() if (match := REDIRECT.match(text))}
        followed = self.http.per_key("wikitext-by-title", list(targets.values()), self._fetch_wikitext)
        return texts | {title: followed[target] for title, target in targets.items() if target in followed}

    def _fetch_wikitext(self, titles: list[str]) -> dict[str, str]:
        unique = sorted(set(titles))
        found: dict[str, str] = {}
        for start in range(0, len(unique), BATCH):
            params = {"action": "query", "titles": "|".join(unique[start : start + BATCH]), "prop": "revisions",
                      "rvprop": "content", "rvslots": "main", "format": "json", "formatversion": 2}
            for page in self.http.json(API, params, namespace="wikipedia")["query"]["pages"]:
                if "revisions" in page:
                    found[page["title"]] = page["revisions"][0]["slots"]["main"]["content"]
        return found

    def leads(self, titles: list[str], language: str) -> dict[str, str]:
        """The source text of each article's opening section, where its infobox is, in a language edition; cached per
        title, a redirect followed."""
        namespace = f"lead-by-title-{language}"
        return self.http.per_key(namespace, titles, lambda missing: self._fetch_leads(missing, edition_api(language)))

    def _fetch_leads(self, titles: list[str], url: str) -> dict[str, str]:
        unique = sorted(set(titles))
        found: dict[str, str] = {}
        for start in range(0, len(unique), EXTRACT_BATCH):
            batch = unique[start : start + EXTRACT_BATCH]
            params = {"action": "query", "titles": "|".join(batch), "prop": "revisions", "rvprop": "content",
                      "rvslots": "main", "rvsection": 0, "redirects": 1, "format": "json", "formatversion": 2}
            query = self.http.json(url, params, namespace="wikipedia")["query"]
            moved = {item["from"]: item["to"] for item in query.get("normalized", [])}
            moved |= {item["from"]: item["to"] for item in query.get("redirects", [])}
            pages = {page["title"]: page["revisions"][0]["slots"]["main"]["content"]
                     for page in query["pages"] if "revisions" in page}
            for title in batch:
                target = moved.get(title, title)
                text = pages.get(moved.get(target, target))
                if text is not None:
                    found[title] = text
        return found

    def categories(self, titles: list[str]) -> dict[str, list[str]]:
        """The visible categories of each article, cached per title."""
        return self.http.per_key("categories-by-title", titles, self._fetch_categories)

    def _fetch_categories(self, titles: list[str]) -> dict[str, list[str]]:
        """Follows continuation, since one batch can exceed the limit."""
        unique = sorted(set(titles))
        found: dict[str, list[str]] = {}
        for start in range(0, len(unique), BATCH):
            params = {"action": "query", "titles": "|".join(unique[start : start + BATCH]), "prop": "categories",
                      "clshow": "!hidden", "cllimit": "max", "format": "json", "formatversion": 2}
            while True:
                data = self.http.json(API, params, namespace="wikipedia")
                for page in data["query"]["pages"]:
                    found.setdefault(page["title"], []).extend(category["title"] for category in page.get("categories", []))
                if "continue" not in data:
                    break
                params = {**params, **data["continue"]}
        return found

    def introductions(self, titles: list[str]) -> dict[str, tuple[str, str | None]]:
        """Plain-text opening section and the short description of each article."""
        unique = sorted(set(titles))
        found: dict[str, tuple[str, str | None]] = {}
        for start in range(0, len(unique), EXTRACT_BATCH):
            params = {"action": "query", "titles": "|".join(unique[start : start + EXTRACT_BATCH]),
                      "prop": "extracts|pageprops", "exintro": 1, "explaintext": 1, "format": "json", "formatversion": 2}
            for page in self.http.json(API, params, namespace="wikipedia")["query"]["pages"]:
                found[page["title"]] = (page.get("extract") or "", page.get("pageprops", {}).get("wikibase-shortdesc"))
        return found

    def article_texts(self, titles: list[str]) -> dict[str, str]:
        """The plain text of whole articles with their == headings ==, one request each."""
        found: dict[str, str] = {}
        for title in sorted(set(titles)):
            params = {"action": "query", "titles": title, "prop": "extracts", "explaintext": 1, "exsectionformat": "wiki",
                      "format": "json", "formatversion": 2}
            pages = self.http.json(API, params, namespace="wikipedia")["query"]["pages"]
            found[title] = (pages[0].get("extract") or "") if pages else ""
        return found

    def page_wikitext(self, title: str) -> str:
        params = {"action": "parse", "page": title, "prop": "wikitext", "format": "json", "formatversion": 2}
        return self.http.json(API, params, namespace="wikipedia")["parse"]["wikitext"]


def without_notes(code: Wikicode) -> Wikicode:
    for tag in code.filter_tags():
        if str(tag.tag).lower() == "ref":
            remove(code, tag)
    for template in code.filter_templates(recursive=False):
        if str(template.name).strip().lower().startswith(NOTE_TEMPLATES):
            remove(code, template)
    return code


def remove(code: Wikicode, node) -> None:
    try:
        code.remove(node)
    except ValueError:
        pass


def link_targets(code: Wikicode | None) -> list[str]:
    if code is None:
        return []
    code = without_notes(code)
    targets = [str(link.title).split("#")[0].split("{{!}}")[0].strip() for link in code.filter_wikilinks()]
    for template in code.filter_templates():
        if str(template.name).strip().lower() in FLAG_TEMPLATES and template.params:
            targets.append(str(template.params[0].value).strip())
    return [target for target in dict.fromkeys(targets) if target and not target.lower().startswith(NON_ARTICLE)]


def place_targets(code: Wikicode | None) -> list[str]:
    """Places a field lists, one per line: the linked article, or a line's unlinked name ('* Paris (987–1682)' in the
    Kingdom of France's list of capitals, beside a linked Versailles)."""
    if code is None:
        return []
    found = []
    for line in LINE_BREAK.split(str(without_notes(code))):
        parsed = mw.parse(line)
        linked = link_targets(parsed)
        if linked:
            found += linked
            continue
        text = LIST_MARKUP.sub("", parsed.strip_code()).strip()
        name = re.split(r"\s*[(,;]", text)[0].strip()
        if PLACE_NAME.fullmatch(name):
            found.append(name)
    return list(dict.fromkeys(found))


def dated_links(code: Wikicode | None) -> dict[str, tuple[int, int]]:
    """The first and last year written after each linked place, up to the next link: 879 and 706 BC for
    '[[Nimrud]] (879–706 BCE)', 1185 and 1393 for '[[Veliko Tarnovo|Tarnovo]]<br />{{small|(1185–1393)}}'. Notes,
    coordinates and days of the month are no years."""
    if code is None:
        return {}
    code = without_notes(mw.parse(str(code)))
    for template in code.filter_templates():
        if str(template.name).strip().lower().startswith(NOTE_TEMPLATES + ("coord",)):
            remove(code, template)
    pieces = WIKILINK.split(DAY.sub("", str(code)))
    found: dict[str, tuple[int, int]] = {}
    for link, text in zip(pieces[1::2], pieces[2::2]):
        targets = link_targets(mw.parse(link))
        sign = -1 if BEFORE_CHRIST.search(text) else 1
        years = [sign * int(year) for year in YEAR.findall(text)]
        if targets and years:
            found.setdefault(targets[0], (min(years), max(years)))
    return found


def lead_capital_links(wikitext: str) -> list[str]:
    """Articles linked from the first sentence of the lead's prose that speaks of a capital (Heian-kyō in "Emperor
    Kammu moved the capital of Japan to Heian-kyō")."""
    lead = mw.parse(SECTION.split(wikitext, maxsplit=1)[0])
    for template in lead.filter_templates(recursive=False):
        remove(lead, template)
    for paragraph in str(lead).splitlines():
        for sentence in SENTENCE_END.split(paragraph):
            parsed = mw.parse(sentence)
            if CAPITAL_WORD.search(parsed.strip_code()):
                return link_targets(parsed)
    return []


def lead_links(wikitext: str) -> list[str]:
    """Articles linked from the prose of the lead section, ignoring infoboxes and other templates."""
    lead = mw.parse(SECTION.split(wikitext, maxsplit=1)[0])
    for template in lead.filter_templates(recursive=False):
        remove(lead, template)
    return link_targets(lead)


def plain_text(code: Wikicode | None) -> str:
    """Infobox wikitext as readable text: dash templates become dashes, wrappers like {{nowrap|…}} or {{circa|…}}
    keep their content, notes and line breaks go."""
    if code is None:
        return ""
    code = without_notes(mw.parse(str(code)))
    for template in reversed(code.filter_templates()):
        name = str(template.name).strip().lower()
        positional = [param for param in template.params if not param.showkey]
        if name in DASH_TEMPLATES:
            replacement = " – "
        elif name in ("circa", "c.", "c"):
            replacement = f"c. {positional[0].value}" if positional else "c."
        else:
            replacement = str(positional[-1].value) if positional else ""
        try:
            code.replace(template, replacement)
        except ValueError:
            pass
    text = mw.parse(str(code)).strip_code()
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", text.splitlines()[0] if text.strip() else "")).strip()


def infobox_years(fields: dict[str, Wikicode]) -> tuple[Span | None, Span | None]:
    """year_start and year_end, falling back to the two ends of life_span."""
    start, end = parse(plain_text(fields.get("year_start"))), parse(plain_text(fields.get("year_end")))
    if start is None or end is None:
        span_start, span_end = parse_range(plain_text(fields.get("life_span")))
        start, end = start or span_start, end or span_end
    return start, end


def numbered(fields: dict[str, Wikicode], prefix: str) -> list[str]:
    """Numbered fields such as p1, p2: each holds a link, or the bare article title that the infobox links itself."""
    keys = sorted((key for key in fields if re.fullmatch(rf"{prefix}\d+", key)), key=lambda key: int(key[len(prefix):]))
    found: list[str] = []
    for key in keys:
        if str(fields[key]).strip():
            found.extend(link_targets(fields[key]) or bare_title(fields[key]))
    return list(dict.fromkeys(found))


def bare_title(value: Wikicode) -> list[str]:
    """'Kingdom of France (1791–92){{!}}Kingdom of France' names the article before the escaped pipe."""
    title = mw.parse(str(value).split("{{!}}")[0]).strip_code().strip()
    return [title] if title else []


def infobox_head(wikitext: str) -> str:
    """The article up to the end of its first country infobox, which is all the parser needs and much shorter than the
    article. The whole article when that infobox may sit inside another template."""
    found = INFOBOX_START.search(wikitext)
    if not found or wikitext.count("{{", 0, found.start()) != wikitext.count("}}", 0, found.start()):
        return wikitext
    depth = 0
    for brace in BRACES.finditer(wikitext, found.start()):
        depth += 1 if brace.group() == "{{" else -1
        if depth == 0:
            return wikitext[:brace.end()]
    return wikitext


def ruler_succession(wikitext: str) -> dict[str, list[str]]:
    """The predecessor and successor a person's infobox names (the first of each numbered set, for the title the
    infobox leads with)."""
    for template in mw.parse(SECTION.split(wikitext, maxsplit=1)[0]).filter_templates(recursive=False):
        if str(template.name).strip().lower().startswith("infobox"):
            fields = {str(param.name).strip(): param.value for param in template.params}
            return {key: next((link_targets(fields[name]) for name in (key, f"{key}1") if name in fields
                               and link_targets(fields[name])), []) for key in ("predecessor", "successor")}
    return {"predecessor": [], "successor": []}


def lead_succession(wikitext: str, language: str) -> tuple[str | None, str | None]:
    """The first predecessor and the first successor listed by the country infobox in the opening section of an article
    in a language edition: the lowest-numbered field of each kind, which holds a link or the bare title the infobox
    links itself, and of several links the first. The field names are those the edition's former-country infoboxes
    use, beside the English p1 and s1 that most share."""
    patterns = [re.compile("|".join(filter(None, pair))) for pair in
                zip(SUCCESSION_FIELDS, EDITION_SUCCESSION_FIELDS.get(language, ("", "")))]
    for template in mw.parse(wikitext).filter_templates(recursive=False):
        fields = {re.sub(r"[\s_]+", " ", str(param.name).strip().casefold()): param.value for param in template.params}
        found = tuple(first_listed(fields, pattern) for pattern in patterns)
        if any(found):
            return found
    return None, None


def first_listed(fields: dict[str, Wikicode], pattern: re.Pattern) -> str | None:
    for key in sorted((key for key in fields if pattern.fullmatch(key)), key=lambda key: int(re.sub(r"\D", "", key) or 0)):
        if targets := link_targets(fields[key]) or bare_title(fields[key]):
            return targets[0]
    return None


def person_titles(wikitext: str) -> list[str]:
    """The titles and offices a person's infobox gives ("Empress of Russia", "Mahapatih of the Majapahit Empire"), as
    plain text, in the order the infobox lists them."""
    for template in mw.parse(SECTION.split(wikitext, maxsplit=1)[0]).filter_templates(recursive=False):
        if str(template.name).strip().lower().startswith("infobox"):
            return [text for param in template.params if TITLE_FIELD.fullmatch(str(param.name).strip())
                    and (text := param.value.strip_code().strip())]
    return []


def country_infobox(wikitext: str) -> Infobox | None:
    for template in mw.parse(infobox_head(wikitext)).filter_templates(recursive=False):
        if str(template.name).strip().lower() in INFOBOXES:
            fields = {str(param.name).strip(): param.value for param in template.params}
            start, end = infobox_years(fields)
            return Infobox(
                start=start,
                end=end,
                capital=place_targets(fields.get("capital")),
                capital_years=dated_links(fields.get("capital")),
                predecessors=numbered(fields, "p"),
                successors=numbered(fields, "s"),
                leaders=numbered(fields, "leader"),
                rows=office_rows(fields),
            )
    return None


def office_rows(fields: dict[str, Wikicode]) -> list[OfficeRow]:
    """The leader, deputy and representative rows, each with the title its kind is given (title_leader) and the years
    written beside it (year_leader1)."""
    rows = []
    for key, value in fields.items():
        match = OFFICE_ROW.fullmatch(key)
        if match and str(value).strip():
            kind, number = match.groups()
            title = fields.get(f"title_{kind}")
            rows.append(OfficeRow(kind, link_targets(value) or bare_title(value), title.strip_code().strip() if title else "",
                                  link_targets(title) if title else [],
                                  fields[f"year_{kind}{number}"].strip_code().strip() if f"year_{kind}{number}" in fields else ""))
    return rows
