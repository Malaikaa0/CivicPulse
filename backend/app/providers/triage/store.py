"""The small key-value surface the triage cache and outcome log need from Redis.

Kept separate from providers/cache.py (the readiness probe) on purpose: this is the hot path of
every complaint submission, so it has its own tight timeouts and its own narrow interface, and
the code that uses it can be tested against a dictionary instead of a server.
"""

from typing import Protocol

import redis


class TriageStoreError(Exception):
    """The store could not be reached or refused the operation."""


class TriageStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    def incr(self, key: str) -> int: ...

    def push_capped(self, key: str, value: str, max_length: int) -> None:
        """Add `value` at the head of the list and keep only the newest `max_length`."""

    def list_range(self, key: str, start: int, stop: int) -> list[str]:
        """Entries from `start` to `stop` inclusive, head (newest) first."""


class RedisTriageStore:
    def __init__(self, url: str, timeout_seconds: float = 0.5) -> None:
        # Short timeouts: this sits in front of every triage call, so an unreachable Redis must
        # cost a fraction of a second, not hold a citizen's request hostage.
        self._client: redis.Redis = redis.Redis.from_url(
            url,
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
            decode_responses=True,
        )

    def get(self, key: str) -> str | None:
        try:
            value = self._client.get(key)
        except redis.RedisError as error:
            raise TriageStoreError("triage store unavailable") from error
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        try:
            self._client.set(key, value, ex=ttl_seconds)
        except redis.RedisError as error:
            raise TriageStoreError("triage store unavailable") from error

    def incr(self, key: str) -> int:
        try:
            return int(self._client.incr(key))
        except redis.RedisError as error:
            raise TriageStoreError("triage store unavailable") from error

    def push_capped(self, key: str, value: str, max_length: int) -> None:
        try:
            # One transaction, so a concurrent push can never see the list over its cap.
            pipe = self._client.pipeline(transaction=True)
            pipe.lpush(key, value)
            pipe.ltrim(key, 0, max_length - 1)
            pipe.execute()
        except redis.RedisError as error:
            raise TriageStoreError("triage store unavailable") from error

    def list_range(self, key: str, start: int, stop: int) -> list[str]:
        try:
            items = self._client.lrange(key, start, stop)
        except redis.RedisError as error:
            raise TriageStoreError("triage store unavailable") from error
        return [item for item in items if isinstance(item, str)]
