"""Stats cache and rate limiter against a real Redis (TEST_REDIS_URL) and, for the stats counts, a
real PostgreSQL (TEST_DATABASE_URL). Each group skips when its infrastructure is not configured.

    TEST_REDIS_URL=redis://127.0.0.1:56381/0
There is no sleeping: expiry is checked through Redis's own TTL, never by waiting for it.
"""

import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.cache_wiring import build_stats_service, get_rate_limiter
from app.cache_wiring import get_stats_service as stats_dependency
from app.config import Settings, get_settings
from app.domain import Category, Priority, Status
from app.main import create_app
from app.providers.cache import RedisCache
from app.repositories.models import Complaint
from app.repositories.stats import StatsRepository
from app.routes.rate_limit import enforce_rate_limit
from app.services.rate_limit import RateLimiter
from app.services.stats import CACHE_KEY, StatsService, build_stats

pytestmark = pytest.mark.integration

LIMIT = 3
WINDOW = 60
DEAD_REDIS = "redis://127.0.0.1:1/0"  # nothing listens on port 1: connection refused at once


@pytest.fixture(scope="module")
def redis_url() -> str:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")
    return url


@pytest.fixture
def raw(redis_url: str) -> Iterator[redis.Redis]:
    """A plain client for assertions the application code has no reason to make."""
    client: redis.Redis = redis.Redis.from_url(redis_url, decode_responses=True)
    client.delete(CACHE_KEY)
    yield client
    client.delete(CACHE_KEY)
    client.close()


@pytest.fixture
def prefix(raw: redis.Redis) -> Iterator[str]:
    """A rate-limit key prefix unique to the test, cleaned up afterwards."""
    name = f"test-rl:{uuid.uuid4().hex}"
    yield name
    for key in raw.scan_iter(f"{name}:*"):
        raw.delete(key)


@pytest.fixture
def cache(redis_url: str) -> RedisCache:
    return RedisCache(redis_url)


def _limiter(cache: RedisCache, prefix: str, limit: int = LIMIT) -> RateLimiter:
    return RateLimiter(cache, limit=limit, window_seconds=WINDOW, key_prefix=prefix)


def _add(session: Session, category: Category, priority: Priority, status: Status) -> None:
    session.add(
        Complaint(
            text="Burst water main flooding Street 12, water entering ground floors",
            location="Street 12",
            category=category,
            priority=priority,
            status=status,
            triaged_by="rules",
            triage_latency_ms=1,
        )
    )


# ---- RedisCache primitives ----


def test_set_get_delete_and_ttl_round_trip(cache: RedisCache, raw: redis.Redis) -> None:
    key = f"test-cache:{uuid.uuid4().hex}"
    try:
        assert cache.get(key) is None
        assert cache.ttl(key) == -2  # no such key

        cache.set(key, "value", 30)

        assert cache.get(key) == "value"
        assert 0 < cache.ttl(key) <= 30
        cache.delete(key)
        assert cache.get(key) is None
    finally:
        raw.delete(key)


def test_ping_succeeds_against_a_live_server(cache: RedisCache) -> None:
    cache.ping()


# ---- rate limiter: the distributed proof ----


def test_two_pods_share_one_count(redis_url: str, prefix: str) -> None:
    # Two RedisCache objects = two connection pools, as in two backend pods. An in-process
    # counter would give each pod its own count and allow 2 x LIMIT requests in total.
    pod_a = _limiter(RedisCache(redis_url), prefix)
    pod_b = _limiter(RedisCache(redis_url), prefix)

    decisions = [
        pod_a.check("203.0.113.7").allowed,
        pod_b.check("203.0.113.7").allowed,
        pod_a.check("203.0.113.7").allowed,
        pod_b.check("203.0.113.7").allowed,  # the 4th request overall, on the other pod
        pod_a.check("203.0.113.7").allowed,
    ]

    assert decisions == [True, True, True, False, False]


def test_other_clients_are_unaffected_by_a_blocked_one(cache: RedisCache, prefix: str) -> None:
    limiter = _limiter(cache, prefix)
    for _ in range(LIMIT + 1):
        limiter.check("203.0.113.7")

    assert limiter.check("203.0.113.7").allowed is False
    assert limiter.check("198.51.100.9").allowed is True


