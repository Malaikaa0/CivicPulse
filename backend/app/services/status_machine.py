"""Complaint status state machine.

The rules live here, and only here. The frontend asks the backend which moves are allowed
(allowed_targets) instead of keeping its own copy, so there is one source of truth.
"""

from collections.abc import Mapping

from app.domain import Status

# The whole rulebook as data. A missing or empty entry means "terminal": no way out.
TRANSITIONS: Mapping[Status, frozenset[Status]] = {
    Status.OPEN: frozenset({Status.IN_PROGRESS, Status.REJECTED}),
    Status.IN_PROGRESS: frozenset({Status.RESOLVED, Status.REJECTED}),
    Status.RESOLVED: frozenset(),
    Status.REJECTED: frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, current: Status, requested: Status) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"Cannot change status from '{current.value}' to '{requested.value}'")


def allowed_targets(current: Status) -> list[Status]:
    """Statuses reachable from `current`, in a stable order."""
    return sorted(TRANSITIONS[current], key=list(Status).index)


def ensure_transition_allowed(current: Status, requested: Status) -> None:
    """Raise InvalidTransition unless `current -> requested` is in the table."""
    if requested not in TRANSITIONS[current]:
        raise InvalidTransition(current, requested)
