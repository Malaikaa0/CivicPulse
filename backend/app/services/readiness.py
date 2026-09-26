"""Readiness: is every dependency this service needs reachable?"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessReport:
    failed: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.failed


class ReadinessService:
    def __init__(self, checks: Mapping[str, Callable[[], None]]) -> None:
        # Each check raises on failure and returns nothing on success.
        self._checks = checks

    def check(self) -> ReadinessReport:
        # Run every check rather than stopping at the first failure, so the 503 names
        # everything that is down.
        failed = []
        for name, check in self._checks.items():
            try:
                check()
            except Exception:
                failed.append(name)
        return ReadinessReport(failed=tuple(failed))
