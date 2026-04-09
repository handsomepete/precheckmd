"""Pydantic schemas for API request/response bodies."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------- Job ----------

class JobCreate(BaseModel):
    job_type: str = Field(..., examples=["dummy", "compliance_report"])
    input: dict[str, Any] = Field(default_factory=dict)


class JobQueued(BaseModel):
    """Minimal response returned by POST /jobs."""
    job_id: str
    status: str = "queued"


class JobResponse(BaseModel):
    """Full job detail returned by GET /jobs/{id}."""
    job_id: str           # human-facing name matches POST /jobs response
    job_type: str
    status: str
    input_payload: dict[str, Any]
    artifact_ids: list[str] = Field(default_factory=list)
    result_summary: dict[str, Any] | None
    error_message: str | None
    token_input_used: int
    token_output_used: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": False}


# ---------- Artifact ----------

class ArtifactResponse(BaseModel):
    id: str
    job_id: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}
