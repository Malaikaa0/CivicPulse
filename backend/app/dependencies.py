"""Wiring: builds the objects the routes depend on, from settings."""

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.providers.cache import RedisCache
from app.providers.triage.factory import create_triage_provider
from app.repositories.complaints import ComplaintRepository
from app.repositories.database import create_db_engine, ping_database
from app.services.complaints import ComplaintService
from app.services.readiness import ReadinessService
from app.services.triage import TriageService


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


@lru_cache
def get_engine() -> Engine:
    return create_db_engine(get_settings().database_url)


def get_session() -> Iterator[Session]:
    """One session per request, always closed afterwards (which also returns its connection to
    the pool and rolls back anything left uncommitted)."""
    with Session(get_engine()) as session:
        yield session


@lru_cache
def get_triage_service() -> TriageService:
    return TriageService(create_triage_provider(get_settings()))


def get_complaint_service(
    session: Annotated[Session, Depends(get_session)],
    triage: Annotated[TriageService, Depends(get_triage_service)],
) -> ComplaintService:
    return ComplaintService(ComplaintRepository(session), triage)
