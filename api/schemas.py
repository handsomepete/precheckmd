"""Pydantic schemas for API request/response bodies."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------- Job ----------

class JobCreate(BaseModel):
    job_type: str = Field(..., examples=["compliance_report"])
    input: dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    id: str
    job_type: str
    status: str
    input_payload: dict[str, Any]
    result_summary: dict[str, Any] | None
    error_message: str | None
    token_input_used: int
    token_output_used: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


# ---------- Artifact ----------

class ArtifactResponse(BaseModel):
    id: str
    job_id: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}
