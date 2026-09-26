"""Content-hash cache in front of a triage provider.

A burst main reported by nine neighbours should cost one inference, not nine. The key is a hash
of the provider name and the redacted, lower-cased, whitespace-collapsed text, so the same
complaint typed with different casing, spacing or phone number shares an entry.

Data protection (ADR-0004): the key is derived from the redacted text only, and what is stored
is the validated TriageResult with its summary redacted, never the complaint. `location` is not
part of the key because the LLM never receives it.

The cache is an optimisation and must never be a failure: a Redis outage, a slow Redis or a
damaged entry all degrade to "ask the provider". Only successful results are stored; an error
from the provider propagates unchanged so the triage service still retries and falls back.
"""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from app.providers.triage.base import TriageProvider, TriageResult
from app.providers.triage.redaction import redact_pii
from app.providers.triage.store import TriageStore

logger = logging.getLogger(__name__)

# Bump when TriageResult or the normalisation changes, so old entries are never misread.
KEY_PREFIX = "triage:v1:"
HITS_KEY = "triage:stats:hits"
MISSES_KEY = "triage:stats:misses"

MAX_SUMMARY_LENGTH = 140  # TriageResult.summary; redacting a very short email can lengthen it


@dataclass(frozen=True)
class CacheStats:
    hits: int
    misses: int

    @property
    def hit_rate(self) -> float | None:
        lookups = self.hits + self.misses
        return self.hits / lookups if lookups else None


class CacheCounters:
    """Hit and miss counts, kept in the store so every replica adds to the same totals."""

    def __init__(self, store: TriageStore) -> None:
        self._store = store

    def hit(self) -> None:
        self._store.incr(HITS_KEY)

    def miss(self) -> None:
        self._store.incr(MISSES_KEY)

    def snapshot(self) -> CacheStats:
        return CacheStats(hits=self._read(HITS_KEY), misses=self._read(MISSES_KEY))

    def _read(self, key: str) -> int:
        raw = self._store.get(key)
        try:
            return int(raw) if raw is not None else 0
        except ValueError:
            return 0


def normalise(text: str) -> str:
    """Redact first, then fold case and whitespace, so trivially different text collides."""
    return " ".join(redact_pii(text).lower().split())


class CachingTriage:
    def __init__(self, provider: TriageProvider, store: TriageStore, ttl_seconds: int) -> None:
        self._provider = provider
        self._store = store
        self._ttl = ttl_seconds
        self._counters = CacheCounters(store)
        # Same name as the wrapped provider, so triaged_by still says who did the work.
        self.name = provider.name

    def cache_key(self, text: str) -> str:
        material = f"{self._provider.name}\n{normalise(text)}"
        return KEY_PREFIX + hashlib.sha256(material.encode()).hexdigest()

    def stats(self) -> CacheStats:
        return self._counters.snapshot()

    def hit_rate(self) -> float | None:
        """Hits over lookups so far, or None before the first lookup."""
        return self.stats().hit_rate

    def triage(self, text: str, location: str) -> TriageResult:
        key = self.cache_key(text)

        try:
            raw = self._store.get(key)
        except Exception:  # deliberately broad: the cache must never break triage
            logger.warning("triage cache unavailable, asking the provider directly")
            return self._provider.triage(text, location)  # no writes: they would only time out

        cached = self._decode(raw)
        if cached is not None:
            self._count(self._counters.hit)
            return cached

        self._count(self._counters.miss)
        result = self._provider.triage(text, location)  # TriageError propagates, nothing stored
        self._remember(key, result)
        return result

    @staticmethod
    def _decode(raw: str | None) -> TriageResult | None:
        if raw is None:
            return None
        try:
            return TriageResult.model_validate_json(raw)
        except (ValidationError, ValueError, TypeError):
            # A damaged or outdated entry is a miss; the fresh result overwrites it.
            logger.warning("ignoring unreadable triage cache entry")
            return None

    @staticmethod
    def _storable(result: TriageResult) -> TriageResult:
        # A summary may quote the complaint (the rule-based provider does), and the next
        # near-duplicate is handed this entry: it must not inherit the first reporter's number.
        summary = redact_pii(result.summary)[:MAX_SUMMARY_LENGTH]
        return result.model_copy(update={"summary": summary})

    def _remember(self, key: str, result: TriageResult) -> None:
        try:
            self._store.set(key, self._storable(result).model_dump_json(), self._ttl)
        except Exception:
            logger.warning("could not write triage cache entry")

    @staticmethod
    def _count(record: Callable[[], None]) -> None:
        try:
            record()
        except Exception:
            logger.warning("could not update triage cache counters")
