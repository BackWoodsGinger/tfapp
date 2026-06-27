"""
Group absence analytics for dashboard preview and PDF reports.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .models import CustomUser, Occurrence, OccurrenceSubtype, OccurrenceType
from .services.time_processing import scheduled_hours_for_range

TARDY_SUBTYPES = frozenset(
    {
        OccurrenceSubtype.TARDY_IN_GRACE,
        OccurrenceSubtype.TARDY_OUT_OF_GRACE,
    }
)

FT_WEEKLY_MIN = 30.0
FT_WEEKLY_MAX = 40.0
PT_WEEKLY_MAX = 29.0


def _group_label(user: CustomUser, group_by: str) -> str:
    if group_by == "department":
        return (user.department or "").strip() or "(No department)"
    if group_by == "supervisor":
        return (
            user.supervisor.payroll_display_name()
            if getattr(user, "supervisor_id", None)
            else "(No supervisor)"
        )
    if group_by == "group_lead":
        return (
            user.group_lead.payroll_display_name()
            if getattr(user, "group_lead_id", None)
            else "(No group lead)"
        )
    return (user.department or "").strip() or "(No department)"


def _avg_weekly_scheduled_hours(user: CustomUser, start: date, end: date) -> float:
    days = (end - start).days + 1
    if days <= 0:
        return 0.0
    total = scheduled_hours_for_range(user, start, end)
    return total / (days / 7.0)


def _employment_band(avg_weekly_hours: float) -> str:
    if avg_weekly_hours <= PT_WEEKLY_MAX:
        return "part_time"
    if avg_weekly_hours >= FT_WEEKLY_MIN:
        return "full_time"
    return "between"


def _week_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        week_end = min(cur + timedelta(days=6), end)
        windows.append((cur, week_end))
        cur = week_end + timedelta(days=1)
    return windows


def _extrapolate_next(values: list[float]) -> float:
    """Linear trend extrapolation one step ahead (simple predictive absenteeism)."""
    n = len(values)
    if n == 0:
        return 0.0
    if n == 1:
        return round(values[0], 2)
    xs = list(range(n))
    mx = (n - 1) / 2.0
    my = sum(values) / n
    var_x = sum((x - mx) ** 2 for x in xs)
    if var_x <= 0:
        return round(my, 2)
    cov_xy = sum((xs[i] - mx) * (values[i] - my) for i in range(n))
    beta = cov_xy / var_x
    alpha = my - beta * mx
    projected = alpha + beta * n
    return round(max(0.0, projected), 2)


def _occ_hours(occurrences: list[Occurrence]) -> float:
    return sum(float(o.duration_hours or 0) for o in occurrences)


def _tardy_hours(occurrences: list[Occurrence]) -> float:
    return sum(float(o.duration_hours or 0) for o in occurrences if o.subtype in TARDY_SUBTYPES)


def _early_departure_hours(occurrences: list[Occurrence]) -> float:
    return sum(
        float(o.duration_hours or 0)
        for o in occurrences
        if o.is_variance_to_schedule and o.subtype not in TARDY_SUBTYPES
    )


def _scheduled_for_users(users: list[CustomUser], start: date, end: date) -> float:
    return sum(scheduled_hours_for_range(u, start, end) for u in users if not u.is_exempt)


def _absence_rate_pct(absence_hours: float, scheduled_hours: float) -> float:
    if scheduled_hours <= 0:
        return 0.0
    return round(100.0 * absence_hours / scheduled_hours, 2)


def _weekly_unplanned_rates(
    users: list[CustomUser],
    occurrences: list[Occurrence],
    start: date,
    end: date,
) -> list[float]:
    user_ids = {u.id for u in users}
    occs = [o for o in occurrences if o.user_id in user_ids]
    rates: list[float] = []
    for w_start, w_end in _week_windows(start, end):
        sched = _scheduled_for_users(users, w_start, w_end)
        unpl = sum(
            float(o.duration_hours or 0)
            for o in occs
            if w_start <= o.date <= w_end and o.occurrence_type == OccurrenceType.UNPLANNED
        )
        rates.append(_absence_rate_pct(unpl, sched))
    return rates


def compute_group_analytics(
    *,
    occurrences: list[Occurrence],
    visible_users: list[CustomUser],
    start_date: date,
    end_date: date,
    group_by: str,
) -> dict:
    """
    Company and per-group KPIs plus chart segment data for group report preview.
    """
    ne_users = [u for u in visible_users if not u.is_exempt]
    users_by_group: dict[str, list[CustomUser]] = defaultdict(list)
    for u in ne_users:
        users_by_group[_group_label(u, group_by)].append(u)

    total_scheduled = _scheduled_for_users(ne_users, start_date, end_date)
    total_absence = _occ_hours(occurrences)
    tardy_h = _tardy_hours(occurrences)
    early_h = _early_departure_hours(occurrences)
    planned_h = sum(float(o.duration_hours or 0) for o in occurrences if o.occurrence_type == OccurrenceType.PLANNED)
    unplanned_h = sum(
        float(o.duration_hours or 0) for o in occurrences if o.occurrence_type == OccurrenceType.UNPLANNED
    )
    other_h = max(0.0, total_absence - tardy_h - early_h)

    ft_count = pt_count = between_count = 0
    for u in ne_users:
        band = _employment_band(_avg_weekly_scheduled_hours(u, start_date, end_date))
        if band == "full_time":
            ft_count += 1
        elif band == "part_time":
            pt_count += 1
        else:
            between_count += 1

    company = {
        "scheduled_hours": round(total_scheduled, 2),
        "absence_hours": round(total_absence, 2),
        "absence_rate_pct": _absence_rate_pct(total_absence, total_scheduled),
        "tardy_hours": round(tardy_h, 2),
        "early_departure_hours": round(early_h, 2),
        "other_absence_hours": round(other_h, 2),
        "planned_hours": round(planned_h, 2),
        "unplanned_hours": round(unplanned_h, 2),
        "planned_pct": round(100.0 * planned_h / total_absence, 1) if total_absence > 0 else 0.0,
        "unplanned_pct": round(100.0 * unplanned_h / total_absence, 1) if total_absence > 0 else 0.0,
        "full_time_count": ft_count,
        "part_time_count": pt_count,
        "between_count": between_count,
        "non_exempt_count": len(ne_users),
        "predicted_unplanned_pct": _extrapolate_next(_weekly_unplanned_rates(ne_users, occurrences, start_date, end_date)),
    }

    by_group: list[dict] = []
    for label in sorted(users_by_group.keys(), key=lambda s: s.lower()):
        group_users = users_by_group[label]
        group_user_ids = {u.id for u in group_users}
        group_occs = [o for o in occurrences if o.user_id in group_user_ids]
        g_sched = _scheduled_for_users(group_users, start_date, end_date)
        g_absence = _occ_hours(group_occs)
        g_tardy = _tardy_hours(group_occs)
        g_early = _early_departure_hours(group_occs)
        g_planned = sum(
            float(o.duration_hours or 0)
            for o in group_occs
            if o.occurrence_type == OccurrenceType.PLANNED
        )
        g_unplanned = sum(
            float(o.duration_hours or 0)
            for o in group_occs
            if o.occurrence_type == OccurrenceType.UNPLANNED
        )
        g_ft = g_pt = 0
        for u in group_users:
            band = _employment_band(_avg_weekly_scheduled_hours(u, start_date, end_date))
            if band == "full_time":
                g_ft += 1
            elif band == "part_time":
                g_pt += 1

        by_group.append(
            {
                "group_label": label,
                "scheduled_hours": round(g_sched, 2),
                "absence_hours": round(g_absence, 2),
                "absence_rate_pct": _absence_rate_pct(g_absence, g_sched),
                "tardy_hours": round(g_tardy, 2),
                "early_departure_hours": round(g_early, 2),
                "other_absence_hours": round(max(0.0, g_absence - g_tardy - g_early), 2),
                "planned_hours": round(g_planned, 2),
                "unplanned_hours": round(g_unplanned, 2),
                "full_time_count": g_ft,
                "part_time_count": g_pt,
                "predicted_unplanned_pct": _extrapolate_next(
                    _weekly_unplanned_rates(group_users, group_occs, start_date, end_date)
                ),
            }
        )

    return {"company": company, "by_group": by_group}
