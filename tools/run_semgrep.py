"""Tool: run_semgrep

Run Semgrep static analysis on a target directory and return structured
findings. Uses the auto config by default, which selects rules based on
detected languages.
"""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum number of findings to return to keep context size manageable
MAX_FINDINGS = 200


def run_semgrep(target_dir: str, config: str = "auto") -> dict:
    """Run Semgrep on *target_dir* and return the findings as a dict.

    Args:
        target_dir: Absolute path to the directory to scan.
        config: Semgrep rule config to use (default: "auto" for language-aware
                rule selection). Can also be a ruleset ID like "p/owasp-top-ten".

    Returns:
        Dict with keys:
          - "findings": list of finding dicts (rule_id, path, line, message, severity)
          - "stats": dict with total counts by severity
          - "error": str or None if semgrep exited non-zero
    """
    if not Path(target_dir).is_dir():
        return {"findings": [], "stats": {}, "error": f"Not a directory: {target_dir}"}

    logger.info("run_semgrep: scanning %s (config=%s)", target_dir, config)
    result = subprocess.run(
        [
            "semgrep",
            "--config", config,
            "--json",
            "--no-git-ignore",
            "--max-target-bytes", "500000",
            target_dir,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )

    error_msg = None
    if result.returncode not in (0, 1):  # 0=ok, 1=findings found, >1=error
        error_msg = result.stderr[:2000] if result.stderr else "semgrep exited with unknown error"
        logger.warning("run_semgrep: non-zero exit %d: %s", result.returncode, error_msg)

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"findings": [], "stats": {}, "error": f"Failed to parse semgrep output: {result.stdout[:500]}"}

    findings = []
    severity_counts: dict[str, int] = {}
    for r in raw.get("results", [])[:MAX_FINDINGS]:
        sev = r.get("extra", {}).get("severity", "INFO")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        findings.append({
            "rule_id": r.get("check_id", ""),
            "path": r.get("path", ""),
            "line": r.get("start", {}).get("line", 0),
            "message": r.get("extra", {}).get("message", ""),
            "severity": sev,
        })

    logger.info("run_semgrep: %d findings (%s)", len(findings), severity_counts)
    return {"findings": findings, "stats": severity_counts, "error": error_msg}
