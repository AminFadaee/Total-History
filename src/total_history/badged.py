from collections import defaultdict

from total_history.sources.wikidata import Wikidata
from total_history.sources.wikipedia import Wikipedia, lead_succession

SIDES = ("before", "after")


class BadgedInfoboxes:
    """What the quality-badged Wikipedia articles on a civilization say came before and after it: the Wikidata items
    their country infoboxes list first as predecessor and as successor, one vote per language edition."""

    def __init__(self, wikipedia: Wikipedia, wikidata: Wikidata):
        self.wikipedia = wikipedia
        self.wikidata = wikidata

    def votes(self, ids: list[str]) -> dict[str, dict[str, dict[str, list[str]]]]:
        """For each civilization and side, the languages that list each item first (featured, good and recommended
        articles; the link is followed through redirects to its item)."""
        articles = self.wikidata.quality_articles(ids)
        found: dict[str, dict[str, dict[str, list[str]]]] = {
            qid: {side: defaultdict(list) for side in SIDES} for qid in ids}
        for language in sorted({language for titles in articles.values() for language in titles}):
            titles = {qid: found_titles[language] for qid, found_titles in articles.items() if language in found_titles}
            leads = self.wikipedia.leads(sorted(set(titles.values())), language)
            listed = {qid: lead_succession(leads[title], language) for qid, title in titles.items() if title in leads}
            pages = self.wikipedia.resolve(sorted({title for pair in listed.values() for title in pair if title}), language)
            for qid, pair in listed.items():
                for side, title in zip(SIDES, pair):
                    page = pages.get(title) if title else None
                    if page and page.qid and page.qid != qid:
                        found[qid][side][page.qid].append(language)
        return found
