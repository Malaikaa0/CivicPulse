import itertools

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain import Status
from app.routes.errors import register_error_handlers
from app.services.status_machine import (
    InvalidTransition,
    allowed_targets,
    ensure_transition_allowed,
)

# Written out independently of TRANSITIONS on purpose: the test states the spec, so a wrong edit
# to the table cannot also "fix" its own test.
ALLOWED = {
    (Status.OPEN, Status.IN_PROGRESS),
    (Status.OPEN, Status.REJECTED),
    (Status.IN_PROGRESS, Status.RESOLVED),
    (Status.IN_PROGRESS, Status.REJECTED),
}
ALL_PAIRS = list(itertools.product(Status, Status))


@pytest.mark.parametrize(("current", "requested"), ALL_PAIRS)
def test_every_transition_pair_matches_the_spec(current: Status, requested: Status) -> None:
    if (current, requested) in ALLOWED:
        ensure_transition_allowed(current, requested)  # must not raise
    else:
        with pytest.raises(InvalidTransition):
            ensure_transition_allowed(current, requested)


@pytest.mark.parametrize("terminal", [Status.RESOLVED, Status.REJECTED])
def test_terminal_states_have_no_way_out(terminal: Status) -> None:
    assert allowed_targets(terminal) == []


def test_allowed_targets_are_listed_in_stable_order() -> None:
    assert allowed_targets(Status.OPEN) == [Status.IN_PROGRESS, Status.REJECTED]
    assert allowed_targets(Status.IN_PROGRESS) == [Status.RESOLVED, Status.REJECTED]


def test_error_message_names_the_attempted_transition() -> None:
    with pytest.raises(InvalidTransition) as info:
        ensure_transition_allowed(Status.RESOLVED, Status.OPEN)

    assert str(info.value) == "Cannot change status from 'resolved' to 'open'"


def test_invalid_transition_becomes_a_409_with_the_message() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        ensure_transition_allowed(Status.REJECTED, Status.IN_PROGRESS)

    response = TestClient(app).get("/boom")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Cannot change status from 'rejected' to 'in_progress'",
        "current": "rejected",
        "requested": "in_progress",
    }
