"""The complaint endpoints over HTTP, with an in-memory store: no database, no network, no sleeping.

Every request goes through the real FastAPI app, validation and error handlers; only the service's
dependency is overridden.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_complaint_service
from app.domain import Category, Priority, Status
from app.main import create_app
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
from app.services.complaints import ComplaintService
from app.services.triage import TriageService

TEXT = "Burst water main flooding Street 12, water entering ground floors"
CREATE = {"text": TEXT, "location": "Street 12"}
BASE_TIME = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class InMemoryStore:
    """Stands in for ComplaintRepository, including filtering, newest-first order and paging."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, Complaint] = {}
        self.commits = 0
        self.rollbacks = 0
        self.fail_on_add: Exception | None = None
        self.searches: list[tuple[ComplaintFilters, int, int]] = []

    def add(self, **fields: Any) -> Complaint:
        if self.fail_on_add is not None:
            raise self.fail_on_add
        number = len(self.rows) + 1
        complaint = Complaint(**fields)
        complaint.id = uuid.UUID(int=number)
        complaint.status = Status.OPEN
        complaint.created_at = complaint.updated_at = BASE_TIME + timedelta(minutes=number)
        self.rows[complaint.id] = complaint
        return complaint

    def get(self, complaint_id: uuid.UUID, *, for_update: bool = False) -> Complaint | None:
        return self.rows.get(complaint_id)

    def search(
        self, filters: ComplaintFilters, *, limit: int, offset: int
    ) -> tuple[list[Complaint], int]:
        self.searches.append((filters, limit, offset))
        matching = [
            c
            for c in self.rows.values()
            if filters.category in (None, c.category)
            and filters.priority in (None, c.priority)
            and filters.status in (None, c.status)
        ]
        matching.sort(key=lambda c: c.created_at, reverse=True)
        return matching[offset : offset + limit], len(matching)

    def set_status(self, complaint: Complaint, status: Status) -> Complaint:
        complaint.status = status
        complaint.updated_at += timedelta(minutes=1)
        return complaint

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


ClientFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(store: InMemoryStore) -> ClientFactory:
    def factory(
        provider: SimulatedTriage | None = None,
        on_write: Callable[[], None] | None = None,
        *,
        raise_server_exceptions: bool = True,
    ) -> TestClient:
        triage = TriageService(
            provider or SimulatedTriage(), sleep=lambda _: None, jitter=lambda: 0.0
        )
        service = (
            ComplaintService(store, triage, on_write)
            if on_write
            else ComplaintService(store, triage)
        )
        app = create_app()
        app.dependency_overrides[get_complaint_service] = lambda: service
        return TestClient(app, raise_server_exceptions=raise_server_exceptions)

    return factory


@pytest.fixture
def client(make_client: ClientFactory) -> TestClient:
    return make_client()


def _create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post("/api/complaints", json={**CREATE, **overrides})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _field_errors(response: Any) -> dict[str, str]:
    """Asserts the 400 shape and returns {field: message}."""
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert all(set(entry) == {"field", "message"} for entry in detail)
    return {entry["field"]: entry["message"] for entry in detail}


# ---- POST /api/complaints ----


def test_create_returns_201_with_the_triaged_complaint(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.post("/api/complaints", json={**CREATE, "reporter_contact": "0300-1112233"})

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id",
        "text",
        "location",
        "reporter_contact",
        "category",
        "priority",
        "status",
        "ai_summary",
        "triaged_by",
        "triage_latency_ms",
        "created_at",
        "updated_at",
        "allowed_transitions",
    }
    assert uuid.UUID(body["id"]) in store.rows
    assert body["text"] == TEXT
    assert body["location"] == "Street 12"
    assert body["reporter_contact"] == "0300-1112233"
    assert body["category"] == "water"
    assert body["priority"] == "high"
    assert body["status"] == "open"
    assert body["ai_summary"]
    assert body["triaged_by"] == "rules"  # simulated results are stored as rules
    assert body["triage_latency_ms"] >= 0
    assert datetime.fromisoformat(body["created_at"]) == BASE_TIME + timedelta(minutes=1)
    assert body["allowed_transitions"] == ["in_progress", "rejected"]
    assert store.commits == 1


def test_surrounding_whitespace_is_stripped_before_storing(
    client: TestClient, store: InMemoryStore
) -> None:
    body = _create(
        client, text=f"  \n{TEXT}\t ", location="  Street 12  ", reporter_contact="  0300-1  "
    )

    assert body["text"] == TEXT
    assert body["location"] == "Street 12"
    assert body["reporter_contact"] == "0300-1"
    stored = next(iter(store.rows.values()))
    assert (stored.text, stored.location) == (TEXT, "Street 12")


