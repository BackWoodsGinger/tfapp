"""PDF generator for the July 4, 2026 holiday pay manual test plan."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


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
    story.append(Paragraph("Independence Day 2026 — Observed Thursday, July 2", body))
    story.append(Paragraph("Payroll CSV upload workflow · Week ending July 4, 2026", body))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("Overview", h2))
    story.append(
        Paragraph(
            "This plan validates holiday pay for the July 4, 2026 Independence Day "
            "(calendar Saturday, observed Thursday July 2). Use the payroll page "
            "Download template → edit CSV → Upload → Close Payroll flow. "
            "Close payroll on <b>Tuesday, July 7, 2026</b> so the trailing bookend "
            "(Monday July 6) is complete.",
            body,
        )
    )

    story.append(Paragraph("Policy Under Test", h2))
    for item in [
        "Part-time employees are not eligible for holiday pay.",
        "Full-time employees must fully cover the last scheduled shift before and "
        "the first scheduled shift after the observed holiday (work or planned leave).",
        "Any unplanned absence (full or partial) on a bookend shift removes holiday pay.",
        "PTO applies only to missed bookend hours, not to the holiday itself.",
        "Saturday holidays are observed on the preceding Thursday; Sunday holidays "
        "on the following Monday.",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Paragraph("Key Dates", h2))
    key_dates = [
        ["Date", "Day", "Role"],
        ["Jun 28 – Jul 1", "Sun – Wed", "Normal work; Wed Jul 1 = leading bookend"],
        ["Jul 2", "Thu", "Observed holiday — no punches"],
        ["Jul 3", "Fri", "Company off (most); 4-hr shift for Fri-schedule workers"],
        ["Jul 4", "Sat", "Calendar holiday — unscheduled for Mon–Fri staff"],
        ["Jul 6", "Mon", "Trailing bookend (Mon–Thu schedules)"],
        ["Jul 7", "Tue", "Recommended payroll close date"],
    ]
    t = Table(key_dates, colWidths=[1.4 * inch, 0.9 * inch, 3.9 * inch])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
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
    story.append(t)
    story.append(Spacer(1, 0.1 * inch))
    story.append(
        Paragraph(
            "<b>Payroll week under test:</b> Sunday Jun 28 – Saturday Jul 4, 2026 "
            "(week ending <b>2026-07-04</b>).",
            body,
        )
    )

    story.append(Paragraph("Test Employees", h2))
    employees = [
        ["ID", "Profile", "Schedule", "Purpose"],
        ["FT-OK", "Full-time", "Mon–Thu 1st shift", "Should receive holiday pay"],
        ["FT-LEAD", "Full-time", "Mon–Thu", "Unplanned miss Wed Jul 1 → no holiday"],
        ["FT-TRAIL", "Full-time", "Mon–Thu", "Unplanned miss Mon Jul 6 → no holiday"],
        ["FT-PTO", "Full-time", "Mon–Thu", "Planned PTO Wed Jul 1 → still eligible"],
        ["PT", "Part-time", "Mon–Thu", "Never eligible"],
        ["FT-FRI", "Full-time", "Mon–Thu 9 hr + Fri 4 hr", "Holiday Thu; 4 hr Fri Jul 3"],
    ]
    t2 = Table(employees, colWidths=[0.75 * inch, 0.9 * inch, 1.5 * inch, 2.95 * inch])
    t2.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
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
    story.append(t2)
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
    trail = [
        ["Employee", "Mon Jul 6 punches", "Simulates"],
        ["FT-OK, FT-LEAD, FT-PTO, PT, FT-FRI", "Full scheduled shift", "Good trailing attendance"],
        ["FT-TRAIL", "Clear all punch cells", "Unplanned trailing absence"],
    ]
    t3 = Table(trail, colWidths=[2.0 * inch, 1.6 * inch, 2.5 * inch])
    t3.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(t3)
    story.append(
        Paragraph(
            "Monday Jul 6 is in the next payroll week but must exist in the database "
            "before closing the holiday week.",
            body,
        )
    )

    story.append(Paragraph("Step 2 — Upload Holiday Week (Jun 28 – Jul 4)", h2))
    story.append(
        Paragraph(
            "Week Ending <b>2026-07-04</b> → Download template → edit → upload.",
            body,
        )
    )
    step2 = [
        ["Period / day", "CSV edits"],
        ["Mon Jun 29 – Tue Jun 30", "Full shifts for all test employees"],
        ["Wed Jul 1 (leading bookend)", "FT-OK/TRAIL/PT/FRI: full shift · FT-LEAD: empty · FT-PTO: empty if PTO approved"],
        ["Thu Jul 2 (observed holiday)", "Empty punches for everyone"],
        ["Fri Jul 3", "Mon–Thu workers: empty · FT-FRI: 4-hr shift"],
        ["Sat Jul 4", "Empty (unscheduled)"],
    ]
    t4 = Table(step2, colWidths=[1.5 * inch, 4.5 * inch])
    t4.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(t4)
    story.append(
        Paragraph(
            "Empty punch cells delete that day’s time entry (simulates unplanned absence). "
            "Partial misses: keep clock_in, set clock_out early.",
            body,
        )
    )

    story.append(Paragraph("Step 3 — Close Payroll (Tue Jul 7)", h2))
    for item in [
        "Open Payroll → Week Ending 2026-07-04.",
        "Confirm weekly totals (work Mon–Wed; no Jul 2 punches).",
        "Resolve override / lunch review items in the Close Payroll modal.",
        "Close Payroll and save the exported CSV.",
        "Verify Holiday - Paid occurrences on Jul 2 and the holiday hours column.",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Paragraph("Expected Results", h2))
    expected = [
        ["Employee", "Holiday hrs (Jul 2)", "PTO applied", "Notes"],
        ["FT-OK", "~9.0", "0", "Perfect bookends"],
        ["FT-LEAD", "0", "Wed Jul 1 missed time only", "Leading unplanned miss"],
        ["FT-TRAIL", "0", "Mon Jul 6 missed time only", "Trailing unplanned miss"],
        ["FT-PTO", "~9.0", "Wed Jul 1 only (planned)", "Planned leave on bookend OK"],
        ["PT", "0", "Per normal rules", "Part-time ineligible"],
        ["FT-FRI", "~9.0 Thu", "0 on holiday", "4 hr worked Fri Jul 3"],
    ]
    t5 = Table(expected, colWidths=[0.85 * inch, 1.0 * inch, 1.35 * inch, 2.9 * inch])
    t5.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(t5)

    story.append(Paragraph("Timing Checklist", h2))
    checklist = [
        ["When", "Action"],
        ["Before Jul 1", "Create test employees; approve FT-PTO time-off for Wed Jul 1"],
        ["Mon Jul 6", "Upload week ending 2026-07-11 CSV (Mon bookend punches)"],
        ["Mon–Tue Jun 29–Jul 1", "Upload week ending 2026-07-04 CSV"],
        ["Tue Jul 7", "Re-upload Jul 4 CSV if needed; Close Payroll for 2026-07-04"],
        ["After close", "Review CSV holiday column + occurrence records"],
    ]
    t6 = Table(checklist, colWidths=[1.3 * inch, 4.7 * inch])
    t6.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(t6)

    story.append(Paragraph("Optional Follow-Up", h2))
    story.append(
        Paragraph(
            "Close week ending 2026-07-11 to confirm FT-TRAIL’s Mon Jul 6 PTO/exchange "
            "finalizes correctly in its own payroll week.",
            body,
        )
    )

    doc.build(story)
