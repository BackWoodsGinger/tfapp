"""PDF generator for the July 4, 2026 holiday pay manual test plan."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

USABLE_WIDTH = letter[0] - 1.5 * inch  # 0.75" margins each side


def _cell_style(base, *, bold: bool = False) -> ParagraphStyle:
    return ParagraphStyle(
        f"TableCell{'Bold' if bold else ''}",
        parent=base,
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=9,
        leading=11,
        wordWrap="CJK",
    )


def _para(text, style) -> Paragraph:
    if isinstance(text, Paragraph):
        return text
    return Paragraph(str(text).replace("\n", "<br/>"), style)


def _table(data, col_widths, styles, header_rows=1):
    normal = _cell_style(styles["Normal"])
    header = _cell_style(styles["Normal"], bold=True)
    wrapped = []
    for row_idx, row in enumerate(data):
        cell_style = header if row_idx < header_rows else normal
        wrapped.append([_para(cell, cell_style) for cell in row])
    t = Table(wrapped, colWidths=col_widths, repeatRows=header_rows)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#edf2f7")),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def write_holiday_test_plan_pdf(output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="July 4, 2026 Holiday Pay Manual Test Plan",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PlanTitle",
        parent=styles["Heading1"],
        fontSize=18,
        spaceAfter=14,
        textColor=colors.HexColor("#1a365d"),
    )
    h2 = ParagraphStyle(
        "PlanH2",
        parent=styles["Heading2"],
        fontSize=13,
        spaceBefore=14,
        spaceAfter=8,
        textColor=colors.HexColor("#2c5282"),
    )
    body = ParagraphStyle(
        "PlanBody",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        spaceAfter=6,
    )
    bullet = ParagraphStyle(
        "PlanBullet",
        parent=body,
        leftIndent=18,
        bulletIndent=6,
        spaceAfter=4,
    )

    story = []

    story.append(Paragraph("Holiday Pay Manual Test Plan", title_style))
    story.append(Paragraph("Independence Day 2026 — Payroll week ending July 4", body))
    story.append(Paragraph("Holiday week plans · CSV upload · Close payroll", body))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("Overview", h2))
    story.append(
        Paragraph(
            "July 4, 2026 falls on Saturday. Company policy treats this holiday week differently "
            "for <b>4-day</b> (Mon–Thu, no Friday schedule) and <b>5-day</b> (includes Friday) "
            "employees. Before closing payroll, staff must complete the "
            "<b>Holiday week plan</b> for Independence Day 2026 on the Payroll page. "
            "Close payroll on <b>Tuesday, July 7, 2026</b> so the trailing bookend (Monday July 6) "
            "has passed.",
            body,
        )
    )

    story.append(Paragraph("Company schedule rules (this holiday)", h2))
    for item in [
        "<b>4-day employees</b> — Thursday July 2 is the paid holiday (no work). "
        "Holiday pay hours = their prevailing shift length from schedule (typically ~10 hr).",
        "<b>5-day employees</b> — Thursday July 2 is a <b>4-hour work day</b>. "
        "Friday July 3 is closed; paid holiday on July 3 at their prevailing weekday rate (~9 hr).",
        "The holiday plan grid marks <i>which dates</i> are paid (any Holiday pay value &gt; 0). "
        "Actual pay amounts come from each employee's schedule, not the number in the grid.",
        "Bookends use each employee's <b>effective</b> work days during the holiday week "
        "(plan intersected with their real schedule).",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Paragraph("Policy Under Test", h2))
    for item in [
        "Part-time employees are not eligible for holiday pay.",
        "Full-time employees in their first 90 days (hire or service date) are not eligible.",
        "Payroll close is blocked until the Independence Day 2026 holiday week plan is complete.",
        "No complete plan for the holiday week means no holiday pay is created.",
        "Full-time employees must fully cover bookend shifts (work or planned leave).",
        "Any unplanned absence (full or partial) on a bookend shift removes holiday pay.",
        "PTO applies only to missed bookend hours, not to the holiday itself.",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Paragraph("Key Dates", h2))
    story.append(
        _table(
            [
                ["Date", "Day", "4-day template", "5-day template"],
                ["Jun 28 – Jul 1", "Sun – Wed", "Normal work; Wed Jul 1 = leading bookend", "Same"],
                ["Jul 2", "Thu", "Paid holiday (no work)", "Work day (4 hr in plan)"],
                ["Jul 3", "Fri", "Not scheduled / closed", "Paid holiday (no work)"],
                ["Jul 4", "Sat", "Unscheduled", "Unscheduled"],
                ["Jul 6", "Mon", "Trailing bookend", "Trailing bookend"],
                ["Jul 7", "Tue", "Recommended payroll close", "Same"],
            ],
            [0.95 * inch, 0.6 * inch, 2.2 * inch, 2.25 * inch],
            styles,
        )
    )
    story.append(Spacer(1, 0.1 * inch))
    story.append(
        Paragraph(
            "<b>Payroll week under test:</b> Sunday Jun 28 – Saturday Jul 4, 2026 "
            "(week ending <b>2026-07-04</b>).",
            body,
        )
    )

    story.append(Paragraph("Test Employees", h2))
    story.append(
        _table(
            [
                ["ID", "Profile", "Schedule", "Purpose"],
                ["FT-4DAY", "Full-time", "Mon–Thu, 10 hr/day", "Paid holiday Thu Jul 2 (~10 hr from schedule)"],
                ["FT-5DAY", "Full-time", "Mon–Thu 9 hr + Fri 4 hr", "Work Thu Jul 2 (4 hr); holiday Fri Jul 3 (~9 hr)"],
                ["FT-LEAD", "Full-time", "Mon–Thu, 10 hr", "Unplanned miss Wed Jul 1 → no holiday"],
                ["FT-TRAIL", "Full-time", "Mon–Thu, 10 hr", "Unplanned miss Mon Jul 6 → no holiday"],
                ["FT-PTO", "Full-time", "Mon–Thu, 10 hr", "Planned PTO Wed Jul 1 → still eligible"],
                ["FT-NEW", "Full-time", "Mon–Thu, 10 hr", "Hire &lt; 90 days on Jul 2 → no holiday"],
                ["PT", "Part-time", "Mon–Thu", "Never eligible"],
            ],
            [0.7 * inch, 0.8 * inch, 1.35 * inch, 3.15 * inch],
            styles,
        )
    )
    story.append(
        Paragraph(
            "Configure payroll names, schedules, hire dates, and is_part_time in admin before testing. "
            "Approve planned time-off for FT-PTO covering Wed Jul 1 before close.",
            body,
        )
    )

    story.append(Paragraph("Step 0 — Configure Holiday Week Plan", h2))
    story.append(
        Paragraph(
            "Payroll → <b>Holiday week plans</b> → Independence Day (2026). "
            "Set work and holiday-pay columns for the 4-day and 5-day templates. "
            "Use any value &gt; 0 in Holiday pay to mark a paid day; amounts are calculated from "
            "each employee's schedule at close.",
            body,
        )
    )
    story.append(
        _table(
            [
                ["Date", "4-day: Work", "4-day: Holiday pay", "5-day: Work", "5-day: Holiday pay"],
                ["Thu Jul 2", "0", "&gt; 0 (marks paid)", "4", "0"],
                ["Fri Jul 3", "0", "0", "0", "&gt; 0 (marks paid)"],
                ["Other days", "Per normal template", "0 unless paid", "Per normal template", "0 unless paid"],
            ],
            [0.85 * inch, 0.95 * inch, 1.15 * inch, 0.95 * inch, 1.15 * inch],
            styles,
        )
    )
    story.append(
        Paragraph(
            "Plan must show <b>Complete</b> before Close Payroll is allowed for week ending 2026-07-04.",
            body,
        )
    )

    story.append(Paragraph("Step 1 — Seed Trailing Bookend (Mon Jul 6)", h2))
    story.append(
        Paragraph(
            "Payroll → Week Ending <b>2026-07-11</b> → Download template. "
            "Edit Monday 2026-07-06 rows only, then upload.",
            body,
        )
    )
    story.append(
        _table(
            [
                ["Employee", "Mon Jul 6 punches", "Simulates"],
                ["FT-4DAY, FT-5DAY, FT-LEAD, FT-PTO, PT", "Full scheduled shift", "Good trailing attendance"],
                ["FT-TRAIL", "Clear all punch cells", "Unplanned trailing absence"],
            ],
            [1.85 * inch, 1.55 * inch, 2.6 * inch],
            styles,
        )
    )

    story.append(Paragraph("Step 2 — Upload Holiday Week (Jun 28 – Jul 4)", h2))
    story.append(
        _table(
            [
                ["Day", "4-day employees", "5-day (FT-5DAY)"],
                [
                    "Wed Jul 1 (leading bookend)",
                    "FT-4DAY/TRAIL/PT: full shift · FT-LEAD: empty · FT-PTO: empty if PTO approved",
                    "Full Wed shift per schedule",
                ],
                ["Thu Jul 2", "Empty — paid holiday", "4 hr shift punches"],
                ["Fri Jul 3", "Empty (not on schedule)", "Empty — expect holiday pay from plan + schedule"],
                ["Mon–Tue, Sat", "Per schedule / empty as needed", "Same"],
            ],
            [1.05 * inch, 2.95 * inch, 2.95 * inch],
            styles,
        )
    )
    story.append(
        Paragraph(
            "Empty punch cells delete that day's time entry (simulates unplanned absence).",
            body,
        )
    )

    story.append(Paragraph("Step 3 — Close Payroll (Tue Jul 7)", h2))
    for item in [
        "Open Payroll → Week Ending 2026-07-04.",
        "Confirm no holiday-plan warning banner (plan must be complete).",
        "Resolve override / lunch review items in the Close Payroll modal.",
        "Close Payroll and save the exported CSV.",
        "Verify Holiday - Paid occurrences: correct <b>date</b> and <b>hours</b> per employee schedule.",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Paragraph("Expected Results", h2))
    story.append(
        _table(
            [
                ["Employee", "Holiday pay", "Worked in week", "Notes"],
                ["FT-4DAY", "~10 hr on Jul 2", "Mon–Wed only", "From schedule; perfect bookends"],
                ["FT-5DAY", "~9 hr on Jul 3", "Mon–Wed + 4 hr Thu Jul 2", "Thu worked; Fri paid holiday"],
                ["FT-LEAD", "0", "As entered", "Leading unplanned miss on Wed Jul 1"],
                ["FT-TRAIL", "0", "As entered", "Trailing unplanned miss on Mon Jul 6"],
                ["FT-PTO", "~10 hr on Jul 2", "Mon–Tue + PTO Wed", "Planned bookend OK"],
                ["FT-NEW", "0", "As entered", "Under 90 days on holiday date"],
                ["PT", "0", "Per schedule", "Part-time ineligible"],
            ],
            [0.75 * inch, 1.15 * inch, 1.35 * inch, 2.75 * inch],
            styles,
        )
    )

    story.append(Paragraph("Timing Checklist", h2))
    story.append(
        _table(
            [
                ["When", "Action"],
                ["Before testing", "Complete Independence Day 2026 holiday week plan on Payroll page"],
                ["Before Jul 1", "Create test employees; approve FT-PTO time-off for Wed Jul 1"],
                ["Mon Jul 6", "Upload week ending 2026-07-11 CSV (Mon bookend punches)"],
                ["Mon–Tue Jun 29–Jul 1", "Upload week ending 2026-07-04 CSV"],
                ["Tue Jul 7", "Re-upload Jul 4 CSV if needed; Close Payroll for 2026-07-04"],
                ["After close", "Compare Holiday - Paid dates/hours to Expected Results"],
            ],
            [1.25 * inch, USABLE_WIDTH - 1.25 * inch],
            styles,
        )
    )

    doc.build(story)
