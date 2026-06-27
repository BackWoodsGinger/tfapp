"""
PNG charts for group absence reports (dashboard preview + PDF).

xhtml2pdf does not reliably render CSS bars or SVG; embedded PNG data URIs print in PDFs.
"""
from __future__ import annotations

import base64
from io import BytesIO

# Navy, gray, red, black palette (RGB)
_NAVY = (28, 45, 92)
_NAVY_MID = (68, 98, 158)
_GRAY = (108, 112, 118)
_GRAY_LT = (186, 190, 196)
_RED = (190, 48, 54)
_RED_DK = (128, 28, 32)
_BLACK = (26, 26, 28)

_SLICE_COLORS = [
    _NAVY,
    (45, 72, 128),
    _NAVY_MID,
    (96, 122, 176),
    (74, 78, 86),
    _GRAY,
    (145, 149, 156),
    _GRAY_LT,
    _RED_DK,
    _RED,
    (212, 72, 72),
    _BLACK,
    (48, 48, 52),
    (68, 68, 72),
    (92, 92, 96),
]

_SERIES_COLORS = {
    "tardy": _RED,
    "early": (237, 125, 49),
    "other": _NAVY_MID,
    "planned": _NAVY,
    "unplanned": _RED,
    "full_time": _NAVY,
    "part_time": _GRAY,
}

_CHART_BG = (255, 255, 255)
_SLICE_OUTLINE = (255, 255, 255)
_PIE_RIM = _BLACK
_LEGEND_TEXT = _BLACK
_LEGEND_MUTED = (88, 90, 94)
_SUPERSAMPLE = 2

_PROFILES = {
    "dashboard": {"canvas_width": 520, "pie_diameter": 200, "legend_max_lines": 14},
    "pdf": {"canvas_width": 720, "pie_diameter": 280, "legend_max_lines": 18},
}


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


def _png_data_uri(img) -> str:
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _downscale(img, logical_w: int, logical_h: int, scale: int):
    if scale > 1:
        from PIL import Image

        return img.resize((logical_w, logical_h), Image.Resampling.LANCZOS)
    return img


def group_pie_png_data_uri(
    segments: list[tuple[str, float]],
    *,
    profile: str = "dashboard",
    layout: str = "vertical",
) -> str | None:
    """Pie chart; vertical layout stacks legend under the pie (fits narrow columns)."""
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

    opts = _PROFILES.get(profile, _PROFILES["dashboard"])
    canvas_width = opts["canvas_width"]
    pie_diameter = opts["pie_diameter"]
    legend_max_lines = opts["legend_max_lines"]

    legend_lines = min(len(cleaned), legend_max_lines)
    legend_line_height = 20
    margin = 16
    font_size = 13
    scale = _SUPERSAMPLE

    if layout == "vertical":
        pie_block = pie_diameter + margin * 2
        legend_block = margin + legend_lines * legend_line_height + 12
        logical_w = canvas_width
        logical_h = pie_block + legend_block
        cx = logical_w // 2
        cy = margin + pie_diameter // 2
        legend_x = margin
        legend_y_start = pie_block
    else:
        logical_h = max(300, margin * 2 + legend_lines * legend_line_height)
        logical_w = canvas_width
        cx = margin + pie_diameter // 2
        cy = logical_h // 2
        legend_x = margin + pie_diameter + 20
        legend_y_start = margin

    w, h = logical_w * scale, logical_h * scale
    img = Image.new("RGB", (w, h), _CHART_BG)
    draw = ImageDraw.Draw(img)

    d = pie_diameter * scale
    bbox = (
        cx * scale - d // 2,
        cy * scale - d // 2,
        cx * scale + d // 2,
        cy * scale + d // 2,
    )

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
    draw.ellipse(bbox, outline=_PIE_RIM, width=max(2, scale * 2))

    font = _load_chart_font(font_size * scale)
    swatch = 12 * scale
    y = legend_y_start * scale
    for i in range(legend_lines):
        lab, val = cleaned[i]
        pct = 100.0 * val / total
        color = _SLICE_COLORS[i % len(_SLICE_COLORS)]
        draw.rectangle(
            (legend_x * scale, y, legend_x * scale + swatch, y + swatch),
            fill=color,
            outline=_PIE_RIM,
            width=max(1, scale),
        )
        short = (lab[:32] + "…") if len(lab) > 33 else lab
        val_txt = str(int(val)) if val == int(val) else f"{val:.1f}"
        txt = f"{short}  {val_txt}  ({pct:.0f}%)"
        draw.text((legend_x * scale + swatch + 6 * scale, y - scale), txt, fill=_LEGEND_TEXT, font=font)
        y += legend_line_height * scale

    if len(cleaned) > legend_max_lines:
        draw.text(
            (legend_x * scale, y + 2 * scale),
            f"… +{len(cleaned) - legend_max_lines} more",
            fill=_LEGEND_MUTED,
            font=font,
        )

    return _png_data_uri(_downscale(img, logical_w, logical_h, scale))


