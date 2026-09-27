"""Wiring for the Redis-backed features: the stats cache and the rate limiter.

Kept apart from dependencies.py for now; the lead can fold the two together. One Redis client and
one database engine are built lazily and shared by every request (both are thread-safe pools).
"""

from collections.abc import Callable
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.providers.cache import RedisCache
from app.repositories.database import create_db_engine
from app.repositories.stats import GroupCount, StatsRepository
from app.services.rate_limit import RateLimiter
from app.services.stats import StatsService


def build_cache(settings: Settings) -> RedisCache:
    return RedisCache(settings.redis_url)


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
def get_cache() -> RedisCache:
    return build_cache(get_settings())


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(create_db_engine(get_settings().database_url), expire_on_commit=False)


@lru_cache
def get_stats_service() -> StatsService:
    return build_stats_service(get_cache(), get_session_factory(), get_settings())


@lru_cache
def get_rate_limiter() -> RateLimiter:
    return build_rate_limiter(get_cache(), get_settings())
