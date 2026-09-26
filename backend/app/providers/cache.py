"""Redis client wrapper. Outbound integration, kept behind a small interface."""

import redis


class RedisCache:
    def __init__(self, url: str) -> None:
        # Short timeouts: a probe must fail fast instead of hanging on an unreachable host.
        self._client: redis.Redis = redis.Redis.from_url(
            url, socket_connect_timeout=2, socket_timeout=2
        )

    def ping(self) -> None:
        """Raise if Redis cannot be reached."""
        self._client.ping()
