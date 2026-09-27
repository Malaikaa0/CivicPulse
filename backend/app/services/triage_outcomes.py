"""The last few triage outcomes, kept in Redis so every replica shows the same picture.

This is the data behind GET /api/meta/providers: which provider answered, how long it took and
whether the rules fallback had to step in. Only those four facts are stored, never complaint text.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, ValidationError

from app.providers.triage.store import TriageStore
from app.services.triage import TriageOutcome

logger = logging.getLogger(__name__)

OUTCOMES_KEY = "triage:outcomes"
MAX_OUTCOMES = 20


class RecordedOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    latency_ms: int
    fallback: bool
    at: datetime


class TriageOutcomeLog:
    def __init__(
        self,
        store: TriageStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._clock = clock

    def record(self, outcome: TriageOutcome) -> None:
        entry = RecordedOutcome(
            provider=outcome.triaged_by,
            latency_ms=outcome.latency_ms,
            fallback=outcome.fallback,
            at=self._clock(),
        )
        self._store.push_capped(OUTCOMES_KEY, entry.model_dump_json(), MAX_OUTCOMES)

    def recent(self) -> list[RecordedOutcome]:
        """Up to the last 20 outcomes, newest first."""
        entries = []
        for raw in self._store.list_range(OUTCOMES_KEY, 0, MAX_OUTCOMES - 1):
            try:
                entries.append(RecordedOutcome.model_validate_json(raw))
            except ValidationError:
                logger.warning("skipping unreadable triage outcome entry")
        return entries
