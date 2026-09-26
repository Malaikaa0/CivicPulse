"""The outcome log and the TriageService recorder hook, against an in-memory store."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from triage_store_fake import FakeStore

from app.providers.triage.base import TriageResult
from app.providers.triage.errors import TriageBadRequest, TriageTimeout
from app.providers.triage.simulated import SimulatedTriage
from app.services.triage import FALLBACK_NAME, TriageOutcome, TriageService
from app.services.triage_outcomes import (
    MAX_OUTCOMES,
    OUTCOMES_KEY,
    RecordedOutcome,
    TriageOutcomeLog,
)

TEXT = "Burst water main flooding Street 12, call me on 0300-1234567"
T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


class Ticking:
    """A clock that advances one second per reading, so ordering is unambiguous."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        current, self.now = self.now, self.now + timedelta(seconds=1)
        return current


def _outcome(
    provider: str = "llm:gemini", latency_ms: int = 120, fallback: bool = False
) -> TriageOutcome:
    result = TriageResult.model_validate(
        {"category": "water", "priority": "high", "summary": "Burst main", "confidence": 0.9}
    )
    return TriageOutcome(
        result=result, triaged_by=provider, latency_ms=latency_ms, fallback=fallback
    )


def test_a_recorded_outcome_keeps_provider_latency_fallback_and_time() -> None:
    log = TriageOutcomeLog(FakeStore(), clock=Ticking())

    log.record(_outcome("llm:groq", latency_ms=340, fallback=False))

    assert log.recent() == [
        RecordedOutcome(provider="llm:groq", latency_ms=340, fallback=False, at=T0)
    ]


def test_the_stored_entry_holds_only_the_four_facts() -> None:
    store = FakeStore()
    TriageOutcomeLog(store, clock=Ticking()).record(_outcome(fallback=True))

    [raw] = store.lists[OUTCOMES_KEY]

    assert json.loads(raw) == {
        "provider": "llm:gemini",
        "latency_ms": 120,
        "fallback": True,
        "at": "2026-09-27T12:00:00Z",
    }


def test_outcomes_come_back_newest_first() -> None:
    log = TriageOutcomeLog(FakeStore(), clock=Ticking())

    for latency in (10, 20, 30):
        log.record(_outcome(latency_ms=latency))

    assert [o.latency_ms for o in log.recent()] == [30, 20, 10]


def test_only_the_newest_twenty_are_kept() -> None:
    store = FakeStore()
    log = TriageOutcomeLog(store, clock=Ticking())

    for latency in range(1, 26):
        log.record(_outcome(latency_ms=latency))

    recent = log.recent()
    assert MAX_OUTCOMES == 20
    assert [o.latency_ms for o in recent] == list(range(25, 5, -1))
    assert len(store.lists[OUTCOMES_KEY]) == 20  # trimmed on write, not only on read


def test_an_empty_log_is_an_empty_list() -> None:
    assert TriageOutcomeLog(FakeStore()).recent() == []


def test_an_unreadable_entry_is_skipped_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeStore()
    log = TriageOutcomeLog(store, clock=Ticking())
    log.record(_outcome(latency_ms=5))
    store.lists[OUTCOMES_KEY].insert(0, "{broken")
    store.lists[OUTCOMES_KEY].insert(0, '{"provider": "x"}')

    with caplog.at_level(logging.WARNING):
        recent = log.recent()

    assert [o.latency_ms for o in recent] == [5]
    assert "unreadable triage outcome" in caplog.text


def test_the_default_clock_stamps_utc() -> None:
    store = FakeStore()
    log = TriageOutcomeLog(store)

    log.record(_outcome())

    [entry] = log.recent()
    assert entry.at.tzinfo is not None
    assert entry.at.utcoffset() == timedelta(0)


# --- the recorder hook on TriageService --------------------------------------------------------


def _service(
    provider: SimulatedTriage, recorder: Callable[[TriageOutcome], None] | None = None
) -> tuple[TriageService, list[TriageOutcome]]:
    seen: list[TriageOutcome] = []
    service = TriageService(
        provider,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
        recorder=recorder or seen.append,
    )
    return service, seen


def test_the_recorder_sees_a_successful_outcome() -> None:
    service, seen = _service(SimulatedTriage())

    returned = service.triage(TEXT, "Street 12")

    assert seen == [returned]
    assert seen[0].fallback is False
    assert seen[0].triaged_by == "rules"


def test_the_recorder_sees_a_fallback_outcome() -> None:
    service, seen = _service(SimulatedTriage(always_fail=TriageBadRequest()))

    returned = service.triage(TEXT, "Street 12")

    assert seen == [returned]
    assert seen[0].fallback is True
    assert seen[0].triaged_by == FALLBACK_NAME


def test_the_recorder_is_called_once_per_triage_even_after_a_retry() -> None:
    service, seen = _service(SimulatedTriage(script=[TriageTimeout(), None]))

    service.triage(TEXT, "Street 12")

    assert len(seen) == 1


def test_a_raising_recorder_is_swallowed_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    def broken(outcome: TriageOutcome) -> None:
        raise ConnectionError("redis down, password=hunter2")

    service, _ = _service(SimulatedTriage(), recorder=broken)

    with caplog.at_level(logging.WARNING):
        outcome = service.triage(TEXT, "Street 12")

    assert outcome.fallback is False
    assert outcome.result.category.value == "water"
    assert "recorder failed: ConnectionError" in caplog.text
    assert "hunter2" not in caplog.text  # the class is logged, never the message


def test_a_raising_recorder_does_not_change_a_fallback_outcome() -> None:
    def broken(outcome: TriageOutcome) -> None:
        raise RuntimeError("boom")

    service, _ = _service(SimulatedTriage(always_fail=TriageBadRequest()), recorder=broken)

    outcome = service.triage(TEXT, "Street 12")

    assert outcome.fallback is True
    assert outcome.failed_provider == "simulated"


def test_no_recorder_is_the_default() -> None:
    outcome = TriageService(SimulatedTriage()).triage(TEXT, "Street 12")

    assert outcome.fallback is False


def test_the_log_is_a_valid_recorder_end_to_end() -> None:
    store = FakeStore()
    log = TriageOutcomeLog(store, clock=Ticking())
    service = TriageService(SimulatedTriage(), recorder=log.record)

    service.triage(TEXT, "Street 12")

    [entry] = log.recent()
    assert (entry.provider, entry.fallback) == ("rules", False)
    assert TEXT.lower() not in "".join(store.lists[OUTCOMES_KEY]).lower()
