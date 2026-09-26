"""The triage service under every failure mode, with no real sleeping and no network."""

import pytest

from app.domain import Category
from app.providers.triage.base import TriageResult
from app.providers.triage.errors import (
    TriageBadRequest,
    TriageError,
    TriageInvalidOutput,
    TriageRateLimited,
    TriageServerError,
    TriageTimeout,
)
from app.providers.triage.rules import RuleBasedTriage
from app.providers.triage.simulated import SimulatedTriage
from app.services.triage import FALLBACK_NAME, TriageService

TEXT = "Burst water main flooding Street 12, water entering ground floors"


class Recorder:
    """Stands in for time.sleep so tests can assert on waits without ever waiting."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class ExplodingProvider:
    """A provider with a bug: raises something that is not a TriageError."""

    name = "llm:groq"

    def __init__(self) -> None:
        self.calls = 0

    def triage(self, text: str, location: str) -> TriageResult:
        self.calls += 1
        raise RuntimeError("boom")


def _service(provider: SimulatedTriage | ExplodingProvider, sleep: Recorder) -> TriageService:
    return TriageService(provider, sleep=sleep, jitter=lambda: 0.0)


def test_success_on_the_first_try_does_not_retry_or_wait() -> None:
    provider, sleep = SimulatedTriage(), Recorder()

    outcome = _service(provider, sleep).triage(TEXT, "Street 12")

    assert outcome.fallback is False
    assert outcome.result.category == Category.WATER
    assert provider.calls == 1
    assert sleep.sleeps == []


def test_simulated_results_are_stored_as_rules() -> None:
    outcome = _service(SimulatedTriage(), Recorder()).triage(TEXT, "Street 12")

    assert outcome.triaged_by == "rules"


@pytest.mark.parametrize("error", [TriageTimeout(), TriageRateLimited(), TriageServerError()])
def test_a_transient_failure_is_retried_once_and_can_recover(error: TriageError) -> None:
    provider, sleep = SimulatedTriage(script=[error, None]), Recorder()

    outcome = _service(provider, sleep).triage(TEXT, "Street 12")

    assert outcome.fallback is False
    assert outcome.triaged_by == "rules"
    assert provider.calls == 2
    assert len(sleep.sleeps) == 1


def test_it_gives_up_after_exactly_one_retry() -> None:
    provider = SimulatedTriage(always_fail=TriageTimeout())

    outcome = _service(provider, Recorder()).triage(TEXT, "Street 12")

    assert provider.calls == 2  # the first attempt plus one retry, never a third
    assert outcome.fallback is True


@pytest.mark.parametrize("error", [TriageBadRequest(), TriageInvalidOutput()])
def test_a_non_retryable_failure_is_never_retried(error: TriageError) -> None:
    provider, sleep = SimulatedTriage(always_fail=error), Recorder()

    outcome = _service(provider, sleep).triage(TEXT, "Street 12")

    assert provider.calls == 1
    assert sleep.sleeps == []
    assert outcome.fallback is True


def test_fallback_records_rules_fallback_and_matches_the_rule_based_result() -> None:
    outcome = _service(SimulatedTriage(always_fail=TriageServerError()), Recorder()).triage(
        TEXT, "Street 12"
    )

    assert outcome.triaged_by == FALLBACK_NAME == "rules:fallback"
    assert outcome.result == RuleBasedTriage().triage(TEXT, "Street 12")


def test_fallback_says_which_provider_failed_and_why() -> None:
    outcome = _service(SimulatedTriage(always_fail=TriageRateLimited()), Recorder()).triage(
        TEXT, "Street 12"
    )

    assert outcome.failed_provider == "simulated"
    assert outcome.error_class == "TriageRateLimited"


def test_success_carries_no_failure_details() -> None:
    outcome = _service(SimulatedTriage(), Recorder()).triage(TEXT, "Street 12")

    assert outcome.failed_provider is None
    assert outcome.error_class is None


def test_an_unexpected_bug_in_a_provider_still_degrades_to_the_fallback() -> None:
    provider, sleep = ExplodingProvider(), Recorder()

    outcome = _service(provider, sleep).triage(TEXT, "Street 12")

    assert outcome.fallback is True
    assert outcome.error_class == "RuntimeError"
    assert provider.calls == 1  # not a known-transient error, so no retry
    assert sleep.sleeps == []


def test_retry_wait_is_jittered_within_bounds() -> None:
    sleep = Recorder()
    service = TriageService(
        SimulatedTriage(script=[TriageTimeout(), None]),
        retry_delay_seconds=0.5,
        sleep=sleep,
        jitter=lambda: 0.5,
    )

    service.triage(TEXT, "Street 12")

    assert sleep.sleeps == [0.75]  # 0.5 * (1 + 0.5)


def test_latency_is_measured_across_the_whole_call_including_the_retry() -> None:
    ticks = iter([10.0, 10.3])  # started, then finished 300 ms later
    service = TriageService(
        SimulatedTriage(script=[TriageTimeout(), None]),
        sleep=Recorder(),
        jitter=lambda: 0.0,
        clock=lambda: next(ticks),
    )

    assert service.triage(TEXT, "Street 12").latency_ms == 300


def test_a_provider_that_always_raises_never_lets_an_exception_escape() -> None:
    # The must-have from the spec: a third party failing must not become our failure.
    for error in (TriageTimeout(), TriageServerError(), TriageBadRequest(), TriageInvalidOutput()):
        outcome = _service(SimulatedTriage(always_fail=error), Recorder()).triage(TEXT, "x")
        assert outcome.triaged_by == "rules:fallback"
