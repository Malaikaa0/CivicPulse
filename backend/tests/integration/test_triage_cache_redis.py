"""The triage cache, outcome log and /api/meta/providers against a real Redis.

Set TEST_REDIS_URL to run them, e.g. redis://127.0.0.1:56380/0. Without it they are skipped.
Only keys under the triage: prefix are touched, so a shared Redis is not wiped.
"""

import json
import os
from collections.abc import Iterator

import pytest
import redis
from fastapi.testclient import TestClient

from app.ai_wiring import get_meta_service
from app.config import Settings
from app.domain import Category, Priority
from app.main import create_app
from app.providers.triage.base import TriageResult
from app.providers.triage.caching import (
    HITS_KEY,
    MISSES_KEY,
    CacheCounters,
    CachingTriage,
)
from app.providers.triage.simulated import SimulatedTriage
from app.providers.triage.store import RedisTriageStore, TriageStoreError
from app.services.meta import MetaService, describe_provider
from app.services.triage import TriageOutcome, TriageService
from app.services.triage_outcomes import OUTCOMES_KEY, TriageOutcomeLog

pytestmark = pytest.mark.integration

TTL = 86400
TEXT = "Burst water main flooding Street 12, call me on 0300-1234567"


@pytest.fixture(scope="module")
def redis_url() -> str:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")
    return url


@pytest.fixture
def client(redis_url: str) -> Iterator[redis.Redis]:
    raw = redis.Redis.from_url(redis_url, decode_responses=True)
    _clear(raw)
    yield raw
    _clear(raw)
    raw.close()


def _clear(raw: redis.Redis) -> None:
    for key in raw.scan_iter("triage:*"):
        raw.delete(key)


@pytest.fixture
def store(redis_url: str, client: redis.Redis) -> RedisTriageStore:
    return RedisTriageStore(redis_url)


def _outcome(latency_ms: int) -> TriageOutcome:
    result = TriageResult(
        category=Category.WATER, priority=Priority.HIGH, summary="Burst main", confidence=0.9
    )
    return TriageOutcome(
        result=result, triaged_by="llm:gemini", latency_ms=latency_ms, fallback=False
    )


def test_set_and_get_round_trip_with_a_real_ttl(
    store: RedisTriageStore, client: redis.Redis
) -> None:
    store.set("triage:v1:abc", "value", TTL)

    assert store.get("triage:v1:abc") == "value"
    assert 0 < client.ttl("triage:v1:abc") <= TTL


def test_get_of_a_missing_key_is_none(store: RedisTriageStore) -> None:
    assert store.get("triage:v1:missing") is None


def test_incr_counts_up_from_nothing(store: RedisTriageStore) -> None:
    assert [store.incr("triage:stats:x") for _ in range(3)] == [1, 2, 3]


def test_push_capped_keeps_the_newest_entries_first(
    store: RedisTriageStore, client: redis.Redis
) -> None:
    for n in range(25):
        store.push_capped("triage:list", str(n), 20)

    assert client.llen("triage:list") == 20
    assert store.list_range("triage:list", 0, 19) == [str(n) for n in range(24, 4, -1)]
    assert store.list_range("triage:list", 0, 2) == ["24", "23", "22"]


def test_an_unreachable_redis_raises_a_store_error(redis_url: str) -> None:
    dead = RedisTriageStore("redis://127.0.0.1:1/0", timeout_seconds=0.2)

    with pytest.raises(TriageStoreError):
        dead.get("k")


def test_the_cache_hits_on_a_second_identical_complaint(
    store: RedisTriageStore, client: redis.Redis
) -> None:
    provider = SimulatedTriage()
    cache = CachingTriage(provider, store, TTL)

    first = cache.triage(TEXT, "Street 12")
    second = cache.triage(TEXT.upper(), "Street 12")

    assert provider.calls == 1
    assert second.category == first.category == Category.WATER
    assert cache.stats().hits == 1
    assert cache.stats().misses == 1
    assert cache.hit_rate() == 0.5
    [key] = client.scan_iter("triage:v1:*")
    assert 0 < client.ttl(key) <= TTL


