"""Maps domain errors to HTTP responses. Routes stay free of error-handling logic."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.status_machine import InvalidTransition


def _invalid_transition(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, InvalidTransition):
        raise exc
    return JSONResponse(
        status_code=409,
        content={
            "detail": str(exc),
            "current": exc.current.value,
            "requested": exc.requested.value,
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(InvalidTransition, _invalid_transition)
