"""Tool: run_gitleaks

Scan a repository for hardcoded secrets using Gitleaks and return
structured findings.
"""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_FINDINGS = 100


def run_gitleaks(target_dir: str) -> dict:
    """Scan *target_dir* for secrets using Gitleaks.

    Args:
        target_dir: Absolute path to the git repository to scan.

    Returns:
        Dict with keys:
          - "findings": list of finding dicts (rule_id, file, line, secret_partial,
                        commit, author, description)
          - "total": int total count
          - "error": str or None
    """
    if not Path(target_dir).is_dir():
        return {"findings": [], "total": 0, "error": f"Not a directory: {target_dir}"}

    report_path = Path(target_dir) / ".gitleaks_report.json"
    logger.info("run_gitleaks: scanning %s", target_dir)

    result = subprocess.run(
        [
            "gitleaks",
            "detect",
            "--source", target_dir,
            "--report-format", "json",
            "--report-path", str(report_path),
            "--no-git",       # scan files directly, not git history (faster for shallow clones)
            "--exit-code", "0",  # always exit 0 so we can read the report
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        error_msg = result.stderr[:2000] if result.stderr else "gitleaks error"
        logger.warning("run_gitleaks: exit %d: %s", result.returncode, error_msg)
        return {"findings": [], "total": 0, "error": error_msg}

    if not report_path.exists():
        # No findings file means no secrets found
        logger.info("run_gitleaks: no secrets found")
        return {"findings": [], "total": 0, "error": None}

    try:
        with open(report_path) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return {"findings": [], "total": 0, "error": str(exc)}
    finally:
        # Clean up the report so it is not accidentally included in artifacts
        report_path.unlink(missing_ok=True)

    if not isinstance(raw, list):
        raw = []

    findings = []
    for item in raw[:MAX_FINDINGS]:
        # Redact secret value - only expose a short partial for identification
        secret = item.get("Secret", "") or ""
        secret_partial = (secret[:4] + "..." + secret[-2:]) if len(secret) > 8 else "***"
        findings.append({
            "rule_id": item.get("RuleID", ""),
            "description": item.get("Description", ""),
            "file": item.get("File", ""),
            "line": item.get("StartLine", 0),
            "commit": item.get("Commit", ""),
            "author": item.get("Author", ""),
            "secret_partial": secret_partial,
        })

    logger.info("run_gitleaks: %d findings", len(findings))
    return {"findings": findings, "total": len(raw), "error": None}
