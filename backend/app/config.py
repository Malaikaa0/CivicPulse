"""Application settings, loaded from environment variables.

There are no defaults for the database and Redis URLs on purpose: a hard-coded default would
have to point at localhost, which never works between containers.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    redis_url: str

    triage_provider: Literal["llm", "ollama", "rules", "simulated"] = "simulated"
    triage_timeout_seconds: float = 10.0
    triage_cache_ttl_seconds: int = 86400

    # Used when TRIAGE_PROVIDER=llm. One provider class serves both vendors, because both
    # expose an OpenAI-compatible endpoint. Keys are SecretStr so they never appear in a repr
    # or a log line, and they come from the environment only.
    llm_vendor: Literal["gemini", "groq"] = "gemini"
    gemini_api_key: SecretStr | None = None
    # Pinned to a named model on purpose: an alias like "-latest" can change behaviour under us.
    # Models get retired (gemini-2.5-flash-lite already was), so this is configurable.
    gemini_model: str = "gemini-3.5-flash-lite"
    groq_api_key: SecretStr | None = None
    groq_model: str | None = None

    stats_cache_ttl_seconds: int = 30
    rate_limit_requests: int = 10
    rate_limit_window_seconds: int = 60

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
