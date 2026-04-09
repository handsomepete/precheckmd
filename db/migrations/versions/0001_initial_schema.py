"""Initial schema: clients, jobs, artifacts, agent_transcripts, kb_documents.

Revision ID: 0001
Revises:
Create Date: 2026-04-08 00:00:00.000000

Uses raw SQL throughout to avoid SQLAlchemy type-event conflicts with
PostgreSQL ENUM and pgvector column types.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE clients (
            id          UUID PRIMARY KEY,
            name        VARCHAR(255) NOT NULL,
            api_key_hash VARCHAR(64) NOT NULL UNIQUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute(
        "CREATE TYPE job_status AS ENUM "
        "('pending', 'running', 'completed', 'failed', 'cancelled')"
    )

    op.execute("""
        CREATE TABLE jobs (
            id                  UUID PRIMARY KEY,
            client_id           UUID REFERENCES clients(id),
            job_type            VARCHAR(64) NOT NULL,
            status              job_status NOT NULL DEFAULT 'pending',
            input_payload       JSONB NOT NULL DEFAULT '{}',
            result_summary      JSONB,
            error_message       TEXT,
            token_input_used    INTEGER NOT NULL DEFAULT 0,
            token_output_used   INTEGER NOT NULL DEFAULT 0,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at          TIMESTAMPTZ,
            completed_at        TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX ix_jobs_status ON jobs (status)")

    op.execute("""
        CREATE TABLE artifacts (
            id           UUID PRIMARY KEY,
            job_id       UUID NOT NULL REFERENCES jobs(id),
            filename     VARCHAR(512) NOT NULL,
            mime_type    VARCHAR(128) NOT NULL DEFAULT 'application/octet-stream',
            size_bytes   BIGINT NOT NULL DEFAULT 0,
            storage_path VARCHAR(1024) NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE agent_transcripts (
            id           UUID PRIMARY KEY,
            job_id       UUID NOT NULL REFERENCES jobs(id),
            sequence     INTEGER NOT NULL,
            role         VARCHAR(32) NOT NULL,
            content_type VARCHAR(64) NOT NULL,
            content      JSONB NOT NULL DEFAULT '{}',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_transcript_job_seq UNIQUE (job_id, sequence)
        )
    """)
    op.execute(
        "CREATE INDEX ix_agent_transcripts_job_id ON agent_transcripts (job_id)"
    )

    op.execute("""
        CREATE TABLE kb_documents (
            id          UUID PRIMARY KEY,
            source      VARCHAR(256) NOT NULL,
            chunk_index INTEGER NOT NULL,
            title       VARCHAR(512) NOT NULL,
            body        TEXT NOT NULL,
            embedding   vector(384),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX ix_kb_documents_source ON kb_documents (source)")
    op.execute(
        "CREATE INDEX ix_kb_documents_embedding ON kb_documents "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kb_documents")
    op.execute("DROP TABLE IF EXISTS agent_transcripts")
    op.execute("DROP TABLE IF EXISTS artifacts")
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP TABLE IF EXISTS clients")
    op.execute("DROP TYPE IF EXISTS job_status")
    op.execute("DROP EXTENSION IF EXISTS vector")
