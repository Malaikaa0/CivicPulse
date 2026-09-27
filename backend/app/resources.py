"""The process-wide connections: one database engine, one Redis client.

Readiness, request sessions, the stats cache and the rate limiter all share these, so the app
holds one connection pool to each server instead of one per feature. Each is built lazily on first
use and registered for release on shutdown (see app.lifecycle).
"""

from functools import lru_cache

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.lifecycle import register_closer
from app.providers.cache import RedisCache
from app.repositories.database import create_db_engine


@lru_cache
def get_engine() -> Engine:
    engine = create_db_engine(get_settings().database_url)
    register_closer(engine.dispose)
    return engine


@lru_cache
def get_cache() -> RedisCache:
    cache = RedisCache(get_settings().redis_url)
    register_closer(cache.close)
    return cache


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    # expire_on_commit=False: the stats query reads plain rows after its session has committed.
    return sessionmaker(get_engine(), expire_on_commit=False)
