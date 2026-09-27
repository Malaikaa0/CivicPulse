"""How the pieces are connected: one triage service per app, shared connections, and the rate
limiter on submission only. Nothing here needs a database or Redis."""

import re
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from triage_store_fake import FakeStore

from app import ai_wiring, cache_wiring, dependencies, lifecycle, resources
from app.config import Settings
from app.main import create_app
from app.metrics import Metrics
from app.providers.triage.errors import TriageServerError
from app.providers.triage.simulated import SimulatedTriage
from app.routes.rate_limit import enforce_rate_limit
from app.services.triage import TriageOutcome

TEXT = "Burst water main flooding Street 12, water entering ground floors"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://u:p@db/x",
        "redis_url": "redis://cache:6379/0",
        "triage_provider": "simulated",
        "gemini_api_key": SecretStr("unused"),
        **overrides,
    }
    return Settings.model_validate(values)


def _request(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app, "headers": []})


def _app_with_metrics() -> FastAPI:
    app = FastAPI()
    app.state.metrics = Metrics()
    return app


def _metrics_text(app: FastAPI) -> str:
    return app.state.metrics.render().decode()


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeStore]:
    store = FakeStore()
    monkeypatch.setattr(ai_wiring, "get_triage_store", lambda url: store)
    monkeypatch.setattr(dependencies, "get_settings", lambda: _settings())
    ai_wiring.get_outcome_log.cache_clear()
    yield store
    ai_wiring.get_outcome_log.cache_clear()


# ---- the triage service: one per app, feeding the outcome log AND the metrics ----


def test_the_triage_service_is_built_once_per_app(fake_store: FakeStore) -> None:
    app = _app_with_metrics()

    first = dependencies.get_triage_service(_request(app))

    assert first is dependencies.get_triage_service(_request(app))


def test_each_app_gets_its_own_service_and_its_own_metrics(fake_store: FakeStore) -> None:
    app_a, app_b = _app_with_metrics(), _app_with_metrics()

    service_a = dependencies.get_triage_service(_request(app_a))
    service_b = dependencies.get_triage_service(_request(app_b))
    service_a.triage(TEXT, "Street 12")

    assert service_a is not service_b
    assert 'triage_duration_seconds_count{triaged_by="rules"} 1.0' in _metrics_text(app_a)
    assert 'triage_duration_seconds_count{triaged_by="rules"}' not in _metrics_text(app_b)


def test_an_outcome_reaches_both_the_outcome_log_and_the_metrics(fake_store: FakeStore) -> None:
    app = _app_with_metrics()

    dependencies.get_triage_service(_request(app)).triage(TEXT, "Street 12")

    assert len(fake_store.lists["triage:outcomes"]) == 1  # /api/meta/providers reads this
    assert 'triage_duration_seconds_count{triaged_by="rules"} 1.0' in _metrics_text(app)


def test_a_fallback_is_counted_by_provider_and_error_class(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    monkeypatch.setattr(
        ai_wiring,
        "create_triage_provider",
        lambda settings: SimulatedTriage(always_fail=TriageServerError()),
    )
    app = _app_with_metrics()
    service = dependencies.get_triage_service(_request(app))
    # Skip the real retry wait: this test is about the counting, not the timing.
    service._sleep = lambda _: None

    outcome = service.triage(TEXT, "Street 12")

    assert outcome.fallback is True
    line = re.search(r"triage_fallbacks_total\{[^}]*\} (\S+)", _metrics_text(app))
    assert line is not None
    assert "TriageServerError" in line.group(0) and line.group(1) == "1.0"


def test_an_app_without_metrics_still_gets_a_working_triage_service(
    fake_store: FakeStore,
) -> None:
    service = dependencies.get_triage_service(_request(FastAPI()))

    assert service.triage(TEXT, "Street 12").triaged_by == "rules"
    assert len(fake_store.lists["triage:outcomes"]) == 1


def test_a_failing_recorder_does_not_starve_the_others(fake_store: FakeStore) -> None:
    seen: list[TriageOutcome] = []

    def broken(outcome: TriageOutcome) -> None:
        raise RuntimeError("metrics are down")

    service = ai_wiring.build_triage_service(_settings(), extra_recorders=[broken, seen.append])

    service.triage(TEXT, "Street 12")

    assert len(seen) == 1  # the recorder after the broken one still ran
    assert len(fake_store.lists["triage:outcomes"]) == 1  # and so did the outcome log


# ---- one engine and one Redis client, shared ----


def test_every_feature_shares_one_engine_and_one_redis_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[str] = []

    class FakeEngine:
        def dispose(self) -> None: ...

    class FakeCache:
        def __init__(self, url: str) -> None:
            built.append("redis")

        def ping(self) -> None: ...

        def close(self) -> None: ...

    def fake_engine(url: str) -> FakeEngine:
        built.append("engine")
        return FakeEngine()

    caches = (
        resources.get_engine,
        resources.get_cache,
        resources.get_session_factory,
        dependencies.get_readiness_service,
        cache_wiring.get_stats_service,
        cache_wiring.get_rate_limiter,
    )
    for cached in caches:
        cached.cache_clear()
    lifecycle.close_all()
    monkeypatch.setattr(resources, "create_db_engine", fake_engine)
    monkeypatch.setattr(resources, "RedisCache", FakeCache)
    try:
        dependencies.get_engine()  # what request sessions use
        dependencies.get_readiness_service()  # what /ready uses
        cache_wiring.get_stats_service()  # what /api/stats uses
        cache_wiring.get_rate_limiter()  # what the limiter uses

        assert sorted(built) == ["engine", "redis"]  # one of each, not one per feature
    finally:
        lifecycle.close_all()
        for cached in caches:
            cached.cache_clear()


# ---- only submission is rate limited ----


def test_only_submitting_a_complaint_is_rate_limited() -> None:
    # Swap the limiter for one that always refuses, then see which requests it stops. Bad input on
    # purpose: a refusal comes before validation, and everything else answers with a 400.
    def refuse() -> None:
        raise HTTPException(status_code=429, detail="limited")

    app = create_app()
    app.dependency_overrides[enforce_rate_limit] = refuse
    client = TestClient(app)

    assert client.post("/api/complaints", json={}).status_code == 429  # submission: limited
    assert client.get("/api/complaints", params={"page": 0}).status_code == 400  # not limited
    assert client.get("/api/complaints/not-a-uuid").status_code == 400
    assert client.patch("/api/complaints/not-a-uuid/status", json={}).status_code == 400
    assert client.get("/health").status_code == 200
