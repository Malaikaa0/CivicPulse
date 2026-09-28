import pytest
from pydantic import ValidationError

from app.config import Settings
from app.domain import Category
from app.providers.triage.base import TriageProvider
from app.providers.triage.errors import (
    TriageBadRequest,
    TriageError,
    TriageInvalidOutput,
    TriageRateLimited,
    TriageServerError,
    TriageTimeout,
)
from app.providers.triage.factory import create_triage_provider
from app.providers.triage.rules import RuleBasedTriage
from app.providers.triage.simulated import SimulatedTriage

TEXT = "Burst water main flooding Street 12, water entering ground floors"


def _settings(provider: str) -> Settings:
    return Settings.model_validate(
        {"database_url": "postgresql://x", "redis_url": "redis://x", "triage_provider": provider}
    )


def test_simulated_triage_satisfies_the_provider_interface() -> None:
    provider: TriageProvider = SimulatedTriage()  # checked by mypy

    assert provider.name == "simulated"


def test_same_seed_and_text_always_give_the_same_result() -> None:
    a = SimulatedTriage(seed=7).triage(TEXT, "Street 12")
    b = SimulatedTriage(seed=7).triage(TEXT, "Street 12")

    assert a == b


def test_different_seeds_give_different_confidence() -> None:
    confidences = {SimulatedTriage(seed=s).triage(TEXT, "x").confidence for s in range(10)}

    assert len(confidences) > 1


def test_classification_is_meaningful_so_tests_can_assert_on_it() -> None:
    assert SimulatedTriage().triage(TEXT, "Street 12").category == Category.WATER


def test_confidence_stays_in_range() -> None:
    for seed in range(50):
        assert 0.0 <= SimulatedTriage(seed=seed).triage(TEXT, "x").confidence <= 1.0


def test_script_fails_the_first_call_then_succeeds() -> None:
    provider = SimulatedTriage(script=[TriageTimeout(), None])

    with pytest.raises(TriageTimeout):
        provider.triage(TEXT, "x")
    assert provider.triage(TEXT, "x").category == Category.WATER
    assert provider.calls == 2


def test_script_is_used_up_then_calls_succeed() -> None:
    provider = SimulatedTriage(script=[TriageServerError()])

    with pytest.raises(TriageServerError):
        provider.triage(TEXT, "x")

    for _ in range(3):
        provider.triage(TEXT, "x")
    assert provider.calls == 4


def test_always_fail_raises_on_every_call() -> None:
    provider = SimulatedTriage(always_fail=TriageRateLimited())

    for _ in range(3):
        with pytest.raises(TriageRateLimited):
            provider.triage(TEXT, "x")
    assert provider.calls == 3


def test_script_takes_priority_over_always_fail() -> None:
    provider = SimulatedTriage(script=[None], always_fail=TriageServerError())

    provider.triage(TEXT, "x")  # scripted success
    with pytest.raises(TriageServerError):
        provider.triage(TEXT, "x")


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (TriageTimeout(), True),
        (TriageRateLimited(), True),
        (TriageServerError(), True),
        (TriageBadRequest(), False),
        (TriageInvalidOutput(), False),
    ],
)
def test_only_transient_failures_are_retryable(error: TriageError, retryable: bool) -> None:
    assert error.retryable is retryable


def test_factory_selects_the_provider_named_in_settings() -> None:
    assert isinstance(create_triage_provider(_settings("rules")), RuleBasedTriage)
    assert isinstance(create_triage_provider(_settings("simulated")), SimulatedTriage)


def test_unbuilt_providers_are_rejected_by_settings_validation() -> None:
    # Ollama was evaluated and not built (issue #45). Rejecting it here, when settings load,
    # means a misconfigured deploy fails at startup instead of 500ing on the first complaint.
    with pytest.raises(ValidationError):
        _settings("ollama")
