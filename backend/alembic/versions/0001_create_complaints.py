"""create complaints table

Revision ID: 0001
Revises:
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORY = ("water", "electricity", "sanitation", "roads", "streetlights", "other")
PRIORITY = ("high", "normal", "low")
STATUS = ("open", "in_progress", "resolved", "rejected")


def upgrade() -> None:
    op.create_table(
        "complaints",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("location", sa.String(200), nullable=False),
        sa.Column("reporter_contact", sa.String(200), nullable=True),
        sa.Column("category", sa.Enum(*CATEGORY, name="complaint_category"), nullable=False),
        sa.Column("priority", sa.Enum(*PRIORITY, name="complaint_priority"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*STATUS, name="complaint_status"),
            server_default="open",
            nullable=False,
        ),
        sa.Column("ai_summary", sa.String(140), nullable=True),
        sa.Column("triaged_by", sa.String(20), nullable=False),
        sa.Column("triage_latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(text) BETWEEN 10 AND 2000", name="ck_complaints_text_length"
        ),
        sa.CheckConstraint(
            "char_length(location) BETWEEN 3 AND 200", name="ck_complaints_location_length"
        ),
        sa.CheckConstraint(
            "ai_summary IS NULL OR char_length(ai_summary) <= 140",
            name="ck_complaints_summary_length",
        ),
        sa.CheckConstraint(
            "triaged_by IN ('llm:groq', 'llm:ollama', 'rules', 'rules:fallback')",
            name="ck_complaints_triaged_by",
        ),
        sa.CheckConstraint("triage_latency_ms >= 0", name="ck_complaints_latency_nonnegative"),
    )

    # Serves the dashboard list: WHERE status = ? [AND priority = ?]. status leads, so status-only
    # filters use it too; a priority-only filter does not (and is not a query we run).
    op.create_index("ix_complaints_status_priority", "complaints", ["status", "priority"])

    # Serves ORDER BY created_at DESC LIMIT n OFFSET m, the newest-first pagination of the
    # dashboard. A btree is scanned backwards at no cost, so no DESC is needed.
    op.create_index("ix_complaints_created_at", "complaints", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_complaints_created_at", table_name="complaints")
    op.drop_index("ix_complaints_status_priority", table_name="complaints")
    op.drop_table("complaints")
    # Enum types belong to the schema, not the table, so dropping the table leaves them behind.
    for name in ("complaint_status", "complaint_priority", "complaint_category"):
        op.execute(sa.text(f"DROP TYPE {name}"))
