from collections import Counter
from fractions import Fraction

MIN_SUPPORT = 0.3
MIN_VOTES = 3
MIN_LEAD = 2
STRONG_SUPPORT = 0.5


def ballot(support: dict[str, float]) -> dict[str, Fraction]:
    """One voter's vote: its best-supported candidates share one vote, unless even the best has less than 0.3 of support,
    when the voter backs none of them."""
    best = max(support.values(), default=0)
    leaders = [candidate for candidate, value in support.items() if value >= best - 1e-12]
    return {candidate: Fraction(1, len(leaders)) for candidate in leaders} if best >= MIN_SUPPORT else {}


def agreed(supports: list[dict[str, float]], candidates: int) -> str | None:
    """The candidate at least three voters put first, and, among several candidates, by at least two votes more than
    the runner-up; at least one voter must back it with half of its support or more, not three weak signals."""
    votes = Counter()
    for support in supports:
        votes.update(ballot(support))
    (best, top), (_, second) = (votes.most_common(2) + [(None, 0)] * 2)[:2]
    strong = any(support.get(best, 0) >= STRONG_SUPPORT for support in supports)
    return best if top >= MIN_VOTES and strong and (candidates < 2 or top - second >= MIN_LEAD) else None
