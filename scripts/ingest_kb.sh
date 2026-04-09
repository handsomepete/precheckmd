#!/usr/bin/env bash
# Ingest KB documents into a running postgres instance.
#
# Usage (stack running via docker-compose):
#   ./scripts/ingest_kb.sh
#
# Usage (local postgres, env already set):
#   DATABASE_URL=postgresql://nox:nox@localhost:5432/nox ./scripts/ingest_kb.sh

set -euo pipefail

echo "=== Nox KB Ingestion ==="

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "Using DATABASE_URL from environment."
  python -m kb.ingest "$@"
else
  echo "DATABASE_URL not set - running via docker-compose exec api..."
  docker-compose exec api python -m kb.ingest "$@"
fi

echo "=== Ingestion complete ==="
