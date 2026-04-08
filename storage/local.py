"""Local filesystem artifact store. Implements the same interface as the future S3 store."""

import os
import shutil
from pathlib import Path

from api.config import settings


class LocalArtifactStore:
    """Store artifacts on the local filesystem under ARTIFACT_DIR/{job_id}/."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.artifact_dir)

    def job_dir(self, job_id: str) -> Path:
        d = self.base_dir / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write(self, job_id: str, filename: str, data: bytes) -> str:
        """Write bytes to the store. Returns the storage path."""
        path = self.job_dir(job_id) / filename
        path.write_bytes(data)
        return str(path)

    def read(self, storage_path: str) -> bytes:
        return Path(storage_path).read_bytes()

    def delete_job(self, job_id: str) -> None:
        d = self.base_dir / job_id
        if d.exists():
            shutil.rmtree(d)

    def exists(self, storage_path: str) -> bool:
        return Path(storage_path).exists()


artifact_store = LocalArtifactStore()
