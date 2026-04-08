"""Initial schema: clients, jobs, artifacts, agent_transcripts, kb_documents.

Revision ID: 0001
Revises:
Create Date: 2026-04-08 00:00:00.000000

"""
from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # clients
    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("api_key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # job_status enum
    job_status = postgresql.ENUM(
        "pending", "running", "completed", "failed", "cancelled",
        name="job_status",
    )
    job_status.create(op.get_bind())

    # jobs
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("clients.id"),
            nullable=True,
        ),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("status", sa.Enum(name="job_status"), nullable=False, server_default="pending"),
        sa.Column("input_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("result_summary", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("token_input_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_output_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])

    # artifacts
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("jobs.id"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # agent_transcripts
    op.create_table(
        "agent_transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("jobs.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_agent_transcripts_job_id", "agent_transcripts", ["job_id"])
    op.create_unique_constraint(
        "uq_transcript_job_seq", "agent_transcripts", ["job_id", "sequence"]
    )

    # kb_documents
    op.create_table(
        "kb_documents",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("source", sa.String(256), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_kb_documents_source", "kb_documents", ["source"])

    # HNSW index for fast ANN search on embeddings
    op.execute(
        "CREATE INDEX ix_kb_documents_embedding ON kb_documents "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("kb_documents")
    op.drop_table("agent_transcripts")
    op.drop_table("artifacts")
    op.drop_table("jobs")
    op.drop_table("clients")
    op.execute("DROP TYPE IF EXISTS job_status")
    op.execute("DROP EXTENSION IF EXISTS vector")
