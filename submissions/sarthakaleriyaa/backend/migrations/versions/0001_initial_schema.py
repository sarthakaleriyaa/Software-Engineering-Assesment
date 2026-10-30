"""Initial schema: users, documents, chunks with pgvector, question history, worker heartbeats.

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIMENSIONS = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("stored_path", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
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
            "status IN ('queued', 'processing', 'ready', 'failed')",
            name="ck_documents_status",
        ),
    )
    op.create_index("ix_documents_id", "documents", ["id"])
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_document_chunks_id", "document_chunks", ["id"])
    op.create_index(
        "ix_document_chunks_document_id", "document_chunks", ["document_id"]
    )
    # Approximate nearest-neighbour index for cosine distance.
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "question_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("document_ids", sa.JSON(), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_question_history_id", "question_history", ["id"])
    op.create_index(
        "ix_question_history_user_id", "question_history", ["user_id"]
    )
    op.create_index(
        "ix_question_history_created_at", "question_history", ["created_at"]
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(100), primary_key=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_table("question_history")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("users")
