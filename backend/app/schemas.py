"""API request and response models. The OpenAPI schema the frontend client is typed against is
generated from these."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator
from pydantic_core import PydanticCustomError

from app.domain import Category, Priority, Status, TriagedBy
from app.services.status_machine import allowed_targets


class ComplaintCreate(BaseModel):
    # Stripped before the length checks, so "   short   " is judged as "short", and a text made
    # only of spaces counts as empty.
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=10, max_length=2000)
    location: str = Field(min_length=3, max_length=200)
    reporter_contact: str | None = Field(default=None, max_length=200)

    @field_validator("text", "location", "reporter_contact")
    @classmethod
    def _no_nul_characters(cls, value: str | None) -> str | None:
        # PostgreSQL cannot store NUL in text; left alone it would surface as a 500 on bad input.
        if value is not None and "\x00" in value:
            raise PydanticCustomError("nul_character", "Must not contain NUL characters")
        return value

    @field_validator("reporter_contact")
    @classmethod
    def _blank_contact_is_absent(cls, value: str | None) -> str | None:
        return value or None


class StatusChange(BaseModel):
    status: Status


class ComplaintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    text: str
    location: str
    reporter_contact: str | None
    category: Category
    priority: Priority
    status: Status
    ai_summary: str | None
    triaged_by: TriagedBy
    triage_latency_ms: int
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_transitions(self) -> list[Status]:
        """Statuses this complaint may move to next; the frontend keeps no copy of the rules."""
        return allowed_targets(self.status)


class ComplaintPageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[ComplaintOut]
    total: int
    page: int
    page_size: int


# Error bodies, declared on the routes so the OpenAPI document describes what is really sent.


class FieldError(BaseModel):
    field: str
    message: str


class ValidationErrorOut(BaseModel):
    detail: list[FieldError]


class ErrorOut(BaseModel):
    detail: str


class TransitionConflictOut(BaseModel):
    detail: str
    current: Status
    requested: Status
