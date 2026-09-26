"""Persistence for complaints. All SQL about complaints lives in this module."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain import Category, Priority, Status
from app.repositories.models import Complaint


def insert_missing(session: Session, rows: Sequence[Mapping[str, Any]]) -> int:
    """Insert rows whose id is not already present; return how many were actually inserted.

    ON CONFLICT DO NOTHING makes this safe to run repeatedly: existing rows are left exactly as
    they are, including any status an operator has changed since.
    """
    if not rows:
        return 0
    statement = (
        insert(Complaint)
        .values(list(rows))
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(Complaint.id)
    )
    return len(session.execute(statement).all())


@dataclass(frozen=True)
class ComplaintFilters:
    category: Category | None = None
    priority: Priority | None = None
    status: Status | None = None


class ComplaintRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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
    ) -> Complaint:
        complaint = Complaint(
            text=text,
            location=location,
            reporter_contact=reporter_contact,
            category=category,
            priority=priority,
            ai_summary=ai_summary,
            triaged_by=triaged_by,
            triage_latency_ms=triage_latency_ms,
        )
        self._session.add(complaint)
        # Flush and refresh so the database-generated id, status and timestamps are populated.
        self._session.flush()
        self._session.refresh(complaint)
        return complaint

    def get(self, complaint_id: uuid.UUID, *, for_update: bool = False) -> Complaint | None:
        # FOR UPDATE takes a row lock, so two operators changing the same complaint's status at
        # once are serialised instead of both applying a transition from the same old state.
        return self._session.get(Complaint, complaint_id, with_for_update=for_update)

    def search(
        self, filters: ComplaintFilters, *, limit: int, offset: int
    ) -> tuple[list[Complaint], int]:
        conditions = []
        if filters.category is not None:
            conditions.append(Complaint.category == filters.category)
        if filters.priority is not None:
            conditions.append(Complaint.priority == filters.priority)
        if filters.status is not None:
            conditions.append(Complaint.status == filters.status)

        total = self._session.scalar(select(func.count()).select_from(Complaint).where(*conditions))

        # Newest first, served by ix_complaints_created_at; id breaks ties so that pages never
        # overlap or skip rows when several complaints share a timestamp.
        page = self._session.scalars(
            select(Complaint)
            .where(*conditions)
            .order_by(Complaint.created_at.desc(), Complaint.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return list(page), total or 0

    def set_status(self, complaint: Complaint, status: Status) -> Complaint:
        complaint.status = status
        self._session.flush()
        self._session.refresh(complaint)  # picks up the new updated_at
        return complaint

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