@pytest.mark.parametrize("contact", ["", "   ", "\t\n", None])
def test_a_blank_reporter_contact_becomes_null(client: TestClient, contact: str | None) -> None:
    assert _create(client, reporter_contact=contact)["reporter_contact"] is None


def test_an_omitted_reporter_contact_is_null(client: TestClient) -> None:
    assert _create(client)["reporter_contact"] is None


@pytest.mark.parametrize(
    ("text", "location", "contact"),
    [
        ("x" * 10, "abc", None),  # the shortest accepted text and location
        ("x" * 2000, "y" * 200, "z" * 200),  # the longest accepted of all three
    ],
)
def test_the_length_limits_are_inclusive(
    client: TestClient, text: str, location: str, contact: str | None
) -> None:
    body = _create(client, text=text, location=location, reporter_contact=contact)

    assert (body["text"], body["location"]) == (text, location)


def test_a_length_that_only_reaches_the_minimum_after_stripping_is_rejected(
    client: TestClient,
) -> None:
    # Ten characters including padding is nine after stripping.
    response = client.post("/api/complaints", json={**CREATE, "text": " " + "x" * 8 + " "})

    assert "text" in _field_errors(response)


# ---- the spec's must-have, at HTTP level ----


@pytest.mark.parametrize(
    "error",
    [
        TriageTimeout(),
        TriageRateLimited(),
        TriageServerError(),
        TriageBadRequest(),
        TriageInvalidOutput(),
    ],
    ids=lambda error: type(error).__name__,
)
def test_a_provider_that_always_raises_still_gives_201_and_a_rules_fallback(
    make_client: ClientFactory, error: TriageError
) -> None:
    client = make_client(SimulatedTriage(always_fail=error))

    response = client.post("/api/complaints", json=CREATE)

    assert response.status_code == 201
    body = response.json()
    assert body["triaged_by"] == "rules:fallback"
    assert body["category"] == "water"  # still triaged, by the rules
    assert body["ai_summary"]


def test_a_provider_that_recovers_on_the_retry_is_not_recorded_as_a_fallback(
    make_client: ClientFactory,
) -> None:
    client = make_client(SimulatedTriage(script=[TriageTimeout(), None]))

    assert client.post("/api/complaints", json=CREATE).json()["triaged_by"] == "rules"


def test_the_fallback_writes_exactly_one_warning(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    client = make_client(SimulatedTriage(always_fail=TriageServerError()))

    with caplog.at_level(logging.WARNING, logger="civicpulse.complaints"):
        body = client.post("/api/complaints", json=CREATE).json()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].complaint_id == body["id"]  # type: ignore[attr-defined]


# ---- POST validation: 400 with field-level errors, never 422 ----


@pytest.mark.parametrize(
    ("payload", "field", "message"),
    [
        ({**CREATE, "text": "too short"}, "text", "String should have at least 10 characters"),
        ({**CREATE, "text": "x" * 2001}, "text", "String should have at most 2000 characters"),
        ({**CREATE, "text": ""}, "text", "String should have at least 10 characters"),
        ({**CREATE, "text": " " * 50}, "text", "String should have at least 10 characters"),
        ({**CREATE, "location": "ab"}, "location", "String should have at least 3 characters"),
        (
            {**CREATE, "location": "y" * 201},
            "location",
            "String should have at most 200 characters",
        ),
        ({**CREATE, "location": "   "}, "location", "String should have at least 3 characters"),
        (
            {**CREATE, "reporter_contact": "z" * 201},
            "reporter_contact",
            "String should have at most 200 characters",
        ),
        ({"location": "Street 12"}, "text", "Field required"),
        ({"text": TEXT}, "location", "Field required"),
        ({**CREATE, "text": TEXT + "\u0000"}, "text", "Must not contain NUL characters"),
        ({**CREATE, "location": "Str\u0000eet"}, "location", "Must not contain NUL characters"),
        (
            {**CREATE, "reporter_contact": "03\u000000"},
            "reporter_contact",
            "Must not contain NUL characters",
        ),
        ({**CREATE, "text": None}, "text", "Input should be a valid string"),
        ({**CREATE, "text": 12345678901}, "text", "Input should be a valid string"),
        ({**CREATE, "location": ["Street 12"]}, "location", "Input should be a valid string"),
        ({**CREATE, "reporter_contact": 5}, "reporter_contact", "Input should be a valid string"),
    ],
)
def test_invalid_create_bodies_give_a_field_level_400(
    client: TestClient,
    store: InMemoryStore,
    payload: dict[str, Any],
    field: str,
    message: str,
) -> None:
    response = client.post("/api/complaints", json=payload)

    assert response.status_code == 400
    assert response.json() == {"detail": [{"field": field, "message": message}]}
    assert store.rows == {}  # rejected before any triage or persistence


