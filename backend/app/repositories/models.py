"""SQLAlchemy table definitions. Schema changes are made through Alembic migrations only;
nothing in the application ever calls create_all()."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy import text as sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain import Category, Priority, Status


class Base(DeclarativeBase):
    pass


def _pg_enum[E: StrEnum](enum_cls: type[E], name: str) -> SAEnum:
    # Store the lowercase values ("in_progress"), not the Python member names.
    return SAEnum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class Complaint(Base):
    __tablename__ = "complaints"
    __table_args__ = (
        # Limits are enforced here as well as in the app: the database is the last line of
        # defence against a bug or a script that bypasses the API.
        CheckConstraint("char_length(text) BETWEEN 10 AND 2000", name="ck_complaints_text_length"),
        CheckConstraint(
            "char_length(location) BETWEEN 3 AND 200", name="ck_complaints_location_length"
        ),
        CheckConstraint(
            "ai_summary IS NULL OR char_length(ai_summary) <= 140",
            name="ck_complaints_summary_length",
        ),
        CheckConstraint(
            "triaged_by IN ('llm:groq', 'llm:ollama', 'rules', 'rules:fallback')",
            name="ck_complaints_triaged_by",
        ),
        CheckConstraint("triage_latency_ms >= 0", name="ck_complaints_latency_nonnegative"),
        # Serves the dashboard list: WHERE status = ? [AND priority = ?]. Leading column is
        # status, so it also serves status-only filters; priority-only filters do not use it.
        Index("ix_complaints_status_priority", "status", "priority"),
        # Serves ORDER BY created_at DESC LIMIT n OFFSET m (newest-first pagination). A btree
        # is scanned backwards for free, so no DESC is needed.
        Index("ix_complaints_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    reporter_contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    category: Mapped[Category] = mapped_column(_pg_enum(Category, "complaint_category"))
    priority: Mapped[Priority] = mapped_column(_pg_enum(Priority, "complaint_priority"))
    status: Mapped[Status] = mapped_column(
        _pg_enum(Status, "complaint_status"),
        server_default=Status.OPEN.value,
    )
    ai_summary: Mapped[str | None] = mapped_column(String(140), nullable=True)
    triaged_by: Mapped[str] = mapped_column(String(20))
    triage_latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
