"""The wiring in app/dependencies.py. Nothing here connects to a database."""

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import dependencies
from app.config import get_settings
from app.providers.triage.factory import create_triage_provider
from app.repositories.complaints import ComplaintRepository
from app.services.complaints import ComplaintService
from app.services.triage import TriageService


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # The wiring is cached per process; each test builds its own from a known environment.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@dbhost:5432/civic")
    monkeypatch.setenv("TRIAGE_PROVIDER", "simulated")
    # Captured now: a test may replace get_engine with a plain function, which has no cache.
    caches = (get_settings, dependencies.get_engine)
    for cached in caches:
        cached.cache_clear()
    yield
    for cached in caches:
        cached.cache_clear()


def test_the_engine_is_built_once_from_the_configured_database_url() -> None:
    engine = dependencies.get_engine()

    assert engine is dependencies.get_engine()
    assert engine.url.host == "dbhost"
    assert engine.url.database == "civic"
    engine.dispose()


def test_the_session_is_closed_when_the_request_is_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dependencies, "get_engine", lambda: create_engine("sqlite://"))
    requests = dependencies.get_session()

    session = next(requests)
    assert isinstance(session, Session)
    session.execute(text("SELECT 1"))
    assert session.in_transaction()  # a request leaves work open until something ends it

    with pytest.raises(StopIteration):
        next(requests)  # the request finished

    assert not session.in_transaction()  # closed: the connection went back to the pool


def test_the_session_is_closed_even_when_the_request_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dependencies, "get_engine", lambda: create_engine("sqlite://"))
    requests = dependencies.get_session()
    session = next(requests)
    session.execute(text("SELECT 1"))

    with pytest.raises(RuntimeError):
        requests.throw(RuntimeError("route blew up"))

    assert not session.in_transaction()


def test_each_request_gets_its_own_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dependencies, "get_engine", lambda: create_engine("sqlite://"))

    first = next(dependencies.get_session())
    second = next(dependencies.get_session())

    assert first is not second


def test_the_complaint_service_is_built_over_the_repository_and_the_triage_service() -> None:
    session = Session()
    triage = TriageService(create_triage_provider(get_settings()))

    stats = SimpleNamespace(invalidate=lambda: None)

    service = dependencies.get_complaint_service(session, triage, stats)

    assert isinstance(service, ComplaintService)
    assert isinstance(service._store, ComplaintRepository)
    assert service._triage is triage
    assert service._on_write is stats.invalidate  # a write drops the stats cache