def test_every_invalid_field_is_reported_at_once(client: TestClient) -> None:
    response = client.post("/api/complaints", json={"text": "short", "reporter_contact": "z" * 201})

    assert _field_errors(response) == {
        "text": "String should have at least 10 characters",
        "location": "Field required",
        "reporter_contact": "String should have at most 200 characters",
    }


def test_an_invalid_body_never_reaches_the_triage_provider(make_client: ClientFactory) -> None:
    provider = SimulatedTriage()
    client = make_client(provider)

    client.post("/api/complaints", json={"text": "short", "location": "Street 12"})

    assert provider.calls == 0


@pytest.mark.parametrize(
    ("content", "headers"),
    [
        (None, {}),  # no body at all
        ("{not json", {"content-type": "application/json"}),
        ("[]", {"content-type": "application/json"}),
        ("null", {"content-type": "application/json"}),
    ],
)
def test_a_missing_or_malformed_body_is_a_400_naming_the_body(
    client: TestClient, content: str | None, headers: dict[str, str]
) -> None:
    response = client.post("/api/complaints", content=content, headers=headers)

    assert list(_field_errors(response)) == ["body"]


# ---- GET /api/complaints/{id} ----


def test_get_returns_the_complaint_that_was_created(client: TestClient) -> None:
    created = _create(client, reporter_contact="0300-1112233")

    response = client.get(f"/api/complaints/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_of_an_unknown_id_is_a_404_naming_it(client: TestClient) -> None:
    unknown = uuid.uuid4()

    response = client.get(f"/api/complaints/{unknown}")

    assert response.status_code == 404
    assert response.json() == {"detail": f"Complaint {unknown} not found"}


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "12345", "00000000-0000-0000-0000-00000000000g"])
def test_a_malformed_id_is_a_field_level_400_not_a_404(client: TestClient, bad_id: str) -> None:
    response = client.get(f"/api/complaints/{bad_id}")

    errors = _field_errors(response)
    assert list(errors) == ["complaint_id"]
    assert "UUID" in errors["complaint_id"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (Status.OPEN, ["in_progress", "rejected"]),
        (Status.IN_PROGRESS, ["resolved", "rejected"]),
        (Status.RESOLVED, []),
        (Status.REJECTED, []),
    ],
)
def test_allowed_transitions_follow_the_state_machine_for_every_status(
    client: TestClient, store: InMemoryStore, status: Status, expected: list[str]
) -> None:
    created = _create(client)
    store.rows[uuid.UUID(created["id"])].status = status

    body = client.get(f"/api/complaints/{created['id']}").json()

    assert body["status"] == status.value
    assert body["allowed_transitions"] == expected


# ---- GET /api/complaints ----


def test_list_defaults_to_the_first_page_of_twenty(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.get("/api/complaints")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}
    assert store.searches == [(ComplaintFilters(), 20, 0)]


def test_list_passes_filters_and_paging_to_the_service(
    client: TestClient, store: InMemoryStore
) -> None:
    client.get(
        "/api/complaints",
        params={
            "category": "water",
            "priority": "high",
            "status": "in_progress",
            "page": 3,
            "page_size": 5,
        },
    )

    filters = ComplaintFilters(
        category=Category.WATER, priority=Priority.HIGH, status=Status.IN_PROGRESS
    )
    assert store.searches == [(filters, 5, 10)]  # page 3 of size 5 starts at row 10


@pytest.mark.parametrize("field", ["category", "priority", "status"])
def test_each_filter_is_passed_on_alone(
    client: TestClient, store: InMemoryStore, field: str
) -> None:
    value = {"category": "roads", "priority": "low", "status": "rejected"}[field]

    client.get("/api/complaints", params={field: value})

    (filters, _, _), *_ = store.searches
    assert getattr(filters, field).value == value
    others = [other for other in ("category", "priority", "status") if other != field]
    assert all(getattr(filters, other) is None for other in others)


