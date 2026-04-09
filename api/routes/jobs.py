"""Job CRUD endpoints.

POST   /jobs                              - create and enqueue a job
GET    /jobs/{job_id}                     - fetch job status, metadata, artifact IDs
GET    /jobs/{job_id}/artifacts           - list artifacts (full detail)
GET    /jobs/{job_id}/artifacts/{id}      - download a single artifact
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_api_key
from api.config import settings
from api.deps import get_db
from api.queue import job_queue
from api.schemas import ArtifactResponse, JobCreate, JobQueued, JobResponse
from db.models import Artifact, Job
from storage.local import artifact_store

# Use string reference so the API process never imports worker modules
# (worker deps like `docker` may not be installed in the API environment).
_RUN_JOB = "worker.runner.run_job"

router = APIRouter(prefix="/jobs", tags=["jobs"])

Auth = Annotated[str, Depends(require_api_key)]
DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobQueued)
async def create_job(body: JobCreate, _: Auth, db: DB) -> JobQueued:
    """Create a job, write it to the DB, and enqueue it on RQ.

    Returns {"job_id": "...", "status": "queued"} immediately.
    """
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        job_type=body.job_type,
        status="pending",
        input_payload=body.input,
    )
    db.add(job)
    await db.commit()

    job_queue.enqueue(
        _RUN_JOB,
        job_id,
        job_timeout=settings.job_timeout_seconds,
    )

    return JobQueued(job_id=job_id, status="queued")


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, _: Auth, db: DB) -> JobResponse:
    """Return full job detail including embedded artifact IDs."""
    job = await _require_job(job_id, db)

    result = await db.execute(select(Artifact.id).where(Artifact.job_id == job_id))
    artifact_ids = [row[0] for row in result.all()]

    return JobResponse(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        input_payload=job.input_payload,
        artifact_ids=artifact_ids,
        result_summary=job.result_summary,
        error_message=job.error_message,
        token_input_used=job.token_input_used,
        token_output_used=job.token_output_used,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/{job_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(job_id: str, _: Auth, db: DB) -> list[ArtifactResponse]:
    """List all artifacts for a job with full metadata."""
    await _require_job(job_id, db)
    result = await db.execute(select(Artifact).where(Artifact.job_id == job_id))
    return [ArtifactResponse.model_validate(a) for a in result.scalars().all()]


@router.get("/{job_id}/artifacts/{artifact_id}")
async def download_artifact(job_id: str, artifact_id: str, _: Auth, db: DB) -> Response:
    """Download a single artifact by ID."""
    await _require_job(job_id, db)
    result = await db.execute(
        select(Artifact).where(Artifact.id == artifact_id, Artifact.job_id == job_id)
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if not artifact_store.exists(artifact.storage_path):
        raise HTTPException(status_code=404, detail="Artifact file missing from storage")
    data = artifact_store.read(artifact.storage_path)
    return Response(
        content=data,
        media_type=artifact.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
    )


async def _require_job(job_id: str, db: AsyncSession) -> Job:
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
