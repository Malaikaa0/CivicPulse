"""GET /api/meta/providers and the wiring behind it, with an in-memory store."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from triage_store_fake import FakeStore

from app import ai_wiring
from app.ai_wiring import build_triage_service, get_meta_service
from app.config import Settings
from app.domain import Category, Priority
from app.main import create_app
from app.providers.triage.base import TriageResult
from app.providers.triage.caching import CacheCounters, CachingTriage
from app.services.meta import MetaService, describe_provider
from app.services.triage import TriageOutcome, TriageService
from app.services.triage_outcomes import MAX_OUTCOMES, TriageOutcomeLog

T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
GEMINI_KEY = "AIza-gemini-secret-value"
GROQ_KEY = "gsk-groq-secret-value"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://u:p@db/x",
        "redis_url": "redis://cache:6379/0",
        "triage_provider": "simulated",
        "gemini_api_key": SecretStr(GEMINI_KEY),
        "groq_api_key": SecretStr(GROQ_KEY),
        **overrides,
    }
    return Settings.model_validate(values)


class Ticking:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        current, self.now = self.now, self.now + timedelta(seconds=1)
        return current


def _client(settings: Settings, store: FakeStore) -> TestClient:
    service = MetaService(
        describe_provider(settings),
        TriageOutcomeLog(store, clock=Ticking()),
        CacheCounters(store),
    )
    app = create_app()
    app.dependency_overrides[get_meta_service] = lambda: service
    return TestClient(app)


def _record(store: FakeStore, count: int, *, fallback: bool = False) -> None:
    result = TriageResult(
        category=Category.WATER, priority=Priority.HIGH, summary="Burst main", confidence=0.9
    )
    log = TriageOutcomeLog(store, clock=Ticking())
    for latency in range(1, count + 1):
        outcome = TriageOutcome(
            result=result, triaged_by="llm:gemini", latency_ms=latency, fallback=fallback
        )
        log.record(outcome)


# --- describe_provider -------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["simulated", "rules", "ollama"])
def test_vendor_and_model_are_null_unless_the_provider_is_llm(provider: str) -> None:
    info = describe_provider(_settings(triage_provider=provider))

    assert (info.active_provider, info.vendor, info.model) == (provider, None, None)


def test_llm_with_gemini_reports_vendor_and_model() -> None:
    info = describe_provider(
        _settings(triage_provider="llm", llm_vendor="gemini", gemini_model="gemini-x")
    )

    assert (info.active_provider, info.vendor, info.model) == ("llm", "gemini", "gemini-x")


def test_llm_with_groq_reports_vendor_and_its_own_model() -> None:
    info = describe_provider(
        _settings(triage_provider="llm", llm_vendor="groq", groq_model="llama-y")
    )

    assert (info.vendor, info.model) == ("groq", "llama-y")


def test_llm_with_groq_and_no_model_reports_a_null_model() -> None:
    info = describe_provider(_settings(triage_provider="llm", llm_vendor="groq"))

    assert (info.vendor, info.model) == ("groq", None)


# --- the endpoint ------------------------------------------------------------------------------


def test_response_shape_with_no_traffic_yet() -> None:
    response = _client(_settings(), FakeStore()).get("/api/meta/providers")

    assert response.status_code == 200
    assert response.json() == {
        "active_provider": "simulated",
        "vendor": None,
        "model": None,
        "recent_outcomes": [],
        "cache": {"hits": 0, "misses": 0, "hit_rate": None},
        "outcome_store_available": True,
    }


def test_response_lists_outcomes_newest_first_with_cache_figures() -> None:
    store = FakeStore()
    _record(store, 3)
    store.values["triage:stats:hits"] = "8"
    store.values["triage:stats:misses"] = "2"

    body = _client(_settings(), store).get("/api/meta/providers").json()

    assert [o["latency_ms"] for o in body["recent_outcomes"]] == [3, 2, 1]
    assert body["recent_outcomes"][0] == {
        "provider": "llm:gemini",
        "latency_ms": 3,
        "fallback": False,
        "at": "2026-09-27T12:00:02Z",
    }
    assert body["cache"] == {"hits": 8, "misses": 2, "hit_rate": 0.8}


def test_response_flags_fallbacks() -> None:
    store = FakeStore()
    _record(store, 1, fallback=True)

    body = _client(_settings(), store).get("/api/meta/providers").json()

    assert body["recent_outcomes"][0]["fallback"] is True


def test_response_never_carries_more_than_twenty_outcomes() -> None:
    store = FakeStore()
    _record(store, MAX_OUTCOMES + 7)

    body = _client(_settings(), store).get("/api/meta/providers").json()

    assert len(body["recent_outcomes"]) == 20
    assert body["recent_outcomes"][0]["latency_ms"] == 27


def test_llm_response_names_vendor_and_model_but_never_a_key() -> None:
    settings = _settings(triage_provider="llm", llm_vendor="gemini", gemini_model="gemini-x")

    response = _client(settings, FakeStore()).get("/api/meta/providers")

    body = response.json()
    assert (body["active_provider"], body["vendor"], body["model"]) == ("llm", "gemini", "gemini-x")
    assert GEMINI_KEY not in response.text
    assert GROQ_KEY not in response.text
    assert "api_key" not in response.text.lower()


def test_still_answers_200_when_redis_is_down(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeStore()
    _record(store, 2)
    store.fail_lists = True
    store.fail_get = True

    response = _client(_settings(), store).get("/api/meta/providers")

    assert response.status_code == 200
    assert response.json() == {
        "active_provider": "simulated",
        "vendor": None,
        "model": None,
        "recent_outcomes": [],
        "cache": {"hits": None, "misses": None, "hit_rate": None},
        "outcome_store_available": False,
    }
    assert "unavailable" in caplog.text


# --- wiring ------------------------------------------------------------------------------------


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeStore]:
    store = FakeStore()
    monkeypatch.setattr(ai_wiring, "get_triage_store", lambda url: store)
    ai_wiring.get_outcome_log.cache_clear()
    yield store
    ai_wiring.get_outcome_log.cache_clear()


def test_build_triage_service_caches_and_records(fake_store: FakeStore) -> None:
    service = build_triage_service(_settings(triage_cache_ttl_seconds=600))
    text = "Burst water main flooding Street 12"

    first = service.triage(text, "x")
    second = service.triage(text.upper(), "x")

    assert isinstance(service, TriageService)
    assert first.result == second.result
    assert CacheCounters(fake_store).snapshot().hits == 1
    assert list(fake_store.ttls.values()) == [600]
    assert len(fake_store.lists["triage:outcomes"]) == 2


def test_the_cache_wraps_the_configured_provider_and_keeps_its_name(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    captured: list[object] = []
    real = TriageService

    def spy(provider: object, **kwargs: object) -> TriageService:
        captured.append(provider)
        return real(provider, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ai_wiring, "TriageService", spy)

    build_triage_service(_settings(triage_provider="rules"))

    [provider] = captured
    assert isinstance(provider, CachingTriage)
    assert provider.name == "rules"


def test_get_meta_service_is_built_from_settings(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    monkeypatch.setattr(ai_wiring, "get_settings", lambda: _settings(triage_provider="rules"))

    report = get_meta_service().providers_report()

    assert report.provider.active_provider == "rules"
    assert report.store_available is True


def test_get_triage_service_is_built_once(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    monkeypatch.setattr(ai_wiring, "get_settings", lambda: _settings())
    ai_wiring.get_triage_service.cache_clear()
    try:
        assert ai_wiring.get_triage_service() is ai_wiring.get_triage_service()
    finally:
        ai_wiring.get_triage_service.cache_clear()


def test_the_real_store_and_log_are_shared_per_url() -> None:
    ai_wiring.get_triage_store.cache_clear()
    ai_wiring.get_outcome_log.cache_clear()
    try:
        url = "redis://cache:6379/0"  # never connected to: clients connect lazily
        assert ai_wiring.get_triage_store(url) is ai_wiring.get_triage_store(url)
        assert ai_wiring.get_outcome_log(url) is ai_wiring.get_outcome_log(url)
    finally:
        ai_wiring.get_triage_store.cache_clear()
        ai_wiring.get_outcome_log.cache_clear()
