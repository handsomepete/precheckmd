"""Tool: render_pdf

Render a structured SOC 2 compliance report as a PDF using ReportLab.
The PDF has a cover page, executive summary table, and per-control detail
sections with color-coded status badges.

No em dashes are used anywhere in generated output.
"""

import logging
from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from tools.write_artifact import write_artifact

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

_C_NAVY = HexColor("#1a2b4a")       # cover / section headers
_C_SLATE = HexColor("#2c3e50")      # body text headings
_C_ACCENT = HexColor("#2980b9")     # accent blue
_C_LIGHT_BG = HexColor("#f5f7fa")   # alternate row / section bg

_C_PASS = HexColor("#27ae60")       # status: pass
_C_FAIL = HexColor("#c0392b")       # status: fail
_C_PARTIAL = HexColor("#e67e22")    # status: partial
_C_UNKNOWN = HexColor("#7f8c8d")    # status: unknown

_C_CRITICAL = HexColor("#c0392b")
_C_HIGH = HexColor("#e67e22")
_C_MEDIUM = HexColor("#f39c12")
_C_LOW = HexColor("#27ae60")
_C_INFO = HexColor("#2980b9")


def _status_color(status: str):
    return {
        "pass": _C_PASS,
        "fail": _C_FAIL,
        "partial": _C_PARTIAL,
        "unknown": _C_UNKNOWN,
    }.get(status.lower(), _C_UNKNOWN)


def _severity_color(severity: str):
    return {
        "critical": _C_CRITICAL,
        "high": _C_HIGH,
        "medium": _C_MEDIUM,
        "low": _C_LOW,
        "info": _C_INFO,
    }.get(severity.lower(), _C_INFO)


def _status_label(status: str) -> str:
    return status.upper()


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _build_styles() -> dict:
    base = getSampleStyleSheet()

    styles = {}

    styles["cover_title"] = ParagraphStyle(
        "cover_title",
        fontName="Helvetica-Bold",
        fontSize=28,
        textColor=_C_NAVY,
        spaceAfter=12,
        leading=34,
        alignment=TA_LEFT,
    )
    styles["cover_subtitle"] = ParagraphStyle(
        "cover_subtitle",
        fontName="Helvetica",
        fontSize=13,
        textColor=_C_SLATE,
        spaceAfter=6,
        leading=18,
        alignment=TA_LEFT,
    )
    styles["cover_meta"] = ParagraphStyle(
        "cover_meta",
        fontName="Helvetica",
        fontSize=10,
        textColor=_C_SLATE,
        spaceAfter=4,
        leading=14,
        alignment=TA_LEFT,
    )
    styles["section_heading"] = ParagraphStyle(
        "section_heading",
        fontName="Helvetica-Bold",
        fontSize=14,
        textColor=_C_NAVY,
        spaceBefore=18,
        spaceAfter=6,
        leading=18,
        alignment=TA_LEFT,
    )
    styles["control_heading"] = ParagraphStyle(
        "control_heading",
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=_C_SLATE,
        spaceBefore=4,
        spaceAfter=4,
        leading=16,
        alignment=TA_LEFT,
    )
    styles["label"] = ParagraphStyle(
        "label",
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=_C_SLATE,
        spaceAfter=2,
        leading=13,
    )
    styles["body"] = ParagraphStyle(
        "body",
        fontName="Helvetica",
        fontSize=9,
        textColor=black,
        spaceAfter=6,
        leading=13,
    )
    styles["footer"] = ParagraphStyle(
        "footer",
        fontName="Helvetica",
        fontSize=8,
        textColor=_C_SLATE,
        alignment=TA_CENTER,
    )
    styles["toc_label"] = ParagraphStyle(
        "toc_label",
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=_C_ACCENT,
        spaceAfter=2,
        leading=14,
    )
    return styles


# ---------------------------------------------------------------------------
# Page template callbacks
# ---------------------------------------------------------------------------

def _draw_header_footer(canvas, doc, report_title: str) -> None:
    canvas.saveState()
    w, h = letter
    margin = 0.75 * inch

    # Top rule
    canvas.setStrokeColor(_C_ACCENT)
    canvas.setLineWidth(1.5)
    canvas.line(margin, h - 0.55 * inch, w - margin, h - 0.55 * inch)

    # Header: report title (left) + page number (right)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(_C_SLATE)
    canvas.drawString(margin, h - 0.45 * inch, report_title[:80])
    canvas.drawRightString(w - margin, h - 0.45 * inch, f"Page {doc.page}")

    # Bottom rule
    canvas.setLineWidth(0.5)
    canvas.line(margin, 0.65 * inch, w - margin, 0.65 * inch)
    canvas.setFont("Helvetica", 7)
    canvas.drawCentredString(
        w / 2,
        0.45 * inch,
        "Generated by Nox Agent Runtime - Confidential",
    )
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------

