"""Deterministic keyword triage. The fallback that is always available and never fails.

Scoring is weighted: a decisive word ("streetlight") outweighs several incidental ones
("bijli", "load shedding" in a complaint that is really about a lamp). Ties are broken by a
fixed precedence, so the same text always gives the same answer.
"""

import re

from app.domain import Category, Priority
from app.providers.triage.base import TriageResult

# category -> {regex: weight}. Urdu words are common in real complaints, so they are included.
_CATEGORY_KEYWORDS: dict[Category, dict[str, int]] = {
    Category.STREETLIGHTS: {
        r"\bstreet ?lights?\b": 6,
        r"\blight pole\b": 3,
        r"\blamps?\b": 2,
        r"\bflicker": 1,
    },
    Category.SANITATION: {
        r"\bsewer(age)?\b": 3,
        r"\bsewage\b": 3,
        r"\bmanholes?\b": 3,
        r"\bgarbage\b": 3,
        r"\bkachra\b": 3,
        r"\bdustbins?\b": 3,
        r"\btoilets?\b": 3,
        r"\bnali\b": 3,
        r"\bdrains?\b": 2,
        r"\bgutter\b": 3,
        r"\bmosquito": 1,
        r"\bstink": 1,
    },
    Category.WATER: {
        r"\bwater main\b": 3,
        r"\bwater connection\b": 3,
        r"\btanker": 2,
        r"\bpani\b": 2,
        r"\bpipes?\b": 2,
        r"\bleak": 2,
        r"\btap\b": 1,
        r"\bwater\b": 1,
        r"\bpressure\b": 1,
        r"\bsupply\b": 1,
    },
    Category.ELECTRICITY: {
        r"\belectricity\b": 3,
        r"\belectric\b": 2,
        r"\btransformer": 3,
        r"\bvoltage\b": 3,
        r"\bload ?shedding\b": 3,
        r"\bbijli\b": 2,
        r"\bwires?\b": 2,
        r"\bmeter\b": 2,
        r"\bbill\b": 1,
        r"\bpole\b": 1,
        r"\bspark": 1,
        r"\boutage": 2,
    },
    Category.ROADS: {
        r"\bpotholes?\b": 3,
        r"\bspeed breakers?\b": 3,
        r"\bzebra\b": 2,
        r"\bcaved\b": 2,
        r"\basphalt\b|\btarmac\b": 2,
        r"\bdigging\b": 1,
        r"\bhole\b": 1,
        r"\broad\b": 1,
    },
    Category.OTHER: {
        r"\bstray\b": 3,
        r"\bdogs?\b": 2,
        r"\bencroach": 3,
        r"\bloudspeaker": 3,
        r"\bnoise\b": 2,
        r"\bswings?\b": 2,
        r"\bbench(es)?\b": 2,
    },
}

# When categories tie, the more specific one wins.
_PRECEDENCE = (
    Category.STREETLIGHTS,
    Category.SANITATION,
    Category.WATER,
    Category.ELECTRICITY,
    Category.ROADS,
    Category.OTHER,
)

_HIGH = re.compile(
    r"(?<!not )flood|\bburst\b|\bspark|\bfallen\b|\blive wire\b|\bopen manhole\b|"
    r"\bwithout cover\b|\buncovered\b|\bsick\b|\binjur|\bbitten\b|\bbite\b|\bdie\b|"
    r"\baccident|\bdanger|\burgent|\bemergency\b|\bfire\b|\bsnatch|\bdialysis\b|"
    r"\boverflow|\bcollapse|\bcaved\b|\bfell\b|"
    r"\b(since|for) (two|three|four|five|\d+) (days|weeks)\b"
)
_LOW = re.compile(r"\bsmall\b|\bminor\b|\bslight|\bfaded\b|\bflicker|\birritating\b|\brust")

_SUMMARY_LIMIT = 140


def _summarise(text: str) -> str:
    """First sentence, whitespace collapsed, cut on a word boundary to fit the limit."""
    flat = " ".join(text.split())
    sentence = re.split(r"(?<=[.!?])\s", flat, maxsplit=1)[0]
    if len(sentence) <= _SUMMARY_LIMIT:
        return sentence
    cut = sentence[: _SUMMARY_LIMIT - 1].rsplit(" ", 1)[0]
    return cut + "…"


def _score(text: str) -> dict[Category, int]:
    return {
        category: sum(weight for pattern, weight in keywords.items() if re.search(pattern, text))
        for category, keywords in _CATEGORY_KEYWORDS.items()
    }


class RuleBasedTriage:
    name = "rules"

    def triage(self, text: str, location: str) -> TriageResult:
        lowered = text.lower()

        scores = _score(lowered)
        best = max(scores.values())
        category = next(c for c in _PRECEDENCE if scores[c] == best) if best > 0 else Category.OTHER

        if _HIGH.search(lowered):
            priority = Priority.HIGH
        elif _LOW.search(lowered):
            priority = Priority.LOW
        else:
            priority = Priority.NORMAL

        # Keyword rules are never very sure of themselves; an unmatched text is a guess.
        confidence = min(0.85, 0.5 + 0.05 * best) if best > 0 else 0.3

        return TriageResult(
            category=category,
            priority=priority,
            summary=_summarise(text) or "Complaint received",
            confidence=confidence,
        )
