"""Stats aggregation, the read-through cache and GET /api/stats, with a fake cache and fake
database source: no Redis, no PostgreSQL, no sleeping."""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.cache_wiring import get_stats_service
from app.domain import Category, Priority, Status
from app.main import create_app
from app.repositories.stats import GroupCount
from app.services.stats import CACHE_KEY, Stats, StatsService, build_stats


class FakeCache:
    def __init__(self) -> None:
        self.entries: dict[str, object] = {}
        self.set_calls: list[tuple[str, str, int]] = []
        self.deleted: list[str] = []
        self.fail_get: Exception | None = None
        self.fail_set: Exception | None = None
        self.fail_delete: Exception | None = None

    def get(self, key: str) -> str | None:
        if self.fail_get is not None:
            raise self.fail_get
        return self.entries.get(key)  # type: ignore[return-value]

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        if self.fail_set is not None:
            raise self.fail_set
        self.set_calls.append((key, value, ttl_seconds))
        self.entries[key] = value

    def delete(self, key: str) -> None:
        if self.fail_delete is not None:
            raise self.fail_delete
        self.deleted.append(key)
        self.entries.pop(key, None)


class FakeSource:
    """Stands in for the database. Counts reads so tests can see when it was consulted."""

    def __init__(self, groups: list[GroupCount] | None = None) -> None:
        self.groups = groups or []
        self.reads = 0
        self.fail: Exception | None = None

    def __call__(self) -> list[GroupCount]:
        self.reads += 1
        if self.fail is not None:
            raise self.fail
        return list(self.groups)


GROUPS = [
    GroupCount(Category.WATER, Priority.HIGH, Status.OPEN, 3),
    GroupCount(Category.WATER, Priority.LOW, Status.RESOLVED, 1),
    GroupCount(Category.ROADS, Priority.NORMAL, Status.OPEN, 2),
]


def _service(cache: FakeCache, source: FakeSource, ttl: int = 30) -> StatsService:
    return StatsService(cache, source, ttl_seconds=ttl)


# ---- aggregation ----


def test_build_stats_counts_each_breakdown() -> None:
    stats = build_stats(GROUPS)

    assert stats.total == 6
    assert stats.by_category[Category.WATER] == 4
    assert stats.by_category[Category.ROADS] == 2
    assert stats.by_priority == {Priority.HIGH: 3, Priority.NORMAL: 2, Priority.LOW: 1}
    assert stats.by_status[Status.OPEN] == 5
    assert stats.by_status[Status.RESOLVED] == 1


def test_build_stats_has_every_enum_value_with_zeros_when_there_are_no_rows() -> None:
    stats = build_stats([])

    assert stats.model_dump(mode="json") == {
        "total": 0,
        "by_category": {c.value: 0 for c in Category},
        "by_priority": {p.value: 0 for p in Priority},
        "by_status": {s.value: 0 for s in Status},
    }


def test_missing_combinations_are_filled_with_zero() -> None:
    stats = build_stats(GROUPS)

    assert stats.by_category[Category.SANITATION] == 0
    assert stats.by_status[Status.REJECTED] == 0
    assert set(stats.by_category) == set(Category)


# ---- read-through behaviour ----


def test_first_call_is_a_miss_and_the_second_is_a_hit_with_the_same_numbers() -> None:
    source = FakeSource(GROUPS)
    service = _service(FakeCache(), source)

    first, first_hit = service.get()
    second, second_hit = service.get()

    assert (first_hit, second_hit) == (False, True)
    assert first == second
    assert source.reads == 1  # the hit never touched the database


def test_the_entry_is_stored_with_the_configured_ttl() -> None:
    cache = FakeCache()

    _service(cache, FakeSource(GROUPS), ttl=30).get()

    assert [(key, ttl) for key, _, ttl in cache.set_calls] == [(CACHE_KEY, 30)]


def test_the_ttl_comes_from_the_setting_not_a_constant() -> None:
    cache = FakeCache()

    _service(cache, FakeSource(GROUPS), ttl=7).get()

    assert cache.set_calls[0][2] == 7


def test_invalidate_makes_the_next_call_a_miss_that_sees_new_data() -> None:
    source = FakeSource(GROUPS)
    service = _service(FakeCache(), source)
    before, _ = service.get()

    source.groups = [*GROUPS, GroupCount(Category.OTHER, Priority.LOW, Status.OPEN, 1)]
    _, stale_hit = service.get()
    service.invalidate()
    after, fresh_hit = service.get()

    assert stale_hit is True  # without invalidation the old numbers are served
    assert fresh_hit is False
    assert (before.total, after.total) == (6, 7)


def test_invalidate_deletes_the_stats_key() -> None:
    cache = FakeCache()
    service = _service(cache, FakeSource(GROUPS))
    service.get()

    service.invalidate()

    assert cache.deleted == [CACHE_KEY]
    assert CACHE_KEY not in cache.entries


def test_invalidate_never_raises_when_the_cache_is_down(caplog: pytest.LogCaptureFixture) -> None:
    cache = FakeCache()
    cache.fail_delete = ConnectionError("redis is down")

    with caplog.at_level(logging.WARNING, logger="civicpulse.stats"):
        _service(cache, FakeSource()).invalidate()

    assert "invalidation failed" in caplog.text


# ---- a broken cache degrades to the database, it never fails the request ----

VALID = build_stats(GROUPS).model_dump_json()
ZEROS = {"water": 0, "electricity": 0, "sanitation": 0, "roads": 0, "streetlights": 0, "other": 0}
PRIORITIES = {"high": 0, "normal": 0, "low": 0}
STATUSES = {"open": 0, "in_progress": 0, "resolved": 0, "rejected": 0}


