"""Maps domain errors to HTTP responses. Routes stay free of error-handling logic."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas import ErrorOut, FieldError, ValidationErrorOut
from app.services.complaints import ComplaintNotFound
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


def _complaint_not_found(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ComplaintNotFound):
        raise exc
    return JSONResponse(status_code=404, content=ErrorOut(detail=str(exc)).model_dump())


def _field_name(location: tuple[int | str, ...]) -> str:
    """The client-facing field for a Pydantic error location such as ("body", "text") or
    ("query", "page_size"): the part after where it came from."""
    if not location:
        return "request"
    # Integers are list indexes or, for a malformed JSON body, a character offset; neither is a
    # name the client sent. Fall back to the source ("body") when nothing else is left.
    names = [str(part) for part in location[1:] if isinstance(part, str)]
    return ".".join(names) or str(location[0])


def _validation_failed(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    body = ValidationErrorOut(
        detail=[
            FieldError(field=_field_name(tuple(error["loc"])), message=error["msg"])
            for error in exc.errors()
        ]
    )
    return JSONResponse(status_code=400, content=body.model_dump())


def _without_default_422(app: FastAPI) -> None:
    """Validation failures are 400 here, so drop the 422 (and its schemas) that FastAPI adds to
    every operation by default; a typed client generated from the document would otherwise be
    told about a status this API never returns."""
    build = app.openapi

    def openapi() -> dict[str, Any]:
        first_build = app.openapi_schema is None
        schema = build()
        if first_build:
            for path_item in schema["paths"].values():
                for operation in path_item.values():
                    operation.get("responses", {}).pop("422", None)
            for name in ("HTTPValidationError", "ValidationError"):
                schema.get("components", {}).get("schemas", {}).pop(name, None)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(InvalidTransition, _invalid_transition)
    app.add_exception_handler(ComplaintNotFound, _complaint_not_found)
    app.add_exception_handler(RequestValidationError, _validation_failed)
    _without_default_422(app)
