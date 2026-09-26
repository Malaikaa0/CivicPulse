"""Rate limiter logic and its HTTP dependency, against a fake counter store with an injected
clock: no Redis, no network, no sleeping."""

import logging
import math

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.cache_wiring import get_rate_limiter
from app.config import Settings, get_settings
from app.routes.rate_limit import client_ip, enforce_rate_limit
from app.services.rate_limit import CounterStore, RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeCounterStore:
    """Fixed windows like Redis: the first hit starts the window, and it resets on expiry."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._windows: dict[str, tuple[int, float]] = {}
        self.keys_seen: list[str] = []

    def increment_with_expiry(self, key: str, window_seconds: int) -> tuple[int, int]:
        self.keys_seen.append(key)
        now = self._clock.now
        count, expires_at = self._windows.get(key, (0, now + window_seconds))
        if now >= expires_at:
            count, expires_at = 0, now + window_seconds
        self._windows[key] = (count + 1, expires_at)
        return count + 1, max(1, math.ceil(expires_at - now))


class BrokenStore:
    def increment_with_expiry(self, key: str, window_seconds: int) -> tuple[int, int]:
        raise ConnectionError("redis is down")


class OddStore:
    """Returns whatever it is told, to prove the limiter clamps a bad reset time."""

    def __init__(self, count: int, reset_in: int) -> None:
        self._result = (count, reset_in)

    def increment_with_expiry(self, key: str, window_seconds: int) -> tuple[int, int]:
        return self._result


def _limiter(clock: FakeClock, *, limit: int = 3, window: int = 60) -> RateLimiter:
    return RateLimiter(FakeCounterStore(clock), limit=limit, window_seconds=window)


# ---- RateLimiter ----


def test_allows_up_to_the_limit_then_blocks() -> None:
    limiter = _limiter(FakeClock(), limit=3)

    decisions = [limiter.check("1.2.3.4").allowed for _ in range(5)]

    assert decisions == [True, True, True, False, False]


def test_retry_after_is_a_whole_number_within_the_window() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, limit=1, window=60)
    limiter.check("1.2.3.4")

    for elapsed in (0, 1, 30, 59):
        clock.now = 1_000.0 + elapsed
        retry_after = limiter.check("1.2.3.4").retry_after_seconds
        assert isinstance(retry_after, int)
        assert 1 <= retry_after <= 60
        assert retry_after == 60 - elapsed


def test_retry_after_is_at_least_one_when_the_window_is_almost_over() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, limit=1, window=60)
    limiter.check("1.2.3.4")
    clock.now += 59.9

    assert limiter.check("1.2.3.4").retry_after_seconds == 1


def test_different_clients_have_independent_counts() -> None:
    limiter = _limiter(FakeClock(), limit=2)
    for _ in range(3):
        limiter.check("1.1.1.1")

    assert limiter.check("1.1.1.1").allowed is False
    assert limiter.check("2.2.2.2").allowed is True


def test_requests_pass_again_once_the_window_resets() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, limit=2, window=60)
    for _ in range(3):
        limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4").allowed is False

    clock.advance(60)

    assert limiter.check("1.2.3.4").allowed is True


def test_a_blocked_request_does_not_extend_the_window() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, limit=1, window=60)
    limiter.check("1.2.3.4")
    clock.advance(30)
    limiter.check("1.2.3.4")  # blocked

    clock.advance(30)  # the original window ends here, whatever happened in between

    assert limiter.check("1.2.3.4").allowed is True


def test_the_key_is_namespaced_and_carries_the_client() -> None:
    store = FakeCounterStore(FakeClock())
    RateLimiter(store, limit=1, window_seconds=60).check("9.9.9.9")

    assert store.keys_seen == ["ratelimit:complaints:9.9.9.9"]


def test_fails_open_and_warns_when_the_store_raises(caplog: pytest.LogCaptureFixture) -> None:
    limiter = RateLimiter(BrokenStore(), limit=1, window_seconds=60)

    with caplog.at_level(logging.WARNING, logger="civicpulse.rate_limit"):
        decisions = [limiter.check("1.2.3.4") for _ in range(5)]

    assert all(decision.allowed for decision in decisions)
    assert "allowing request" in caplog.text
    assert "ConnectionError" in caplog.text


@pytest.mark.parametrize(("reset_in", "expected"), [(0, 1), (-1, 1), (9999, 60)])
def test_retry_after_is_clamped_to_the_window(reset_in: int, expected: int) -> None:
    limiter = RateLimiter(OddStore(count=99, reset_in=reset_in), limit=1, window_seconds=60)

    decision = limiter.check("1.2.3.4")

    assert decision.allowed is False
    assert decision.retry_after_seconds == expected


# ---- client_ip ----


def _request(*, peer: tuple[str, int] | None, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "headers": headers, "client": peer})


@pytest.mark.parametrize("forwarded", [None, "203.0.113.9", "203.0.113.9, 10.0.0.1"])
def test_forwarded_header_is_ignored_unless_trusted(forwarded: str | None) -> None:
    request = _request(peer=("10.0.0.5", 1234), forwarded=forwarded)

    assert client_ip(request, trust_forwarded_for=False) == "10.0.0.5"


@pytest.mark.parametrize(
    ("forwarded", "expected"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("203.0.113.9, 10.0.0.1, 10.0.0.2", "203.0.113.9"),  # first hop is the client
        ("  203.0.113.9  ,10.0.0.1", "203.0.113.9"),
        ("2001:db8::1, 10.0.0.1", "2001:db8::1"),
    ],
)
def test_first_forwarded_hop_is_used_when_trusted(forwarded: str, expected: str) -> None:
    request = _request(peer=("10.0.0.5", 1234), forwarded=forwarded)

    assert client_ip(request, trust_forwarded_for=True) == expected


@pytest.mark.parametrize(
    "forwarded", [None, "", "  ", "not-an-ip", "999.1.1.1", "unknown, 1.2.3.4"]
)
def test_trusted_but_unusable_forwarded_header_falls_back_to_the_peer(
    forwarded: str | None,
) -> None:
    request = _request(peer=("10.0.0.5", 1234), forwarded=forwarded)

    assert client_ip(request, trust_forwarded_for=True) == "10.0.0.5"


def test_a_request_without_a_peer_address_gets_a_fixed_key() -> None:
    assert client_ip(_request(peer=None), trust_forwarded_for=False) == "unknown"


# ---- enforce_rate_limit on a tiny app with its own POST route ----


def _app(store: CounterStore, *, limit: int = 3, window: int = 60, trust: bool = False) -> FastAPI:
    app = FastAPI()

    @app.post("/things", dependencies=[Depends(enforce_rate_limit)])
    def create() -> dict[str, bool]:
        return {"ok": True}

    limiter = RateLimiter(store, limit=limit, window_seconds=window)
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_settings] = lambda: Settings(trust_forwarded_for=trust)
    return app


def test_requests_within_the_limit_succeed_and_the_next_is_429() -> None:
    client = TestClient(_app(FakeCounterStore(FakeClock()), limit=3))

    statuses = [client.post("/things").status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 429]


def test_429_carries_an_integer_retry_after_and_names_the_limit_and_window() -> None:
    clock = FakeClock()
    client = TestClient(_app(FakeCounterStore(clock), limit=3, window=60))
    for _ in range(3):
        client.post("/things")
    clock.advance(20)

    response = client.post("/things")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "40"
    detail = response.json()["detail"]
    assert "3 requests" in detail
    assert "60 seconds" in detail


def test_the_window_reset_lets_requests_through_again() -> None:
    clock = FakeClock()
    client = TestClient(_app(FakeCounterStore(clock), limit=1, window=60))
    client.post("/things")
    assert client.post("/things").status_code == 429

    clock.advance(60)

    assert client.post("/things").status_code == 200


def test_each_peer_address_has_its_own_limit() -> None:
    app = _app(FakeCounterStore(FakeClock()), limit=1)
    first = TestClient(app, client=("198.51.100.1", 5000))
    second = TestClient(app, client=("198.51.100.2", 5000))
    first.post("/things")

    assert first.post("/things").status_code == 429
    assert second.post("/things").status_code == 200


def test_a_spoofed_forwarded_header_cannot_dodge_the_limit_by_default() -> None:
    client = TestClient(_app(FakeCounterStore(FakeClock()), limit=2, trust=False))

    statuses = [
        client.post("/things", headers={"X-Forwarded-For": f"203.0.113.{n}"}).status_code
        for n in range(1, 5)
    ]

    assert statuses == [200, 200, 429, 429]


def test_forwarded_header_separates_clients_when_trusted() -> None:
    client = TestClient(_app(FakeCounterStore(FakeClock()), limit=1, trust=True))

    def post(address: str) -> int:
        return client.post("/things", headers={"X-Forwarded-For": address}).status_code

    assert post("203.0.113.1") == 200
    assert post("203.0.113.1") == 429
    assert post("203.0.113.2") == 200


def test_requests_are_not_refused_while_redis_is_down() -> None:
    client = TestClient(_app(BrokenStore(), limit=1))

    statuses = [client.post("/things").status_code for _ in range(3)]

    assert statuses == [200, 200, 200]
