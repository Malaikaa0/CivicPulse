"""What the operator sees on GET /api/meta/providers: the active provider and how it is doing."""

import logging
from dataclasses import dataclass

from app.config import Settings
from app.providers.triage.caching import CacheCounters, CacheStats
from app.services.triage_outcomes import RecordedOutcome, TriageOutcomeLog

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderInfo:
    active_provider: str
    vendor: str | None  # only meaningful for the hosted llm provider
    model: str | None


@dataclass(frozen=True)
class ProvidersReport:
    provider: ProviderInfo
    recent_outcomes: list[RecordedOutcome]
    cache: CacheStats | None
    # False when Redis could not be read: outcomes and cache figures are then absent, not zero.
    store_available: bool


def describe_provider(settings: Settings) -> ProviderInfo:
    """Which provider is configured. Reads names only; a key never passes through here."""
    if settings.triage_provider != "llm":
        return ProviderInfo(settings.triage_provider, vendor=None, model=None)
    model = settings.groq_model if settings.llm_vendor == "groq" else settings.gemini_model
    return ProviderInfo(settings.triage_provider, vendor=settings.llm_vendor, model=model)


class MetaService:
    def __init__(
        self, info: ProviderInfo, outcomes: TriageOutcomeLog, counters: CacheCounters
    ) -> None:
        self._info = info
        self._outcomes = outcomes
        self._counters = counters

    def providers_report(self) -> ProvidersReport:
        try:
            recent = self._outcomes.recent()
            cache = self._counters.snapshot()
        except Exception:  # an observability endpoint must still answer when Redis is down
            logger.warning("triage outcome store unavailable for /api/meta/providers")
            return ProvidersReport(self._info, [], None, store_available=False)
        return ProvidersReport(self._info, recent, cache, store_available=True)
