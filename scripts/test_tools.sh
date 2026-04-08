#!/usr/bin/env bash
# Standalone tool tests - run inside the sandbox container or locally
# (requires git, semgrep, gitleaks on PATH and Python deps installed).
#
# Usage:
#   # inside sandbox container:
#   docker run --rm -e JOB_ID=test123 -e SCRATCH_DIR=/tmp/scratch \
#     -e ARTIFACT_DIR=/tmp/artifacts nox-sandbox bash /app/scripts/test_tools.sh
#
#   # locally (install requirements.txt first):
#   JOB_ID=test123 SCRATCH_DIR=/tmp/scratch ARTIFACT_DIR=/tmp/artifacts \
#   DATABASE_URL="" bash scripts/test_tools.sh

set -euo pipefail

export JOB_ID="${JOB_ID:-test123}"
export SCRATCH_DIR="${SCRATCH_DIR:-/tmp/nox_scratch_test}"
export ARTIFACT_DIR="${ARTIFACT_DIR:-/tmp/nox_artifacts_test}"

mkdir -p "$SCRATCH_DIR" "$ARTIFACT_DIR/$JOB_ID"

echo "=== test: git_clone ==="
python - <<'EOF'
import os, sys
sys.path.insert(0, '/app')
from tools.git_clone import git_clone
path = git_clone("https://github.com/gitleaks/gitleaks", dest_name="gitleaks_test")
print(f"  cloned to: {path}")
assert os.path.isdir(path), "clone dir missing"
print("  PASS")
EOF

echo ""
echo "=== test: read_file ==="
python - <<'EOF'
import sys
sys.path.insert(0, '/app')
from tools.read_file import read_file
contents = read_file("gitleaks_test/README.md")
assert len(contents) > 100, "README should have content"
print(f"  read {len(contents)} bytes")
print("  PASS")
EOF

echo ""
echo "=== test: run_semgrep ==="
python - <<'EOF'
import sys
sys.path.insert(0, '/app')
from tools.run_semgrep import run_semgrep
import os
target = os.path.join(os.environ["SCRATCH_DIR"], "gitleaks_test")
result = run_semgrep(target)
print(f"  findings: {len(result['findings'])}, stats: {result['stats']}")
assert "findings" in result
print("  PASS")
EOF

echo ""
echo "=== test: run_gitleaks ==="
python - <<'EOF'
import sys
sys.path.insert(0, '/app')
from tools.run_gitleaks import run_gitleaks
import os
target = os.path.join(os.environ["SCRATCH_DIR"], "gitleaks_test")
result = run_gitleaks(target)
print(f"  findings: {result['total']}, error: {result['error']}")
assert "findings" in result
print("  PASS")
EOF

echo ""
echo "=== test: write_artifact ==="
python - <<'EOF'
import sys, os
sys.path.insert(0, '/app')
from tools.write_artifact import write_artifact
path = write_artifact("test_output.txt", "hello from write_artifact", "text/plain")
print(f"  written to: {path}")
assert os.path.exists(path)
print("  PASS")
EOF

echo ""
echo "=== test: render_pdf (stub) ==="
python - <<'EOF'
import sys, os
sys.path.insert(0, '/app')
from tools.render_pdf import render_pdf
path = render_pdf(
    title="Test Report",
    findings=[{"control": "CC6.1", "status": "pass", "evidence": "MFA enabled", "recommendation": ""}],
    metadata={"repo_url": "https://github.com/test/repo"},
)
print(f"  written to: {path}")
assert os.path.exists(path)
print("  PASS")
EOF

echo ""
echo "All tool tests passed."
