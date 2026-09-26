"""RedisTriageStore's error handling, with a stand-in client (the real server is in integration)."""

from typing import Any

import pytest
import redis

from app.providers.triage.store import RedisTriageStore, TriageStoreError


class DownClient:
    """Every command fails the way redis-py fails when the server is unreachable."""

    def _fail(self, *args: Any, **kwargs: Any) -> Any:
        raise redis.ConnectionError("Error 111 connecting to redis:6379")

    get = set = incr = lrange = _fail

    def pipeline(self, transaction: bool = True) -> "DownClient":
        return self

    lpush = ltrim = _fail

    def execute(self) -> Any:
        return self._fail()


def _down_store() -> RedisTriageStore:
    store = RedisTriageStore("redis://cache:6379/0")  # never connected to: clients are lazy
    store._client = DownClient()  # type: ignore[assignment]
    return store


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.get("k"),
        lambda s: s.set("k", "v", 60),
        lambda s: s.incr("k"),
        lambda s: s.push_capped("k", "v", 20),
        lambda s: s.list_range("k", 0, 19),
    ],
    ids=["get", "set", "incr", "push_capped", "list_range"],
)
def test_redis_failures_surface_as_store_errors(operation: Any) -> None:
    with pytest.raises(TriageStoreError) as raised:
        operation(_down_store())

    assert str(raised.value) == "triage store unavailable"  # no host or port in the message
    assert isinstance(raised.value.__cause__, redis.ConnectionError)


def test_the_client_is_built_with_short_timeouts() -> None:
    store = RedisTriageStore("redis://cache:6379/0", timeout_seconds=0.25)

    kwargs = store._client.connection_pool.connection_kwargs
    assert kwargs["socket_timeout"] == 0.25
    assert kwargs["socket_connect_timeout"] == 0.25
    assert kwargs["decode_responses"] is True
