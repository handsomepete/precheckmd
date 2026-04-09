"""Standalone KB query script for testing and debugging.

Usage:
    DATABASE_URL=postgresql://nox:nox@localhost:5432/nox \
    python -m kb.query "access control requirements for SOC 2"

    python -m kb.query "hardcoded secrets" --source soc2 --top-k 3
"""

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the knowledge base.")
    parser.add_argument("query", help="Natural language query string")
    parser.add_argument("--source", default=None, help="Filter by source (e.g. 'soc2')")
    parser.add_argument("--top-k", type=int, default=5, help="Number of results (default 5)")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL is required", file=sys.stderr)
        sys.exit(1)

    # Set context env vars so tools/query_kb.py works standalone
    os.environ["DATABASE_URL"] = db_url
    os.environ.setdefault("JOB_ID", "query-cli")

    from tools.query_kb import query_kb

    results = query_kb(query=args.query, source=args.source, top_k=args.top_k)

    if not results:
        print("No results found.")
        return

    for i, r in enumerate(results, 1):
        print(f"\n{'='*60}")
        print(f"Result {i}: [{r['source']}] {r['title']}")
        print(f"Score: {r['score']:.4f}")
        print(f"{'-'*60}")
        # Print first 400 chars of body
        body_preview = r["body"][:400]
        if len(r["body"]) > 400:
            body_preview += " ..."
        print(body_preview)


if __name__ == "__main__":
    main()
