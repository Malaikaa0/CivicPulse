"""Wiring for the Redis-backed features: the stats cache and the rate limiter.

They share the process-wide engine and Redis client from app.resources.
"""

from collections.abc import Callable
from functools import lru_cache

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.providers.cache import RedisCache
from app.repositories.stats import GroupCount, StatsRepository
from app.resources import get_cache, get_session_factory
from app.services.rate_limit import RateLimiter
from app.services.stats import StatsService

# get_cache and get_session_factory are re-exported: they used to live here.
__all__ = [
    "build_rate_limiter",
    "build_stats_service",
    "get_cache",
    "get_rate_limiter",
    "get_session_factory",
    "get_stats_service",
]


def build_stats_service(
    cache: RedisCache, session_factory: Callable[[], Session], settings: Settings
) -> StatsService:
    def load_groups() -> list[GroupCount]:
        with session_factory() as session:
            return StatsRepository(session).group_counts()

    return StatsService(cache, load_groups, ttl_seconds=settings.stats_cache_ttl_seconds)


def build_rate_limiter(cache: RedisCache, settings: Settings) -> RateLimiter:
    return RateLimiter(
        cache,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


@lru_cache
def get_stats_service() -> StatsService:
    return build_stats_service(get_cache(), get_session_factory(), get_settings())


@lru_cache
def get_rate_limiter() -> RateLimiter:
    return build_rate_limiter(get_cache(), get_settings())
