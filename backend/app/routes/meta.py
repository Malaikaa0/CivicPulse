"""Operator-facing observability: which triage provider is active and how it has been doing."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.ai_wiring import get_meta_service
from app.services.meta import MetaService

router = APIRouter(prefix="/api/meta", tags=["meta"])


class OutcomeOut(BaseModel):
    provider: str
    latency_ms: int
    fallback: bool
    at: datetime


class CacheOut(BaseModel):
    hits: int | None
    misses: int | None
    hit_rate: float | None


class ProvidersOut(BaseModel):
    active_provider: str
    vendor: str | None
    model: str | None
    recent_outcomes: list[OutcomeOut]
    cache: CacheOut
    outcome_store_available: bool


@router.get("/providers")
def providers(service: Annotated[MetaService, Depends(get_meta_service)]) -> ProvidersOut:
    report = service.providers_report()
    cache = report.cache
    return ProvidersOut(
        active_provider=report.provider.active_provider,
        vendor=report.provider.vendor,
        model=report.provider.model,
        recent_outcomes=[OutcomeOut(**o.model_dump()) for o in report.recent_outcomes],
        cache=CacheOut(
            hits=cache.hits if cache else None,
            misses=cache.misses if cache else None,
            hit_rate=cache.hit_rate if cache else None,
        ),
        outcome_store_available=report.store_available,
    )
