"""Tool: write_artifact

Write a file to the job's artifact output directory and register it in
the manifest so the worker can record it in the database after the sandbox
container exits.
"""

import json
import logging
import os
from pathlib import Path

from tools.context import ARTIFACT_DIR, JOB_ID

logger = logging.getLogger(__name__)

_MANIFEST_FILENAME = "artifacts.json"


def _artifact_dir() -> Path:
    d = Path(ARTIFACT_DIR) / JOB_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_manifest(artifact_dir: Path) -> list[dict]:
    manifest_path = artifact_dir / _MANIFEST_FILENAME
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_manifest(artifact_dir: Path, manifest: list[dict]) -> None:
    manifest_path = artifact_dir / _MANIFEST_FILENAME
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


def write_artifact(
    filename: str,
    content: str | bytes,
    mime_type: str = "text/plain",
) -> str:
    """Write *content* to the artifact directory as *filename*.

    The artifact is registered in artifacts.json so the worker can record
    it in the database after the container exits.

    Args:
        filename: Output filename (e.g. "report.pdf", "findings.json").
                  Must not contain path separators.
        content: File content as str (UTF-8) or bytes.
        mime_type: MIME type for the artifact (e.g. "application/pdf").

    Returns:
        Absolute path to the written file.

    Raises:
        ValueError: If filename contains path separators.
    """
    if os.sep in filename or "/" in filename:
        raise ValueError(f"filename must not contain path separators: '{filename}'")

    artifact_dir = _artifact_dir()
    dest = artifact_dir / filename

    if isinstance(content, str):
        data = content.encode("utf-8")
    else:
        data = content

    with open(dest, "wb") as f:
        f.write(data)

    # Update manifest
    manifest = _load_manifest(artifact_dir)
    # Replace existing entry with same filename
    manifest = [e for e in manifest if e.get("filename") != filename]
    manifest.append({
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": len(data),
    })
    _save_manifest(artifact_dir, manifest)

    logger.info("write_artifact: %s (%d bytes, %s)", filename, len(data), mime_type)
    return str(dest)
