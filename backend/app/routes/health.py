"""Liveness probe.

Must not touch the database or Redis: a failing liveness probe restarts the pod, so it may only
report that this process is alive. Dependency checks belong to /ready.
"""

from fastapi import APIRouter

router = APIRouter(tags=["ops"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
