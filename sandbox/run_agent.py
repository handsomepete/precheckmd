"""Sandbox agent entry point.

Reads JOB_ID and SCRATCH_DIR from the environment, loads job_config.json
from the scratch directory, dispatches to the correct agent module, and
exits 0 on success or 1 on failure.

The worker spawns one container per job and waits for this process to exit.
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

    logger.info("Job %s starting (type=%s)", job_id, job_type)

    try:
        if job_type == "compliance_report":
            from agents.compliance_report import run
            run(job_id=job_id, input_payload=input_payload)
        else:
            logger.error("Unknown job_type: '%s'", job_type)
            return 1
    except Exception:
        logger.exception("Job %s failed", job_id)
        return 1

    logger.info("Job %s finished successfully", job_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
