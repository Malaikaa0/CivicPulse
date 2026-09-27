"""Chooses the triage provider from configuration (TRIAGE_PROVIDER)."""

from pydantic import SecretStr

from app.config import Settings
from app.providers.triage.base import TriageProvider
from app.providers.triage.llm import LLMTriage
from app.providers.triage.rules import RuleBasedTriage
from app.providers.triage.simulated import SimulatedTriage

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class ProviderConfigurationError(RuntimeError):
    """The selected provider cannot be built from the current settings."""


def _require(value: SecretStr | str | None, variable: str, provider: str) -> str:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not raw:
        # Fail at startup, not on the first citizen's request. Names the variable, never a value.
        raise ProviderConfigurationError(f"{variable} must be set when using {provider}")
    return raw


def _create_llm(settings: Settings) -> LLMTriage:
    if settings.llm_vendor == "groq":
        return LLMTriage(
            name="llm:groq",
            api_key=_require(settings.groq_api_key, "GROQ_API_KEY", "llm_vendor=groq"),
            base_url=GROQ_BASE_URL,
            model=_require(settings.groq_model, "GROQ_MODEL", "llm_vendor=groq"),
            timeout_seconds=settings.triage_timeout_seconds,
        )
    return LLMTriage(
        name="llm:gemini",
        api_key=_require(settings.gemini_api_key, "GEMINI_API_KEY", "llm_vendor=gemini"),
        base_url=GEMINI_BASE_URL,
        model=settings.gemini_model,
        timeout_seconds=settings.triage_timeout_seconds,
    )


def create_triage_provider(settings: Settings) -> TriageProvider:
    match settings.triage_provider:
        case "rules":
            return RuleBasedTriage()
        case "simulated":
            return SimulatedTriage()
        case "llm":
            return _create_llm(settings)
        case "ollama":
            raise NotImplementedError("TRIAGE_PROVIDER='ollama' is not implemented yet")
