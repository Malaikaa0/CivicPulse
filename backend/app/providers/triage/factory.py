"""Chooses the triage provider from configuration (TRIAGE_PROVIDER)."""

from app.config import Settings
from app.providers.triage.base import TriageProvider
from app.providers.triage.rules import RuleBasedTriage
from app.providers.triage.simulated import SimulatedTriage


def create_triage_provider(settings: Settings) -> TriageProvider:
    match settings.triage_provider:
        case "rules":
            return RuleBasedTriage()
        case "simulated":
            return SimulatedTriage()
        case "llm" | "ollama":
            raise NotImplementedError(
                f"TRIAGE_PROVIDER={settings.triage_provider!r} is not implemented yet"
            )