def donut_png_data_uri(
    segments: list[tuple[str, float]],
    *,
    profile: str = "dashboard",
) -> str | None:
    """Two- or few-slice donut for planned vs unplanned, FT vs PT, etc."""
    from PIL import Image, ImageDraw

    cleaned = [(str(l), float(v)) for l, v in segments if float(v) > 0]
    total = sum(v for _, v in cleaned)
    if total <= 0 or not cleaned:
        return None

    opts = _PROFILES.get(profile, _PROFILES["dashboard"])
    logical_w = min(opts["canvas_width"], 360)
    pie_diameter = min(opts["pie_diameter"], 180)
    scale = _SUPERSAMPLE
    margin = 20
    logical_h = pie_diameter + margin * 2 + len(cleaned) * 22 + 8
    cx = logical_w // 2
    cy = margin + pie_diameter // 2

    w, h = logical_w * scale, logical_h * scale
    img = Image.new("RGB", (w, h), _CHART_BG)
    draw = ImageDraw.Draw(img)
    d = pie_diameter * scale
    bbox = (cx * scale - d // 2, cy * scale - d // 2, cx * scale + d // 2, cy * scale + d // 2)
    inner = int(d * 0.52)

    start_angle = -90.0
    for i, (_, val) in enumerate(cleaned):
        extent = 360.0 * val / total
        end_angle = start_angle + extent
        color = _SLICE_COLORS[i % len(_SLICE_COLORS)]
        draw.pieslice(bbox, start_angle, end_angle, fill=color, outline=_SLICE_OUTLINE, width=max(2, scale))
        start_angle = end_angle

    draw.ellipse(bbox, outline=_PIE_RIM, width=max(2, scale))
    draw.ellipse(
        (cx * scale - inner // 2, cy * scale - inner // 2, cx * scale + inner // 2, cy * scale + inner // 2),
        fill=_CHART_BG,
        outline=_PIE_RIM,
        width=max(1, scale),
    )

    font = _load_chart_font(13 * scale)
    y = (margin + pie_diameter + 12) * scale
    for i, (lab, val) in enumerate(cleaned):
        pct = 100.0 * val / total
        color = _SLICE_COLORS[i % len(_SLICE_COLORS)]
        sw = 12 * scale
        draw.rectangle((margin * scale, y, margin * scale + sw, y + sw), fill=color, outline=_PIE_RIM)
        val_txt = str(int(val)) if val == int(val) else f"{val:.1f}"
        draw.text(
            (margin * scale + sw + 6 * scale, y - scale),
            f"{lab}  {val_txt}  ({pct:.0f}%)",
            fill=_LEGEND_TEXT,
            font=font,
        )
        y += 22 * scale

    return _png_data_uri(_downscale(img, logical_w, logical_h, scale))


def horizontal_bar_chart_png_data_uri(
    rows: list[tuple[str, float]],
    *,
    profile: str = "dashboard",
    unit: str = "",
    max_value: float | None = None,
) -> str | None:
    """Horizontal bars — absence rate %, predicted %, hours by group."""
    from PIL import Image, ImageDraw

    cleaned = [(str(l), float(v)) for l, v in rows if float(v) > 0]
    if not cleaned:
        return None

    opts = _PROFILES.get(profile, _PROFILES["dashboard"])
    logical_w = opts["canvas_width"]
    row_h = 26
    margin = 16
    label_w = 140
    bar_area = logical_w - margin * 2 - label_w - 48
    logical_h = margin * 2 + len(cleaned) * row_h
    scale = _SUPERSAMPLE
    peak = max_value if max_value and max_value > 0 else max(v for _, v in cleaned)
    if peak <= 0:
        peak = 1.0

    img = Image.new("RGB", (logical_w * scale, logical_h * scale), _CHART_BG)
    draw = ImageDraw.Draw(img)
    font = _load_chart_font(12 * scale)

    y = margin * scale
    for i, (lab, val) in enumerate(cleaned):
        color = _SLICE_COLORS[i % len(_SLICE_COLORS)]
        short = (lab[:18] + "…") if len(lab) > 19 else lab
        draw.text((margin * scale, y + 4 * scale), short, fill=_LEGEND_TEXT, font=font)
        bx = (margin + label_w) * scale
        bw = max(4 * scale, int(bar_area * scale * val / peak))
        draw.rectangle((bx, y + 4 * scale, bx + bw, y + 18 * scale), fill=color, outline=_PIE_RIM)
        suffix = unit or ""
        val_txt = f"{val:.1f}{suffix}" if isinstance(val, float) and val != int(val) else f"{int(val)}{suffix}"
        draw.text((bx + bw + 6 * scale, y + 4 * scale), val_txt, fill=_LEGEND_TEXT, font=font)
        y += row_h * scale

    return _png_data_uri(_downscale(img, logical_w, logical_h, scale))


def stacked_horizontal_bar_png_data_uri(
    groups: list[str],
    series: list[tuple[str, list[float]]],
    *,
    profile: str = "dashboard",
) -> str | None:
    """Stacked horizontal bars per group (tardy / early / other hours)."""
    from PIL import Image, ImageDraw

    if not groups or not series:
        return None
    totals = [sum(series[j][1][i] for j in range(len(series))) for i in range(len(groups))]
    if not any(t > 0 for t in totals):
        return None

    opts = _PROFILES.get(profile, _PROFILES["dashboard"])
    logical_w = opts["canvas_width"]
    row_h = 28
    margin = 16
    label_w = 130
    bar_area = logical_w - margin * 2 - label_w - 8
    legend_h = 28
    logical_h = margin * 2 + len(groups) * row_h + legend_h
    peak = max(totals) or 1.0
    scale = _SUPERSAMPLE

    img = Image.new("RGB", (logical_w * scale, logical_h * scale), _CHART_BG)
    draw = ImageDraw.Draw(img)
    font = _load_chart_font(11 * scale)

    y = margin * scale
    for i, group in enumerate(groups):
        short = (group[:16] + "…") if len(group) > 17 else group
        draw.text((margin * scale, y + 6 * scale), short, fill=_LEGEND_TEXT, font=font)
        bx = (margin + label_w) * scale
        x_cursor = bx
        total_w = int(bar_area * scale * totals[i] / peak)
        for key, values in series:
            val = values[i] if i < len(values) else 0.0
            if val <= 0 or totals[i] <= 0:
                continue
            seg_w = max(1, int(total_w * val / totals[i]))
            color = _SERIES_COLORS.get(key, _GRAY)
            draw.rectangle((x_cursor, y + 6 * scale, x_cursor + seg_w, y + 20 * scale), fill=color)
            x_cursor += seg_w
        y += row_h * scale

    lx = margin * scale
    ly = y + 4 * scale
    for key, _ in series:
        color = _SERIES_COLORS.get(key, _GRAY)
        draw.rectangle((lx, ly, lx + 10 * scale, ly + 10 * scale), fill=color, outline=_PIE_RIM)
        draw.text((lx + 14 * scale, ly - scale), key.replace("_", " ").title(), fill=_LEGEND_TEXT, font=font)
        lx += 100 * scale

    return _png_data_uri(_downscale(img, logical_w, logical_h, scale))


def group_report_pie_pair_uris(
    group_rows: list,
    *,
    profile: str = "pdf",
) -> tuple[str | None, str | None]:
    """(hours pie, records pie) for PDF compatibility."""
    if not group_rows:
        return None, None
    layout = "side" if profile == "pdf" else "vertical"
    hours = group_pie_png_data_uri(
        [(r["group_label"], float(r["total_hours"])) for r in group_rows],
        profile=profile,
        layout=layout,
    )
    records = group_pie_png_data_uri(
        [(r["group_label"], float(r["occurrence_count"])) for r in group_rows],
        profile=profile,
        layout=layout,
    )
    return hours, records


def build_group_analytics_chart_uris(analytics: dict, *, profile: str = "dashboard") -> dict:
    """Chart URIs for the expanded group analytics dashboard preview."""
    company = analytics["company"]
    by_group = analytics["by_group"]

    absence_rate_rows = [(g["group_label"], g["absence_rate_pct"]) for g in by_group if g["absence_rate_pct"] > 0]
    predicted_rows = [(g["group_label"], g["predicted_unplanned_pct"]) for g in by_group if g["predicted_unplanned_pct"] > 0]

    groups_with_hours = [g for g in by_group if g["absence_hours"] > 0]
    group_labels = [g["group_label"] for g in groups_with_hours]

    return {
        "absence_rate_uri": horizontal_bar_chart_png_data_uri(
            absence_rate_rows, profile=profile, unit="%", max_value=max(10.0, company["absence_rate_pct"] * 1.2)
        ),
        "predicted_uri": horizontal_bar_chart_png_data_uri(
            predicted_rows, profile=profile, unit="%", max_value=max(10.0, company["predicted_unplanned_pct"] * 1.2)
        ),
        "planned_unplanned_uri": donut_png_data_uri(
            [("Planned", company["planned_hours"]), ("Unplanned", company["unplanned_hours"])],
            profile=profile,
        ),
        "workforce_uri": donut_png_data_uri(
            [("Full-time (30–40h)", company["full_time_count"]), ("Part-time (≤29h)", company["part_time_count"])],
            profile=profile,
        )
        if company["full_time_count"] + company["part_time_count"] > 0
        else None,
        "attendance_breakdown_uri": stacked_horizontal_bar_png_data_uri(
            group_labels,
            [
                ("tardy", [g["tardy_hours"] for g in groups_with_hours]),
                ("early", [g["early_departure_hours"] for g in groups_with_hours]),
                ("other", [g["other_absence_hours"] for g in groups_with_hours]),
            ],
            profile=profile,
        )
        if group_labels
        else None,
        "hours_by_group_uri": group_pie_png_data_uri(
            [(g["group_label"], g["absence_hours"]) for g in by_group if g["absence_hours"] > 0],
            profile=profile,
            layout="vertical",
        ),
    }
