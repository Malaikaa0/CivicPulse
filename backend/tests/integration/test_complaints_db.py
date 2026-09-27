"""The real repository and service against a real PostgreSQL."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.domain import Category, Priority, Status
from app.providers.triage.errors import TriageServerError
from app.providers.triage.simulated import SimulatedTriage
from app.repositories.complaints import ComplaintFilters, ComplaintRepository
from app.repositories.models import Complaint
from app.services.complaints import ComplaintService
from app.services.triage import TriageService

pytestmark = pytest.mark.integration

TEXT = "Burst water main flooding Street 12, water entering ground floors"
BASE_TIME = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _add(session: Session, **overrides: object) -> Complaint:
    fields: dict[str, object] = {
        "text": TEXT,
        "location": "Street 12",
        "category": Category.WATER,
        "priority": Priority.HIGH,
        "triaged_by": "rules",
        "triage_latency_ms": 1,
        "created_at": BASE_TIME,
    }
    complaint = Complaint(**{**fields, **overrides})
    session.add(complaint)
    session.flush()
    return complaint


def _service(session: Session, provider: SimulatedTriage | None = None) -> ComplaintService:
    triage = TriageService(provider or SimulatedTriage(), sleep=lambda _: None, jitter=lambda: 0.0)
    return ComplaintService(ComplaintRepository(session), triage)


# ---- repository ----


def test_add_populates_the_database_generated_fields(session: Session) -> None:
    complaint = ComplaintRepository(session).add(
        text=TEXT,
        location="Street 12",
        reporter_contact=None,
        category=Category.WATER,
        priority=Priority.HIGH,
        ai_summary="Burst main",
        triaged_by="rules",
        triage_latency_ms=3,
    )

    assert isinstance(complaint.id, uuid.UUID)
    assert complaint.status == Status.OPEN  # the column default
    assert complaint.created_at.tzinfo is not None  # timestamptz, not a naive timestamp
    assert complaint.updated_at is not None


def test_get_returns_the_row_and_none_for_an_unknown_id(session: Session) -> None:
    complaint = _add(session)
    repo = ComplaintRepository(session)

    assert repo.get(complaint.id) is complaint
    assert repo.get(uuid.uuid4()) is None


def test_search_filters_by_each_field_and_reports_the_total(session: Session) -> None:
    _add(session, category=Category.WATER, priority=Priority.HIGH, status=Status.OPEN)
    _add(session, category=Category.WATER, priority=Priority.LOW, status=Status.RESOLVED)
    _add(session, category=Category.ROADS, priority=Priority.HIGH, status=Status.OPEN)
    repo = ComplaintRepository(session)

    def count(**kwargs: object) -> int:
        return repo.search(ComplaintFilters(**kwargs), limit=100, offset=0)[1]  # type: ignore[arg-type]

    assert count() == 3
    assert count(category=Category.WATER) == 2
    assert count(priority=Priority.HIGH) == 2
    assert count(status=Status.RESOLVED) == 1
    assert count(category=Category.WATER, priority=Priority.HIGH) == 1
    assert count(category=Category.ROADS, status=Status.RESOLVED) == 0


def test_search_returns_the_newest_first(session: Session) -> None:
    old = _add(session, created_at=BASE_TIME)
    new = _add(session, created_at=BASE_TIME + timedelta(days=2))
    mid = _add(session, created_at=BASE_TIME + timedelta(days=1))

    items, _ = ComplaintRepository(session).search(ComplaintFilters(), limit=10, offset=0)

    assert [c.id for c in items] == [new.id, mid.id, old.id]


def test_pagination_covers_every_row_exactly_once_even_with_equal_timestamps(
    session: Session,
) -> None:
    # Ten rows sharing one timestamp: without a tie-breaker, pages could overlap or skip rows.
    ids = {_add(session).id for _ in range(10)}
    repo = ComplaintRepository(session)

    seen: list[uuid.UUID] = []
    for page in range(4):
        items, total = repo.search(ComplaintFilters(), limit=3, offset=page * 3)
        assert total == 10
        seen.extend(c.id for c in items)

    assert len(seen) == 10
    assert set(seen) == ids


def test_a_page_past_the_end_is_empty_but_still_reports_the_total(session: Session) -> None:
    _add(session)

    items, total = ComplaintRepository(session).search(ComplaintFilters(), limit=10, offset=50)

    assert items == []
    assert total == 1


def test_set_status_updates_updated_at_and_survives_a_commit(
    session: Session, engine: Engine
) -> None:
    complaint = _add(session)
    session.commit()  # the create and the status change are separate requests in real use
    repo = ComplaintRepository(session)
    before = complaint.updated_at
    # PostgreSQL's now() is the transaction start time, so a change in the same transaction as
    # the insert would carry the same timestamp; the commit above makes this a new transaction.

    repo.set_status(complaint, Status.IN_PROGRESS)
    repo.commit()

    with Session(engine) as other:
        stored = ComplaintRepository(other).get(complaint.id)
        assert stored is not None
        assert stored.status == Status.IN_PROGRESS
        assert stored.updated_at > before


def test_the_database_itself_rejects_a_text_that_is_too_short(session: Session) -> None:
    with pytest.raises(IntegrityError):
        _add(session, text="short")
    session.rollback()


def test_the_database_itself_rejects_an_unknown_triaged_by(session: Session) -> None:
    with pytest.raises(IntegrityError):
        _add(session, triaged_by="simulated")
    session.rollback()


def test_a_row_lock_makes_a_second_status_change_wait(session: Session, engine: Engine) -> None:
    complaint = _add(session)
    session.commit()
    holder = ComplaintRepository(session)
    holder.get(complaint.id, for_update=True)  # takes the lock and keeps the transaction open

    with Session(engine) as other:
        other.execute(text("SET LOCAL lock_timeout = '300ms'"))  # fail fast instead of waiting
        with pytest.raises((OperationalError, DBAPIError)):
            ComplaintRepository(other).get(complaint.id, for_update=True)

    session.rollback()  # releases the lock


# ---- service on the real database ----


def test_create_persists_the_complaint_with_its_triage_result(session: Session) -> None:
    complaint = _service(session).create(TEXT, "Street 12", "0300-1112233")

    with Session(session.get_bind()) as fresh:
        stored = ComplaintRepository(fresh).get(complaint.id)
        assert stored is not None
        assert stored.category == Category.WATER
        assert stored.triaged_by == "rules"
        assert stored.triage_latency_ms >= 0
        assert stored.reporter_contact == "0300-1112233"


def test_a_provider_that_always_raises_still_persists_a_rules_fallback_row(
    session: Session,
) -> None:
    # The spec's must-have, all the way down to the database.
    service = _service(session, SimulatedTriage(always_fail=TriageServerError()))

    complaint = service.create(TEXT, "Street 12", None)

    with Session(session.get_bind()) as fresh:
        stored = ComplaintRepository(fresh).get(complaint.id)
        assert stored is not None
        assert stored.triaged_by == "rules:fallback"
        assert stored.category == Category.WATER


def test_change_status_walks_the_state_machine_on_real_rows(session: Session) -> None:
    service = _service(session)
    complaint = service.create(TEXT, "Street 12", None)

    assert service.change_status(complaint.id, Status.IN_PROGRESS).status == Status.IN_PROGRESS
    assert service.change_status(complaint.id, Status.RESOLVED).status == Status.RESOLVED
