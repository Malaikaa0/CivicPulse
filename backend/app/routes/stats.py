"""GET /api/stats. HTTP only: the caching logic lives in StatsService."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.cache_wiring import get_stats_service
from app.services.stats import Stats, StatsService

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
def get_stats(
    response: Response,
    service: Annotated[StatsService, Depends(get_stats_service)],
) -> Stats:
    stats, hit = service.get()
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    return stats
