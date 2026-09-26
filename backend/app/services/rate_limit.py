"""Fixed-window rate limiting: at most `limit` requests per client per `window_seconds`.

The counter lives in Redis, not in this process. With the backend autoscaled to N pods, a per-pod
counter would let one client send N times the limit (each pod sees only its share), and a pod
restart would wipe the count. One shared counter enforces the limit for the whole deployment.

A fixed window allows a burst of up to 2x the limit across a window boundary (limit at the end of
one window, limit again at the start of the next). That is accepted for intake protection: it is
one atomic operation per request, where a sliding window or token bucket needs more state.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("civicpulse.rate_limit")


class CounterStore(Protocol):
    """RedisCache satisfies this; tests substitute a fake with a controllable clock."""

    def increment_with_expiry(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Add 1 to the counter, starting the window if there is none.

        Returns (count, whole seconds until the window resets)."""
        ...


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    # Seconds until the window resets, always >= 1: the value for the Retry-After header.
    retry_after_seconds: int


class RateLimiter:
    def __init__(
        self,
        store: CounterStore,
        *,
        limit: int,
        window_seconds: int,
        key_prefix: str = "ratelimit:complaints",
    ) -> None:
        self._store = store
        self.limit = limit
        self.window_seconds = window_seconds
        self._key_prefix = key_prefix

    def check(self, client_id: str) -> RateLimitDecision:
        """Count this request against client_id and say whether it may proceed."""
        try:
            count, reset_in = self._store.increment_with_expiry(
                f"{self._key_prefix}:{client_id}", self.window_seconds
            )
        except Exception as exc:
            # Fail open. The limiter protects complaint intake; it must not be able to take it
            # down. While Redis is unreachable, citizens can still report, unthrottled.
            logger.warning(
                "rate limiter store unavailable, allowing request: error=%s", type(exc).__name__
            )
            return RateLimitDecision(allowed=True, retry_after_seconds=1)

        # Clamp so a bad store value can never produce a 0 or negative Retry-After.
        retry_after = min(max(reset_in, 1), self.window_seconds)
        return RateLimitDecision(allowed=count <= self.limit, retry_after_seconds=retry_after)
