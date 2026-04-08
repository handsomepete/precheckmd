"""RQ worker entry point.

Start with:
    rq worker nox --url $REDIS_URL
"""
# This module is intentionally minimal. The rq CLI discovers and runs workers.
# Job runners are registered in worker/runner.py and imported at module load.

from worker import runner  # noqa: F401 - registers job functions with RQ
