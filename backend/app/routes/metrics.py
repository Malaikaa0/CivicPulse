"""Prometheus scrape endpoint. HTTP only: the numbers live in app.metrics."""

from fastapi import APIRouter, Request, Response

from app.metrics import CONTENT_TYPE, Metrics

router = APIRouter(tags=["ops"])


# async on purpose: it runs on the event loop, so a scrape still answers when the threadpool
# is saturated by slow sync handlers, which is exactly when the numbers are wanted.
@router.get("/metrics")
async def metrics(request: Request) -> Response:
    registry: Metrics = request.app.state.metrics
    return Response(content=registry.render(), media_type=CONTENT_TYPE)
