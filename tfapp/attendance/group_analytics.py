"""
Group absence analytics for dashboard preview and PDF reports.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .models import CustomUser, Occurrence, OccurrenceSubtype, OccurrenceType
from .services.time_processing import build_daily_scheduled_hours_map

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


def _employment_band(avg_weekly_hours: float) -> str:
    if avg_weekly_hours <= PT_WEEKLY_MAX:
        return "part_time"
    if avg_weekly_hours >= FT_WEEKLY_MIN:
        return "full_time"
    return "between"


def _week_windows(start: date, end: date) -> list[tuple[int, int]]:
    """Inclusive date-index ranges (into shared date list) per calendar week."""
    if start > end:
        return []
    dates: list[date] = []
    d = start
    while d <= end:
        dates.append(d)
        d += timedelta(days=1)
    date_to_i = {day: i for i, day in enumerate(dates)}
    windows: list[tuple[int, int]] = []
    cur = start
    while cur <= end:
        week_end = min(cur + timedelta(days=6), end)
        windows.append((date_to_i[cur], date_to_i[week_end]))
        cur = week_end + timedelta(days=1)
    return windows


def _extrapolate_next(values: list[float]) -> float:
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
    return round(max(0.0, alpha + beta * n), 2)


def _absence_rate_pct(absence_hours: float, scheduled_hours: float) -> float:
    if scheduled_hours <= 0:
        return 0.0
    return round(100.0 * absence_hours / scheduled_hours, 2)


@dataclass
class _ScheduleIndex:
    dates: list[date]
    daily_by_user: dict[int, list[float]]
    prefix_by_user: dict[int, list[float]]
    week_windows: list[tuple[int, int]]
    period_days: int

    @classmethod
    def build(cls, users: list[CustomUser], start: date, end: date) -> _ScheduleIndex:
        dates, daily_by_user = build_daily_scheduled_hours_map(users, start, end)
        prefix_by_user = {
            uid: _prefix(daily) for uid, daily in daily_by_user.items()
        }
        return cls(
            dates=dates,
            daily_by_user=daily_by_user,
            prefix_by_user=prefix_by_user,
            week_windows=_week_windows(start, end),
            period_days=len(dates),
        )

    def scheduled_sum(self, user_ids: set[int], i0: int, i1: int) -> float:
        total = 0.0
        for uid in user_ids:
            prefix = self.prefix_by_user.get(uid)
            if prefix:
                total += prefix[i1 + 1] - prefix[i0]
        return total

    def avg_weekly_hours(self, user_id: int) -> float:
        prefix = self.prefix_by_user.get(user_id)
        if not prefix or self.period_days <= 0:
            return 0.0
        total = prefix[-1]
        return total / (self.period_days / 7.0)


def _prefix(values: list[float]) -> list[float]:
    out = [0.0]
    for v in values:
        out.append(out[-1] + v)
    return out


def _index_occurrences(occurrences: list[Occurrence]) -> dict[int, list[Occurrence]]:
    by_user: dict[int, list[Occurrence]] = defaultdict(list)
    for o in occurrences:
        by_user[o.user_id].append(o)
    return by_user


def _sum_occ_hours(occs: list[Occurrence], pred) -> float:
    return sum(float(o.duration_hours or 0) for o in occs if pred(o))


def _weekly_unplanned_rates(
    schedule: _ScheduleIndex,
    occs_by_user: dict[int, list[Occurrence]],
    user_ids: set[int],
) -> list[float]:
    rates: list[float] = []
    for i0, i1 in schedule.week_windows:
        sched = schedule.scheduled_sum(user_ids, i0, i1)
        unpl = 0.0
        for uid in user_ids:
            for o in occs_by_user.get(uid, ()):
                if (
                    o.occurrence_type == OccurrenceType.UNPLANNED
                    and schedule.dates[i0] <= o.date <= schedule.dates[i1]
                ):
                    unpl += float(o.duration_hours or 0)
        rates.append(_absence_rate_pct(unpl, sched))
    return rates


def _group_occurrences(
    occs_by_user: dict[int, list[Occurrence]], user_ids: set[int]
) -> list[Occurrence]:
    out: list[Occurrence] = []
    for uid in user_ids:
        out.extend(occs_by_user.get(uid, ()))
    return out


def compute_group_analytics(
    *,
    occurrences: list[Occurrence],
    visible_users: list[CustomUser],
    start_date: date,
    end_date: date,
    group_by: str,
) -> dict:
    ne_users = [u for u in visible_users if not u.is_exempt]
    ne_ids = {u.id for u in ne_users}
    schedule = _ScheduleIndex.build(ne_users, start_date, end_date)
    occs_by_user = _index_occurrences(occurrences)

    users_by_group: dict[str, list[CustomUser]] = defaultdict(list)
    group_user_ids: dict[str, set[int]] = defaultdict(set)
    for u in ne_users:
        label = _group_label(u, group_by)
        users_by_group[label].append(u)
        group_user_ids[label].add(u.id)

    total_scheduled = schedule.scheduled_sum(ne_ids, 0, schedule.period_days - 1)
    total_absence = _sum_occ_hours(occurrences, lambda o: True)
    tardy_h = _sum_occ_hours(occurrences, lambda o: o.subtype in TARDY_SUBTYPES)
    early_h = _sum_occ_hours(
        occurrences,
        lambda o: o.is_variance_to_schedule and o.subtype not in TARDY_SUBTYPES,
    )
    planned_h = _sum_occ_hours(
        occurrences, lambda o: o.occurrence_type == OccurrenceType.PLANNED
    )
    unplanned_h = _sum_occ_hours(
        occurrences, lambda o: o.occurrence_type == OccurrenceType.UNPLANNED
    )
    other_h = max(0.0, total_absence - tardy_h - early_h)

    ft_count = pt_count = between_count = 0
    for u in ne_users:
        band = _employment_band(schedule.avg_weekly_hours(u.id))
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
        "predicted_unplanned_pct": _extrapolate_next(
            _weekly_unplanned_rates(schedule, occs_by_user, ne_ids)
        ),
    }

    by_group: list[dict] = []
    for label in sorted(users_by_group.keys(), key=lambda s: s.lower()):
        uids = group_user_ids[label]
        group_occs = _group_occurrences(occs_by_user, uids)
        g_sched = schedule.scheduled_sum(uids, 0, schedule.period_days - 1)
        g_absence = _sum_occ_hours(group_occs, lambda o: True)
        g_tardy = _sum_occ_hours(group_occs, lambda o: o.subtype in TARDY_SUBTYPES)
        g_early = _sum_occ_hours(
            group_occs,
            lambda o: o.is_variance_to_schedule and o.subtype not in TARDY_SUBTYPES,
        )
        g_planned = _sum_occ_hours(
            group_occs, lambda o: o.occurrence_type == OccurrenceType.PLANNED
        )
        g_unplanned = _sum_occ_hours(
            group_occs, lambda o: o.occurrence_type == OccurrenceType.UNPLANNED
        )
        g_ft = g_pt = 0
        for uid in uids:
            band = _employment_band(schedule.avg_weekly_hours(uid))
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
                    _weekly_unplanned_rates(schedule, occs_by_user, uids)
                ),
            }
        )

    return {"company": company, "by_group": by_group}
