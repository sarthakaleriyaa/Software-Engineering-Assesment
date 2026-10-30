"""Store uploaded file bytes in Postgres (document_blobs).

Additive only: existing documents keep their files on disk and still work.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_blobs",
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("document_blobs")
