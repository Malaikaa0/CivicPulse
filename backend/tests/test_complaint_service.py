"""ComplaintService logic against an in-memory store: no database, no network, no sleeping."""

import logging
import uuid
from datetime import UTC, datetime

import pytest

from app.domain import Category, Priority, Status
from app.providers.triage.errors import (
    TriageBadRequest,
    TriageError,
    TriageInvalidOutput,
    TriageRateLimited,
    TriageServerError,
    TriageTimeout,
)
from app.providers.triage.simulated import SimulatedTriage
from app.repositories.complaints import ComplaintFilters
from app.repositories.models import Complaint
from app.services.complaints import ComplaintNotFound, ComplaintService
from app.services.status_machine import InvalidTransition
from app.services.triage import TriageService

TEXT = "Burst water main flooding Street 12, water entering ground floors"


class FakeStore:
    """Stands in for the repository. Records what was asked of it."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.rows: dict[uuid.UUID, Complaint] = {}
        self.events = events if events is not None else []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on_add: Exception | None = None
        self.locked_reads: list[bool] = []
        self.last_search: tuple[ComplaintFilters, int, int] | None = None

    def add(self, **fields: object) -> Complaint:
        self.events.append("add")
        if self.fail_on_add is not None:
            raise self.fail_on_add
        complaint = Complaint(**fields)
        complaint.id = uuid.uuid4()
        complaint.status = Status.OPEN
        complaint.created_at = complaint.updated_at = datetime.now(UTC)
        self.rows[complaint.id] = complaint
        return complaint

    def get(self, complaint_id: uuid.UUID, *, for_update: bool = False) -> Complaint | None:
        self.locked_reads.append(for_update)
        return self.rows.get(complaint_id)

    def search(
        self, filters: ComplaintFilters, *, limit: int, offset: int
    ) -> tuple[list[Complaint], int]:
        self.last_search = (filters, limit, offset)
        return [], 0

    def set_status(self, complaint: Complaint, status: Status) -> Complaint:
        complaint.status = status
        return complaint

    def commit(self) -> None:
        self.events.append("commit")
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class RecordingProvider(SimulatedTriage):
    def __init__(self, events: list[str], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._events = events

    def triage(self, text: str, location: str):  # type: ignore[no-untyped-def]
        self._events.append("triage")
        return super().triage(text, location)


def _service(store: FakeStore, provider: SimulatedTriage | None = None) -> ComplaintService:
    triage = TriageService(provider or SimulatedTriage(), sleep=lambda _: None, jitter=lambda: 0.0)
    return ComplaintService(store, triage)


# ---- create: triage -> persist -> commit ----


def test_create_stores_the_triage_result() -> None:
    store = FakeStore()

    complaint = _service(store).create(TEXT, "Street 12", "0300-1112233")

    assert complaint.category == Category.WATER
    assert complaint.priority == Priority.HIGH
    assert complaint.status == Status.OPEN
    assert complaint.triaged_by == "rules"  # simulated results are stored as rules
    assert complaint.ai_summary
    assert complaint.reporter_contact == "0300-1112233"
    assert store.commits == 1


def test_triage_happens_before_the_database_is_touched() -> None:
    # A slow LLM call must not hold a database connection open while it waits.
    events: list[str] = []
    store = FakeStore(events)

    _service(store, RecordingProvider(events)).create(TEXT, "Street 12", None)

    assert events == ["triage", "add", "commit"]


@pytest.mark.parametrize(
    "error",
    [
        TriageTimeout(),
        TriageRateLimited(),
        TriageServerError(),
        TriageBadRequest(),
        TriageInvalidOutput(),
    ],
)
def test_a_provider_that_always_raises_still_stores_the_complaint_as_a_fallback(
    error: TriageError,
) -> None:
    # The spec's must-have, at service level: a third party failing must not become our failure.
    store = FakeStore()

    complaint = _service(store, SimulatedTriage(always_fail=error)).create(TEXT, "Street 12", None)

    assert complaint.triaged_by == "rules:fallback"
    assert complaint.category == Category.WATER  # still triaged, by the rules
    assert store.commits == 1


def test_a_fallback_logs_exactly_one_warning_with_id_provider_and_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore()

    with caplog.at_level(logging.DEBUG, logger="civicpulse.complaints"):
        complaint = _service(store, SimulatedTriage(always_fail=TriageRateLimited())).create(
            TEXT, "Street 12", None
        )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    record = warnings[0]
    assert record.complaint_id == str(complaint.id)  # type: ignore[attr-defined]
    assert record.provider == "simulated"  # type: ignore[attr-defined]
    assert record.error_class == "TriageRateLimited"  # type: ignore[attr-defined]


def test_a_normal_triage_logs_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="civicpulse.complaints"):
        _service(FakeStore()).create(TEXT, "Street 12", None)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_persistence_failure_rolls_back_and_is_not_swallowed() -> None:
    store = FakeStore()
    store.fail_on_add = RuntimeError("database is down")

    with pytest.raises(RuntimeError, match="database is down"):
        _service(store).create(TEXT, "Street 12", None)

    assert store.rollbacks == 1
    assert store.commits == 0


# ---- get ----


def test_get_returns_the_complaint() -> None:
    store = FakeStore()
    created = _service(store).create(TEXT, "Street 12", None)

    assert _service(store).get(created.id) is created


def test_get_of_an_unknown_id_raises_not_found() -> None:
    with pytest.raises(ComplaintNotFound):
        _service(FakeStore()).get(uuid.uuid4())


# ---- change_status ----


def test_a_valid_transition_is_applied_under_a_row_lock() -> None:
    store = FakeStore()
    created = _service(store).create(TEXT, "Street 12", None)

    updated = _service(store).change_status(created.id, Status.IN_PROGRESS)

    assert updated.status == Status.IN_PROGRESS
    assert store.locked_reads[-1] is True  # FOR UPDATE, so concurrent changes are serialised
    assert store.commits == 2  # the create and the change


def test_an_invalid_transition_raises_rolls_back_and_changes_nothing() -> None:
    store = FakeStore()
    created = _service(store).create(TEXT, "Street 12", None)
    created.status = Status.RESOLVED  # terminal

    with pytest.raises(InvalidTransition) as caught:
        _service(store).change_status(created.id, Status.OPEN)

    assert str(caught.value) == "Cannot change status from 'resolved' to 'open'"
    assert created.status == Status.RESOLVED
    assert store.rollbacks == 1
    assert store.commits == 1  # only the create


def test_changing_the_status_of_an_unknown_complaint_raises_not_found() -> None:
    store = FakeStore()

    with pytest.raises(ComplaintNotFound):
        _service(store).change_status(uuid.uuid4(), Status.IN_PROGRESS)

    assert store.rollbacks == 1


# ---- search ----


@pytest.mark.parametrize(("page", "size", "offset"), [(1, 20, 0), (2, 20, 20), (3, 5, 10)])
def test_search_turns_page_and_size_into_limit_and_offset(
    page: int, size: int, offset: int
) -> None:
    store = FakeStore()
    filters = ComplaintFilters(category=Category.WATER)

    result = _service(store).search(filters, page=page, page_size=size)

    assert store.last_search == (filters, size, offset)
    assert (result.page, result.page_size) == (page, size)
