"""What can go wrong when asking a provider to triage, and whether trying again could help.

Providers translate their own failures (HTTP status, SDK exception, timeout) into these, so the
retry and fallback logic depends on this taxonomy and never on a particular SDK.
"""


class TriageError(Exception):
    # Only failures that are plausibly transient are worth a second attempt.
    retryable: bool = False


class TriageTimeout(TriageError):
    retryable = True


class TriageRateLimited(TriageError):
    """HTTP 429 from the provider."""

    retryable = True


class TriageServerError(TriageError):
    """HTTP 5xx from the provider."""

    retryable = True


class TriageBadRequest(TriageError):
    """HTTP 400: our request was wrong and will be wrong again, so never retry it."""

    retryable = False


class TriageInvalidOutput(TriageError):
    """The provider answered, but not with anything that validates as a TriageResult."""

    retryable = False
