"""Wiring: builds the objects the routes depend on, from settings."""

from functools import lru_cache

from app.config import get_settings
from app.providers.cache import RedisCache
from app.repositories.database import create_db_engine, ping_database
from app.services.readiness import ReadinessService


@lru_cache
def get_readiness_service() -> ReadinessService:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    cache = RedisCache(settings.redis_url)
    return ReadinessService(
        {
            "postgres": lambda: ping_database(engine),
            "redis": cache.ping,
        }
    )
