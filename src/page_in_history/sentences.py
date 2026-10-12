"""Sentences from a Wikipedia opening paragraph: the first one without its subject, for what a person or a
civilization is known for."""
import re

NAME_GLOSS = re.compile(r"^(?:(?:also|commonly|better|often) (?:known|named|called)|"
                        r"(?:his|her) (?:full |regnal |birth |personal )?(?:name|form of address|title)s?)\b", re.IGNORECASE)
MIN_NEW_WORDS = 2
FILLER = {"the", "a", "an", "of", "from", "to", "and", "in", "ad", "bc", "bce", "ce"}
COPULA = re.compile(r"\b(?:was|is|were)\s+", re.IGNORECASE)
PARENTHESES = re.compile(r"\s*\([^()]*\)")
SENTENCE_END = re.compile(r"(?:(?<=[a-z0-9\])])|(?<=\s[IVX]{2})|(?<=\s[IVX]{3})|(?<=\s[IVX]{4})|(?<=\s(?:AD|BC|CE))|(?<=\sBCE))"
                          r"\.\s+(?=[A-Z])"
                          r"|(?<=\s[IVX])\.\s+(?=(?:He|She|His|Her|They|Their|The|In|After|During|It|As|Under)\b)")


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
    return next((line.strip() for line in extract.splitlines() if line.strip()), "")


def predicate(paragraph: str) -> str:
    """The opening sentence without its subject: 'The founder of the Achaemenid Empire.'"""
    sentence = SENTENCE_END.split(PARENTHESES.sub("", paragraph), maxsplit=1)[0].strip()
    parts = COPULA.split(sentence, maxsplit=1)
    if len(parts) < 2:
        return ""
    text = parts[1].strip().rstrip(".") + "."
    return text[:1].upper() + text[1:]


def contribution(paragraph: str, role_text: str, description: str | None) -> str:
    """What the person is known for: the opening sentence without its subject, or the next sentence that says more
    than the role, at least two words it does not have, when the opening one only repeats it ('The tenth Mughal
    emperor from 1713 to 1719') or only glosses the name ('Also known as …', 'His form of address was …'); else
    Wikidata's description if it says that much more; else nothing."""
    sentences = [sentence.strip() for sentence in SENTENCE_END.split(PARENTHESES.sub("", paragraph)) if sentence.strip()]
    candidates = [predicate(paragraph)] + sentences[1:]
    text = next((candidate for candidate in candidates
                 if candidate and balanced(candidate) == candidate and not NAME_GLOSS.match(candidate)
                 and len(words(candidate) - words(role_text)) >= MIN_NEW_WORDS), "")
    if not text and description and len(words(description) - words(role_text)) >= MIN_NEW_WORDS:
        text = description[:1].upper() + description[1:]
    return text if not text or text[-1] in ".!?\"”" else text + "."


def words(text: str) -> set[str]:
    """The words that carry meaning, without years, which roles and articles write differently ('AD 69', '69')."""
    return set(re.findall(r"[a-z]+", text.lower())) - FILLER