def test_the_expiry_is_set_atomically_with_the_first_hit(
    cache: RedisCache, raw: redis.Redis, prefix: str
) -> None:
    key = f"{prefix}:203.0.113.7"

    _limiter(cache, prefix).check("203.0.113.7")

    # Read straight after the first hit: the key must already carry its expiry.
    assert 0 < raw.ttl(key) <= WINDOW
    assert raw.get(key) == "1"


def test_a_counter_that_lost_its_expiry_is_repaired(
    cache: RedisCache, raw: redis.Redis, prefix: str
) -> None:
    # What a non-atomic INCR-then-EXPIRE would leave behind after a crash: a key that never ends.
    key = f"{prefix}:203.0.113.7"
    raw.set(key, 2)
    assert raw.ttl(key) == -1

    _limiter(cache, prefix).check("203.0.113.7")

    assert 0 < raw.ttl(key) <= WINDOW
    assert raw.get(key) == "3"


def test_retry_after_from_redis_is_a_whole_number_within_the_window(
    cache: RedisCache, prefix: str
) -> None:
    limiter = _limiter(cache, prefix, limit=1)
    limiter.check("203.0.113.7")

    decision = limiter.check("203.0.113.7")

    assert decision.allowed is False
    assert isinstance(decision.retry_after_seconds, int)
    assert 1 <= decision.retry_after_seconds <= WINDOW


def test_the_counter_script_reloads_after_the_server_forgets_it(
    cache: RedisCache, raw: redis.Redis, prefix: str
) -> None:
    limiter = _limiter(cache, prefix)
    limiter.check("203.0.113.7")

    raw.script_flush()  # as after a Redis restart or failover

    assert limiter.check("203.0.113.7").allowed is True
    assert raw.get(f"{prefix}:203.0.113.7") == "2"


def test_concurrent_requests_from_two_pods_never_exceed_the_limit(
    redis_url: str, prefix: str
) -> None:
    limit = 10
    pods = [_limiter(RedisCache(redis_url), prefix, limit=limit) for _ in range(2)]

    def hit(n: int) -> bool:
        return pods[n % 2].check("203.0.113.7").allowed

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(hit, range(60)))

    assert results.count(True) == limit


def test_the_limiter_fails_open_when_redis_is_unreachable() -> None:
    # The real redis-py connection error must be one the limiter catches. Two requests with a
    # limit of 1: the second would be blocked if the outage were counted.
    limiter = RateLimiter(RedisCache(DEAD_REDIS), limit=1, window_seconds=WINDOW)

    assert [limiter.check("203.0.113.7").allowed for _ in range(2)] == [True, True]


# ---- rate limiter over HTTP ----


def _tiny_app(cache: RedisCache, prefix: str, limit: int = LIMIT) -> FastAPI:
    app = FastAPI()

    @app.post("/things", dependencies=[Depends(enforce_rate_limit)])
    def create() -> dict[str, bool]:
        return {"ok": True}

    limiter = _limiter(cache, prefix, limit)
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_settings] = lambda: Settings()
    return app


def test_http_429_with_retry_after_after_the_limit(cache: RedisCache, prefix: str) -> None:
    client = TestClient(_tiny_app(cache, prefix))

    statuses = [client.post("/things").status_code for _ in range(LIMIT + 1)]
    blocked = client.post("/things")

    assert statuses == [200, 200, 200, 429]
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= WINDOW
    assert f"{LIMIT} requests" in blocked.json()["detail"]
    assert f"{WINDOW} seconds" in blocked.json()["detail"]


def test_http_limit_holds_across_two_separate_apps(redis_url: str, prefix: str) -> None:
    # Two complete apps, each with its own Redis client, standing in for two pods behind a
    # load balancer that spreads one client's requests between them.
    pod_a = TestClient(_tiny_app(RedisCache(redis_url), prefix))
    pod_b = TestClient(_tiny_app(RedisCache(redis_url), prefix))

    statuses = [
        pod_a.post("/things").status_code,
        pod_b.post("/things").status_code,
        pod_a.post("/things").status_code,
        pod_b.post("/things").status_code,
    ]

    assert statuses == [200, 200, 200, 429]


# ---- stats: real Redis with a real database ----


@pytest.fixture
def stats_service(engine: Engine, cache: RedisCache, raw: redis.Redis) -> StatsService:
    return build_stats_service(
        cache, sessionmaker(engine, expire_on_commit=False), Settings(stats_cache_ttl_seconds=30)
    )


