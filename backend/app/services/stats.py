"""Complaint statistics with a read-through Redis cache.

Why the cache has BOTH a TTL and explicit invalidation:
- Invalidation alone is not enough. It can fail (Redis unreachable at that moment), be missed by a
  write path added later, or lose a race: a reader that computed the old numbers just before a
  write can store them just after the invalidation. Stale data would then live forever.
- A TTL alone is not enough. A complaint submitted now would not show up in the stats for up to
  30 seconds, which is the behaviour the spec rules out.
Invalidation makes the common case immediate; the TTL bounds the damage when it does not happen.
"""

import logging
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, NonNegativeInt, ValidationError, model_validator

from app.domain import Category, Priority, Status
from app.repositories.stats import GroupCount

logger = logging.getLogger("civicpulse.stats")

CACHE_KEY = "stats:v1"  # bump the suffix if Stats changes shape, so old entries are ignored


class Stats(BaseModel):
    """Every enum value is always present, with 0 when there are no rows, so clients need not
    fill gaps."""

    model_config = ConfigDict(strict=True)

    total: NonNegativeInt
    by_category: dict[Category, NonNegativeInt]
    by_priority: dict[Priority, NonNegativeInt]
    by_status: dict[Status, NonNegativeInt]

    @model_validator(mode="after")
    def _is_complete_and_consistent(self) -> "Stats":
        # Also guards what comes back from the cache: an entry missing a key, or whose
        # breakdowns disagree with the total, is rejected rather than served.
        for breakdown, members in (
            (self.by_category, Category),
            (self.by_priority, Priority),
            (self.by_status, Status),
        ):
            if set(breakdown) != set(members):
                raise ValueError("breakdown must contain every enum value exactly once")
            if sum(breakdown.values()) != self.total:
                raise ValueError("breakdown does not add up to total")
        return self


def build_stats(groups: list[GroupCount]) -> Stats:
    by_category = dict.fromkeys(Category, 0)
    by_priority = dict.fromkeys(Priority, 0)
    by_status = dict.fromkeys(Status, 0)
    for group in groups:
        by_category[group.category] += group.count
        by_priority[group.priority] += group.count
        by_status[group.status] += group.count
    return Stats(
        total=sum(by_status.values()),
        by_category=by_category,
        by_priority=by_priority,
        by_status=by_status,
    )


class StatsCache(Protocol):
    """RedisCache satisfies this; tests substitute a fake."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    def delete(self, key: str) -> None: ...


class StatsService:
    def __init__(
        self,
        cache: StatsCache,
        load_groups: Callable[[], list[GroupCount]],
        *,
        ttl_seconds: int,
    ) -> None:
        # load_groups opens its own database session, so a cache HIT never touches the database.
        self._cache = cache
        self._load_groups = load_groups
        self._ttl_seconds = ttl_seconds

    def get(self) -> tuple[Stats, bool]:
        """Return (stats, hit). A cache problem of any kind degrades to a database read and a
        MISS; it never fails the request."""
        cached = self._read_cache()
        if cached is not None:
            return cached, True

        stats = build_stats(self._load_groups())
        try:
            self._cache.set(CACHE_KEY, stats.model_dump_json(), self._ttl_seconds)
        except Exception as exc:
            logger.warning("stats cache write failed: error=%s", type(exc).__name__)
        return stats, False

    def invalidate(self) -> None:
        """Drop the cached stats so the next read sees the database. Call after a write.

        Never raises: the write it follows is already committed, and failing the caller over a
        cache problem would report an error for a complaint that was in fact saved. The TTL
        bounds how long a missed invalidation can leave stale numbers.
        """
        try:
            self._cache.delete(CACHE_KEY)
        except Exception as exc:
            logger.warning("stats cache invalidation failed: error=%s", type(exc).__name__)

    def _read_cache(self) -> Stats | None:
        try:
            raw = self._cache.get(CACHE_KEY)
            if raw is None:
                return None
            return Stats.model_validate_json(raw)
        except ValidationError:
            logger.warning("stats cache entry is invalid, recomputing")
        except Exception as exc:
            logger.warning("stats cache read failed: error=%s", type(exc).__name__)
        return None
