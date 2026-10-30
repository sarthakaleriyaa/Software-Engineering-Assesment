"""Add documents.available_at for worker retry backoff.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "available_at")
