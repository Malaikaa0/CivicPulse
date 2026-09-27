"""Graceful shutdown: the closer registry and the lifespan that runs it."""

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import dependencies, lifecycle
from app.lifecycle import close_all, register_closer
from app.main import create_app


@pytest.fixture(autouse=True)
def empty_registry() -> Iterator[None]:
    close_all()
    yield
    close_all()


def test_closers_run_newest_first() -> None:
    order: list[str] = []
    register_closer(lambda: order.append("opened first"))
    register_closer(lambda: order.append("opened last"))

    assert close_all() == 0

    assert order == ["opened last", "opened first"]


def test_every_closer_runs_even_if_one_raises(caplog: pytest.LogCaptureFixture) -> None:
    ran: list[str] = []

    def broken() -> None:
        raise ConnectionError("pool already gone")

    register_closer(lambda: ran.append("a"))
    register_closer(broken)
    register_closer(lambda: ran.append("c"))

    with caplog.at_level(logging.ERROR, logger="civicpulse.lifecycle"):
        failed = close_all()  # must not raise

    assert failed == 1
    assert ran == ["c", "a"]
    (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert record.exc_info is not None
    assert "broken" in record.closer


def test_close_all_is_idempotent() -> None:
    calls: list[int] = []
    register_closer(lambda: calls.append(1))

    close_all()
    assert close_all() == 0

    assert calls == [1]


def test_close_all_with_nothing_registered_is_a_no_op() -> None:
    assert close_all() == 0


def test_a_closer_may_return_a_value() -> None:
    # Redis.close() and Engine.dispose() do not return None consistently; that must not matter.
    register_closer(lambda: 42)

    assert close_all() == 0


def test_closers_run_on_lifespan_shutdown_not_before() -> None:
    closed: list[str] = []
    register_closer(lambda: closed.append("resource"))

    with TestClient(create_app()) as client:
        client.get("/health")
        assert closed == []  # still serving: nothing may be closed yet

    assert closed == ["resource"]


def test_shutdown_is_visible_in_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="civicpulse.lifecycle"):
        with TestClient(create_app()):
            pass

    messages = [r.getMessage() for r in caplog.records if r.name == "civicpulse.lifecycle"]
    assert messages == ["shutting down", "resources closed"]
    (closed,) = [r for r in caplog.records if r.getMessage() == "resources closed"]
    assert closed.levelno == logging.INFO
    assert closed.failed == 0


def test_a_failing_closer_makes_the_shutdown_line_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> None:
        raise RuntimeError("stuck")

    register_closer(broken)

    with caplog.at_level(logging.INFO, logger="civicpulse.lifecycle"):
        with TestClient(create_app()):
            pass

    (closed,) = [r for r in caplog.records if r.getMessage() == "resources closed"]
    assert closed.levelno == logging.WARNING
    assert closed.failed == 1


def test_shutdown_still_completes_when_the_app_never_opened_a_resource() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200


@pytest.fixture
def fresh_wiring() -> Iterator[None]:
    dependencies.get_readiness_service.cache_clear()
    yield
    dependencies.get_readiness_service.cache_clear()


def test_the_readiness_wiring_registers_the_engine_and_the_redis_client(
    fresh_wiring: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str] = []

    class FakeEngine:
        def dispose(self) -> None:
            closed.append("engine")

    class FakeClient:
        def close(self) -> None:
            closed.append("redis")

    class FakeCache:
        _client = FakeClient()

        def __init__(self, url: str) -> None: ...

        def ping(self) -> None: ...

    monkeypatch.setattr(dependencies, "create_db_engine", lambda url: FakeEngine())
    monkeypatch.setattr(dependencies, "RedisCache", FakeCache)

    dependencies.get_readiness_service()
    assert closed == []

    assert close_all() == 0
    assert sorted(closed) == ["engine", "redis"]


def test_the_real_engine_and_redis_client_close_cleanly(fresh_wiring: None) -> None:
    # Both are lazy, so building them needs no server; closing them must not need one either.
    dependencies.get_readiness_service()

    assert len(lifecycle._closers) == 2
    assert close_all() == 0
