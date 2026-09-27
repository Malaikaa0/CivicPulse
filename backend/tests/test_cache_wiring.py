"""The builders assemble the objects from settings. Nothing here connects to Redis or the
database: both clients connect lazily, on first use."""

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import sessionmaker

from app import cache_wiring
from app.config import Settings
from app.providers.cache import RedisCache
from app.services.rate_limit import RateLimiter
from app.services.stats import StatsService

CACHED_BUILDERS = (
    cache_wiring.get_cache,
    cache_wiring.get_session_factory,
    cache_wiring.get_stats_service,
    cache_wiring.get_rate_limiter,
)


@pytest.fixture(autouse=True)
def _fresh_builders() -> Iterator[None]:
    for builder in CACHED_BUILDERS:
        builder.cache_clear()
    yield
    for builder in CACHED_BUILDERS:
        builder.cache_clear()


def test_the_rate_limiter_reads_its_limit_and_window_from_settings() -> None:
    limiter = cache_wiring.build_rate_limiter(
        RedisCache("redis://example:6379/0"),
        Settings(rate_limit_requests=7, rate_limit_window_seconds=11),
    )

    assert (limiter.limit, limiter.window_seconds) == (7, 11)


def test_the_cached_builders_return_one_shared_instance_each() -> None:
    assert isinstance(cache_wiring.get_cache(), RedisCache)
    assert isinstance(cache_wiring.get_session_factory(), sessionmaker)
    assert isinstance(cache_wiring.get_stats_service(), StatsService)
    assert isinstance(cache_wiring.get_rate_limiter(), RateLimiter)
    for builder in CACHED_BUILDERS:
        assert builder() is builder()
