#!/usr/bin/env bash
# Smoke test: create a dummy job, poll until complete, download the artifact.
# Usage: API_KEY=changeme-api-key ./scripts/smoke_test.sh

set -euo pipefail

BASE="${BASE_URL:-http://localhost:8000}"
KEY="${API_KEY:-changeme-api-key}"

echo "=== Health check ==="
curl -sf "$BASE/health" | python3 -m json.tool

echo ""
echo "=== Create dummy job ==="
RESP=$(curl -sf -X POST "$BASE/jobs" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"job_type":"dummy","input":{"sleep_seconds":2}}')
echo "$RESP" | python3 -m json.tool
JOB_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Job ID: $JOB_ID"

echo ""
echo "=== Polling status ==="
for i in $(seq 1 30); do
  STATUS=$(curl -sf "$BASE/jobs/$JOB_ID" -H "X-API-Key: $KEY" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "  [$i] status: $STATUS"
  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]]; then
    break
  fi
  sleep 1
done

echo ""
echo "=== Final job state ==="
curl -sf "$BASE/jobs/$JOB_ID" -H "X-API-Key: $KEY" | python3 -m json.tool

echo ""
echo "=== Artifacts ==="
ARTIFACTS=$(curl -sf "$BASE/jobs/$JOB_ID/artifacts" -H "X-API-Key: $KEY")
echo "$ARTIFACTS" | python3 -m json.tool
ARTIFACT_ID=$(echo "$ARTIFACTS" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")

echo ""
echo "=== Download artifact ==="
curl -sf "$BASE/jobs/$JOB_ID/artifacts/$ARTIFACT_ID" -H "X-API-Key: $KEY"
echo ""
echo "=== Done ==="
