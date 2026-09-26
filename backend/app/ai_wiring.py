"""Wiring for the AI layer: builds the triage service, its cache and the outcome log from settings.

Kept apart from dependencies.py so the pieces can be composed and tested on their own. The
Redis store and the outcome log are cached per URL: they are shared, thread-safe clients.
"""

from functools import lru_cache

from app.config import Settings, get_settings
from app.providers.triage.caching import CacheCounters, CachingTriage
from app.providers.triage.factory import create_triage_provider
from app.providers.triage.store import RedisTriageStore, TriageStore
from app.services.meta import MetaService, describe_provider
from app.services.triage import TriageService
from app.services.triage_outcomes import TriageOutcomeLog


@lru_cache
def get_triage_store(redis_url: str) -> TriageStore:
    return RedisTriageStore(redis_url)


@lru_cache
def get_outcome_log(redis_url: str) -> TriageOutcomeLog:
    return TriageOutcomeLog(get_triage_store(redis_url))


def build_triage_service(settings: Settings) -> TriageService:
    """The configured provider, behind the content-hash cache, recording every outcome."""
    provider = CachingTriage(
        create_triage_provider(settings),
        get_triage_store(settings.redis_url),
        settings.triage_cache_ttl_seconds,
    )
    return TriageService(provider, recorder=get_outcome_log(settings.redis_url).record)


@lru_cache
def get_triage_service() -> TriageService:
    return build_triage_service(get_settings())


def get_meta_service() -> MetaService:
    settings = get_settings()
    return MetaService(
        describe_provider(settings),
        get_outcome_log(settings.redis_url),
        CacheCounters(get_triage_store(settings.redis_url)),
    )
