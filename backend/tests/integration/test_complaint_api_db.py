"""The complaint endpoints end to end: real app, real wiring, real PostgreSQL.

Only the engine (pointed at the test database) and, where a test needs it, the triage provider
are substituted. The session, repository, service and routes are the ones the app really uses.
"""

import threading
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app import dependencies
from app.config import get_settings
from app.domain import Status
from app.main import create_app
from app.providers.triage.errors import TriageServerError
from app.providers.triage.simulated import SimulatedTriage
from app.repositories.complaints import ComplaintRepository
from app.services.triage import TriageService

pytestmark = pytest.mark.integration

WATER = "Burst water main flooding Street 12, water entering ground floors"
ROADS = "Large pothole on the main road near the market gate"
LIGHT = "Streetlight outside house 5 flickers at night, a minor nuisance"
SANITATION = "Garbage has not been collected from our lane this week"
ELECTRICITY = "Transformer in our block gives low voltage every evening"

ClientFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[ClientFactory]:
    monkeypatch.setattr(dependencies, "get_engine", lambda: engine)
    monkeypatch.setenv("TRIAGE_PROVIDER", "simulated")
    get_settings.cache_clear()
    dependencies.get_triage_service.cache_clear()

    def factory(provider: SimulatedTriage | None = None) -> TestClient:
        app = create_app()
        if provider is not None:
            triage = TriageService(provider, sleep=lambda _: None, jitter=lambda: 0.0)
            app.dependency_overrides[dependencies.get_triage_service] = lambda: triage
        return TestClient(app)

    yield factory

    get_settings.cache_clear()
    dependencies.get_triage_service.cache_clear()
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE complaints"))


@pytest.fixture
def client(make_client: ClientFactory) -> TestClient:
    return make_client()


def _post(
    client: TestClient, text_: str, location: str = "Street 12", **extra: Any
) -> dict[str, Any]:
    response = client.post("/api/complaints", json={"text": text_, "location": location, **extra})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _patch(client: TestClient, complaint_id: str, status: str) -> Any:
    return client.patch(f"/api/complaints/{complaint_id}/status", json={"status": status})


def _ids(response: Any) -> list[str]:
    return [item["id"] for item in response.json()["items"]]


# ---- create and read back ----


def test_a_created_complaint_can_be_read_back_unchanged(client: TestClient, engine: Engine) -> None:
    created = _post(client, WATER, "  Street 12 ", reporter_contact="0300-1112233")

    fetched = client.get(f"/api/complaints/{created['id']}")

    assert fetched.status_code == 200
    assert fetched.json() == created
    assert created["category"] == "water"
    assert created["priority"] == "high"
    assert created["status"] == "open"
    assert created["triaged_by"] == "rules"
    assert created["location"] == "Street 12"
    assert created["allowed_transitions"] == ["in_progress", "rejected"]
    assert datetime.fromisoformat(created["created_at"]).tzinfo is not None  # timestamptz
    with Session(engine) as fresh:  # and it really is in the database, committed
        stored = ComplaintRepository(fresh).get(uuid.UUID(created["id"]))
        assert stored is not None
        assert stored.text == WATER
        assert stored.reporter_contact == "0300-1112233"


def test_a_blank_contact_is_stored_as_null(client: TestClient, engine: Engine) -> None:
    created = _post(client, WATER, reporter_contact="   ")

    assert created["reporter_contact"] is None
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT reporter_contact FROM complaints WHERE id = :id"), {"id": created["id"]}
        ).scalar_one()
    assert stored is None


def test_urdu_and_other_unicode_text_round_trips(client: TestClient) -> None:
    unicode_text = "Pani ka pressure bohat kam hai، گلی نمبر 12 میں پانی نہیں آ رہا"

    created = _post(client, unicode_text, "گلی نمبر 12")

    assert client.get(f"/api/complaints/{created['id']}").json()["text"] == unicode_text


