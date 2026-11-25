"""enable vector extension

Revision ID: 7b3c2d4e8f10
Revises: 86e1f447e83b
Create Date: 2026-11-26 00:29:18.925045
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7b3c2d4e8f10"
down_revision: str | Sequence[str] | None = "86e1f447e83b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
