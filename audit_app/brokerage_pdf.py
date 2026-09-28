"""Branded, paginated PDF for the brokerage audit statistics view."""

from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo


def build_brokerage_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    navy = colors.HexColor("#133657")
    blue = colors.HexColor("#28567e")
    muted = colors.HexColor("#637589")
    pale = colors.HexColor("#f1f5f9")
    width, height = landscape(letter)
    generated = datetime.now(ZoneInfo(report["timezone"]))
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=(width, height), leftMargin=44, rightMargin=44,
                            topMargin=128, bottomMargin=86, title="Brokerage audit statistics",
                            author="Cornerstone Association of REALTORS")

    heading = ParagraphStyle("heading", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=navy, spaceAfter=5)
    subtitle = ParagraphStyle("subtitle", fontName="Helvetica", fontSize=9.5, leading=14, textColor=muted)
    card_label = ParagraphStyle("card-label", fontName="Helvetica-Bold", fontSize=7.5, leading=11, textColor=muted)
    card_value = ParagraphStyle("card-value", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=navy)
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=9, leading=12, textColor=navy)
    cell_right = ParagraphStyle("cell-right", parent=cell, alignment=TA_RIGHT)
    cell_head = ParagraphStyle("cell-head", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
                               textColor=colors.white, alignment=TA_LEFT)
    cell_head_right = ParagraphStyle("cell-head-right", parent=cell_head, alignment=TA_RIGHT)
    note = ParagraphStyle("note", fontName="Helvetica", fontSize=8.5, leading=12, textColor=muted)

    logo = Path(__file__).parent / "static" / "cornerstone-logo-white.png"

    def decorate(canvas, pdf):
        canvas.saveState()
        canvas.setFillColor(navy)
        canvas.rect(0, height - 106, width, 106, fill=1, stroke=0)
        canvas.drawImage(str(logo), 39, height - 93, width=252, height=82, mask="auto", preserveAspectRatio=True)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 19)
        canvas.drawString(330, height - 45, "Brokerage audit statistics")
        canvas.setFont("Helvetica", 10)
        canvas.drawString(330, height - 65, f'{report["period_label"].title()}  |  {report["start"]:%b %d, %Y} - {report["end"]:%b %d, %Y}')
        canvas.setFont("Helvetica", 8)
        canvas.drawString(330, height - 81, f'Generated {generated:%b %d, %Y at %I:%M %p} {generated:%Z}')
        canvas.setStrokeColor(colors.HexColor("#d8e1e9"))
        canvas.line(44, 68, width - 44, 68)
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(44, 52, "MLS Audit Desk  |  Internal management report")
        page = f'Page {pdf.page}'
        canvas.drawRightString(width - 44, 52, page)
        canvas.restoreState()

    def rate_text(value):
        return f"{value:.1f}%" if value is not None else "N/A"

    cards = [
        ("LISTINGS CONSIDERED", f'{report["total"]:,}'),
        ("AUDITED LISTINGS", f'{report["audited"]:,}'),
        ("AUDIT PERCENTAGE", rate_text(report["percentage"])),
        ("COMPLETED AUDITS", f'{report["completed"]:,}'),
        ("PASS RATE", rate_text(report["pass_rate"])),
        ("FAIL RATE", rate_text(report["fail_rate"])),
    ]
    coverage = (f'Available app records begin {report["first_recorded"]:%b %d, %Y}. Earlier dates in this selected period have no app history.'
                if report["first_recorded"] and report["first_recorded"] > report["start"] else
                "The selected period is covered by available app history.")
    story = [Paragraph("Audit selection by brokerage", heading),
             Paragraph("Unique new Active listings first processed by MLS Audit Desk during the selected period.", subtitle),
             Spacer(1, 14)]
    card_table = Table([[Paragraph(label, card_label) for label, _ in cards],
                        [Paragraph(value, card_value) for _, value in cards]],
                       colWidths=[704 / 6] * 6, rowHeights=[20, 39])
    card_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), pale),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e1e9")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e1e9")),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.extend([card_table, Spacer(1, 10), Paragraph(coverage +
                  f' {report["completed"]:,} of {report["audited"]:,} selected audits have a recorded result. Includes manual test runs.', note),
                  Spacer(1, 16), Paragraph("Brokerage detail", heading),
                  Paragraph("Ordered by listing volume. Branches with the same brokerage name are combined.", subtitle),
                  Spacer(1, 10)])

    data = [[Paragraph("BROKERAGE", cell_head), Paragraph("LISTINGS", cell_head_right),
             Paragraph("AUDITED", cell_head_right), Paragraph("AUDIT %", cell_head_right),
             Paragraph("PASSED", cell_head_right), Paragraph("PASS RATE", cell_head_right),
             Paragraph("FAILED", cell_head_right), Paragraph("FAIL RATE", cell_head_right)]]
    for row in report["rows"]:
        name = escape(row["name"])
        if row["branch_count"] > 1:
            name += f'<br/><font size="7" color="#637589">{row["branch_count"]} branches combined</font>'
        data.append([Paragraph(name, cell), Paragraph(f'{row["listings"]:,}', cell_right),
                     Paragraph(f'{row["audited"]:,}', cell_right), Paragraph(rate_text(row["percentage"]), cell_right),
                     Paragraph(f'{row["passed"]:,}', cell_right), Paragraph(rate_text(row["pass_rate"]), cell_right),
                     Paragraph(f'{row["failed"]:,}', cell_right), Paragraph(rate_text(row["fail_rate"]), cell_right)])
    if not report["rows"]:
        data.append([Paragraph("No listings were recorded in this period.", cell)] + [""] * 7)
    table = Table(data, colWidths=[263] + [63] * 7, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), blue),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fb")]),
        ("LINEBELOW", (0, 0), (-1, 0), 1, blue),
        ("LINEBELOW", (0, 1), (-1, -1), 0.35, colors.HexColor("#dfe5eb")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.extend([table, Spacer(1, 14)])

    story.append(Paragraph("Method: Audited listings have a saved audit selection, regardless of email delivery. "
                           "Audit percentage is audited / listings considered. Pass and fail rates are passed or failed / completed audits; pending audits are excluded. Includes manual test runs. "
                           f'{coverage} This is not a count of every listing in the MLS.', note))
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return output.getvalue()
