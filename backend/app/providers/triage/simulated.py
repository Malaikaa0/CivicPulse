"""Deterministic fake provider for tests and CI: no network, no clock, no randomness.

Classification is delegated to the rule-based provider so tests can assert meaningful
categories ("burst water main" is water). What SimulatedTriage adds is a seeded confidence
and scripted failures, so the retry and fallback paths can be exercised on demand.
"""

import hashlib
from collections.abc import Sequence

from app.providers.triage.base import TriageResult
from app.providers.triage.errors import TriageError
from app.providers.triage.rules import RuleBasedTriage


class SimulatedTriage:
    name = "simulated"

    def __init__(
        self,
        seed: int = 0,
        script: Sequence[TriageError | None] = (),
        always_fail: TriageError | None = None,
    ) -> None:
        """
        script:      one entry per call, in order. An error is raised, None succeeds. Once the
                     script is used up, calls succeed. ([Timeout, None] = fail once, then work.)
        always_fail: raised on every call after the script, for "the provider is down".
        """
        self._seed = seed
        self._script = list(script)
        self._always_fail = always_fail
        self._rules = RuleBasedTriage()
        self.calls = 0

    def triage(self, text: str, location: str) -> TriageResult:
        self.calls += 1

        failure = self._script.pop(0) if self._script else self._always_fail
        if failure is not None:
            raise failure

        base = self._rules.triage(text, location)
        return base.model_copy(update={"confidence": self._confidence(text)})

    def _confidence(self, text: str) -> float:
        # Same seed and text always give the same value; different seeds give different ones.
        digest = hashlib.sha256(f"{self._seed}:{text}".encode()).digest()
        return round(0.5 + (digest[0] / 255) * 0.49, 2)  # 0.50 .. 0.99
