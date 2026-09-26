"""Complaint endpoints. HTTP only: parse, validate, call the service, serialise."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_complaint_service
from app.domain import Category, Priority, Status
from app.repositories.complaints import ComplaintFilters
from app.schemas import (
    ComplaintCreate,
    ComplaintOut,
    ComplaintPageOut,
    ErrorOut,
    StatusChange,
    TransitionConflictOut,
    ValidationErrorOut,
)
from app.services.complaints import ComplaintService

router = APIRouter(prefix="/api/complaints", tags=["complaints"])

MAX_PAGE = 1_000_000

ServiceDep = Annotated[ComplaintService, Depends(get_complaint_service)]

_BAD_REQUEST: dict[int | str, dict[str, Any]] = {
    400: {"model": ValidationErrorOut, "description": "Field-level validation errors"}
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorOut, "description": "No complaint with that id"}
}


@router.post("", status_code=201, responses=_BAD_REQUEST)
def create_complaint(body: ComplaintCreate, service: ServiceDep) -> ComplaintOut:
    complaint = service.create(body.text, body.location, body.reporter_contact)
    return ComplaintOut.model_validate(complaint)


@router.get("", responses=_BAD_REQUEST)
def list_complaints(
    service: ServiceDep,
    category: Category | None = None,
    priority: Priority | None = None,
    status: Status | None = None,
    # The upper bound keeps the OFFSET inside PostgreSQL's bigint: without it, a huge page number
    # is a 500 instead of a 400. It is far beyond any real data set.
    page: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ComplaintPageOut:
    filters = ComplaintFilters(category=category, priority=priority, status=status)
    result = service.search(filters, page=page, page_size=page_size)
    return ComplaintPageOut.model_validate(result)


@router.get("/{complaint_id}", responses={**_BAD_REQUEST, **_NOT_FOUND})
def get_complaint(complaint_id: uuid.UUID, service: ServiceDep) -> ComplaintOut:
    return ComplaintOut.model_validate(service.get(complaint_id))


@router.patch(
    "/{complaint_id}/status",
    responses={
        **_BAD_REQUEST,
        **_NOT_FOUND,
        409: {"model": TransitionConflictOut, "description": "Transition not allowed"},
    },
)
def change_complaint_status(
    complaint_id: uuid.UUID, body: StatusChange, service: ServiceDep
) -> ComplaintOut:
    return ComplaintOut.model_validate(service.change_status(complaint_id, body.status))
