"""Tool: render_pdf

Render a structured compliance report as a PDF using ReportLab.
Full implementation in Step 6. This stub writes a plain-text placeholder
so the agent pipeline can be tested end-to-end before the PDF renderer
is complete.
"""

import logging

from tools.write_artifact import write_artifact

logger = logging.getLogger(__name__)

_STUB_NOTICE = "[PDF rendering not yet implemented - plain text placeholder]"


def render_pdf(
    title: str,
    findings: list[dict],
    metadata: dict | None = None,
) -> str:
    """Render a compliance report PDF and write it as a job artifact.

    Args:
        title: Report title (e.g. "SOC 2 Compliance Report - my-org/my-repo").
        findings: List of finding dicts, each with at minimum:
                  - "control": str (e.g. "CC6.1 Logical Access")
                  - "status": str ("pass" | "fail" | "partial" | "unknown")
                  - "evidence": str (supporting evidence or rationale)
                  - "recommendation": str (remediation guidance, if applicable)
        metadata: Optional dict of report metadata (repo_url, scan_date, etc.).

    Returns:
        Absolute path to the written PDF artifact.
    """
    logger.warning("render_pdf: stub implementation - writing text placeholder")

    meta = metadata or {}
    lines = [
        _STUB_NOTICE,
        "",
        f"Title: {title}",
        f"Repo: {meta.get('repo_url', 'N/A')}",
        f"Scan date: {meta.get('scan_date', 'N/A')}",
        "",
        "FINDINGS",
        "--------",
    ]
    for i, f in enumerate(findings, 1):
        lines.append(
            f"{i}. [{f.get('status', '?').upper()}] {f.get('control', 'Unknown control')}"
        )
        if f.get("evidence"):
            lines.append(f"   Evidence: {f['evidence']}")
        if f.get("recommendation"):
            lines.append(f"   Recommendation: {f['recommendation']}")
        lines.append("")

    content = "\n".join(lines)
    path = write_artifact("report.pdf", content, mime_type="application/pdf")
    return path
