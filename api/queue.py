"""Synchronous Redis connection and RQ queue used by the API to enqueue jobs.

RQ is a sync library, so we use redis-py's sync client here even though the
FastAPI app is async. The enqueue call is fast enough that running it inline
(without an executor) is acceptable.
"""

from redis import Redis
from rq import Queue

from api.config import settings

redis_conn = Redis.from_url(settings.redis_url)
job_queue = Queue("nox", connection=redis_conn)
