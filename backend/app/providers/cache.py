"""Redis client wrapper. Outbound integration, kept behind a small interface."""

import math

import redis

# The fixed-window counter as ONE script. Redis runs a script atomically, so no other command can
# run between the INCR and the expiry. Two separate commands (INCR, then EXPIRE) are not safe: if
# the process dies in between, the key exists with no expiry, the count never resets, and that
# client is blocked forever. Checking PTTL instead of "count == 1" also repairs a key that
# somehow lost its expiry, rather than trusting that it was created by this script.
_INCREMENT_WITH_EXPIRY = """
local count = redis.call('INCR', KEYS[1])
local pttl = redis.call('PTTL', KEYS[1])
if pttl < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
  pttl = tonumber(ARGV[1])
end
return {count, pttl}
"""


class RedisCache:
    def __init__(self, url: str) -> None:
        # Short timeouts: a probe must fail fast instead of hanging on an unreachable host.
        # decode_responses so callers deal in str, not bytes.
        self._client: redis.Redis = redis.Redis.from_url(
            url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True
        )
        self._increment_script = self._client.register_script(_INCREMENT_WITH_EXPIRY)

    def ping(self) -> None:
        """Raise if Redis cannot be reached."""
        self._client.ping()

    def get(self, key: str) -> str | None:
        value = self._client.get(key)
        return None if value is None else str(value)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store a value that Redis itself discards after ttl_seconds."""
        self._client.set(key, value, ex=ttl_seconds)

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def ttl(self, key: str) -> int:
        """Seconds until the key expires; -1 if it never does, -2 if it does not exist."""
        return int(self._client.ttl(key))

    def increment_with_expiry(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Atomically add 1 to the counter, starting a window_seconds expiry when it has none.

        Returns (count, seconds until the counter resets), the latter rounded up and at least 1.
        """
        count, pttl_ms = self._increment_script(keys=[key], args=[window_seconds * 1000])
        return int(count), max(1, math.ceil(int(pttl_ms) / 1000))
