"""RQ job dispatcher.

`run_job` is the single entry point enqueued for every job. It:
1. Marks the job as running in Postgres.
2. Dispatches to the appropriate per-type runner.
3. Marks the job completed or failed.

All per-type runners live in worker/jobs/ and must conform to the signature:
    def run(job_id: str, input_payload: dict, db) -> dict | None
They return an optional result_summary dict and raise on failure.
"""

import logging
import traceback
from datetime import datetime, timezone

from db.models import Job
from db.session import SyncSessionLocal

logger = logging.getLogger(__name__)

# Registry: job_type -> callable
_RUNNERS: dict = {}


def register(job_type: str):
    """Decorator to register a runner function for a job type."""
    def decorator(fn):
        _RUNNERS[job_type] = fn
        return fn
    return decorator


def run_job(job_id: str) -> None:
    """Entry point called by the RQ worker process."""
    with SyncSessionLocal() as db:
        job: Job | None = db.get(Job, job_id)
        if not job:
            logger.error("run_job: job %s not found in DB", job_id)
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        runner_fn = _RUNNERS.get(job.job_type)
        if runner_fn is None:
            _fail(db, job, f"Unknown job_type '{job.job_type}'")
            return

        try:
            result_summary = runner_fn(job_id, job.input_payload, db)
            summary = result_summary or {}
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.result_summary = summary
            # Persist token usage if the sandbox runner reported it
            if "token_input_used" in summary:
                job.token_input_used = int(summary["token_input_used"])
            if "token_output_used" in summary:
                job.token_output_used = int(summary["token_output_used"])
            db.commit()
            logger.info("Job %s completed", job_id)
        except Exception:
            tb = traceback.format_exc()
            logger.exception("Job %s failed", job_id)
            _fail(db, job, tb)


def _fail(db, job: Job, message: str) -> None:
    job.status = "failed"
    job.completed_at = datetime.now(timezone.utc)
    job.error_message = message
    db.commit()


# Import job modules so their @register decorators fire at worker startup.
from worker.jobs import compliance_report, dummy  # noqa: E402, F401