def test_list_returns_serialised_complaints_newest_first_with_the_total(
    client: TestClient,
) -> None:
    ids = [_create(client, text=f"{TEXT} number {n}")["id"] for n in range(5)]

    body = client.get("/api/complaints", params={"page_size": 2}).json()

    assert body["total"] == 5
    assert (body["page"], body["page_size"]) == (1, 2)
    assert [item["id"] for item in body["items"]] == [ids[4], ids[3]]
    assert body["items"][0]["allowed_transitions"] == ["in_progress", "rejected"]

    second = client.get("/api/complaints", params={"page_size": 2, "page": 2}).json()
    assert [item["id"] for item in second["items"]] == [ids[2], ids[1]]
    last = client.get("/api/complaints", params={"page_size": 2, "page": 3}).json()
    assert [item["id"] for item in last["items"]] == [ids[0]]


def test_a_page_past_the_end_is_empty_but_reports_the_total(client: TestClient) -> None:
    _create(client)

    body = client.get("/api/complaints", params={"page": 9}).json()

    assert body == {"items": [], "total": 1, "page": 9, "page_size": 20}


def test_list_filters_narrow_the_results(client: TestClient) -> None:
    water = _create(client)["id"]
    _create(client, text="Streetlight outside house 5 is not working at night", location="House 5")

    only_water = client.get("/api/complaints", params={"category": "water"}).json()
    nothing = client.get("/api/complaints", params={"status": "resolved"}).json()

    assert [item["id"] for item in only_water["items"]] == [water]
    assert only_water["total"] == 1
    assert nothing["items"] == []


@pytest.mark.parametrize("size", [1, 100])
def test_the_page_size_bounds_are_inclusive(client: TestClient, size: int) -> None:
    assert client.get("/api/complaints", params={"page_size": size}).status_code == 200


def test_the_last_allowed_page_is_accepted(client: TestClient, store: InMemoryStore) -> None:
    assert client.get("/api/complaints", params={"page": 1_000_000}).status_code == 200


@pytest.mark.parametrize(
    ("query", "field", "fragment"),
    [
        ({"category": "sewage"}, "category", "Input should be"),
        ({"priority": "urgent"}, "priority", "Input should be"),
        ({"status": "closed"}, "status", "Input should be"),
        ({"category": "WATER"}, "category", "Input should be"),  # values are case-sensitive
        ({"page": 0}, "page", "greater than or equal to 1"),
        ({"page": -3}, "page", "greater than or equal to 1"),
        ({"page": 1_000_001}, "page", "less than or equal to 1000000"),
        ({"page": 10**30}, "page", "less than or equal to 1000000"),
        ({"page": "abc"}, "page", "valid integer"),
        ({"page_size": 0}, "page_size", "greater than or equal to 1"),
        ({"page_size": 101}, "page_size", "less than or equal to 100"),
        ({"page_size": "many"}, "page_size", "valid integer"),
    ],
)
def test_invalid_list_parameters_give_a_field_level_400(
    client: TestClient, store: InMemoryStore, query: dict[str, Any], field: str, fragment: str
) -> None:
    response = client.get("/api/complaints", params=query)

    errors = _field_errors(response)
    assert list(errors) == [field]
    assert fragment in errors[field]
    assert store.searches == []


def test_several_invalid_list_parameters_are_all_reported(client: TestClient) -> None:
    response = client.get("/api/complaints", params={"category": "x", "page": 0, "page_size": 101})

    assert set(_field_errors(response)) == {"category", "page", "page_size"}


# ---- PATCH /api/complaints/{id}/status ----


def _patch(client: TestClient, complaint_id: Any, status: Any) -> Any:
    return client.patch(f"/api/complaints/{complaint_id}/status", json={"status": status})


def test_patch_applies_a_valid_transition_and_returns_the_updated_complaint(
    client: TestClient,
) -> None:
    created = _create(client)

    response = _patch(client, created["id"], "in_progress")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["status"] == "in_progress"
    assert body["allowed_transitions"] == ["resolved", "rejected"]
    assert datetime.fromisoformat(body["updated_at"]) > datetime.fromisoformat(
        created["updated_at"]
    )
    assert client.get(f"/api/complaints/{created['id']}").json() == body  # it was persisted


