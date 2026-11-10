"""Sentences from a Wikipedia opening paragraph: the first one without its subject, for what a person or a
civilization is known for."""
import re

NAME_GLOSS = re.compile(r"^(?:(?:also|commonly|better|often) (?:known|named|called)|sometimes (?:he|she) is (?:known|called)|"
                        r"(?:his|her) (?:full |regnal |birth |personal )?(?:name|form of address|title)s?)\b", re.IGNORECASE)
MIN_NEW_WORDS = 2
FILLER = {"the", "a", "an", "of", "from", "to", "and", "in", "ad", "bc", "bce", "ce"}
SUBJECT_END = re.compile(r"\b(?:was|is|were|refers to|comprised|comprises|consisted of|consists of|denotes|describes)\s+",
                         re.IGNORECASE)
PARENTHESES = re.compile(r"\s*\([^()]*\)")
SENTENCE_END = re.compile(r"(?:(?<=[a-z0-9\])\"”'’])|(?<=\s[IVX]{2})|(?<=\s[IVX]{3})|(?<=\s[IVX]{4})|(?<=\s(?:AD|BC|CE))|(?<=\sBCE))"
                          r"(?<!\sDr)(?<!\sMr)(?<!\sMrs)(?<!\sSt)(?<!\sMt)(?<!\sc)(?<!\sca)\.\s+(?=[ʻ\"“]?[A-ZÀ-ÖØ-Þ])"
                          r"|(?<=\.[\"”])\s+(?=[A-Z])"
                          r"|(?<=\s[IVX])\.\s+(?=(?:He|She|His|Her|They|Their|The|In|After|During|It|As|Under)\b)")
ENDED = re.compile(r"[.!?][\"”]?$")
DANGLING = re.compile(r"\b(?:the|a|an|of|and|or|to|from|in|by|with|for|at)\.$", re.IGNORECASE)
LEANING = re.compile(r"^(?:Concurrently|Traditionally|However|Moreover|Furthermore|Additionally|In addition,|Also|"
                     r"Meanwhile|Nevertheless|Subsequently|Thereafter|Likewise|Similarly|The two|Both|This|These|Those|"
                     r"Such)\b")
LINEAGE = re.compile(r"^(?:(?:He|She) (?:was|is|may have been) (?:still )?(?:the |a |an )?(?:\w+ )?(?:son|daughter|child|grandson|"
                     r"granddaughter|great-grandson)\b|(?:He|She) (?:was|is) (?:born|numbered|of .* descent)\b|"
                     r"(?:His|Her) (?:son|daughter|father|mother) was\b)"
                     r"|\b(?:he|she) (?:is|was) (?:also |commonly |better |usually )(?:known|referred to|called) as\b"
                     r"|\bsucceeded by\b", re.IGNORECASE)
HATNOTE = re.compile(r"Note:")


def balanced(text: str) -> str:
    """The text without a closing parenthesis that has no opening one, or an opening one never closed, both left
    behind when dates are cut from a description."""
    depth, kept = 0, []
    for character in text:
        if character == ")" and depth == 0:
            continue
        depth += {"(": 1, ")": -1}.get(character, 0)
        kept.append(character)
    text = "".join(kept)
    return text[:text.rfind("(")].rstrip() if depth > 0 else text


def first_paragraph(extract: str) -> str:
    """The opening paragraph without a hatnote ("Note: names are in simplified characters …"), joined across a line
    break left inside its parentheses or its sentence (Chekhov's lead breaks between his Russian name and his dates)."""
    paragraph = ""
    for line in (line.strip() for line in extract.splitlines()):
        if not line or not paragraph and HATNOTE.match(line):
            continue
        paragraph = f"{paragraph} {line}".strip()
        if paragraph.count("(") <= paragraph.count(")") and ENDED.search(paragraph):
            break
    return paragraph


def sentences(paragraph: str) -> list[str]:
    """The sentences without parentheses; one does not end after "Dr.", "St." or "c.", and does after a quotation
    ('… "the father of modern drama". He …', "… 'rightly guided'. Endowed …")."""
    return [sentence.strip() for sentence in SENTENCE_END.split(PARENTHESES.sub("", paragraph)) if sentence.strip()]


def opening(paragraph: str) -> str:
    return next(iter(sentences(paragraph)), "")


def predicate(paragraph: str) -> str:
    """The opening sentence without its subject: 'The founder of the Achaemenid Empire.', and from "refers to",
    "comprised" and the like as from "was" ("The British Empire comprised the dominions, …")."""
    parts = SUBJECT_END.split(opening(paragraph), maxsplit=1)
    if len(parts) < 2:
        return ""
    text = parts[1].strip()
    text = text if ENDED.search(text) else text.rstrip(".") + "."
    return text[:1].upper() + text[1:]


def contribution(paragraph: str, role_text: str, description: str | None, min_new_words: int = MIN_NEW_WORDS) -> str:
    """What the person is known for: the opening sentence without its subject, or the next sentence that says more
    than the role, at least two words it does not have, when the opening one only repeats it ('The tenth Mughal
    emperor from 1713 to 1719'), only glosses the name ('Also known as …', 'His form of address was …') or is left
    hanging once its dates are cut ('… who ruled from'), and skipping one that leans on the sentence before it
    ('Concurrently, he was …'), or, past the opening one, opens on family, birth or a name ('He was son of …', 'He is
    numbered as Olaf I') or tells who succeeded whom; else Wikidata's description if it says that much more; else
    nothing."""
    paragraph = re.sub(r",\s*\.", ".", paragraph)
    candidates = [(predicate(paragraph), True)] + [(sentence, False) for sentence in sentences(paragraph)[1:]]
    text = next((candidate for candidate, opening in candidates
                 if candidate and balanced(candidate) == candidate and not NAME_GLOSS.match(candidate)
                 and not DANGLING.search(candidate) and not LEANING.match(candidate)
                 and (opening or not LINEAGE.search(candidate))
                 and len(words(candidate) - words(role_text)) >= min_new_words), "")
    if not text and description and not LINEAGE.search(description) \
            and len(words(description) - words(role_text)) >= min_new_words:
        text = description[:1].upper() + description[1:]
    text = re.sub(r"\s{2,}", " ", text)
    return text if not text or ENDED.search(text) else text + "."


def words(text: str) -> set[str]:
    """The words that carry meaning, without years, which roles and articles write differently ('AD 69', '69')."""
    return set(re.findall(r"[a-z]+", text.lower())) - FILLER
