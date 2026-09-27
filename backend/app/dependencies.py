"""Wiring: builds the objects the routes depend on, from settings."""

import threading
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.ai_wiring import build_triage_service
from app.cache_wiring import get_stats_service
from app.config import get_settings
from app.metrics import Metrics
from app.repositories.complaints import ComplaintRepository
from app.repositories.database import ping_database
from app.resources import get_cache, get_engine
from app.services.complaints import ComplaintService
from app.services.readiness import ReadinessService
from app.services.stats import StatsService
from app.services.triage import TriageService

# get_engine is re-exported: it used to be defined here.
__all__ = [
    "get_complaint_service",
    "get_engine",
    "get_readiness_service",
    "get_session",
    "get_triage_service",
]


@lru_cache
def get_readiness_service() -> ReadinessService:
    engine = get_engine()
    cache = get_cache()
    return ReadinessService(
        {
            "postgres": lambda: ping_database(engine),
            "redis": cache.ping,
        }
    )


def get_session() -> Iterator[Session]:
    """One session per request, always closed afterwards (which also returns its connection to
    the pool and rolls back anything left uncommitted)."""
    with Session(get_engine()) as session:
        yield session


_triage_lock = threading.Lock()


def get_triage_service(request: Request) -> TriageService:
    """The app's triage service, built once: the configured provider behind the content-hash
    cache, recording every outcome to the outcome log and to this app's metrics.

    Built per app, not per process, because the metrics registry belongs to the app.
    """
    state = request.app.state
    with _triage_lock:
        service: TriageService | None = getattr(state, "triage_service", None)
        if service is None:
            metrics: Metrics | None = getattr(state, "metrics", None)
            recorders = [metrics.observe_triage] if metrics is not None else []
            service = build_triage_service(get_settings(), extra_recorders=recorders)
            state.triage_service = service
        return service


def get_complaint_service(
    session: Annotated[Session, Depends(get_session)],
    triage: Annotated[TriageService, Depends(get_triage_service)],
    stats: Annotated[StatsService, Depends(get_stats_service)],
) -> ComplaintService:
    # Explicit invalidation: after a write commits, the stats cache is dropped, so the new
    # complaint shows in /api/stats immediately instead of up to a TTL later.
    return ComplaintService(ComplaintRepository(session), triage, on_write=stats.invalidate)
