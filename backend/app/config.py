"""Application settings, loaded from environment variables.

There are no defaults for the database and Redis URLs on purpose: a hard-coded default would
have to point at localhost, which never works between containers.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    redis_url: str

    triage_provider: Literal["llm", "ollama", "rules", "simulated"] = "simulated"
    triage_timeout_seconds: float = 10.0
    triage_cache_ttl_seconds: int = 86400

    stats_cache_ttl_seconds: int = 30
    rate_limit_requests: int = 10
    rate_limit_window_seconds: int = 60

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
