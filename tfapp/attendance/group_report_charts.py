"""
PNG pie charts for group absence reports.

xhtml2pdf does not reliably render CSS bars or SVG for charts; embedded PNG
(data URI) matches the existing logo pattern and prints in PDFs.
"""
from __future__ import annotations

import base64
from io import BytesIO

# Navy, gray, red, and black palette (RGB)
_SLICE_COLORS = [
    (28, 45, 92),
    (45, 72, 128),
    (68, 98, 158),
    (96, 122, 176),
    (74, 78, 86),
    (108, 112, 118),
    (145, 149, 156),
    (186, 190, 196),
    (152, 36, 42),
    (190, 48, 54),
    (128, 28, 32),
    (212, 72, 72),
    (26, 26, 28),
    (48, 48, 52),
    (68, 68, 72),
    (92, 92, 96),
]

_CHART_BG = (255, 255, 255)
_SLICE_OUTLINE = (255, 255, 255)
_PIE_RIM = (26, 26, 28)
_LEGEND_TEXT = (26, 26, 28)
_LEGEND_MUTED = (88, 90, 94)
_SUPERSAMPLE = 2
_DEFAULT_PIE_DIAMETER = 280
_DEFAULT_CANVAS_WIDTH = 720


def _load_chart_font(size: int):
    from PIL import ImageFont

    candidates = (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def group_pie_png_data_uri(
    segments: list[tuple[str, float]],
    *,
    pie_diameter: int = _DEFAULT_PIE_DIAMETER,
    legend_max_lines: int = 18,
    canvas_width: int = _DEFAULT_CANVAS_WIDTH,
) -> str | None:
    """
    Build a PNG pie chart + compact legend as a data:image/png;base64,... URI.

    ``segments`` is (label, value) per slice; values must be >= 0.
    Zero-value segments are omitted. Returns None if there is nothing to draw.
    """
    from PIL import Image, ImageDraw

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
    legend_line_height = 22
    margin = 20
    legend_gap = 24
    swatch_size = 14
    font_size = 14
    logical_h = max(320, margin * 2 + legend_lines * legend_line_height)
    logical_w = canvas_width

    scale = _SUPERSAMPLE
    w, h = logical_w * scale, logical_h * scale
    img = Image.new("RGB", (w, h), _CHART_BG)
    draw = ImageDraw.Draw(img)

    d = pie_diameter * scale
    cy = h // 2
    cx = margin * scale + d // 2
    bbox = (cx - d // 2, cy - d // 2, cx + d // 2, cy + d // 2)
    rim = max(2, scale * 2)

    start_angle = -90.0
    for i, (_, val) in enumerate(cleaned):
        extent = 360.0 * val / total
        end_angle = start_angle + extent
        color = _SLICE_COLORS[i % len(_SLICE_COLORS)]
        draw.pieslice(
            bbox,
            start_angle,
            end_angle,
            fill=color,
            outline=_SLICE_OUTLINE,
            width=max(2, scale),
        )
        start_angle = end_angle

    draw.ellipse(bbox, outline=_PIE_RIM, width=rim)

    font = _load_chart_font(font_size * scale)
    legend_x = margin * scale + d + legend_gap * scale
    y = margin * scale
    swatch = swatch_size * scale
    text_x_offset = swatch + 8 * scale

    for i in range(legend_lines):
        lab, val = cleaned[i]
        pct = 100.0 * val / total
        color = _SLICE_COLORS[i % len(_SLICE_COLORS)]
        draw.rectangle(
            (legend_x, y, legend_x + swatch, y + swatch),
            fill=color,
            outline=_PIE_RIM,
            width=max(1, scale),
        )
        short = (lab[:36] + "…") if len(lab) > 37 else lab
        if val == int(val):
            txt = f"{short}  {int(val)}  ({pct:.0f}%)"
        else:
            txt = f"{short}  {val:.1f}  ({pct:.0f}%)"
        draw.text((legend_x + text_x_offset, y - scale), txt, fill=_LEGEND_TEXT, font=font)
        y += legend_line_height * scale

    if len(cleaned) > legend_max_lines:
        draw.text(
            (legend_x, y + 2 * scale),
            f"… +{len(cleaned) - legend_max_lines} more",
            fill=_LEGEND_MUTED,
            font=font,
        )

    if scale > 1:
        img = img.resize((logical_w, logical_h), Image.Resampling.LANCZOS)

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
