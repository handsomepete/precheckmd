"""Compliance report job runner.

Spawns a sandbox container that runs the compliance_report agent against
the target repository. The agent (implemented in Step 5) runs inside the
container with restricted network egress.

Input payload keys:
  - repo_url: str  (required) HTTPS URL of the GitHub repository to audit
"""

import logging

from worker.runner import register
from worker.sandbox_runner import run_in_sandbox

logger = logging.getLogger(__name__)


@register("compliance_report")
def run(job_id: str, input_payload: dict, db) -> dict:
    """Run the SOC 2 compliance report agent inside a sandbox container.

    Args:
        job_id: UUID of the job record.
        input_payload: Must include "repo_url".
        db: Sync SQLAlchemy session (unused here; artifacts collected after container exits).

    Returns:
        result_summary dict.
    """
    repo_url = input_payload.get("repo_url", "").strip()
    if not repo_url:
        raise ValueError("compliance_report requires 'repo_url' in input payload")

    logger.info("compliance_report: starting sandbox for job %s, repo=%s", job_id, repo_url)

    # Tag the payload so the sandbox knows the job_type
    payload = dict(input_payload)
    payload["_job_type"] = "compliance_report"

    return run_in_sandbox(job_id, payload)