def _entry(**overrides: object) -> str:
    body = {
        "total": 0,
        "by_category": ZEROS,
        "by_priority": PRIORITIES,
        "by_status": STATUSES,
        **overrides,
    }
    return json.dumps(body)


BAD_ENTRIES: list[object] = [
    "not json at all",
    "",
    "[]",
    "null",
    "42",
    "{}",
    _entry(total="six"),  # wrong type
    _entry(total=-1),
    _entry(by_category={**ZEROS, "water": "many"}),
    _entry(by_category={k: v for k, v in ZEROS.items() if k != "other"}),  # missing key
    _entry(by_category={**ZEROS, "potholes": 0}),  # unknown key
    _entry(by_priority={**PRIORITIES, "high": 5}),  # does not add up to total
    '{"total": 0, "by_category": {}, "by_priority": {}, "by_status": {}}',
    b"\xff\xfe",  # not text
    12345,  # not even a string
]


@pytest.mark.parametrize("entry", BAD_ENTRIES)
def test_an_unusable_cache_entry_is_a_miss_and_is_replaced(entry: object) -> None:
    cache = FakeCache()
    cache.entries[CACHE_KEY] = entry
    source = FakeSource(GROUPS)
    service = _service(cache, source)

    stats, hit = service.get()
    _, next_hit = service.get()

    assert hit is False
    assert stats.total == 6
    assert next_hit is True  # the bad entry was overwritten with a good one


def test_a_valid_entry_is_served_as_is() -> None:
    cache = FakeCache()
    cache.entries[CACHE_KEY] = VALID
    source = FakeSource()

    stats, hit = _service(cache, source).get()

    assert hit is True
    assert stats.total == 6
    assert source.reads == 0


@pytest.mark.parametrize("failing", ["fail_get", "fail_set"])
def test_a_cache_that_raises_still_yields_stats_as_a_miss(
    failing: str, caplog: pytest.LogCaptureFixture
) -> None:
    cache = FakeCache()
    setattr(cache, failing, ConnectionError("redis is down"))
    service = _service(cache, FakeSource(GROUPS))

    with caplog.at_level(logging.WARNING, logger="civicpulse.stats"):
        stats, hit = service.get()

    assert (stats.total, hit) == (6, False)
    assert "stats cache" in caplog.text


def test_a_database_failure_is_not_swallowed() -> None:
    source = FakeSource()
    source.fail = RuntimeError("database is down")

    with pytest.raises(RuntimeError):
        _service(FakeCache(), source).get()


# ---- the Stats schema ----


def test_stats_rejects_a_breakdown_that_does_not_add_up() -> None:
    with pytest.raises(ValidationError):
        Stats(
            total=1,
            by_category=dict.fromkeys(Category, 0),
            by_priority=dict.fromkeys(Priority, 0),
            by_status=dict.fromkeys(Status, 0),
        )


# ---- GET /api/stats ----


def _client(cache: FakeCache, source: FakeSource) -> TestClient:
    app = create_app()
    service = _service(cache, source)
    app.dependency_overrides[get_stats_service] = lambda: service
    return TestClient(app)


def test_route_reports_miss_then_hit_in_the_x_cache_header() -> None:
    client = _client(FakeCache(), FakeSource(GROUPS))

    first = client.get("/api/stats")
    second = client.get("/api/stats")

    assert first.status_code == second.status_code == 200
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert first.json() == second.json()


def test_route_body_has_the_documented_shape_with_every_value_present() -> None:
    body = _client(FakeCache(), FakeSource(GROUPS)).get("/api/stats").json()

    assert set(body) == {"total", "by_category", "by_priority", "by_status"}
    assert body["total"] == 6
    assert body["by_category"] == {
        "water": 4,
        "electricity": 0,
        "sanitation": 0,
        "roads": 2,
        "streetlights": 0,
        "other": 0,
    }
    assert set(body["by_priority"]) == {"high", "normal", "low"}
    assert set(body["by_status"]) == {"open", "in_progress", "resolved", "rejected"}


def test_route_with_no_complaints_returns_zeros() -> None:
    body = _client(FakeCache(), FakeSource()).get("/api/stats").json()

    assert body["total"] == 0
    assert set(body["by_category"].values()) == {0}


def test_route_is_200_and_miss_when_the_cache_is_down() -> None:
    cache = FakeCache()
    cache.fail_get = cache.fail_set = ConnectionError("redis is down")

    response = _client(cache, FakeSource(GROUPS)).get("/api/stats")

    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS"
    assert response.json()["total"] == 6


def test_route_is_200_and_miss_for_a_corrupted_entry() -> None:
    cache = FakeCache()
    cache.entries[CACHE_KEY] = "{corrupt"

    response = _client(cache, FakeSource(GROUPS)).get("/api/stats")

    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS"


def test_route_sees_a_new_complaint_immediately_after_invalidate() -> None:
    cache = FakeCache()
    source = FakeSource(GROUPS)
    app = create_app()
    service = _service(cache, source)
    app.dependency_overrides[get_stats_service] = lambda: service
    client = TestClient(app)
    assert client.get("/api/stats").json()["total"] == 6

    source.groups = [*GROUPS, GroupCount(Category.OTHER, Priority.LOW, Status.OPEN, 1)]
    service.invalidate()
    response = client.get("/api/stats")

    assert response.json()["total"] == 7
    assert response.headers["X-Cache"] == "MISS"