def test_a_provider_that_always_raises_still_gives_201_and_stores_a_rules_fallback(
    make_client: ClientFactory, engine: Engine
) -> None:
    # The spec's must-have, through HTTP, the real session and a real database.
    client = make_client(SimulatedTriage(always_fail=TriageServerError()))

    response = client.post("/api/complaints", json={"text": WATER, "location": "Street 12"})

    assert response.status_code == 201
    assert response.json()["triaged_by"] == "rules:fallback"
    with engine.connect() as connection:
        stored = connection.execute(text("SELECT triaged_by, category FROM complaints")).one()
    assert tuple(stored) == ("rules:fallback", "water")


def test_rejected_input_stores_nothing(client: TestClient, engine: Engine) -> None:
    for payload in ({"text": "short", "location": "Street 12"}, {"text": WATER}, {}):
        assert client.post("/api/complaints", json=payload).status_code == 400

    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM complaints")).scalar_one() == 0


# ---- list: filters and pagination ----


@pytest.fixture
def five(client: TestClient) -> dict[str, str]:
    """Five complaints of different kinds, created oldest to newest; returns {kind: id}."""
    ids = {
        "water": _post(client, WATER)["id"],  # water, high
        "roads": _post(client, ROADS)["id"],  # roads, normal
        "light": _post(client, LIGHT)["id"],  # streetlights, low
        "sanitation": _post(client, SANITATION)["id"],  # sanitation, normal
        "electricity": _post(client, ELECTRICITY)["id"],  # electricity, normal
    }
    assert _patch(client, ids["roads"], "in_progress").status_code == 200
    assert _patch(client, ids["light"], "rejected").status_code == 200
    return ids


def test_list_is_newest_first_and_reports_the_total(
    client: TestClient, five: dict[str, str]
) -> None:
    response = client.get("/api/complaints")

    body = response.json()
    assert response.status_code == 200
    assert (body["total"], body["page"], body["page_size"]) == (5, 1, 20)
    assert _ids(response) == list(reversed(list(five.values())))  # created oldest to newest


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({"category": "water"}, ["water"]),
        ({"category": "streetlights"}, ["light"]),
        ({"category": "other"}, []),
        ({"priority": "high"}, ["water"]),
        ({"priority": "normal"}, ["electricity", "sanitation", "roads"]),
        ({"priority": "low"}, ["light"]),
        ({"status": "open"}, ["electricity", "sanitation", "water"]),
        ({"status": "in_progress"}, ["roads"]),
        ({"status": "rejected"}, ["light"]),
        ({"status": "resolved"}, []),
        ({"priority": "normal", "status": "open"}, ["electricity", "sanitation"]),
        ({"category": "roads", "status": "in_progress", "priority": "normal"}, ["roads"]),
        ({"category": "roads", "status": "open"}, []),
    ],
)
def test_each_filter_and_their_combinations_narrow_the_list(
    client: TestClient, five: dict[str, str], query: dict[str, str], expected: list[str]
) -> None:
    response = client.get("/api/complaints", params=query)

    assert response.status_code == 200
    assert _ids(response) == [five[kind] for kind in expected]
    assert response.json()["total"] == len(expected)


def test_pagination_walks_every_complaint_exactly_once(
    client: TestClient, five: dict[str, str]
) -> None:
    newest_first = list(reversed(list(five.values())))

    pages = [
        client.get("/api/complaints", params={"page": n, "page_size": 2}) for n in (1, 2, 3, 4)
    ]

    assert [_ids(p) for p in pages] == [newest_first[0:2], newest_first[2:4], newest_first[4:], []]
    assert all(p.json()["total"] == 5 for p in pages)  # the total ignores paging
    assert [p.json()["page"] for p in pages] == [1, 2, 3, 4]


def test_pagination_applies_after_filtering(client: TestClient, five: dict[str, str]) -> None:
    response = client.get("/api/complaints", params={"status": "open", "page": 2, "page_size": 2})

    assert _ids(response) == [five["water"]]
    assert response.json()["total"] == 3


def test_the_page_size_cap_is_enforced(client: TestClient, five: dict[str, str]) -> None:
    assert client.get("/api/complaints", params={"page_size": 100}).status_code == 200

    response = client.get("/api/complaints", params={"page_size": 101})

    assert response.status_code == 400
    assert response.json()["detail"][0]["field"] == "page_size"


