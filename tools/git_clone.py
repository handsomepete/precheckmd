"""Tool: git_clone

Clone a public git repository into a subdirectory of the scratch dir.
Only HTTPS URLs are accepted; SSH is blocked.

Known gap: private repository support is not implemented. Clients who need
to audit private repos will require token-based auth (e.g. GITHUB_TOKEN
injected into the clone URL or a credential helper). This is a planned
future capability; do not implement until there is explicit auth design.
"""

import logging
import os
import re
import subprocess
from pathlib import Path

from tools.context import SCRATCH_DIR

logger = logging.getLogger(__name__)

_HTTPS_RE = re.compile(r"^https://[a-zA-Z0-9._/\-]+(\.git)?$")


def git_clone(repo_url: str, dest_name: str = "repo") -> str:
    """Clone *repo_url* into SCRATCH_DIR/<dest_name>.

    Only HTTPS URLs are permitted. The clone is shallow (depth=1) to keep
    token usage low. Returns the absolute path to the cloned directory.

    Args:
        repo_url: HTTPS URL of the git repository to clone.
        dest_name: Name of the subdirectory inside the scratch dir (default: "repo").

    Returns:
        Absolute path to the cloned repository directory.

    Raises:
        ValueError: If repo_url is not a valid HTTPS URL.
        RuntimeError: If the git clone command fails.
    """
    if not _HTTPS_RE.match(repo_url):
        raise ValueError(
            f"Invalid repo_url '{repo_url}': only HTTPS URLs are accepted."
        )

    dest = Path(SCRATCH_DIR) / dest_name
    if dest.exists():
        logger.info("git_clone: %s already exists, skipping", dest)
        return str(dest)

    logger.info("git_clone: cloning %s -> %s", repo_url, dest)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", repo_url, str(dest)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git clone failed (exit {result.returncode}):\n{result.stderr}"
        )
    logger.info("git_clone: done -> %s", dest)
    return str(dest)