def test_stats_counts_match_the_rows_in_postgres(session: Session) -> None:
    _add(session, Category.WATER, Priority.HIGH, Status.OPEN)
    _add(session, Category.WATER, Priority.HIGH, Status.IN_PROGRESS)
    _add(session, Category.ROADS, Priority.LOW, Status.OPEN)
    _add(session, Category.OTHER, Priority.NORMAL, Status.REJECTED)
    session.commit()

    stats = build_stats(StatsRepository(session).group_counts())

    assert stats.total == 4
    assert stats.by_category[Category.WATER] == 2
    assert stats.by_category[Category.ROADS] == 1
    assert stats.by_category[Category.OTHER] == 1
    assert stats.by_category[Category.ELECTRICITY] == 0
    assert stats.by_priority == {Priority.HIGH: 2, Priority.NORMAL: 1, Priority.LOW: 1}
    assert stats.by_status == {
        Status.OPEN: 2,
        Status.IN_PROGRESS: 1,
        Status.RESOLVED: 0,
        Status.REJECTED: 1,
    }


def test_stats_of_an_empty_table_has_every_value_at_zero(session: Session) -> None:
    stats = build_stats(StatsRepository(session).group_counts())

    assert stats.total == 0
    assert set(stats.by_category) == set(Category)
    assert set(stats.by_category.values()) == {0}
    assert set(stats.by_status) == set(Status)


def test_the_stats_key_gets_a_real_ttl_of_at_most_30_seconds(
    stats_service: StatsService, raw: redis.Redis
) -> None:
    _, hit = stats_service.get()

    assert hit is False
    assert 0 < raw.ttl(CACHE_KEY) <= 30


def test_second_call_is_a_hit_from_redis(stats_service: StatsService, session: Session) -> None:
    _add(session, Category.WATER, Priority.HIGH, Status.OPEN)
    session.commit()

    first, first_hit = stats_service.get()
    second, second_hit = stats_service.get()

    assert (first_hit, second_hit) == (False, True)
    assert first == second
    assert first.total == 1


def test_invalidate_deletes_the_key_and_the_next_read_sees_new_rows(
    stats_service: StatsService, session: Session, raw: redis.Redis
) -> None:
    stats_service.get()
    assert raw.exists(CACHE_KEY) == 1

    _add(session, Category.STREETLIGHTS, Priority.LOW, Status.OPEN)
    session.commit()
    _, still_cached = stats_service.get()
    stats_service.invalidate()

    assert raw.exists(CACHE_KEY) == 0
    fresh, hit = stats_service.get()
    assert still_cached is True  # before invalidation the old numbers are served
    assert hit is False
    assert fresh.total == 1
    assert fresh.by_category[Category.STREETLIGHTS] == 1


def test_a_corrupted_entry_in_redis_is_a_miss_and_is_replaced(
    stats_service: StatsService, raw: redis.Redis
) -> None:
    raw.set(CACHE_KEY, "{corrupt", ex=30)

    stats, hit = stats_service.get()

    assert hit is False
    assert stats.total == 0
    assert stats_service.get()[1] is True


def test_an_unreachable_redis_still_yields_stats_from_the_database(engine: Engine) -> None:
    service = build_stats_service(
        RedisCache(DEAD_REDIS), sessionmaker(engine), Settings(stats_cache_ttl_seconds=30)
    )

    stats, hit = service.get()
    service.invalidate()  # must not raise either

    assert hit is False
    assert stats.total == 0


def test_stats_endpoint_over_http_reports_miss_then_hit_and_updates_on_invalidate(
    stats_service: StatsService, session: Session
) -> None:
    app = create_app()
    app.dependency_overrides[stats_dependency] = lambda: stats_service
    client = TestClient(app)

    first = client.get("/api/stats")
    second = client.get("/api/stats")
    _add(session, Category.WATER, Priority.HIGH, Status.OPEN)
    session.commit()
    stats_service.invalidate()
    third = client.get("/api/stats")

    assert [r.headers["X-Cache"] for r in (first, second, third)] == ["MISS", "HIT", "MISS"]
    assert first.json()["total"] == second.json()["total"] == 0
    assert third.json()["total"] == 1
    assert third.json()["by_category"]["water"] == 1
