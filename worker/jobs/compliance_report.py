"""Compliance report job runner stub.

Full implementation wired in Step 5. Importing this module registers the
job_type so the worker does not return "Unknown job_type" errors during
integration tests.
"""

from worker.runner import register


@register("compliance_report")
def run(job_id: str, input_payload: dict, db) -> dict:
    raise NotImplementedError(
        "compliance_report runner not yet implemented - see Step 5"
    )
