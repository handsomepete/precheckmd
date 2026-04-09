"""KB ingestion script.

Loads markdown files from kb/data/ into the kb_documents table with
fastembed embeddings (BAAI/bge-small-en-v1.5, 384-dim).

Ingestion is idempotent: existing chunks for a given (source, chunk_index)
are replaced on re-run.

Usage:
    # From project root (postgres must be running and migrations applied):
    DATABASE_URL=postgresql://nox:nox@localhost:5432/nox python -m kb.ingest

    # Inside docker-compose:
    docker-compose exec api python -m kb.ingest

    # Ingest a specific source only:
    DATABASE_URL=... python -m kb.ingest --source soc2
"""

import argparse
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("kb.ingest")

_DATA_DIR = Path(__file__).parent / "data"

# Source name derived from filename prefix (e.g. "soc2_cc6_..." -> "soc2")
def _source_from_path(path: Path) -> str:
    stem = path.stem.lower()
    return stem.split("_")[0]


@dataclass
class Chunk:
    source: str
    chunk_index: int
    title: str
    body: str


def _chunk_markdown(source: str, text: str) -> list[Chunk]:
    """Split a markdown document into chunks at level-2 headings (## ...).

    Each chunk includes the heading as its title and the full heading + body
    as its body (so the heading is part of the embedded text for better
    retrieval quality).
    """
    # Split on lines that start with "## "
    parts = re.split(r"(?m)^(## .+)$", text)

    chunks: list[Chunk] = []
    chunk_index = 0

    # parts[0] is the preamble before the first ## heading
    preamble = parts[0].strip()
    if preamble:
        chunks.append(
            Chunk(
                source=source,
                chunk_index=chunk_index,
                title=f"{source} overview",
                body=preamble,
            )
        )
        chunk_index += 1

    # The rest alternates: heading, body, heading, body, ...
    i = 1
    while i < len(parts) - 1:
        heading = parts[i].strip()          # "## CC6.1 - ..."
        body = parts[i + 1].strip()         # everything after the heading
        title = heading.lstrip("#").strip() # "CC6.1 - ..."
        full_body = f"{heading}\n\n{body}"
        chunks.append(
            Chunk(
                source=source,
                chunk_index=chunk_index,
                title=title,
                body=full_body,
            )
        )
        chunk_index += 1
        i += 2

    return chunks


def _embed_chunks(chunks: list[Chunk]) -> list[list[float]]:
    """Generate embeddings for all chunks using fastembed."""
    from fastembed import TextEmbedding

    model = TextEmbedding("BAAI/bge-small-en-v1.5")
    texts = [f"{c.title}\n\n{c.body}" for c in chunks]
    logger.info("Generating embeddings for %d chunks...", len(texts))
    vectors = list(model.embed(texts))
    logger.info("Embeddings done.")
    return [v.tolist() for v in vectors]


def _upsert_chunks(chunks: list[Chunk], embeddings: list[list[float]], db_url: str) -> int:
    """Insert or replace chunks in kb_documents. Returns count of rows written."""
    import psycopg2
    from psycopg2.extras import execute_batch
    from pgvector.psycopg2 import register_vector

    clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    clean_url = clean_url.replace("postgresql+psycopg2://", "postgresql://")

    conn = psycopg2.connect(clean_url)
    register_vector(conn)
    cur = conn.cursor()

    # Delete existing chunks for each (source, chunk_index) combination
    sources = list({c.source for c in chunks})
    for source in sources:
        cur.execute("DELETE FROM kb_documents WHERE source = %s", (source,))
        logger.info("Deleted existing chunks for source='%s'", source)

    rows = [
        (
            str(uuid.uuid4()),
            c.source,
            c.chunk_index,
            c.title,
            c.body,
            emb,
        )
        for c, emb in zip(chunks, embeddings)
    ]

    execute_batch(
        cur,
        """
        INSERT INTO kb_documents (id, source, chunk_index, title, body, embedding)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        rows,
        page_size=50,
    )

    conn.commit()
    cur.close()
    conn.close()
    return len(rows)


def ingest(source_filter: str | None = None, db_url: str | None = None) -> None:
    db_url = db_url or os.environ.get("DATABASE_URL", "")
    if not db_url:
        logger.error("DATABASE_URL is required")
        sys.exit(1)

    md_files = sorted(_DATA_DIR.glob("*.md"))
    if not md_files:
        logger.error("No markdown files found in %s", _DATA_DIR)
        sys.exit(1)

    all_chunks: list[Chunk] = []

    for path in md_files:
        source = _source_from_path(path)
        if source_filter and source != source_filter:
            continue
        text = path.read_text(encoding="utf-8")
        chunks = _chunk_markdown(source, text)
        logger.info("Parsed %s -> source='%s', %d chunks", path.name, source, len(chunks))
        all_chunks.extend(chunks)

    if not all_chunks:
        logger.warning("No chunks to ingest (source_filter=%s)", source_filter)
        return

    embeddings = _embed_chunks(all_chunks)
    count = _upsert_chunks(all_chunks, embeddings, db_url)
    logger.info("Ingested %d chunks into kb_documents.", count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest KB documents into pgvector.")
    parser.add_argument("--source", default=None, help="Ingest only this source (e.g. 'soc2')")
    parser.add_argument("--db-url", default=None, help="Override DATABASE_URL")
    args = parser.parse_args()
    ingest(source_filter=args.source, db_url=args.db_url)
