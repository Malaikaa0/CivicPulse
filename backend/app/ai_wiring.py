"""Wiring for the AI layer: builds the triage service, its cache and the outcome log from settings.

The Redis store and the outcome log are cached per URL: they are shared, thread-safe clients.
"""

import logging
from collections.abc import Callable, Sequence
from functools import lru_cache

from app.config import Settings, get_settings
from app.providers.triage.caching import CacheCounters, CachingTriage
from app.providers.triage.factory import create_triage_provider
from app.providers.triage.store import RedisTriageStore, TriageStore
from app.services.meta import MetaService, describe_provider
from app.services.triage import TriageOutcome, TriageService
from app.services.triage_outcomes import TriageOutcomeLog

logger = logging.getLogger("civicpulse.triage")


@lru_cache
def get_triage_store(redis_url: str) -> TriageStore:
    return RedisTriageStore(redis_url)


@lru_cache
def get_outcome_log(redis_url: str) -> TriageOutcomeLog:
    return TriageOutcomeLog(get_triage_store(redis_url))


def _record_to_all(
    recorders: Sequence[Callable[[TriageOutcome], None]],
) -> Callable[[TriageOutcome], None]:
    """One recorder that feeds several. A failing one must not starve the others."""

    def record(outcome: TriageOutcome) -> None:
        for recorder in recorders:
            try:
                recorder(outcome)
            except Exception as error:
                logger.warning("triage outcome recorder failed: %s", type(error).__name__)

    return record


def build_triage_service(
    settings: Settings,
    extra_recorders: Sequence[Callable[[TriageOutcome], None]] = (),
) -> TriageService:
    """The configured provider, behind the content-hash cache. Every outcome goes to the outcome
    log (for /api/meta/providers) and to each of extra_recorders (for example the metrics)."""
    provider = CachingTriage(
        create_triage_provider(settings),
        get_triage_store(settings.redis_url),
        settings.triage_cache_ttl_seconds,
    )
    recorders = [get_outcome_log(settings.redis_url).record, *extra_recorders]
    return TriageService(provider, recorder=_record_to_all(recorders))


def get_meta_service() -> MetaService:
    settings = get_settings()
    return MetaService(
        describe_provider(settings),
        get_outcome_log(settings.redis_url),
        CacheCounters(get_triage_store(settings.redis_url)),
    )
