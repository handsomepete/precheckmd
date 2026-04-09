# Nox Agent Runtime

Agent-as-a-service platform that runs multi-step Claude-powered workflows
against client inputs (mostly GitHub repos) and produces PDF reports.

**First job type:** SOC 2 compliance report. Point it at a public GitHub
repository and get back a PDF that maps code-level evidence to SOC 2
Trust Service Criteria (CC6, CC7, CC8, CC9).

---

## Table of contents

- [Architecture overview](#architecture-overview)
- [Prerequisites](#prerequisites)
- [Running locally](#running-locally)
- [Ingesting the knowledge base](#ingesting-the-knowledge-base)
- [Submitting a test job](#submitting-a-test-job)
- [API reference](#api-reference)
- [Adding a new job type](#adding-a-new-job-type)
- [Project layout](#project-layout)
- [Configuration reference](#configuration-reference)

---

## Architecture overview

```
                 HTTP
Client ---------> FastAPI (api/)
                    |
                    | enqueue (Redis / RQ)
                    v
                 RQ Worker (worker/)
                    |
                    | docker run nox-sandbox
                    v
                 Sandbox Container
                    |
                    +-- Claude Agent SDK (Anthropic Messages API)
                    |       |
                    |       +-- git_clone
                    |       +-- run_semgrep
                    |       +-- run_gitleaks
                    |       +-- read_file
                    |       +-- query_kb  <----> pgvector (Postgres)
                    |       +-- write_artifact
                    |       +-- render_pdf
                    |
                    | artifacts/ (volume mount)
                    v
                 Artifact store (local filesystem -> S3-compatible later)
```

- **api/** - FastAPI app. Auth via `X-API-Key` header. Endpoints: `POST /jobs`,
  `GET /jobs/{id}`, `GET /jobs/{id}/artifacts`, `GET /jobs/{id}/artifacts/{artifact_id}`.
- **worker/** - RQ worker. Pulls jobs from Redis, spawns one sandbox container per job.
- **sandbox/** - Per-job Docker container (Ubuntu 22.04). Has semgrep, gitleaks, trivy,
  git, Python. Network egress restricted to github.com and api.anthropic.com via iptables.
- **agents/** - One module per job type. `compliance_report.py` runs the SOC 2 audit.
- **tools/** - Python functions exposed as Claude tools: `git_clone`, `run_semgrep`,
  `run_gitleaks`, `read_file`, `query_kb`, `write_artifact`, `render_pdf`.
- **kb/** - Knowledge base ingestion. Loads SOC 2 markdown into pgvector with
  fastembed embeddings (BAAI/bge-small-en-v1.5, 384-dim).

---

## Prerequisites

- Docker and Docker Compose
- An Anthropic API key (`claude-opus-4-6` access required)
- 8 GB RAM recommended (sandbox container + fastembed model)

---

## Running locally

**1. Clone and configure**

```bash
git clone https://github.com/handsomepete/precheckmd
cd precheckmd
cp .env.example .env
# Edit .env - set ANTHROPIC_API_KEY and a strong API_KEY
```

**2. Build images and start the stack**

```bash
# Build all images (api, worker, sandbox)
docker-compose build
docker-compose --profile sandbox-build build sandbox

# Start postgres, redis, api, worker
docker-compose up -d

# Confirm everything is healthy
curl http://localhost:8000/health
# {"status":"ok","database":"reachable"}
```

**3. Apply database migrations**

Migrations run automatically when the `api` container starts (`alembic upgrade head`
is part of its startup command). To run manually:

```bash
docker-compose exec api alembic upgrade head
```

**4. Ingest the knowledge base**

This is a one-time step. It embeds the SOC 2 markdown files and loads them into
pgvector.

```bash
./scripts/ingest_kb.sh
# or: docker-compose exec api python -m kb.ingest
```

Expected output:
```
Parsed soc2_cc6_access_controls.md -> source='soc2', 7 chunks
Parsed soc2_cc7_operations.md      -> source='soc2', 4 chunks
Parsed soc2_cc8_change_management.md -> source='soc2', 3 chunks
Parsed soc2_cc9_risk_mitigation.md  -> source='soc2', 3 chunks
Ingested 17 chunks into kb_documents.
```

---

## Submitting a test job

### Quick end-to-end smoke test (dummy job)

No API key, no Claude calls - just proves the queue works:

```bash
API_KEY=changeme-api-key ./scripts/smoke_test.sh
```

### Submit a real compliance report

```bash
export API_KEY=changeme-api-key   # match API_KEY in your .env

# Create the job
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "job_type": "compliance_report",
    "input": {
      "repo_url": "https://github.com/gitleaks/gitleaks"
    }
  }' | python3 -m json.tool
```

Response:
```json
{
  "id": "3f8a1c2d-...",
  "job_type": "compliance_report",
  "status": "pending",
  "input_payload": { "repo_url": "https://github.com/gitleaks/gitleaks" },
  "result_summary": null,
  "error_message": null,
  "token_input_used": 0,
  "token_output_used": 0,
  "created_at": "2026-04-09T12:00:00Z",
  "started_at": null,
  "completed_at": null
}
```

### Poll for completion

```bash
JOB_ID=3f8a1c2d-...

# Poll until status is completed or failed (jobs typically take 3-10 minutes)
watch -n 5 "curl -s http://localhost:8000/jobs/$JOB_ID \
  -H 'X-API-Key: $API_KEY' | python3 -m json.tool"
```

### List and download artifacts

```bash
# List artifacts
curl -s http://localhost:8000/jobs/$JOB_ID/artifacts \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

Response:
```json
[
  {
    "id": "a1b2c3d4-...",
    "job_id": "3f8a1c2d-...",
    "filename": "report.pdf",
    "mime_type": "application/pdf",
    "size_bytes": 48320,
    "created_at": "2026-04-09T12:08:42Z"
  },
  {
    "id": "b2c3d4e5-...",
    "job_id": "3f8a1c2d-...",
    "filename": "findings.json",
    "mime_type": "application/json",
    "size_bytes": 4218,
    "created_at": "2026-04-09T12:08:40Z"
  },
  {
    "id": "c3d4e5f6-...",
    "job_id": "3f8a1c2d-...",
    "filename": "transcript.json",
    "mime_type": "application/json",
    "size_bytes": 92150,
    "created_at": "2026-04-09T12:08:43Z"
  }
]
```

```bash
ARTIFACT_ID=a1b2c3d4-...

# Download the PDF report
curl -s http://localhost:8000/jobs/$JOB_ID/artifacts/$ARTIFACT_ID \
  -H "X-API-Key: $API_KEY" \
  -o report.pdf

open report.pdf   # macOS
xdg-open report.pdf  # Linux
```

---

## API reference

All endpoints require `X-API-Key: <your key>` header.

### `POST /jobs`

Create and enqueue a job.

**Request body:**
```json
{
  "job_type": "compliance_report",
  "input": {
    "repo_url": "https://github.com/org/repo"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `job_type` | string | yes | `"compliance_report"` or `"dummy"` |
| `input` | object | yes | Job-type-specific payload |
| `input.repo_url` | string | for compliance_report | HTTPS URL of the GitHub repo |
| `input.sleep_seconds` | int | for dummy | Seconds to sleep (default 3) |

**Response:** `202 Accepted` with a `JobResponse` object.

### `GET /jobs/{job_id}`

Fetch job status and metadata.

**Response fields:**

| Field | Description |
|-------|-------------|
| `status` | `pending`, `running`, `completed`, `failed`, or `cancelled` |
| `token_input_used` | Cumulative input tokens consumed by the agent |
| `token_output_used` | Cumulative output tokens consumed by the agent |
| `result_summary` | JSON summary written by the runner on success |
| `error_message` | Full traceback on failure |

### `GET /jobs/{job_id}/artifacts`

List all artifacts for a job. Returns an array of `ArtifactResponse` objects.

### `GET /jobs/{job_id}/artifacts/{artifact_id}`

Download a single artifact. Returns the raw file bytes with `Content-Disposition: attachment`.

### `GET /health`

Returns `{"status": "ok", "database": "reachable"}` if the API and database are up.

---

## Adding a new job type

**1. Create a worker runner in `worker/jobs/`**

```python
# worker/jobs/my_job.py
from worker.runner import register

@register("my_job")
def run(job_id: str, input_payload: dict, db) -> dict:
    # Runs in the RQ worker process.
    # Use run_in_sandbox() for jobs that need the isolated container.
    # Use db (sync SQLAlchemy session) for direct DB operations.
    return {"message": "done"}
```

**2. Register it in `worker/runner.py`**

```python
from worker.jobs import compliance_report, dummy, my_job  # add my_job
```

**3. (If sandboxed) Create an agent in `agents/` and wire `sandbox/run_agent.py`**

```python
# sandbox/run_agent.py - add a branch:
elif job_type == "my_job":
    from agents.my_job import run
    run(job_id=job_id, input_payload=input_payload)
```

**4. Add tool definitions if your agent needs new capabilities**

Add functions to `tools/` following the same pattern (typed, docstrings, no side
effects outside scratch dir and artifact dir).

**5. Test end-to-end**

```bash
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"job_type": "my_job", "input": {}}'
```

---

## Project layout

```
.
├── api/                    FastAPI application
│   ├── Dockerfile
│   ├── auth.py             X-API-Key dependency
│   ├── config.py           pydantic-settings (env vars)
│   ├── deps.py             async DB session dependency
│   ├── main.py             app factory, lifespan
│   ├── queue.py            sync Redis + RQ Queue("nox")
│   ├── schemas.py          Pydantic request/response models
│   └── routes/
│       ├── health.py       GET / and GET /health
│       └── jobs.py         POST /jobs, GET /jobs/...
│
├── worker/                 RQ worker
│   ├── Dockerfile          includes Docker CLI
│   ├── runner.py           dispatcher + @register decorator
│   ├── sandbox_runner.py   Docker SDK container spawner
│   └── jobs/
│       ├── dummy.py        dummy job runner (queue smoke test)
│       └── compliance_report.py  SOC 2 runner (spawns sandbox)
│
├── agents/
│   └── compliance_report.py  Claude agent loop, tool definitions,
│                              system prompt, token budget, transcript
│
├── tools/                  Python functions exposed to the agent
│   ├── context.py          env-based runtime context
│   ├── git_clone.py        shallow HTTPS-only clone
│   ├── run_semgrep.py      SAST scan, JSON output
│   ├── run_gitleaks.py     secret detection, redacted output
│   ├── read_file.py        path-traversal-safe file reader
│   ├── query_kb.py         pgvector cosine similarity search
│   ├── write_artifact.py   writes to artifact dir + manifest
│   └── render_pdf.py       ReportLab PDF generator
│
├── kb/                     Knowledge base
│   ├── data/               SOC 2 markdown source files
│   │   ├── soc2_cc6_access_controls.md
│   │   ├── soc2_cc7_operations.md
│   │   ├── soc2_cc8_change_management.md
│   │   └── soc2_cc9_risk_mitigation.md
│   ├── ingest.py           one-time embedding + upsert script
│   └── query.py            CLI query tool for testing
│
├── sandbox/
│   ├── Dockerfile          Ubuntu 22.04, semgrep, gitleaks, trivy, git, python
│   ├── entrypoint.sh       iptables egress rules, then exec
│   └── run_agent.py        sandbox entry point
│
├── storage/
│   └── local.py            LocalArtifactStore (S3-compatible interface)
│
├── db/
│   ├── models.py           SQLAlchemy ORM (clients, jobs, artifacts,
│   │                       agent_transcripts, kb_documents)
│   ├── session.py          async engine (API) + sync engine (worker)
│   └── migrations/
│       └── versions/
│           └── 0001_initial_schema.py
│
├── scripts/
│   ├── smoke_test.sh       end-to-end queue test via curl
│   ├── ingest_kb.sh        KB ingestion helper
│   └── test_tools.sh       standalone tool tests inside sandbox
│
├── docker-compose.yml      postgres 16, redis 7, api, worker, sandbox
├── alembic.ini
├── requirements.txt
├── requirements-worker.txt
└── .env.example
```

---

## Configuration reference

Copy `.env.example` to `.env` and set:

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (required) | Anthropic API key for Claude |
| `DATABASE_URL` | `postgresql+asyncpg://nox:nox@postgres:5432/nox` | Postgres connection (asyncpg for API) |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `ARTIFACT_DIR` | `/artifacts` | Where job artifacts are stored |
| `API_KEY` | `changeme-api-key` | Bearer key for the HTTP API |
| `MAX_INPUT_TOKENS` | `200000` | Per-job input token budget |
| `MAX_OUTPUT_TOKENS` | `50000` | Per-job output token budget |
| `JOB_TIMEOUT_SECONDS` | `900` | Per-job wall-clock timeout (15 min) |
| `AGENT_MODEL` | `claude-opus-4-6` | Claude model used by the agent |
| `SANDBOX_IMAGE` | `nox-sandbox` | Docker image name for sandbox containers |
| `SANDBOX_NETWORK` | `nox_net` | Docker network sandbox containers join |
| `SCRATCH_BASE` | `/tmp/nox_scratch` | Host path for per-job scratch directories |
