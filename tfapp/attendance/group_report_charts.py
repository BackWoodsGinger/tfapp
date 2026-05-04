"""
PNG pie charts for group absence reports.

xhtml2pdf does not reliably render CSS bars or SVG for charts; embedded PNG
(data URI) matches the existing logo pattern and prints in PDFs.
"""
from __future__ import annotations

import base64
from io import BytesIO

# Distinct slice colors (RGB)
_SLICE_COLORS = [
    (68, 114, 196),
    (237, 125, 49),
    (165, 165, 165),
    (255, 192, 0),
    (91, 155, 213),
    (112, 173, 71),
    (142, 124, 195),
    (38, 68, 120),
    (158, 72, 14),
    (99, 99, 99),
]


def group_pie_png_data_uri(
    segments: list[tuple[str, float]],
    *,
    pie_diameter: int = 168,
    legend_max_lines: int = 18,
) -> str | None:
    """
    Build a PNG pie chart + compact legend as a data:image/png;base64,... URI.

    ``segments`` is (label, value) per slice; values must be >= 0.
    Zero-value segments are omitted. Returns None if there is nothing to draw.
    """
    from PIL import Image, ImageDraw, ImageFont

    cleaned: list[tuple[str, float]] = []
    for lab, val in segments:
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if v > 0:
            cleaned.append((str(lab), v))
    total = sum(v for _, v in cleaned)
    if total <= 0 or not cleaned:
        return None

    legend_lines = min(len(cleaned), legend_max_lines)
    h = min(420, max(200, 24 + legend_lines * 14))
    w = 360
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    margin = 10
    d = pie_diameter
    cy = h // 2
    cx = margin + d // 2
    bbox = (cx - d // 2, cy - d // 2, cx + d // 2, cy + d // 2)

    start_angle = -90.0
    for i, (_, val) in enumerate(cleaned):
        extent = 360.0 * val / total
        end_angle = start_angle + extent
        color = _SLICE_COLORS[i % len(_SLICE_COLORS)]
        draw.pieslice(bbox, start_angle, end_angle, fill=color, outline=(55, 55, 55), width=1)
        start_angle = end_angle

    font = ImageFont.load_default()

    legend_x = margin + d + 12
    y = 12
    for i in range(legend_lines):
        lab, val = cleaned[i]
        pct = 100.0 * val / total
        color = _SLICE_COLORS[i % len(_SLICE_COLORS)]
        draw.rectangle((legend_x, y, legend_x + 9, y + 9), fill=color, outline=(40, 40, 40))
        short = (lab[:30] + "…") if len(lab) > 31 else lab
        if val == int(val):
            txt = f"{short}  {int(val)}  ({pct:.0f}%)"
        else:
            txt = f"{short}  {val:.1f}  ({pct:.0f}%)"
        draw.text((legend_x + 14, y - 1), txt, fill=(20, 20, 20), font=font)
        y += 14

    if len(cleaned) > legend_max_lines:
        draw.text((legend_x, y + 2), f"… +{len(cleaned) - legend_max_lines} more", fill=(80, 80, 80), font=font)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def group_report_pie_pair_uris(group_rows: list) -> tuple[str | None, str | None]:
    """(hours pie data URI, records pie data URI) for dashboard + PDF; (None, None) if empty."""
    if not group_rows:
        return None, None
    hours = group_pie_png_data_uri([(r["group_label"], float(r["total_hours"])) for r in group_rows])
    records = group_pie_png_data_uri([(r["group_label"], float(r["occurrence_count"])) for r in group_rows])
    return hours, records
