"""Job CRUD endpoints.

POST   /jobs                     - create and enqueue a job
GET    /jobs/{job_id}            - fetch job status and metadata
GET    /jobs/{job_id}/artifacts  - list artifacts for a job
GET    /jobs/{job_id}/artifacts/{artifact_id} - download an artifact
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
from api.schemas import ArtifactResponse, JobCreate, JobResponse
from db.models import Artifact, Job
from storage.local import artifact_store
from worker.runner import run_job

router = APIRouter(prefix="/jobs", tags=["jobs"])

Auth = Annotated[str, Depends(require_api_key)]
DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobResponse)
async def create_job(body: JobCreate, _: Auth, db: DB) -> JobResponse:
    """Create a job and enqueue it for processing."""
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        job_type=body.job_type,
        status="pending",
        input_payload=body.input,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue - timeout enforced by RQ as a hard kill
    job_queue.enqueue(
        run_job,
        job_id,
        job_timeout=settings.job_timeout_seconds,
    )

    return JobResponse.model_validate(job)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, _: Auth, db: DB) -> JobResponse:
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse.model_validate(job)


@router.get("/{job_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(job_id: str, _: Auth, db: DB) -> list[ArtifactResponse]:
    await _require_job(job_id, db)
    result = await db.execute(select(Artifact).where(Artifact.job_id == job_id))
    artifacts = result.scalars().all()
    return [ArtifactResponse.model_validate(a) for a in artifacts]


@router.get("/{job_id}/artifacts/{artifact_id}")
async def download_artifact(job_id: str, artifact_id: str, _: Auth, db: DB) -> Response:
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