def test_a_complaint_can_walk_open_in_progress_resolved_and_then_no_further(
    client: TestClient,
) -> None:
    complaint_id = _create(client)["id"]

    assert _patch(client, complaint_id, "in_progress").json()["status"] == "in_progress"
    resolved = _patch(client, complaint_id, "resolved").json()
    assert resolved["status"] == "resolved"
    assert resolved["allowed_transitions"] == []

    response = _patch(client, complaint_id, "open")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Cannot change status from 'resolved' to 'open'",
        "current": "resolved",
        "requested": "open",
    }


@pytest.mark.parametrize(
    ("start", "attempt"),
    [
        ("open", "resolved"),  # cannot skip in_progress
        ("open", "open"),
        ("in_progress", "open"),
        ("in_progress", "in_progress"),
        ("rejected", "in_progress"),
        ("resolved", "rejected"),
    ],
)
def test_an_invalid_transition_is_a_409_naming_it_and_changes_nothing(
    client: TestClient, store: InMemoryStore, start: str, attempt: str
) -> None:
    created = _create(client)
    row = store.rows[uuid.UUID(created["id"])]
    row.status = Status(start)

    response = _patch(client, created["id"], attempt)

    assert response.status_code == 409
    assert response.json()["detail"] == f"Cannot change status from '{start}' to '{attempt}'"
    assert row.status == Status(start)
    assert store.rollbacks == 1


def test_patch_of_an_unknown_id_is_a_404(client: TestClient) -> None:
    unknown = uuid.uuid4()

    response = _patch(client, unknown, "in_progress")

    assert response.status_code == 404
    assert response.json() == {"detail": f"Complaint {unknown} not found"}


def test_patch_with_a_malformed_id_is_a_field_level_400(client: TestClient) -> None:
    assert list(_field_errors(_patch(client, "nope", "in_progress"))) == ["complaint_id"]


@pytest.mark.parametrize("status", ["closed", "OPEN", "", None, 3, ["open"]])
def test_an_invalid_status_value_is_a_field_level_400(client: TestClient, status: Any) -> None:
    created = _create(client)

    errors = _field_errors(_patch(client, created["id"], status))

    assert list(errors) == ["status"]
    assert errors["status"].startswith("Input should be 'open', 'in_progress'")


def test_a_missing_status_is_a_field_level_400(client: TestClient) -> None:
    created = _create(client)

    response = client.patch(f"/api/complaints/{created['id']}/status", json={})

    assert _field_errors(response) == {"status": "Field required"}


def test_a_bad_id_and_a_bad_status_are_both_reported(client: TestClient) -> None:
    assert set(_field_errors(_patch(client, "nope", "closed"))) == {"complaint_id", "status"}


# ---- the on_write hook, seen through HTTP ----


def test_the_hook_runs_once_after_a_create_and_once_after_a_status_change(
    make_client: ClientFactory, store: InMemoryStore
) -> None:
    commits_seen: list[int] = []
    client = make_client(on_write=lambda: commits_seen.append(store.commits))

    created = _create(client)
    assert commits_seen == [1]  # ran after the commit, not before it

    _patch(client, created["id"], "in_progress")
    assert commits_seen == [1, 2]


def test_reads_never_run_the_hook(make_client: ClientFactory) -> None:
    calls: list[None] = []
    client = make_client(on_write=lambda: calls.append(None))
    created = _create(client)
    calls.clear()

    client.get("/api/complaints")
    client.get(f"/api/complaints/{created['id']}")

    assert calls == []


def test_failed_writes_never_run_the_hook(make_client: ClientFactory, store: InMemoryStore) -> None:
    calls: list[None] = []
    client = make_client(on_write=lambda: calls.append(None), raise_server_exceptions=False)
    created = _create(client)
    calls.clear()

    client.post("/api/complaints", json={"text": "short", "location": "x"})  # 400
    _patch(client, created["id"], "resolved")  # 409
    _patch(client, uuid.uuid4(), "in_progress")  # 404
    _patch(client, created["id"], "closed")  # 400
    store.fail_on_add = RuntimeError("database is down")
    assert client.post("/api/complaints", json=CREATE).status_code == 500

    assert calls == []