def test_a_nul_character_in_the_text_is_not_a_server_error(client: TestClient) -> None:
    # PostgreSQL text cannot hold NUL. That is bad input, so it must not surface as a 500.
    response = client.post(
        "/api/complaints",
        json={"text": "Water leak\u0000 near the school", "location": "Street 12"},
    )

    assert response.status_code == 400


def test_a_page_number_too_large_for_the_database_is_a_client_error_not_a_crash(
    client: TestClient,
) -> None:
    response = client.get("/api/complaints", params={"page": 10**30})

    assert response.status_code == 400
    assert response.json()["detail"][0]["field"] == "page"


# ---- status changes ----


def test_a_complaint_walks_open_in_progress_resolved_then_is_locked(client: TestClient) -> None:
    created = _post(client, WATER)
    complaint_id = created["id"]

    in_progress = _patch(client, complaint_id, "in_progress")
    assert in_progress.status_code == 200
    assert in_progress.json()["status"] == "in_progress"
    assert in_progress.json()["allowed_transitions"] == ["resolved", "rejected"]
    assert datetime.fromisoformat(in_progress.json()["updated_at"]) > datetime.fromisoformat(
        created["updated_at"]
    )

    resolved = _patch(client, complaint_id, "resolved")
    assert resolved.status_code == 200
    assert resolved.json()["allowed_transitions"] == []

    for attempt in ("open", "in_progress", "rejected", "resolved"):
        conflict = _patch(client, complaint_id, attempt)
        assert conflict.status_code == 409
        assert conflict.json() == {
            "detail": f"Cannot change status from 'resolved' to '{attempt}'",
            "current": "resolved",
            "requested": attempt,
        }

    assert client.get(f"/api/complaints/{complaint_id}").json()["status"] == "resolved"


def test_a_rejected_transition_leaves_the_stored_status_alone(
    client: TestClient, engine: Engine
) -> None:
    complaint_id = _post(client, WATER)["id"]

    assert _patch(client, complaint_id, "resolved").status_code == 409  # cannot skip in_progress

    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT status::text FROM complaints WHERE id = :id"), {"id": complaint_id}
        ).scalar_one()
    assert stored == Status.OPEN.value


def test_two_simultaneous_changes_from_the_same_state_cannot_both_win(
    make_client: ClientFactory,
) -> None:
    # Both requests read "open". Without the row lock, both would apply open -> in_progress.
    first, second = make_client(), make_client()
    complaint_id = _post(first, WATER)["id"]
    start = threading.Barrier(2)
    codes: list[int] = []

    def change(client: TestClient) -> None:
        start.wait()
        codes.append(_patch(client, complaint_id, "in_progress").status_code)

    threads = [threading.Thread(target=change, args=(c,)) for c in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(codes) == [200, 409]


# ---- 404 and 400 ----


def test_unknown_ids_are_404_for_get_and_patch(client: TestClient) -> None:
    unknown = uuid.uuid4()

    for response in (
        client.get(f"/api/complaints/{unknown}"),
        _patch(client, str(unknown), "in_progress"),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": f"Complaint {unknown} not found"}


def test_malformed_ids_and_statuses_are_field_level_400s(client: TestClient) -> None:
    complaint_id = _post(client, WATER)["id"]

    bad_id = client.get("/api/complaints/not-a-uuid")
    bad_status = _patch(client, complaint_id, "closed")

    assert bad_id.status_code == 400
    assert bad_id.json()["detail"][0]["field"] == "complaint_id"
    assert bad_status.status_code == 400
    assert bad_status.json()["detail"][0]["field"] == "status"


# ---- the session is request-scoped and always released ----


def test_no_connection_is_left_checked_out_after_any_kind_of_request(
    client: TestClient, engine: Engine
) -> None:
    complaint_id = _post(client, WATER)["id"]
    client.get(f"/api/complaints/{complaint_id}")
    client.get(f"/api/complaints/{uuid.uuid4()}")  # 404
    client.get("/api/complaints", params={"status": "open"})
    _patch(client, complaint_id, "resolved")  # 409
    _patch(client, complaint_id, "in_progress")
    _patch(client, str(uuid.uuid4()), "in_progress")  # 404
    client.get("/api/complaints/not-a-uuid")  # 400

    assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
