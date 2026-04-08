"""Sandbox runner: spawn a per-job Docker container, monitor it, collect artifacts.

Called by worker/jobs/compliance_report.py (and any future sandbox-based job type).
The worker process itself is NOT the agent - it just orchestrates the container.
"""

import json
import logging
import os
import uuid
from pathlib import Path

import docker
from docker.errors import ContainerError, ImageNotFound, NotFound
from docker.models.containers import Container

from api.config import settings
from db.models import Artifact
from db.session import SyncSessionLocal

logger = logging.getLogger(__name__)

_SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "nox-sandbox")
_SANDBOX_NETWORK = os.environ.get("SANDBOX_NETWORK", "nox_net")

# Scratch base on the HOST filesystem (mounted into container at /scratch)
_SCRATCH_BASE = os.environ.get("SCRATCH_BASE", "/tmp/nox_scratch")


def run_in_sandbox(job_id: str, input_payload: dict) -> dict:
    """Spawn a sandbox container for *job_id*, wait for it, collect artifacts.

    Returns the result_summary dict to be stored on the Job row.
    Raises RuntimeError on container failure.
    """
    scratch_dir = Path(_SCRATCH_BASE) / job_id
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # Write the job config into the scratch dir so the container can read it
    config_path = scratch_dir / "job_config.json"
    config_path.write_text(
        json.dumps({"job_type": input_payload.get("_job_type", ""), "input": input_payload}),
        encoding="utf-8",
    )

    client = docker.from_env()

    env = {
        "JOB_ID": job_id,
        "SCRATCH_DIR": "/scratch",
        "ARTIFACT_DIR": "/artifacts",
        "DATABASE_URL": settings.database_url,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "MAX_INPUT_TOKENS": str(settings.max_input_tokens),
        "MAX_OUTPUT_TOKENS": str(settings.max_output_tokens),
    }

    volumes = {
        str(scratch_dir): {"bind": "/scratch", "mode": "rw"},
        settings.artifact_dir: {"bind": "/artifacts", "mode": "rw"},
    }

    logger.info("sandbox: starting container for job %s (image=%s)", job_id, _SANDBOX_IMAGE)

    container: Container | None = None
    exit_code: int = -1

    try:
        container = client.containers.run(
            image=_SANDBOX_IMAGE,
            command=["python", "-m", "sandbox.run_agent"],
            environment=env,
            volumes=volumes,
            network=_SANDBOX_NETWORK,
            cap_add=["NET_ADMIN"],
            name=f"nox-job-{job_id}",
            remove=False,     # we remove manually after log collection
            detach=True,
            stdout=True,
            stderr=True,
        )

        # Stream logs to the worker logger while the container runs
        for line in container.logs(stream=True, follow=True):
            logger.info("sandbox[%s]: %s", job_id[:8], line.decode("utf-8", errors="replace").rstrip())

        result = container.wait(timeout=settings.job_timeout_seconds)
        exit_code = result.get("StatusCode", -1)

    except ImageNotFound:
        raise RuntimeError(
            f"Sandbox image '{_SANDBOX_IMAGE}' not found. "
            "Run: docker-compose build sandbox"
        )
    except Exception as exc:
        raise RuntimeError(f"Container error for job {job_id}: {exc}") from exc
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except NotFound:
                pass

    if exit_code != 0:
        raise RuntimeError(
            f"Sandbox container for job {job_id} exited with code {exit_code}"
        )

    logger.info("sandbox: container for job %s finished (exit=%d)", job_id, exit_code)

    # Collect artifacts written by the sandbox
    artifact_count = _collect_artifacts(job_id)

    return {"sandbox_exit_code": exit_code, "artifacts_collected": artifact_count}


def _collect_artifacts(job_id: str) -> int:
    """Read artifacts.json written by the sandbox and insert Artifact rows."""
    artifact_dir = Path(settings.artifact_dir) / job_id
    manifest_path = artifact_dir / "artifacts.json"

    if not manifest_path.exists():
        logger.info("sandbox: no artifacts.json for job %s", job_id)
        return 0

    try:
        with open(manifest_path) as f:
            manifest: list[dict] = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("sandbox: failed to read artifacts.json for job %s: %s", job_id, exc)
        return 0

    with SyncSessionLocal() as db:
        count = 0
        for entry in manifest:
            filename = entry.get("filename", "")
            if not filename:
                continue
            storage_path = str(artifact_dir / filename)
            if not Path(storage_path).exists():
                logger.warning("sandbox: artifact file missing: %s", storage_path)
                continue
            artifact = Artifact(
                id=str(uuid.uuid4()),
                job_id=job_id,
                filename=filename,
                mime_type=entry.get("mime_type", "application/octet-stream"),
                size_bytes=entry.get("size_bytes", Path(storage_path).stat().st_size),
                storage_path=storage_path,
            )
            db.add(artifact)
            count += 1
        db.commit()

    logger.info("sandbox: collected %d artifacts for job %s", count, job_id)
    return count
