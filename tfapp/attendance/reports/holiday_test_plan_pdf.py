"""PDF generator for the July 4, 2026 holiday pay manual test plan."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _table(data, col_widths, header_rows=1):
    t = Table(data, colWidths=col_widths)
    style = [
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#edf2f7")),
        ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    t.setStyle(TableStyle(style))
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
    warn = ParagraphStyle(
        "PlanWarn",
        parent=body,
        textColor=colors.HexColor("#9b2c2c"),
        backColor=colors.HexColor("#fff5f5"),
        borderPadding=6,
        spaceAfter=8,
    )

    story = []

    story.append(Paragraph("Holiday Pay Manual Test Plan", title_style))
    story.append(Paragraph("Independence Day 2026 — Observed Thursday, July 2", body))
    story.append(Paragraph("Payroll CSV upload workflow · Week ending July 4, 2026", body))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("Overview", h2))
    story.append(
        Paragraph(
            "July 4, 2026 falls on Saturday. The company observes the holiday on "
            "<b>Thursday, July 2</b>. How that day is treated depends on the employee’s "
            "regular schedule. Use the payroll page Download template → edit CSV → Upload → "
            "Close Payroll flow. Close payroll on <b>Tuesday, July 7, 2026</b> so the "
            "trailing bookend (Monday July 6) is complete.",
            body,
        )
    )

    story.append(Paragraph("Company schedule rules (this holiday)", h2))
    for item in [
        "<b>Mon–Thu (4-day) employees</b> — normally 10-hour days Mon–Thu. "
        "Thursday July 2 is the paid holiday: no work, holiday pay for their Thursday schedule.",
        "<b>5-day employees</b> — normally 9 hours Mon–Thu and 4 hours Friday. "
        "Thursday July 2 is a <b>work day</b> (4-hour shift). Friday July 3 the business "
        "is closed entirely; they receive <b>9 hours</b> of holiday pay (their usual "
        "weekday rate, not the 4-hour Friday shift).",
        "All other full-time holiday rules still apply: bookend attendance, part-time "
        "exclusion, unplanned absence on bookend days removes holiday pay.",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Paragraph("Policy Under Test", h2))
    for item in [
        "Part-time employees are not eligible for holiday pay.",
        "Full-time employees must fully cover the last scheduled shift before and "
        "the first scheduled shift after the paid holiday (work or planned leave).",
        "Any unplanned absence (full or partial) on a bookend shift removes holiday pay.",
        "PTO applies only to missed bookend hours, not to the holiday itself.",
        "Saturday calendar holidays are observed on the preceding Thursday; Sunday "
        "holidays on the following Monday.",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Paragraph("Key Dates", h2))
    story.append(
        _table(
            [
                ["Date", "Day", "4-day (Mon–Thu 10 hr)", "5-day (9 hr + Fri 4 hr)"],
                ["Jun 28 – Jul 1", "Sun – Wed", "Normal work; Wed Jul 1 = leading bookend", "Same"],
                [
                    "Jul 2",
                    "Thu",
                    "Observed holiday — no punches; paid holiday",
                    "Work 4-hour shift (normal short day moved to Thu)",
                ],
                [
                    "Jul 3",
                    "Fri",
                    "Business closed — no punches (not on their schedule)",
                    "Business closed — no punches; 9 hr holiday pay expected",
                ],
                ["Jul 4", "Sat", "Unscheduled", "Unscheduled"],
                ["Jul 6", "Mon", "Trailing bookend (4-day schedules)", "Trailing bookend"],
                ["Jul 7", "Tue", "Recommended payroll close date", "Same"],
            ],
            [1.0 * inch, 0.65 * inch, 2.15 * inch, 2.2 * inch],
        )
    )
    story.append(Spacer(1, 0.1 * inch))
    story.append(
        Paragraph(
            "<b>Payroll week under test:</b> Sunday Jun 28 – Saturday Jul 4, 2026 "
            "(week ending <b>2026-07-04</b>). Friday July 3 is inside this week.",
            body,
        )
    )

    story.append(Paragraph("Test Employees", h2))
    story.append(
        _table(
            [
                ["ID", "Profile", "Schedule", "Purpose"],
                ["FT-4DAY", "Full-time", "Mon–Thu, 10 hr/day", "Paid holiday Thu Jul 2 (~10 hr)"],
                ["FT-5DAY", "Full-time", "Mon–Thu 9 hr + Fri 4 hr", "Work Thu Jul 2 (4 hr); holiday Fri Jul 3 (~9 hr)"],
                ["FT-LEAD", "Full-time", "Mon–Thu, 10 hr", "Unplanned miss Wed Jul 1 → no holiday"],
                ["FT-TRAIL", "Full-time", "Mon–Thu, 10 hr", "Unplanned miss Mon Jul 6 → no holiday"],
                ["FT-PTO", "Full-time", "Mon–Thu, 10 hr", "Planned PTO Wed Jul 1 → still eligible"],
                ["PT", "Part-time", "Mon–Thu", "Never eligible"],
            ],
            [0.75 * inch, 0.85 * inch, 1.45 * inch, 2.95 * inch],
        )
    )
    story.append(
        Paragraph(
            "Configure payroll names, schedules, and is_part_time in admin before testing. "
            "Approve a planned time-off request for FT-PTO covering Wed Jul 1 before close.",
            body,
        )
    )

    story.append(Paragraph("Step 1 — Seed Trailing Bookend (Mon Jul 6)", h2))
    story.append(
        Paragraph(
            "Payroll page → Week Ending <b>2026-07-11</b> → Download template. "
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
            [2.0 * inch, 1.6 * inch, 2.5 * inch],
        )
    )

    story.append(Paragraph("Step 2 — Upload Holiday Week (Jun 28 – Jul 4)", h2))
    story.append(
        _table(
            [
                ["Day", "4-day employees (FT-4DAY, FT-LEAD, FT-TRAIL, FT-PTO, PT)", "5-day (FT-5DAY)"],
                ["Mon Jun 29 – Tue Jun 30", "Full 10 hr shifts", "Full shifts per schedule"],
                [
                    "Wed Jul 1 (leading bookend)",
                    "FT-4DAY/TRAIL/PT: full shift · FT-LEAD: empty · FT-PTO: empty if PTO approved",
                    "Full Wed shift (9 hr)",
                ],
                ["Thu Jul 2", "Empty — paid holiday", "4 hr shift punches (work day)"],
                ["Fri Jul 3", "Empty (not scheduled; site closed)", "Empty — site closed; expect 9 hr holiday pay"],
                ["Sat Jul 4", "Empty", "Empty"],
            ],
            [1.1 * inch, 2.45 * inch, 2.45 * inch],
        )
    )
    story.append(
        Paragraph(
            "Empty punch cells delete that day’s time entry (simulates unplanned absence).",
            body,
        )
    )

    story.append(Paragraph("Step 3 — Close Payroll (Tue Jul 7)", h2))
    for item in [
        "Open Payroll → Week Ending 2026-07-04.",
        "Resolve override / lunch review items in the Close Payroll modal.",
        "Close Payroll and save the exported CSV.",
        "Verify Holiday - Paid occurrences and the holiday hours column per employee.",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Paragraph("Expected Results (company policy)", h2))
    story.append(
        _table(
            [
                ["Employee", "Holiday pay", "Worked in week", "PTO applied", "Notes"],
                ["FT-4DAY", "~10 hr on Jul 2", "Mon–Wed only", "0", "Perfect bookends"],
                ["FT-5DAY", "~9 hr on Jul 3", "Mon–Wed + 4 hr Thu Jul 2", "0", "Thu worked; Fri closed"],
                ["FT-LEAD", "0", "As entered", "Wed Jul 1 missed time", "Leading unplanned miss"],
                ["FT-TRAIL", "0", "As entered", "Mon Jul 6 missed time", "Trailing unplanned miss"],
                ["FT-PTO", "~10 hr on Jul 2", "Mon–Tue + PTO Wed", "Wed Jul 1 only (planned)", "Planned bookend OK"],
                ["PT", "0", "Per schedule", "Per normal rules", "Part-time ineligible"],
            ],
            [0.8 * inch, 1.05 * inch, 1.0 * inch, 1.0 * inch, 2.15 * inch],
        )
    )

    story.append(Paragraph("Current application behavior (code gap)", h2))
    story.append(
        Paragraph(
            "<b>The app today does not fully implement the 5-day schedule rules above.</b> "
            "Review this section when interpreting test results.",
            warn,
        )
    )
    for item in [
        "Observed Independence Day is a single date — <b>Thursday July 2</b> for everyone "
        "(Saturday → Thursday rule). There is no separate July 3 holiday record.",
        "Holiday pay is created only on the observed date, for hours equal to that day’s "
        "<b>scheduled</b> shift. Mon–Thu 10 hr employees should match policy (~10 hr on Jul 2).",
        "5-day employees are scheduled 9 hr on Thursday: the app will try to grant ~9 hr "
        "holiday pay on <b>Jul 2</b> if bookends pass — not 9 hr on <b>Jul 3</b> as policy requires.",
        "If FT-5DAY works 4 hr on Jul 2, Jul 2 is not a bookend day, but empty punches on "
        "scheduled <b>Jul 3 (Fri)</b> may count as an unplanned miss on the trailing bookend "
        "for the Jul 2 holiday — which can <b>deny</b> holiday pay entirely.",
        "Friday July 3 site closure for 5-day staff requires either a second holiday date, "
        "schedule-aware holiday placement, or manual Holiday - Paid occurrences until code is extended.",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Paragraph("Timing Checklist", h2))
    story.append(
        _table(
            [
                ["When", "Action"],
                ["Before Jul 1", "Create test employees; approve FT-PTO time-off for Wed Jul 1"],
                ["Mon Jul 6", "Upload week ending 2026-07-11 CSV (Mon bookend punches)"],
                ["Mon–Tue Jun 29–Jul 1", "Upload week ending 2026-07-04 CSV"],
                ["Tue Jul 7", "Re-upload Jul 4 CSV if needed; Close Payroll for 2026-07-04"],
                ["After close", "Compare results to Expected vs Current behavior sections"],
            ],
            [1.3 * inch, 4.7 * inch],
        )
    )

    doc.build(story)