def _build_cover(
    title: str,
    metadata: dict,
    findings: list[dict],
    styles: dict,
) -> list:
    story = []

    story.append(Spacer(1, 1.2 * inch))

    # Navy accent bar at the top of cover content
    story.append(
        Table(
            [[""]],
            colWidths=[7 * inch],
            rowHeights=[6],
            style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), _C_ACCENT)]),
        )
    )
    story.append(Spacer(1, 0.3 * inch))

    story.append(Paragraph(title, styles["cover_title"]))
    story.append(Spacer(1, 0.15 * inch))

    repo_url = metadata.get("repo_url", "N/A")
    scan_date = metadata.get("scan_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    job_id = metadata.get("job_id", "N/A")

    story.append(Paragraph(f"Repository: {repo_url}", styles["cover_subtitle"]))
    story.append(Paragraph(f"Scan Date: {scan_date}", styles["cover_meta"]))
    story.append(Paragraph(f"Job ID: {job_id}", styles["cover_meta"]))
    story.append(Spacer(1, 0.4 * inch))

    story.append(HRFlowable(width="100%", thickness=1, color=_C_LIGHT_BG))
    story.append(Spacer(1, 0.25 * inch))

    # Summary counts
    counts = {"pass": 0, "fail": 0, "partial": 0, "unknown": 0}
    for f in findings:
        counts[f.get("status", "unknown").lower()] = (
            counts.get(f.get("status", "unknown").lower(), 0) + 1
        )

    summary_data = [
        [
            _colored_cell("PASS", _C_PASS, str(counts["pass"])),
            _colored_cell("FAIL", _C_FAIL, str(counts["fail"])),
            _colored_cell("PARTIAL", _C_PARTIAL, str(counts["partial"])),
            _colored_cell("UNKNOWN", _C_UNKNOWN, str(counts["unknown"])),
        ]
    ]
    summary_table = Table(
        summary_data,
        colWidths=[1.6 * inch, 1.6 * inch, 1.6 * inch, 1.6 * inch],
        rowHeights=[0.8 * inch],
    )
    summary_table.setStyle(
        TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("BOX", (0, 0), (-1, -1), 0.5, _C_LIGHT_BG),
        ])
    )
    story.append(summary_table)
    story.append(Spacer(1, 0.5 * inch))

    story.append(
        Paragraph(
            "This report was produced by the Nox Agent Runtime compliance analysis "
            "platform. It reflects an automated assessment of the repository's source "
            "code and configuration against selected SOC 2 Trust Service Criteria. "
            "The assessment is based solely on observable evidence in the repository "
            "and should be reviewed by a qualified auditor before formal use.",
            styles["body"],
        )
    )

    return story


