"""Tool: query_kb

Semantic search over the knowledge base using pgvector cosine similarity.
Embeddings are generated with fastembed (BAAI/bge-small-en-v1.5, 384-dim).
"""

import logging
from functools import lru_cache

from tools.context import DATABASE_URL

logger = logging.getLogger(__name__)

_EMBED_MODEL = "BAAI/bge-small-en-v1.5"


@lru_cache(maxsize=1)
def _get_embedder():
    """Load the fastembed model once per process."""
    from fastembed import TextEmbedding
    return TextEmbedding(_EMBED_MODEL)


def _embed(text: str) -> list[float]:
    embedder = _get_embedder()
    vectors = list(embedder.embed([text]))
    return vectors[0].tolist()


def query_kb(
    query: str,
    source: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Search the knowledge base for chunks relevant to *query*.

    Uses cosine similarity over 384-dim fastembed embeddings stored in
    pgvector. Returns the top-k most similar chunks.

    Args:
        query: Natural language query (e.g. "SOC 2 access control requirements").
        source: Optional filter by knowledge base source, e.g. "soc2", "hipaa",
                "owasp_asvs". Pass None to search all sources.
        top_k: Number of results to return (default 5, max 20).

    Returns:
        List of dicts, each with keys: id, source, title, body, score.
        Returns an empty list if the knowledge base has no documents yet.
    """
    top_k = min(top_k, 20)

    if not DATABASE_URL:
        logger.warning("query_kb: DATABASE_URL not set, returning empty results")
        return []

    try:
        import psycopg2
        from pgvector.psycopg2 import register_vector

        # Convert asyncpg URL to plain psycopg2 URL
        db_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        db_url = db_url.replace("postgresql+psycopg2://", "postgresql://")

        conn = psycopg2.connect(db_url)
        register_vector(conn)
        cur = conn.cursor()

        embedding = _embed(query)

        if source:
            cur.execute(
                """
                SELECT id, source, title, body,
                       1 - (embedding <=> %s::vector) AS score
                FROM kb_documents
                WHERE source = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (embedding, source, embedding, top_k),
            )
        else:
            cur.execute(
                """
                SELECT id, source, title, body,
                       1 - (embedding <=> %s::vector) AS score
                FROM kb_documents
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (embedding, embedding, top_k),
            )

        rows = cur.fetchall()
        cur.close()
        conn.close()

        results = [
            {"id": r[0], "source": r[1], "title": r[2], "body": r[3], "score": float(r[4])}
            for r in rows
        ]
        logger.info("query_kb: '%s' -> %d results", query[:60], len(results))
        return results

    except Exception as exc:
        logger.error("query_kb failed: %s", exc)
        return []
