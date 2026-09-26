"""RedisCache passes through to the client correctly. The real server is exercised by the
integration tests; here the client is a fake so the wrapper's own logic runs with no Redis."""

from typing import Any

import pytest
import redis

from app.providers.cache import RedisCache


class FakeScript:
    def __init__(self, result: list[int]) -> None:
        self.result = result
        self.calls: list[tuple[list[str], list[int]]] = []

    def __call__(self, *, keys: list[str], args: list[int]) -> list[int]:
        self.calls.append((keys, args))
        return self.result


class FakeClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int]] = []
        self.deleted: list[str] = []
        self.script = FakeScript([1, 60_000])
        self.pinged = False
        self.options: dict[str, Any] = {}

    def ping(self) -> None:
        self.pinged = True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.set_calls.append((key, value, ex))
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.deleted.append(key)

    def ttl(self, key: str) -> int:
        return 17

    def register_script(self, source: str) -> FakeScript:
        assert "INCR" in source and "PEXPIRE" in source
        return self.script


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    fake = FakeClient()

    def from_url(url: str, **options: Any) -> FakeClient:
        fake.options = options
        return fake

    monkeypatch.setattr(redis.Redis, "from_url", from_url)
    return fake


def test_the_short_socket_timeouts_are_kept(client: FakeClient) -> None:
    RedisCache("redis://example:6379/0")

    assert client.options["socket_connect_timeout"] == 2
    assert client.options["socket_timeout"] == 2


def test_ping_reaches_the_client(client: FakeClient) -> None:
    RedisCache("redis://example:6379/0").ping()

    assert client.pinged


def test_set_stores_with_an_expiry_and_get_reads_it_back(client: FakeClient) -> None:
    cache = RedisCache("redis://example:6379/0")

    assert cache.get("k") is None
    cache.set("k", "v", 30)

    assert client.set_calls == [("k", "v", 30)]
    assert cache.get("k") == "v"


def test_delete_and_ttl_pass_through(client: FakeClient) -> None:
    cache = RedisCache("redis://example:6379/0")

    cache.delete("k")

    assert client.deleted == ["k"]
    assert cache.ttl("k") == 17


@pytest.mark.parametrize(
    ("pttl_ms", "expected_seconds"),
    [(60_000, 60), (59_001, 60), (1_500, 2), (1, 1), (0, 1)],
)
def test_increment_returns_the_count_and_seconds_rounded_up(
    client: FakeClient, pttl_ms: int, expected_seconds: int
) -> None:
    client.script.result = [4, pttl_ms]

    result = RedisCache("redis://example:6379/0").increment_with_expiry("rl:x", 60)

    assert result == (4, expected_seconds)
    assert client.script.calls == [(["rl:x"], [60_000])]
