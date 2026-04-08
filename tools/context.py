"""Runtime context for tools running inside the sandbox container.

Values are injected via environment variables set when the worker spawns
the sandbox container. Tools import from here instead of hard-coding paths.
"""

import os

# Directory where the repo is cloned and scratch files live
SCRATCH_DIR: str = os.environ.get("SCRATCH_DIR", "/scratch")

# Per-job identifier
JOB_ID: str = os.environ.get("JOB_ID", "")

# Root directory for artifacts; tool writes to ARTIFACT_DIR/JOB_ID/
ARTIFACT_DIR: str = os.environ.get("ARTIFACT_DIR", "/artifacts")

# DB connection URL (sync, psycopg2 dialect)
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

# Token budget guardrails (enforced in the agent loop)
MAX_INPUT_TOKENS: int = int(os.environ.get("MAX_INPUT_TOKENS", "200000"))
MAX_OUTPUT_TOKENS: int = int(os.environ.get("MAX_OUTPUT_TOKENS", "50000"))
