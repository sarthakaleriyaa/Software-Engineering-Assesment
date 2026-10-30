"""Drop the HNSW index: retrieval now ranks one user's chunks exactly.

Filtering by owner after an approximate index scan could return no results
for a user (see rag.retrieve_chunks). The exact per-user query cannot use
this index, so keeping it would only slow down inserts.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
