"""Sandbox agent entry point.

Reads JOB_ID from the environment, loads the job config from
/scratch/job_config.json, dispatches to the appropriate agent module,
and exits with 0 on success or 1 on failure.

Wired to Claude Agent SDK in Step 5.
"""

import json
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sandbox.run_agent")


def main() -> int:
    job_id = os.environ.get("JOB_ID", "").strip()
    if not job_id:
        logger.error("JOB_ID env var is required")
        return 1

    scratch_dir = os.environ.get("SCRATCH_DIR", "/scratch")
    config_path = os.path.join(scratch_dir, "job_config.json")

    if not os.path.exists(config_path):
        logger.error("job_config.json not found at %s", config_path)
        return 1

    with open(config_path) as f:
        config = json.load(f)

    job_type = config.get("job_type", "")
    input_payload = config.get("input", {})

    logger.info("Running job %s (type=%s)", job_id, job_type)

    if job_type == "compliance_report":
        # Imported here to avoid circular deps and keep startup fast for other types
        from agents.compliance_report import run as run_compliance
        run_compliance(job_id=job_id, input_payload=input_payload)
    else:
        logger.error("Unknown job_type: %s", job_type)
        return 1

    logger.info("Job %s finished", job_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
