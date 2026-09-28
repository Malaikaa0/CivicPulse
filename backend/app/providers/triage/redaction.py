"""Removes personal identifiers from complaint text before it is sent to a hosted LLM.

What this does NOT do: detect names or street addresses written inside the free text. That
cannot be done reliably with patterns, and pretending otherwise would be worse than saying so.
The residual risk is documented in ADR-0004; deployments that cannot accept it should run
TRIAGE_PROVIDER=rules, which never sends text anywhere.

Design rule: over-redacting a phone number costs the model nothing (it triages on the words
around it), but redacting "Street 12" or "three days" would damage the triage. So numbers are
only removed when they are long enough to be an identifier.
"""

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# A run of digits that may be separated by spaces, dashes, dots or brackets, optionally led by +.
# Whether it is really a phone number or ID is decided by counting digits (see _redact_number).
_NUMBER_RUN = re.compile(r"(?<![\w])\+?\(?\d[\d\s().\-]{6,}\d(?![\w])")

# Fewer digits than this is a house number, a street number, an amount or a duration.
_MIN_IDENTIFIER_DIGITS = 9


def _redact_number(match: re.Match[str]) -> str:
    digits = sum(ch.isdigit() for ch in match.group())
    return "[NUMBER]" if digits >= _MIN_IDENTIFIER_DIGITS else match.group()


def redact_pii(text: str) -> str:
    """Replace emails and identifier-length numbers (phones, national ids) with placeholders."""
    text = _EMAIL.sub("[EMAIL]", text)
    return _NUMBER_RUN.sub(_redact_number, text)
