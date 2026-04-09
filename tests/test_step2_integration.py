"""Integration test for Step 2: job creation, worker pickup, artifact download.

Requires the full docker-compose stack to be running:
    docker-compose up -d
    pytest tests/test_step2_integration.py -v

The test:
  1. Hits POST /jobs with job_type=dummy
  2. Asserts the response shape (job_id, status="queued")
  3. Polls GET /jobs/{job_id} until status is completed or failed (timeout 60s)
  4. Asserts status=completed and artifact_ids is non-empty
  5. Downloads the artifact via GET /jobs/{job_id}/artifacts/{artifact_id}
  6. Asserts the file contents contain "hello from job <job_id>"
"""

import time

import pytest
import requests

BASE_URL = "http://localhost:8000"
API_KEY = "changeme-api-key"
HEADERS = {"X-API-Key": API_KEY}
POLL_INTERVAL = 2   # seconds between status polls
POLL_TIMEOUT = 60   # seconds before the test gives up


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_job(job_type: str = "dummy", input_payload: dict | None = None) -> dict:
    resp = requests.post(
        f"{BASE_URL}/jobs",
        json={"job_type": job_type, "input": input_payload or {}},
        headers=HEADERS,
        timeout=10,
    )
    assert resp.status_code == 202, f"POST /jobs returned {resp.status_code}: {resp.text}"
    return resp.json()


def _get_job(job_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/jobs/{job_id}", headers=HEADERS, timeout=10)
    assert resp.status_code == 200, f"GET /jobs/{job_id} returned {resp.status_code}: {resp.text}"
    return resp.json()


def _poll_until_done(job_id: str, timeout: int = POLL_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _get_job(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_endpoint_is_reachable(self):
        resp = requests.get(f"{BASE_URL}/health", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["database"] == "reachable"


class TestAuthentication:
    def test_missing_api_key_returns_401(self):
        resp = requests.post(
            f"{BASE_URL}/jobs",
            json={"job_type": "dummy", "input": {}},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_wrong_api_key_returns_401(self):
        resp = requests.post(
            f"{BASE_URL}/jobs",
            json={"job_type": "dummy", "input": {}},
            headers={"X-API-Key": "wrong-key"},
            timeout=10,
        )
        assert resp.status_code == 401


class TestPostJobs:
    def test_returns_202_with_job_id_and_queued_status(self):
        body = _post_job("dummy")
        assert "job_id" in body, f"No job_id in response: {body}"
        assert body["status"] == "queued", f"Expected status=queued, got: {body['status']}"

    def test_job_id_is_uuid_string(self):
        import uuid
        body = _post_job("dummy")
        # Will raise ValueError if not a valid UUID
        uuid.UUID(body["job_id"])

    def test_unknown_job_type_still_queues_then_fails(self):
        """Worker should mark job failed for unknown types, not crash."""
        body = _post_job("nonexistent_type")
        assert body["status"] == "queued"
        job_id = body["job_id"]
        job = _poll_until_done(job_id)
        assert job["status"] == "failed"
        assert "Unknown job_type" in (job["error_message"] or "")


class TestGetJob:
    def test_returns_404_for_unknown_job(self):
        resp = requests.get(
            f"{BASE_URL}/jobs/00000000-0000-0000-0000-000000000000",
            headers=HEADERS,
            timeout=10,
        )
        assert resp.status_code == 404

    def test_returns_job_fields(self):
        body = _post_job("dummy")
        job_id = body["job_id"]
        job = _get_job(job_id)
        assert job["job_id"] == job_id
        assert job["job_type"] == "dummy"
        assert job["status"] in ("pending", "running", "queued", "completed")
        assert "artifact_ids" in job
        assert isinstance(job["artifact_ids"], list)


class TestDummyJobEndToEnd:
    """Core Step 2 integration test: create, wait, download, assert."""

    def test_dummy_job_completes_and_artifact_contains_hello(self):
        # 1. Create the job
        queued = _post_job("dummy", {"sleep_seconds": 2})
        assert queued["status"] == "queued"
        job_id = queued["job_id"]

        # 2. Poll until done
        job = _poll_until_done(job_id)
        assert job["status"] == "completed", (
            f"Job ended with status={job['status']}, error={job.get('error_message')}"
        )

        # 3. Artifact IDs are embedded in the GET /jobs/{id} response
        assert len(job["artifact_ids"]) > 0, "Expected at least one artifact"
        artifact_id = job["artifact_ids"][0]

        # 4. Download the artifact
        dl_resp = requests.get(
            f"{BASE_URL}/jobs/{job_id}/artifacts/{artifact_id}",
            headers=HEADERS,
            timeout=10,
        )
        assert dl_resp.status_code == 200, (
            f"Artifact download returned {dl_resp.status_code}: {dl_resp.text}"
        )

        # 5. Assert content
        content = dl_resp.text
        assert f"hello from job {job_id}" in content, (
            f"Expected 'hello from job {job_id}' in artifact, got:\n{content}"
        )

    def test_artifact_list_endpoint_matches_embedded_ids(self):
        queued = _post_job("dummy", {"sleep_seconds": 1})
        job_id = queued["job_id"]
        job = _poll_until_done(job_id)
        assert job["status"] == "completed"

        # GET /jobs/{id}/artifacts should list the same IDs
        list_resp = requests.get(
            f"{BASE_URL}/jobs/{job_id}/artifacts",
            headers=HEADERS,
            timeout=10,
        )
        assert list_resp.status_code == 200
        artifact_list = list_resp.json()
        listed_ids = {a["id"] for a in artifact_list}
        embedded_ids = set(job["artifact_ids"])
        assert listed_ids == embedded_ids

    def test_artifact_content_type_is_text_plain(self):
        queued = _post_job("dummy")
        job_id = queued["job_id"]
        job = _poll_until_done(job_id)
        artifact_id = job["artifact_ids"][0]

        dl_resp = requests.get(
            f"{BASE_URL}/jobs/{job_id}/artifacts/{artifact_id}",
            headers=HEADERS,
            timeout=10,
        )
        assert "text/plain" in dl_resp.headers.get("content-type", "")

    def test_artifact_download_returns_404_for_wrong_job(self):
        queued = _post_job("dummy")
        job_id = queued["job_id"]
        job = _poll_until_done(job_id)
        artifact_id = job["artifact_ids"][0]

        # Same artifact_id but wrong job_id
        resp = requests.get(
            f"{BASE_URL}/jobs/00000000-0000-0000-0000-000000000000/artifacts/{artifact_id}",
            headers=HEADERS,
            timeout=10,
        )
        assert resp.status_code == 404
