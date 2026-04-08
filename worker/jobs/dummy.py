"""Dummy job runner used to prove end-to-end queue flow.

job_type: "dummy"
Input: optional { "sleep_seconds": int }
Output: writes a plain-text artifact and returns a result summary.
"""

import logging
import time
import uuid

from db.models import Artifact
from storage.local import artifact_store
from worker.runner import register

logger = logging.getLogger(__name__)


@register("dummy")
def run(job_id: str, input_payload: dict, db) -> dict:
    sleep_seconds = int(input_payload.get("sleep_seconds", 3))
    logger.info("Dummy job %s: sleeping %ds", job_id, sleep_seconds)
    time.sleep(sleep_seconds)

    # Write a fake text artifact
    content = (
        f"Dummy artifact for job {job_id}\n"
        f"Completed at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        f"Input: {input_payload}\n"
    )
    filename = "dummy_output.txt"
    storage_path = artifact_store.write(job_id, filename, content.encode())

    artifact = Artifact(
        id=str(uuid.uuid4()),
        job_id=job_id,
        filename=filename,
        mime_type="text/plain",
        size_bytes=len(content.encode()),
        storage_path=storage_path,
    )
    db.add(artifact)
    db.commit()

    logger.info("Dummy job %s: artifact written to %s", job_id, storage_path)
    return {"message": "dummy job completed", "artifact": filename}
