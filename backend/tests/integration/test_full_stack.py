"""The whole application over real PostgreSQL and real Redis, through HTTP.

Each piece has its own tests; these prove the connections between them. Needs TEST_DATABASE_URL
and TEST_REDIS_URL (skipped without them). TEST_REDIS_URL must point at a disposable Redis: the
fixture empties its database before and after every test.
"""

import logging
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app import ai_wiring, cache_wiring, dependencies, lifecycle, resources
from app.config import get_settings
from app.main import create_app
from app.providers.triage.errors import TriageBadRequest
from app.providers.triage.simulated import SimulatedTriage

pytestmark = pytest.mark.integration

CREATE = {
    "text": "Burst water main flooding Street 12, water entering ground floors",
    "location": "Street 12",
}
LIMIT = 5


def _clear_caches() -> None:
    for cached in (
        get_settings,
        resources.get_engine,
        resources.get_cache,
        resources.get_session_factory,
        dependencies.get_readiness_service,
        cache_wiring.get_stats_service,
        cache_wiring.get_rate_limiter,
        ai_wiring.get_triage_store,
        ai_wiring.get_outcome_log,
    ):
        cached.cache_clear()


@pytest.fixture
def stack(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("TEST_REDIS_URL is not set")

    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("TRIAGE_PROVIDER", "simulated")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", str(LIMIT))
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("STATS_CACHE_TTL_SECONDS", "30")
    monkeypatch.setenv("TRIAGE_CACHE_TTL_SECONDS", "600")

    lifecycle.close_all()
    _clear_caches()
    admin = redis.Redis.from_url(redis_url)
    admin.flushdb()
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE complaints"))

    with TestClient(create_app()) as client:  # the lifespan releases the connections on exit
        yield client

    _clear_caches()
    admin.flushdb()
    admin.close()
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE complaints"))


def _stats(client: TestClient) -> tuple[str, dict[str, Any]]:
    response = client.get("/api/stats")
    assert response.status_code == 200
    return response.headers["X-Cache"], response.json()


# ---- the stats cache is invalidated by writes ----


def test_a_new_complaint_shows_in_the_stats_immediately_not_after_the_ttl(
    stack: TestClient,
) -> None:
    first_state, before = _stats(stack)
    second_state, _ = _stats(stack)
    assert (first_state, second_state) == ("MISS", "HIT")  # the cache is really in use

    assert stack.post("/api/complaints", json=CREATE).status_code == 201

    state, after = _stats(stack)
    assert state == "MISS"  # explicit invalidation, well inside the 30 s TTL
    assert after["total"] == before["total"] + 1
    assert after["by_category"]["water"] == 1
    assert _stats(stack)[0] == "HIT"


def test_a_status_change_also_invalidates_the_stats(stack: TestClient) -> None:
    complaint = stack.post("/api/complaints", json=CREATE).json()
    _stats(stack)
    assert _stats(stack)[0] == "HIT"

    changed = stack.patch(
        f"/api/complaints/{complaint['id']}/status", json={"status": "in_progress"}
    )

    assert changed.status_code == 200
    state, stats = _stats(stack)
    assert state == "MISS"
    assert stats["by_status"]["in_progress"] == 1
    assert stats["by_status"]["open"] == 0


def test_a_failed_write_does_not_invalidate_the_stats(stack: TestClient) -> None:
    _stats(stack)
    assert _stats(stack)[0] == "HIT"

    rejected = stack.post("/api/complaints", json={"text": "short", "location": "x"})

    assert rejected.status_code == 400
    assert _stats(stack)[0] == "HIT"  # nothing was written, so nothing was dropped


# ---- the rate limiter: submission only ----


def test_submitting_is_rate_limited_but_reading_is_not(stack: TestClient) -> None:
    for _ in range(LIMIT):
        assert stack.post("/api/complaints", json=CREATE).status_code == 201

    blocked = stack.post("/api/complaints", json=CREATE)

    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert "Rate limit" in blocked.json()["detail"]
    # The limit is about submissions; everything else keeps working.
    assert stack.get("/api/complaints").status_code == 200
    assert stack.get("/api/stats").status_code == 200
    assert stack.get("/health").status_code == 200


def test_a_blocked_submission_is_not_stored(stack: TestClient) -> None:
    for _ in range(LIMIT + 2):
        stack.post("/api/complaints", json=CREATE)

    assert stack.get("/api/complaints").json()["total"] == LIMIT


# ---- the triage cache and outcome log are in the complaint flow ----


def test_duplicate_reports_cost_one_inference(stack: TestClient) -> None:
    # A burst main reported by three neighbours: same words, different casing and phone numbers.
    reports = [
        {"text": CREATE["text"] + " Call 0300-1112233", "location": "Street 12"},
        {"text": CREATE["text"].upper() + " call 0301-2223344", "location": "Street 12"},
        {"text": "  " + CREATE["text"] + "   Call 0302-3334455 ", "location": "Street 12"},
    ]

    for report in reports:
        assert stack.post("/api/complaints", json=report).status_code == 201

    meta = stack.get("/api/meta/providers").json()
    assert meta["cache"]["hits"] == 2
    assert meta["cache"]["misses"] == 1
    assert meta["cache"]["hit_rate"] == pytest.approx(2 / 3)
    assert len(meta["recent_outcomes"]) == 3


def test_the_triage_latency_is_stored_and_surfaced_and_they_agree(stack: TestClient) -> None:
    created = stack.post("/api/complaints", json=CREATE).json()

    (outcome,) = stack.get("/api/meta/providers").json()["recent_outcomes"]

    assert isinstance(created["triage_latency_ms"], int) and created["triage_latency_ms"] >= 0
    assert outcome["latency_ms"] == created["triage_latency_ms"]
    assert outcome["fallback"] is False
    assert outcome["provider"] == created["triaged_by"] == "rules"


def test_a_provider_failure_is_visible_in_the_response_the_meta_endpoint_the_metrics_and_the_log(
    stack: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # TriageBadRequest is not retryable, so this needs no real wait.
    monkeypatch.setattr(
        ai_wiring,
        "create_triage_provider",
        lambda settings: SimulatedTriage(always_fail=TriageBadRequest()),
    )

    with caplog.at_level(logging.WARNING, logger="civicpulse.complaints"):
        response = stack.post("/api/complaints", json=CREATE)

    # 1. The citizen still gets a triaged complaint, never an error.
    assert response.status_code == 201
    created = response.json()
    assert created["triaged_by"] == "rules:fallback"
    assert created["category"] == "water"
    # 2. The observability endpoint shows it.
    (outcome,) = stack.get("/api/meta/providers").json()["recent_outcomes"]
    assert outcome["fallback"] is True and outcome["provider"] == "rules:fallback"
    # 3. So do the metrics.
    metrics = stack.get("/metrics").text
    assert any(
        line.startswith("triage_fallbacks_total")
        and "TriageBadRequest" in line
        and line.endswith(" 1.0")
        for line in metrics.splitlines()
    )
    # 4. And exactly one WARNING names the complaint, the provider and the error class.
    (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning.complaint_id == created["id"]  # type: ignore[attr-defined]
    assert warning.provider == "simulated"  # type: ignore[attr-defined]
    assert warning.error_class == "TriageBadRequest"  # type: ignore[attr-defined]


# ---- shared connections and the normal flow ----


def test_readiness_and_requests_use_one_engine_and_one_redis_client(stack: TestClient) -> None:
    assert stack.get("/ready").status_code == 200
    stack.post("/api/complaints", json=CREATE)
    _stats(stack)

    assert resources.get_engine.cache_info().currsize == 1
    assert resources.get_cache.cache_info().currsize == 1


def test_the_whole_flow_from_submission_to_resolution(stack: TestClient) -> None:
    first = stack.post("/api/complaints", json=CREATE).json()
    second = stack.post(
        "/api/complaints",
        json={"text": "Streetlight outside the mosque is not working", "location": "Masjid Bilal"},
    ).json()

    assert first["status"] == "open" and first["allowed_transitions"] == ["in_progress", "rejected"]
    assert second["category"] == "streetlights"
    listing = stack.get("/api/complaints").json()
    assert [c["id"] for c in listing["items"]] == [second["id"], first["id"]]  # newest first
    assert stack.get("/api/complaints", params={"category": "water"}).json()["total"] == 1

    for status in ("in_progress", "resolved"):
        assert (
            stack.patch(
                f"/api/complaints/{first['id']}/status", json={"status": status}
            ).status_code
            == 200
        )
    blocked = stack.patch(f"/api/complaints/{first['id']}/status", json={"status": "open"})
    assert blocked.status_code == 409
    assert "resolved" in blocked.json()["detail"] and "open" in blocked.json()["detail"]
    assert stack.get(f"/api/complaints/{uuid.uuid4()}").status_code == 404
