"""Complaint business logic: triage, persist, and status changes.

The flow for a new complaint is triage -> persist -> commit, in that order. Triage runs before
the database is touched, so a slow LLM call never holds a connection open.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.domain import Category, Priority, Status
from app.repositories.complaints import ComplaintFilters
from app.repositories.models import Complaint
from app.services.status_machine import ensure_transition_allowed
from app.services.triage import TriageService

logger = logging.getLogger("civicpulse.complaints")


class ComplaintNotFound(Exception):
    def __init__(self, complaint_id: uuid.UUID) -> None:
        self.complaint_id = complaint_id
        super().__init__(f"Complaint {complaint_id} not found")


class ComplaintStore(Protocol):
    """What the service needs from persistence. ComplaintRepository satisfies it, and tests
    substitute an in-memory fake."""

    def add(
        self,
        *,
        text: str,
        location: str,
        reporter_contact: str | None,
        category: Category,
        priority: Priority,
        ai_summary: str | None,
        triaged_by: str,
        triage_latency_ms: int,
    ) -> Complaint: ...

    def get(self, complaint_id: uuid.UUID, *, for_update: bool = False) -> Complaint | None: ...

    def search(
        self, filters: ComplaintFilters, *, limit: int, offset: int
    ) -> tuple[list[Complaint], int]: ...

    def set_status(self, complaint: Complaint, status: Status) -> Complaint: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass(frozen=True)
class ComplaintPage:
    items: list[Complaint]
    total: int
    page: int
    page_size: int


def _do_nothing() -> None:
    return None


class ComplaintService:
    def __init__(
        self,
        store: ComplaintStore,
        triage: TriageService,
        # Called after every committed write, e.g. to invalidate cached statistics.
        on_write: Callable[[], None] = _do_nothing,
    ) -> None:
        self._store = store
        self._triage = triage
        self._on_write = on_write

    def create(self, text: str, location: str, reporter_contact: str | None) -> Complaint:
        # 1. Triage first. It never raises: a failing provider degrades to the rule-based one.
        outcome = self._triage.triage(text, location)

        # 2. Persist, and commit before reporting success.
        try:
            complaint = self._store.add(
                text=text,
                location=location,
                reporter_contact=reporter_contact,
                category=outcome.result.category,
                priority=outcome.result.priority,
                ai_summary=outcome.result.summary,
                triaged_by=outcome.triaged_by,
                triage_latency_ms=outcome.latency_ms,
            )
            self._store.commit()
        except Exception:
            self._store.rollback()
            raise
        self._notify_write()

        # 3. One WARNING per fallback. It is logged here, not in the triage service, because
        # only now does the complaint have an id.
        if outcome.fallback:
            logger.warning(
                "triage fell back to rules: complaint_id=%s provider=%s error=%s",
                complaint.id,
                outcome.failed_provider,
                outcome.error_class,
                extra={
                    "complaint_id": str(complaint.id),
                    "provider": outcome.failed_provider,
                    "error_class": outcome.error_class,
                },
            )
        return complaint

    def get(self, complaint_id: uuid.UUID) -> Complaint:
        complaint = self._store.get(complaint_id)
        if complaint is None:
            raise ComplaintNotFound(complaint_id)
        return complaint

    def search(self, filters: ComplaintFilters, *, page: int, page_size: int) -> ComplaintPage:
        items, total = self._store.search(filters, limit=page_size, offset=(page - 1) * page_size)
        return ComplaintPage(items=items, total=total, page=page, page_size=page_size)

    def change_status(self, complaint_id: uuid.UUID, requested: Status) -> Complaint:
        try:
            # Lock the row so a concurrent change cannot slip a transition in between our
            # check and our write.
            complaint = self._store.get(complaint_id, for_update=True)
            if complaint is None:
                raise ComplaintNotFound(complaint_id)
            ensure_transition_allowed(complaint.status, requested)
            self._store.set_status(complaint, requested)
            self._store.commit()
        except Exception:
            self._store.rollback()
            raise
        self._notify_write()
        return complaint

    def _notify_write(self) -> None:
        # The write is already committed, so a failing hook must not turn it into a failed
        # request (the client would retry and create a duplicate). Log it and carry on.
        try:
            self._on_write()
        except Exception:
            logger.exception("on_write hook failed after a committed write")