def _colored_cell(label: str, color, count: str) -> Table:
    """A mini table used as a cell content: colored header + large count number."""
    inner = Table(
        [[Paragraph(f'<font color="white"><b>{label}</b></font>', ParagraphStyle(
            "badge", fontName="Helvetica-Bold", fontSize=9,
            textColor=white, alignment=TA_CENTER, leading=12))],
         [Paragraph(f'<font size="20"><b>{count}</b></font>', ParagraphStyle(
            "count", fontName="Helvetica-Bold", fontSize=20,
            textColor=black, alignment=TA_CENTER, leading=24))]],
        colWidths=[1.4 * inch],
        rowHeights=[0.28 * inch, 0.44 * inch],
    )
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), color),
        ("BACKGROUND", (0, 1), (0, 1), _C_LIGHT_BG),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BOX", (0, 0), (-1, -1), 0.5, color),
    ]))
    return inner


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def _build_summary(findings: list[dict], styles: dict) -> list:
    story = []

    story.append(Paragraph("Findings Summary", styles["section_heading"]))
    story.append(HRFlowable(width="100%", thickness=1, color=_C_ACCENT))
    story.append(Spacer(1, 0.15 * inch))

    headers = [
        Paragraph("<b>Control</b>", styles["label"]),
        Paragraph("<b>Title</b>", styles["label"]),
        Paragraph("<b>Status</b>", styles["label"]),
        Paragraph("<b>Severity</b>", styles["label"]),
    ]
    rows = [headers]

    for i, f in enumerate(findings):
        status = f.get("status", "unknown").lower()
        severity = f.get("severity", "info").lower()
        sc = _status_color(status)
        sevc = _severity_color(severity)

        status_para = Paragraph(
            f'<font color="white"><b> {_status_label(status)} </b></font>',
            ParagraphStyle(
                f"st{i}", fontName="Helvetica-Bold", fontSize=8,
                textColor=white, alignment=TA_CENTER, leading=11,
                backColor=sc, borderPadding=(2, 4, 2, 4),
            ),
        )
        sev_para = Paragraph(
            f'<font color="white"><b> {severity.upper()} </b></font>',
            ParagraphStyle(
                f"sev{i}", fontName="Helvetica-Bold", fontSize=8,
                textColor=white, alignment=TA_CENTER, leading=11,
                backColor=sevc, borderPadding=(2, 4, 2, 4),
            ),
        )
        bg = _C_LIGHT_BG if i % 2 == 0 else white
        row = [
            Paragraph(f.get("control", ""), styles["body"]),
            Paragraph(f.get("title", ""), styles["body"]),
            status_para,
            sev_para,
        ]
        rows.append(row)

    col_widths = [0.85 * inch, 3.5 * inch, 1.1 * inch, 1.05 * inch]
    table = Table(rows, colWidths=col_widths, repeatRows=1)

    ts = TableStyle([
        # Header row
        ("BACKGROUND", (0, 0), (-1, 0), _C_NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ROWBACKGROUND", (0, 1), (-1, -1), [_C_LIGHT_BG, white]),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("ALIGN", (2, 0), (3, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.25, HexColor("#d0d3d4")),
        ("BOX", (0, 0), (-1, -1), 1, _C_ACCENT),
    ])
    table.setStyle(ts)

    story.append(table)
    return story


# ---------------------------------------------------------------------------
# Individual finding detail
# ---------------------------------------------------------------------------

def _build_finding(finding: dict, styles: dict) -> list:
    control = finding.get("control", "?")
    title = finding.get("title", "Unknown Control")
    status = finding.get("status", "unknown").lower()
    evidence = finding.get("evidence", "No evidence recorded.").strip()
    gaps = finding.get("gaps", "").strip()
    recommendation = finding.get("recommendation", "").strip()

    sc = _status_color(status)

    # Status badge as a single-cell mini table
    badge = Table(
        [[Paragraph(
            f'<font color="white"><b>  {_status_label(status)}  </b></font>',
            ParagraphStyle(
                "fbadge", fontName="Helvetica-Bold", fontSize=9,
                textColor=white, alignment=TA_CENTER, leading=12,
            ),
        )]],
        colWidths=[0.85 * inch],
        rowHeights=[0.22 * inch],
    )
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), sc),
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 0), (0, 0), 2),
        ("BOTTOMPADDING", (0, 0), (0, 0), 2),
    ]))

    header_row = Table(
        [[
            Paragraph(f"{control} - {title}", styles["control_heading"]),
            badge,
        ]],
        colWidths=[5.7 * inch, 0.9 * inch],
    )
    header_row.setStyle(TableStyle([
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    content = [
        Spacer(1, 0.08 * inch),
        header_row,
        HRFlowable(width="100%", thickness=0.5, color=HexColor("#d0d3d4")),
        Spacer(1, 0.06 * inch),
        Paragraph("<b>Evidence</b>", styles["label"]),
        Paragraph(_safe_text(evidence), styles["body"]),
    ]

    if gaps:
        content.append(Paragraph("<b>Gaps</b>", styles["label"]))
        content.append(Paragraph(_safe_text(gaps), styles["body"]))

    if recommendation:
        content.append(Paragraph("<b>Recommendation</b>", styles["label"]))
        content.append(Paragraph(_safe_text(recommendation), styles["body"]))

    content.append(Spacer(1, 0.1 * inch))

    return [KeepTogether(content[:5])] + content[5:]


def _safe_text(text: str) -> str:
    """Escape XML special chars and replace any stray em dashes for safety."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Replace em dash (U+2014) and en dash (U+2013) with hyphens
    text = text.replace("\u2014", " - ").replace("\u2013", "-")
    return text


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_pdf(
    title: str,
    findings: list[dict],
    metadata: dict | None = None,
) -> str:
    """Render a compliance report PDF and write it as a job artifact.

    Args:
        title: Report title, e.g. "SOC 2 Compliance Report - org/repo".
        findings: List of control assessment dicts. Each must have at minimum:
                  control, title, status (pass|fail|partial|unknown), evidence.
                  Optional: gaps, recommendation, severity.
        metadata: Optional dict with repo_url, scan_date, job_id.

    Returns:
        Absolute path to the written "report.pdf" artifact.
    """
    meta = metadata or {}
    styles = _build_styles()

    buf = BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.85 * inch,
        title=title,
        author="Nox Agent Runtime",
        subject="SOC 2 Compliance Report",
        creator="Nox Agent Runtime (ReportLab)",
    )

    # Capture title for use in the closure
    _title = title

    def _first_page(canvas, doc):
        # Cover page has no header/footer
        canvas.saveState()
        # Navy sidebar strip on left edge of cover
        canvas.setFillColor(_C_NAVY)
        canvas.rect(0, 0, 0.18 * inch, letter[1], fill=1, stroke=0)
        canvas.restoreState()

    def _later_pages(canvas, doc):
        _draw_header_footer(canvas, doc, _title)

    story: list = []

    # --- Cover page ---
    story.extend(_build_cover(title, meta, findings, styles))
    story.append(PageBreak())

    # --- Summary table ---
    story.extend(_build_summary(findings, styles))
    story.append(PageBreak())

    # --- Detailed findings ---
    story.append(Paragraph("Detailed Findings", styles["section_heading"]))
    story.append(HRFlowable(width="100%", thickness=1, color=_C_ACCENT))
    story.append(Spacer(1, 0.1 * inch))

    for finding in findings:
        story.extend(_build_finding(finding, styles))

    doc.build(story, onFirstPage=_first_page, onLaterPages=_later_pages)

    pdf_bytes = buf.getvalue()
    path = write_artifact("report.pdf", pdf_bytes, mime_type="application/pdf")

    logger.info("render_pdf: wrote %d-byte PDF to %s", len(pdf_bytes), path)
    return path
