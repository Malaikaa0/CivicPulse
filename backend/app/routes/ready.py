"""Readiness probe.

Unlike /health, this one SHOULD depend on the database and cache: a failing readiness probe
only removes the pod from the Service endpoints; it does not restart it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.dependencies import get_readiness_service
from app.services.readiness import ReadinessService

router = APIRouter(tags=["ops"])


@router.get("/ready")
def ready(
    service: Annotated[ReadinessService, Depends(get_readiness_service)],
) -> JSONResponse:
    report = service.check()
    if report.ready:
        return JSONResponse({"status": "ready"})
    return JSONResponse(
        status_code=503,
        content={"status": "unavailable", "failed": list(report.failed)},
    )