def test_a_hook_that_raises_does_not_fail_the_request_or_lose_the_write(
    make_client: ClientFactory, store: InMemoryStore, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> None:
        raise ConnectionError("redis is down")

    client = make_client(on_write=broken)

    with caplog.at_level(logging.ERROR, logger="civicpulse.complaints"):
        created = client.post("/api/complaints", json=CREATE)
        patched = _patch(client, created.json()["id"], "in_progress")

    assert created.status_code == 201
    assert patched.status_code == 200
    assert patched.json()["status"] == "in_progress"
    assert store.commits == 2
    assert store.rollbacks == 0
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2


# ---- /openapi.json ----


@pytest.fixture
def openapi(client: TestClient) -> dict[str, Any]:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


def _ref(schema: dict[str, Any]) -> str:
    return schema["$ref"].rsplit("/", 1)[-1]  # type: ignore[no-any-return]


def test_openapi_lists_the_four_complaint_operations(openapi: dict[str, Any]) -> None:
    paths = openapi["paths"]

    assert set(paths["/api/complaints"]) == {"get", "post"}
    assert set(paths["/api/complaints/{complaint_id}"]) == {"get"}
    assert set(paths["/api/complaints/{complaint_id}/status"]) == {"patch"}


def test_openapi_documents_the_status_codes_each_operation_can_return(
    openapi: dict[str, Any],
) -> None:
    paths = openapi["paths"]

    def codes(path: str, method: str) -> set[str]:
        return set(paths[path][method]["responses"])

    assert codes("/api/complaints", "post") == {"201", "400"}
    assert codes("/api/complaints", "get") == {"200", "400"}
    assert codes("/api/complaints/{complaint_id}", "get") == {"200", "400", "404"}
    assert codes("/api/complaints/{complaint_id}/status", "patch") == {"200", "400", "404", "409"}


def test_openapi_never_advertises_422(openapi: dict[str, Any]) -> None:
    # Validation failures are 400 in this API; a generated client must not be told about a 422.
    assert "422" not in str(openapi["paths"])
    assert "HTTPValidationError" not in openapi["components"]["schemas"]


def test_openapi_describes_the_request_and_response_schemas(openapi: dict[str, Any]) -> None:
    paths = openapi["paths"]
    schemas = openapi["components"]["schemas"]

    post = paths["/api/complaints"]["post"]
    assert _ref(post["requestBody"]["content"]["application/json"]["schema"]) == "ComplaintCreate"
    assert _ref(post["responses"]["201"]["content"]["application/json"]["schema"]) == "ComplaintOut"
    assert _ref(post["responses"]["400"]["content"]["application/json"]["schema"]) == (
        "ValidationErrorOut"
    )

    listing = paths["/api/complaints"]["get"]["responses"]["200"]["content"]["application/json"]
    assert _ref(listing["schema"]) == "ComplaintPageOut"

    patch = paths["/api/complaints/{complaint_id}/status"]["patch"]
    assert _ref(patch["requestBody"]["content"]["application/json"]["schema"]) == "StatusChange"
    assert _ref(patch["responses"]["409"]["content"]["application/json"]["schema"]) == (
        "TransitionConflictOut"
    )
    assert (
        _ref(
            paths["/api/complaints/{complaint_id}"]["get"]["responses"]["404"]["content"][
                "application/json"
            ]["schema"]
        )
        == "ErrorOut"
    )

    create = schemas["ComplaintCreate"]
    assert set(create["required"]) == {"text", "location"}
    assert create["properties"]["text"]["minLength"] == 10
    assert create["properties"]["text"]["maxLength"] == 2000
    assert create["properties"]["location"]["minLength"] == 3
    assert create["properties"]["location"]["maxLength"] == 200

    out = schemas["ComplaintOut"]
    assert {"id", "category", "priority", "status", "triaged_by", "allowed_transitions"} <= set(
        out["properties"]
    )
    assert "allowed_transitions" in out["required"]
    assert set(schemas["Status"]["enum"]) == {"open", "in_progress", "resolved", "rejected"}
    assert set(schemas["ComplaintPageOut"]["required"]) == {"items", "total", "page", "page_size"}


def test_openapi_describes_the_list_query_parameters(openapi: dict[str, Any]) -> None:
    parameters = {p["name"]: p for p in openapi["paths"]["/api/complaints"]["get"]["parameters"]}

    assert set(parameters) == {"category", "priority", "status", "page", "page_size"}
    assert all(p["in"] == "query" and p["required"] is False for p in parameters.values())
    assert parameters["page"]["schema"]["minimum"] == 1
    assert parameters["page"]["schema"]["maximum"] == 1_000_000
    assert parameters["page_size"]["schema"]["minimum"] == 1
    assert parameters["page_size"]["schema"]["maximum"] == 100
    assert parameters["page_size"]["schema"]["default"] == 20


def test_the_openapi_document_is_built_once_and_stays_stable(client: TestClient) -> None:
    assert client.get("/openapi.json").json() == client.get("/openapi.json").json()
