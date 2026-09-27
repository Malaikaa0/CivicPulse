"""allow llm:gemini in triaged_by

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The values are written out in full on purpose: a migration is a frozen snapshot and must not
# change meaning if the application's enum is edited later.
OLD = "triaged_by IN ('llm:groq', 'llm:ollama', 'rules', 'rules:fallback')"
NEW = "triaged_by IN ('llm:groq', 'llm:gemini', 'llm:ollama', 'rules', 'rules:fallback')"


def upgrade() -> None:
    op.drop_constraint("ck_complaints_triaged_by", "complaints", type_="check")
    op.create_check_constraint("ck_complaints_triaged_by", "complaints", NEW)


def downgrade() -> None:
    # Re-creating the stricter constraint fails loudly if any llm:gemini rows exist. That is
    # deliberate: silently rewriting stored data inside a downgrade would be worse.
    op.drop_constraint("ck_complaints_triaged_by", "complaints", type_="check")
    op.create_check_constraint("ck_complaints_triaged_by", "complaints", OLD)
