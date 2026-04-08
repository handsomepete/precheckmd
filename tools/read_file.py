"""Tool: read_file

Read a file from the scratch directory, with path traversal protection
and size limits to keep context usage bounded.
"""

import logging
from pathlib import Path

from tools.context import SCRATCH_DIR

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 50_000  # ~50 KB per file read


def read_file(relative_path: str, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    """Read a file from the scratch directory and return its contents as a string.

    The path is resolved relative to SCRATCH_DIR. Paths that escape the scratch
    directory are rejected.

    Args:
        relative_path: Path relative to the scratch directory (e.g. "repo/src/app.py").
        max_bytes: Maximum number of bytes to return (default 50000). Content is
                   truncated with a notice if the file is larger.

    Returns:
        File contents as a UTF-8 string (with replacement chars for invalid bytes).
        If the file does not exist an explanatory string is returned rather than
        raising, so the agent can note the absence without crashing.
    """
    base = Path(SCRATCH_DIR).resolve()
    target = (base / relative_path).resolve()

    # Block path traversal
    try:
        target.relative_to(base)
    except ValueError:
        return f"[read_file error] Path '{relative_path}' is outside the scratch directory."

    if not target.exists():
        return f"[read_file error] File not found: {relative_path}"

    if target.is_dir():
        # Return directory listing instead of failing
        entries = sorted(str(p.relative_to(base)) for p in target.iterdir())
        listing = "\n".join(entries[:200])
        return f"[directory listing for {relative_path}]\n{listing}"

    size = target.stat().st_size
    with open(target, "rb") as f:
        data = f.read(max_bytes)

    text = data.decode("utf-8", errors="replace")
    if size > max_bytes:
        text += f"\n\n[... file truncated: showed {max_bytes}/{size} bytes ...]"

    logger.info("read_file: %s (%d bytes)", relative_path, min(size, max_bytes))
    return text