def test_redis_holds_no_raw_pii(store: RedisTriageStore, client: redis.Redis) -> None:
    CachingTriage(SimulatedTriage(), store, TTL).triage(TEXT, "Street 12")

    dump = ""
    for key in client.scan_iter("triage:*"):
        dump += f"{key}\n"
        if client.type(key) == "string":
            dump += f"{client.get(key)}\n"
    assert "0300" not in dump
    assert "1234567" not in dump


@pytest.mark.parametrize("damage", ["{oops", "[1,2]", '{"category":"invented"}', ""])
def test_a_corrupted_entry_in_redis_is_a_miss_and_is_replaced(
    store: RedisTriageStore, client: redis.Redis, damage: str
) -> None:
    provider = SimulatedTriage()
    cache = CachingTriage(provider, store, TTL)
    key = cache.cache_key(TEXT)
    client.set(key, damage, ex=TTL)

    result = cache.triage(TEXT, "Street 12")

    assert result.category == Category.WATER
    assert provider.calls == 1
    assert TriageResult.model_validate_json(client.get(key) or "") is not None
    cache.triage(TEXT, "Street 12")
    assert provider.calls == 1


def test_counters_use_real_incr(store: RedisTriageStore, client: redis.Redis) -> None:
    cache = CachingTriage(SimulatedTriage(), store, TTL)

    for text in (TEXT, TEXT, TEXT, "Streetlight out near the school"):
        cache.triage(text, "x")

    assert client.get(HITS_KEY) == "2"
    assert client.get(MISSES_KEY) == "2"
    assert CacheCounters(store).snapshot().hit_rate == 0.5


def test_the_outcome_list_is_capped_in_redis(store: RedisTriageStore, client: redis.Redis) -> None:
    log = TriageOutcomeLog(store)

    for latency in range(1, 26):
        log.record(_outcome(latency))

    assert client.llen(OUTCOMES_KEY) == 20
    assert [o.latency_ms for o in log.recent()] == list(range(25, 5, -1))
    assert set(json.loads(client.lindex(OUTCOMES_KEY, 0) or "{}")) == {
        "provider",
        "latency_ms",
        "fallback",
        "at",
    }


def test_meta_endpoint_reflects_real_traffic(store: RedisTriageStore) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://u:p@db/x",
        redis_url="redis://unused:6379/0",
        triage_provider="simulated",
    )
    service = TriageService(
        CachingTriage(SimulatedTriage(), store, TTL), recorder=TriageOutcomeLog(store).record
    )
    for text in (TEXT, TEXT.lower(), "Streetlight out near the school"):
        service.triage(text, "x")

    meta = MetaService(describe_provider(settings), TriageOutcomeLog(store), CacheCounters(store))
    app = create_app()
    app.dependency_overrides[get_meta_service] = lambda: meta

    response = TestClient(app).get("/api/meta/providers")

    body = response.json()
    assert response.status_code == 200
    assert body["active_provider"] == "simulated"
    assert body["outcome_store_available"] is True
    assert len(body["recent_outcomes"]) == 3
    assert {o["provider"] for o in body["recent_outcomes"]} == {"rules"}
    assert body["cache"] == {"hits": 1, "misses": 2, "hit_rate": pytest.approx(1 / 3)}


def test_meta_endpoint_answers_200_when_redis_is_unreachable(redis_url: str) -> None:
    dead = RedisTriageStore("redis://127.0.0.1:1/0", timeout_seconds=0.2)
    settings = Settings(
        database_url="postgresql+psycopg://u:p@db/x",
        redis_url="redis://unused:6379/0",
        triage_provider="simulated",
    )
    meta = MetaService(describe_provider(settings), TriageOutcomeLog(dead), CacheCounters(dead))
    app = create_app()
    app.dependency_overrides[get_meta_service] = lambda: meta

    response = TestClient(app).get("/api/meta/providers")

    assert response.status_code == 200
    assert response.json()["outcome_store_available"] is False
    assert response.json()["cache"]["hits"] is None


def test_triage_survives_an_unreachable_redis(redis_url: str) -> None:
    dead = RedisTriageStore("redis://127.0.0.1:1/0", timeout_seconds=0.2)
    provider = SimulatedTriage()
    service = TriageService(
        CachingTriage(provider, dead, TTL), recorder=TriageOutcomeLog(dead).record
    )

    outcome = service.triage(TEXT, "Street 12")

    assert outcome.fallback is False
    assert outcome.result.category == Category.WATER
    assert provider.calls == 1
