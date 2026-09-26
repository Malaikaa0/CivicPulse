"""Triage orchestration: turn "a provider that might fail" into "a result, always".

A citizen must never see a 500 because a third party was slow or rate-limited. So this service
retries a transient failure once, then falls back to the deterministic rule-based provider.
The hard 10-second cap on each call belongs to the provider's HTTP client (it owns the socket);
this service owns what happens after a call fails.
"""

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.providers.triage.base import TriageProvider, TriageResult
from app.providers.triage.errors import TriageError
from app.providers.triage.rules import RuleBasedTriage

logger = logging.getLogger(__name__)

FALLBACK_NAME = "rules:fallback"

# The database only accepts the spec's four triaged_by values. The simulated provider is a
# rules stand-in, so it is recorded as such rather than widening the schema.
_STORED_NAME = {"simulated": "rules"}


@dataclass(frozen=True)
class TriageOutcome:
    result: TriageResult
    triaged_by: str  # value to store: llm:groq, llm:ollama, rules or rules:fallback
    latency_ms: int
    fallback: bool
    # Set only on fallback, so the caller can log the WARNING once it knows the complaint id.
    failed_provider: str | None = None
    error_class: str | None = None


class TriageService:
    def __init__(
        self,
        provider: TriageProvider,
        fallback: TriageProvider | None = None,
        *,
        retry_delay_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        # random.random is fine here: jitter spreads retries out, it is not a security control.
        jitter: Callable[[], float] = random.random,  # noqa: S311
        clock: Callable[[], float] = time.perf_counter,
        recorder: Callable[[TriageOutcome], None] | None = None,
    ) -> None:
        self._provider = provider
        self._fallback = fallback or RuleBasedTriage()
        self._retry_delay = retry_delay_seconds
        self._sleep = sleep
        self._jitter = jitter
        self._clock = clock
        self._recorder = recorder

    def triage(self, text: str, location: str) -> TriageOutcome:
        started = self._clock()
        try:
            result = self._attempt_with_one_retry(text, location)
        except Exception as error:  # deliberately broad: any failure must degrade, not surface
            outcome = self._fall_back(text, location, started, error)
        else:
            outcome = TriageOutcome(
                result=result,
                triaged_by=_STORED_NAME.get(self._provider.name, self._provider.name),
                latency_ms=self._elapsed_ms(started),
                fallback=False,
            )

        self._record(outcome)
        return outcome

    def _record(self, outcome: TriageOutcome) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder(outcome)
        except Exception as error:  # observability must never change what the citizen gets
            logger.warning("triage outcome recorder failed: %s", type(error).__name__)

    def _attempt_with_one_retry(self, text: str, location: str) -> TriageResult:
        try:
            return self._provider.triage(text, location)
        except TriageError as error:
            if not error.retryable:
                raise  # a 400 was wrong and will be wrong again
            # Jittered wait so that many requests failing together do not retry in lockstep.
            self._sleep(self._retry_delay * (1 + self._jitter()))
            return self._provider.triage(text, location)  # the one and only retry

    def _fall_back(
        self, text: str, location: str, started: float, error: Exception
    ) -> TriageOutcome:
        return TriageOutcome(
            result=self._fallback.triage(text, location),
            triaged_by=FALLBACK_NAME,
            latency_ms=self._elapsed_ms(started),
            fallback=True,
            failed_provider=self._provider.name,
            error_class=type(error).__name__,
        )

    def _elapsed_ms(self, started: float) -> int:
        return max(0, round((self._clock() - started) * 1000))
