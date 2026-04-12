import io
import logging
from collections import defaultdict

from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django import forms
from django.utils import timezone as django_tz
from django.utils.timezone import now, localdate
from timeclock.models import TimeEntry
from timeclock.forms import TimeEntryForm
from timeclock.tardy_sync import sync_tardy_occurrences_for_time_entry
from django.db import transaction
from django.db.models import Sum, Q
import csv
import re
from calendar import month_name, monthrange
from datetime import time, timedelta, date, datetime, timezone
from typing import Optional
from django.conf import settings as django_settings
from django.core.cache import cache
from django.http import HttpResponse
from django.http import JsonResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from .models import (
    CustomUser,
    Occurrence,
    OccurrenceType,
    OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL,
    PERFECT_ATTENDANCE_DISQUALIFYING_SUBTYPES,
    PayrollPeriod,
    PayrollPeriodUserSnapshot,
    RoleChoices,
    TimeOffRequest,
    TimeOffRequestStatus,
    WorkThroughLunchRequest,
    AdjustPunchRequest,
    AdjustPunchField,
    revert_tardy_occurrences_for_adjust_punch,
    apply_past_due_occurrences,
    ensure_holiday_occurrences_for_range,
    OccurrenceSubtype,
    user_eligible_for_perfect_attendance_new_hire_month,
    ABSENCE_REPORT_FMLA_SUBTYPE,
    ABSENCE_REPORT_LEAVE_AND_NO_PERSONAL_SUBTYPES,
)
from . import approval_emails
from .forms import ReportFilterForm, TimeOffRequestForm, WorkThroughLunchRequestForm, AdjustPunchRequestForm
from .payroll_utils import (
    week_ending_for_date as _week_ending_for_date,
    is_payroll_week_finalized as _is_payroll_week_finalized,
)
from .schedule_utils import (
    crosses_midnight_for_day,
    earliest_clock_in_allowed,
    get_scheduled_shift_end_datetime,
    get_scheduled_start_for_day,
    scheduled_duration_hours_for_day,
    scheduled_hours_for_range,
    scheduled_lunch_datetimes_for_entry,
    suggested_punch_times_for_day,
)
from django.views.decorators.http import require_POST
from django.conf import settings
from pathlib import Path
import base64
import json
from io import BytesIO

def home(request):
    from pages.views import index

    return index(request)

class DateFilterForm(forms.Form):
    date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )

def get_recent_saturdays(count=12):
    """Return list of Saturday dates for payroll week selector (current and prior weeks)."""
    today = date.today()
    # Saturday of current payroll week (week containing today, ending Saturday)
    days_until_saturday = (5 - today.weekday()) % 7  # 5 = Saturday
    current_week_saturday = today + timedelta(days=days_until_saturday)
    return sorted(
        [current_week_saturday - timedelta(weeks=i) for i in range(count)],
        reverse=True,
    )


def can_approve_time_off(approver: CustomUser, target: CustomUser) -> bool:
    """
    Only allow approvals by the user's own group lead, supervisor,
    manager (same department), or any executive.
    """
    if not approver.is_authenticated:
        return False

    if approver.role == RoleChoices.EXECUTIVE:
        return True

    if target.group_lead_id and approver.id == target.group_lead_id:
        return True
    if target.supervisor_id and approver.id == target.supervisor_id:
        return True

    if (
        approver.role == RoleChoices.MANAGER
        and approver.department
        and approver.department == target.department
    ):
        return True

    return False


def get_pending_approval_counts_for_user(approver: CustomUser):
    """
    Count PENDING requests this user is allowed to approve (time off, work-through-lunch, adjust punch).
    Same rules as team_time_off_requests.
    """
    if approver.role not in [
        RoleChoices.GROUP_LEAD,
        RoleChoices.SUPERVISOR,
        RoleChoices.MANAGER,
        RoleChoices.EXECUTIVE,
    ]:
        return {"time_off": 0, "work_through_lunch": 0, "adjust_punch": 0, "total": 0}

    pending_to = TimeOffRequest.objects.filter(status=TimeOffRequestStatus.PENDING)
    n_to = sum(1 for r in pending_to if can_approve_time_off(approver, r.user))

    pending_wtl = WorkThroughLunchRequest.objects.filter(status=TimeOffRequestStatus.PENDING)
    n_wtl = sum(1 for r in pending_wtl if can_approve_time_off(approver, r.user))

    pending_adj = AdjustPunchRequest.objects.filter(status=TimeOffRequestStatus.PENDING)
    n_adj = sum(1 for r in pending_adj if can_approve_time_off(approver, r.user))

    return {
        "time_off": n_to,
        "work_through_lunch": n_wtl,
        "adjust_punch": n_adj,
        "total": n_to + n_wtl + n_adj,
    }


def _payroll_sort_key(user: CustomUser):
    return (
        (user.payroll_lastname or user.last_name or user.username or "").strip().lower(),
        (user.payroll_firstname or user.first_name or "").strip().lower(),
        (user.username or "").strip().lower(),
    )


def _last_day_of_month(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _scheduled_but_not_clocked_in(visible_users, on_date: date, *, at_time=None):
    """
    Users scheduled on ``on_date`` who have not clocked in, only during their shift window:
    from first allowed clock-in (15 minutes before scheduled start) through scheduled end.
    After shift end they are omitted even if they never punched in.
    """
    if at_time is None:
        at_time = django_tz.now()
    now_local = django_tz.localtime(at_time)
    out = []
    for u in visible_users:
        if get_scheduled_start_for_day(u, on_date) is None:
            continue
        entry = TimeEntry.objects.filter(user=u, date=on_date).first()
        if entry is not None and entry.clock_in is not None:
            continue
        earliest = earliest_clock_in_allowed(u, on_date)
        if earliest is not None and now_local < earliest:
            continue
        shift_end = get_scheduled_shift_end_datetime(u, on_date)
        if shift_end is not None and now_local >= shift_end:
            continue
        out.append(u)
    out.sort(key=_payroll_sort_key)
    return out


def _perfect_attendance_with_hours(visible_users, first: date, period_end: date):
    """
    Non-exempt users only: zero UNPLANNED absences in [first, period_end]; no disqualifying
    occurrence subtypes in that range; new hires (hire or service date) only from their first
    full calendar month after hire onward.

    total_hours is the sum of reported_worked_hours on completed time entries in that range
    (0.00 if none); PTO / absence hours are not included.
    """
    rows = []
    for u in visible_users.filter(is_exempt=False):
        anchor = u.hire_date or u.service_date
        if not user_eligible_for_perfect_attendance_new_hire_month(anchor, first):
            continue
        has_unplanned = Occurrence.objects.filter(
            user=u,
            occurrence_type=OccurrenceType.UNPLANNED,
            date__gte=first,
            date__lte=period_end,
        ).exists()
        if has_unplanned:
            continue
        if Occurrence.objects.filter(
            user=u,
            subtype__in=PERFECT_ATTENDANCE_DISQUALIFYING_SUBTYPES,
            date__gte=first,
            date__lte=period_end,
        ).exists():
            continue
        completed = TimeEntry.objects.filter(
            user=u,
            date__gte=first,
            date__lte=period_end,
            clock_in__isnull=False,
            clock_out__isnull=False,
        )
        total_hours = 0.0
        for e in completed:
            total_hours += e.reported_worked_hours()
        rows.append({"user": u, "total_hours": round(total_hours, 2)})
    rows.sort(key=lambda r: _payroll_sort_key(r["user"]))
    return rows


def _perfect_attendance_candidate_users():
    """
    Active employees considered for Perfect Attendance lists. Same queryset for every role so
    all users see the full qualifying list; only reported hours are restricted to executives.
    """
    return CustomUser.objects.filter(is_active=True).order_by(
        "payroll_lastname",
        "payroll_firstname",
        "last_name",
        "first_name",
        "username",
    )


def _first_day_of_quarter(year: int, quarter: int) -> date:
    return date(year, [1, 4, 7, 10][quarter - 1], 1)


def _last_day_of_quarter(year: int, quarter: int) -> date:
    return [date(year, 3, 31), date(year, 6, 30), date(year, 9, 30), date(year, 12, 31)][quarter - 1]


def _range_from_prefix(
    prefix: list[float], date_to_i: dict[date, int], start: date, end: date
) -> float:
    """Inclusive range sum; prefix from _scheduled_and_unplanned_prefixes."""
    if start > end:
        return 0.0
    i = date_to_i.get(start)
    j = date_to_i.get(end)
    if i is None or j is None:
        return 0.0
    return prefix[j + 1] - prefix[i]


def _scheduled_and_unplanned_prefixes(
    users: list, span_start: date, span_end: date
) -> tuple[list[float], list[float], dict[date, int]]:
    """
    One pass over [span_start, span_end]:
    - Scheduled: sum hours across users per day (Python; unavoidable without storing per-day rosters).
    - Unplanned: one DB query for daily totals, then prefix sums for fast range lookups.
    """
    daily_unplanned = {
        row["date"]: float(row["total"] or 0.0)
        for row in Occurrence.objects.filter(
            occurrence_type=OccurrenceType.UNPLANNED,
            date__gte=span_start,
            date__lte=span_end,
        )
        .values("date")
        .annotate(total=Sum("duration_hours"))
    }

    sched_prefix: list[float] = [0.0]
    unplan_prefix: list[float] = [0.0]
    date_to_i: dict[date, int] = {}
    idx = 0
    d = span_start
    while d <= span_end:
        day_sched = sum(scheduled_duration_hours_for_day(u, d) for u in users)
        day_unpl = daily_unplanned.get(d, 0.0)
        sched_prefix.append(sched_prefix[-1] + day_sched)
        unplan_prefix.append(unplan_prefix[-1] + day_unpl)
        date_to_i[d] = idx
        idx += 1
        d += timedelta(days=1)
    return sched_prefix, unplan_prefix, date_to_i


def _absenteeism_pct(unplanned_h: float, scheduled_h: float) -> float:
    if scheduled_h <= 0:
        return 0.0
    return round(100.0 * unplanned_h / scheduled_h, 2)


def _linear_trend_line(values: list[float]) -> list[float]:
    """Least-squares line through points (i, values[i]); same length as values."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [round(values[0], 2)]
    xs = list(range(n))
    mx = (n - 1) / 2.0
    my = sum(values) / n
    var_x = sum((x - mx) ** 2 for x in xs)
    if var_x <= 0:
        return [round(my, 2)] * n
    cov_xy = sum((xs[i] - mx) * (values[i] - my) for i in range(n))
    beta = cov_xy / var_x
    alpha = my - beta * mx
    return [round(alpha + beta * xs[i], 2) for i in range(n)]


def unplanned_absenteeism_chart_data(reference: date | None = None) -> dict:
    """
    Bar chart: last 3 completed calendar years (annual %), then last 3 completed calendar
    quarters, then each month of the current quarter to date.
    Trend line = linear regression over those points. Target = 2%.
    """
    today = reference or date.today()
    cq = (today.month - 1) // 3 + 1
    cy = today.year
    cur_q_start = _first_day_of_quarter(cy, cq)

    # Previous quarter (last fully ended before current quarter)
    day_before = cur_q_start - timedelta(days=1)
    y_end, m_end = day_before.year, day_before.month
    pq = (m_end - 1) // 3 + 1

    labels: list[str] = []
    pcts: list[float] = []

    users = list(
        CustomUser.objects.filter(is_active=True, is_exempt=False).prefetch_related(
            "schedules"
        )
    )

    last_completed_year = today.year - 1
    # Single pass over the full span (all chart periods are inside this range).
    span_start = date(last_completed_year - 2, 1, 1)
    span_end = today
    sched_prefix, unplan_prefix, sched_idx = _scheduled_and_unplanned_prefixes(
        users, span_start, span_end
    )

    def sched_between(ds: date, de: date) -> float:
        return _range_from_prefix(sched_prefix, sched_idx, ds, de)

    def unpl_between(ds: date, de: date) -> float:
        return _range_from_prefix(unplan_prefix, sched_idx, ds, de)

    # Three completed calendar years (oldest first): Jan 1 – Dec 31 each
    for i in range(3):
        y = last_completed_year - 2 + i
        ys = date(y, 1, 1)
        ye = date(y, 12, 31)
        unpl = unpl_between(ys, ye)
        sched = sched_between(ys, ye)
        labels.append(str(y))
        pcts.append(_absenteeism_pct(unpl, sched))

    # Three completed quarters (oldest first)
    qy, qq = y_end, pq
    quarter_windows: list[tuple[date, date, str]] = []
    for _ in range(3):
        qs = _first_day_of_quarter(qy, qq)
        qe = _last_day_of_quarter(qy, qq)
        label = f"Q{qq} {str(qy)[2:]}"
        quarter_windows.append((qs, qe, label))
        qq -= 1
        if qq == 0:
            qq = 4
            qy -= 1
    quarter_windows.reverse()

    for qs, qe, label in quarter_windows:
        unpl = unpl_between(qs, qe)
        sched = sched_between(qs, qe)
        labels.append(label)
        pcts.append(_absenteeism_pct(unpl, sched))

    # Current quarter: one bar per month from quarter start through today
    q_month_starts = {1: 1, 2: 4, 3: 7, 4: 10}[cq]
    for k in range(3):
        month = q_month_starts + k
        if month > 12:
            break
        ms = date(cy, month, 1)
        me = _last_day_of_month(cy, month)
        period_end = min(today, me)
        if ms > today:
            break
        unpl = unpl_between(ms, period_end)
        sched = sched_between(ms, period_end)
        labels.append(f"{month_name[month][:3]} {str(cy)[2:]}")
        pcts.append(_absenteeism_pct(unpl, sched))

    trend = _linear_trend_line(pcts)
    return {
        "labels": labels,
        "values": pcts,
        "trend": trend,
        "target_pct": 2.0,
    }


logger = logging.getLogger(__name__)


@login_required
def absenteeism_chart_api(request):
    """Heavy chart series for the dashboard; loaded via fetch so the dashboard page returns quickly."""
    cache_key = f"absenteeism_chart:{date.today().isoformat()}"
    try:
        data = cache.get(cache_key)
        if data is None:
            raw = unplanned_absenteeism_chart_data()
            data = {
                "labels": list(raw["labels"]),
                "values": [float(x) for x in raw["values"]],
                "trend": [float(x) for x in raw["trend"]],
                "target_pct": float(raw["target_pct"]),
            }
            ttl = getattr(django_settings, "ABSENTEEISM_CHART_CACHE_SECONDS", 3600)
            cache.set(cache_key, data, ttl)
        return JsonResponse(data)
    except Exception as e:
        logger.exception("absenteeism_chart_api failed")
        payload = {"error": "chart_failed", "labels": [], "values": [], "trend": [], "target_pct": 2.0}
        if django_settings.DEBUG:
            payload["detail"] = str(e)
        return JsonResponse(payload, status=500)


@login_required
def dashboard(request):
    user = request.user
    today = date.today()

    selected_date_str = request.GET.get("date")
    if selected_date_str:
        try:
            selected_date = date.fromisoformat(selected_date_str)
        except ValueError:
            selected_date = today
    else:
        selected_date = today

    if user.role == RoleChoices.EXECUTIVE:
        visible_users = CustomUser.objects.all()
    elif user.role == RoleChoices.MANAGER:
        visible_users = CustomUser.objects.filter(department=user.department)
    elif user.role == RoleChoices.SUPERVISOR:
        visible_users = CustomUser.objects.filter(Q(supervisor=user) | Q(id=user.id))
    elif user.role == RoleChoices.GROUP_LEAD:
        visible_users = CustomUser.objects.filter(Q(group_lead=user) | Q(id=user.id))
    elif user.role == RoleChoices.TEAM_LEAD:
        visible_users = CustomUser.objects.filter(Q(team_lead=user) | Q(id=user.id))
    else:
        visible_users = CustomUser.objects.filter(id=user.id)

    visible_users = visible_users.order_by(
        "payroll_lastname",
        "payroll_firstname",
        "last_name",
        "first_name",
        "username",
    )

    selected_slug = request.GET.get("user_slug")
    selected_user_id = request.GET.get("user_id")
    if selected_slug:
        selected_user = get_object_or_404(visible_users, public_slug=selected_slug)
    elif selected_user_id and selected_user_id.isdigit():
        selected_user = get_object_or_404(visible_users, id=int(selected_user_id))
    else:
        selected_user = user

    # Apply any past-due occurrences so balance is current (future requests only deduct when date passes).
    apply_past_due_occurrences(selected_user)

    anniversary = selected_user.service_date.replace(year=today.year) if selected_user.service_date else today
    if today < anniversary:
        anniversary = anniversary.replace(year=today.year - 1)

    past_occurrences = Occurrence.objects.filter(
        user=selected_user, date__gte=anniversary, date__lte=today
    ).order_by('-date')

    future_occurrences = Occurrence.objects.filter(
        user=selected_user, date__gt=today
    ).order_by('date')

    # Only future occurrences that use PTO/personal affect pending and future personal.
    future_occurrences_using_balance = Occurrence.objects.filter(
        user=selected_user,
        date__gt=today,
        subtype__in=OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL,
    )
    future_hours_using_balance = (
        future_occurrences_using_balance.aggregate(total=Sum("duration_hours"))["total"] or 0
    )

    # Pending PTO: hours of future approved time off that will be paid from PTO.
    pending_pto_hours = min(future_hours_using_balance, selected_user.pto_balance)
    # Future Personal: only the hours of future approved time off that would add to unpaid
    # once those dates pass (no PTO left to cover them). Does NOT include current personal_time_balance.
    future_personal_hours_from_occurrences = max(
        0, future_hours_using_balance - selected_user.pto_balance
    )
    future_personal = future_personal_hours_from_occurrences
    # Balance after accounting for future approved time off.
    balance_after_pending = selected_user.pto_balance - pending_pto_hours

    is_lead_role_or_above = user.role in [
        RoleChoices.GROUP_LEAD,
        RoleChoices.SUPERVISOR,
        RoleChoices.MANAGER,
        RoleChoices.EXECUTIVE,
    ]
    if is_lead_role_or_above:
        daily_occurrences = Occurrence.objects.filter(
            user__in=visible_users,
            date=selected_date,
        ).order_by("user__username", "date")
    else:
        daily_occurrences = []

    start_of_week = today - timedelta(days=(today.weekday() + 1) % 7)
    end_of_week = start_of_week + timedelta(days=6)
    ne_users = list(visible_users.filter(is_exempt=False).prefetch_related("schedules"))
    ne_ids = [u.id for u in ne_users]
    entries_by_user = defaultdict(list)
    if ne_ids:
        for e in TimeEntry.objects.filter(
            user_id__in=ne_ids, date__range=[start_of_week, end_of_week]
        ):
            entries_by_user[e.user_id].append(e)

    weekly_totals = []
    for u in ne_users:
        total_actual = 0
        total_reported = 0
        for entry in entries_by_user[u.id]:
            if entry.clock_in and entry.clock_out:
                total_actual += entry.actual_worked_hours()
                total_reported += entry.reported_worked_hours()
        total_scheduled = _scheduled_hours_for_range(u, start_of_week, end_of_week)
        delta = round(total_reported - total_scheduled, 2)
        weekly_totals.append((
            u,
            round(total_actual, 2),
            round(total_reported, 2),
            round(total_scheduled, 2),
            delta,
        ))

    alerts = []
    if user.is_staff:
        problem_entries = TimeEntry.objects.filter(date__range=[start_of_week, end_of_week])
        for e in problem_entries:
            # Match TimeEntry.is_incomplete() (work-through lunch, no scheduled lunch, etc.)
            if e.date < today and e.is_incomplete():
                alerts.append(e)

    # Occurrence report (moved here from Payroll page)
    report_form = ReportFilterForm(request.GET or None)
    report_form.fields["user"].queryset = visible_users
    report_occurrences = []
    report_selected_user = None
    report_today = localdate()
    if report_form.is_valid() and report_form.cleaned_data.get("user"):
        report_selected_user = report_form.cleaned_data["user"]
        report_start_date = report_form.cleaned_data["start_date"]
        report_end_date = report_form.cleaned_data["end_date"]
        if report_start_date and report_end_date:
            report_occurrences = Occurrence.objects.filter(
                user=report_selected_user, date__range=(report_start_date, report_end_date)
            ).order_by("date")

    user_service_dates = {
        str(u.public_slug): u.service_date.isoformat() if u.service_date else None
        for u in visible_users
    }

    override_cutoff = today - timedelta(days=90)
    if is_lead_role_or_above:
        clock_in_overrides = (
            TimeEntry.objects.filter(
                clock_in_authorized_by__isnull=False,
                user__in=visible_users,
                date__gte=override_cutoff,
            )
            .select_related("user", "clock_in_authorized_by")
            .order_by("-date", "-clock_in")[:75]
        )
    else:
        clock_in_overrides = []

    # Perfect Attendance month (default: previous month on the 1st; else current month)
    pa_year_str = request.GET.get("pa_year")
    pa_month_str = request.GET.get("pa_month")
    pa_year = None
    pa_month = None
    if pa_year_str and pa_month_str:
        try:
            py, pm = int(pa_year_str), int(pa_month_str)
            if 1 <= pm <= 12 and 2000 <= py <= 2100:
                pa_year, pa_month = py, pm
        except (ValueError, TypeError):
            pass
    if pa_year is None:
        if today.day == 1:
            if today.month == 1:
                pa_year, pa_month = today.year - 1, 12
            else:
                pa_year, pa_month = today.year, today.month - 1
        else:
            pa_year, pa_month = today.year, today.month

    pa_first = date(pa_year, pa_month, 1)
    pa_last = _last_day_of_month(pa_year, pa_month)
    pa_month_choices = [(i, month_name[i]) for i in range(1, 13)]
    pa_year_choices = list(range(2020, today.year + 2))
    if pa_first > today:
        pa_period_end = None
        pa_period_description = ""
        perfect_attendance_rows = []
        pa_hours_total = 0.0
    else:
        pa_period_end = min(pa_last, today)
        if pa_period_end < pa_last:
            pa_period_description = (
                f"{pa_first:%B %d} – {pa_period_end:%B %d, %Y} (month to date)"
            )
        else:
            pa_period_description = f"{pa_first:%B %Y}"
        pa_cache_key = f"pa_rows_v1:{pa_year}:{pa_month}:{pa_period_end.isoformat()}"
        pa_cache_ttl = getattr(
            django_settings, "PERFECT_ATTENDANCE_CACHE_SECONDS", 10 * 60
        )
        pa_cached = cache.get(pa_cache_key)
        if pa_cached is not None:
            pa_user_ids = [r["user_id"] for r in pa_cached]
            users_by_id = CustomUser.objects.in_bulk(pa_user_ids)
            perfect_attendance_rows = []
            for r in pa_cached:
                u = users_by_id.get(r["user_id"])
                if u is not None:
                    perfect_attendance_rows.append(
                        {"user": u, "total_hours": r["total_hours"]}
                    )
            pa_hours_total = sum(x["total_hours"] for x in perfect_attendance_rows)
        else:
            perfect_attendance_rows = _perfect_attendance_with_hours(
                _perfect_attendance_candidate_users(), pa_first, pa_period_end
            )
            pa_hours_total = sum(r["total_hours"] for r in perfect_attendance_rows)
            cache.set(
                pa_cache_key,
                [
                    {"user_id": r["user"].id, "total_hours": r["total_hours"]}
                    for r in perfect_attendance_rows
                ],
                pa_cache_ttl,
            )

    scheduled_not_clocked = _scheduled_but_not_clocked_in(visible_users, today)

    context = {
        "user_list": visible_users,
        "selected_user": selected_user,
        "selected_date": selected_date,
        "daily_occurrences": daily_occurrences,
        "past_occurrences": past_occurrences,
        "future_occurrences": future_occurrences,
        "current_pto": selected_user.pto_balance,
        "personal_time": selected_user.personal_time_balance,
        "pending_pto_hours": pending_pto_hours,
        "balance_after_pending": balance_after_pending,
        "future_personal": future_personal,
        "future_personal_hours_from_occurrences": future_personal_hours_from_occurrences,
        "final_year_balance": selected_user.final_pto_balance,
        "today": today,
        "weekly_totals": weekly_totals,
        "alerts": alerts,
        "start_of_week": start_of_week,
        "end_of_week": end_of_week,
        "report_form": report_form,
        "report_occurrences": report_occurrences,
        "report_selected_user": report_selected_user,
        "user_service_dates_json": json.dumps(user_service_dates),
        "today_iso": report_today.isoformat(),
        "clock_in_overrides": clock_in_overrides,
        "is_lead_role_or_above": is_lead_role_or_above,
        "scheduled_not_clocked": scheduled_not_clocked,
        "pa_year": pa_year,
        "pa_month": pa_month,
        "pa_first": pa_first,
        "pa_last": pa_last,
        "pa_month_choices": pa_month_choices,
        "pa_year_choices": pa_year_choices,
        "pa_period_description": pa_period_description,
        "pa_period_end": pa_period_end,
        "perfect_attendance_rows": perfect_attendance_rows,
        "pa_hours_total": pa_hours_total,
        "show_perfect_attendance_hours": user.role == RoleChoices.EXECUTIVE,
    }
    return render(request, "attendance/dashboard.html", context)

@login_required
def attendance_list(request, filter_by="today"):
    user = request.user
    today = date.today()

    visible_user = user

    apply_past_due_occurrences(visible_user)

    if visible_user.service_date:
        anniversary = visible_user.service_date.replace(year=today.year)
        if today < anniversary:
            anniversary = visible_user.service_date.replace(year=today.year - 1)
    else:
        anniversary = today

    past_occurrences = Occurrence.objects.filter(
        user=visible_user,
        date__gte=anniversary,
        date__lte=today
    ).order_by('-date')

    future_occurrences = Occurrence.objects.filter(
        user=visible_user,
        date__gt=today
    ).order_by('date')

    future_hours_using_balance = (
        Occurrence.objects.filter(
            user=visible_user,
            date__gt=today,
            subtype__in=OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL,
        ).aggregate(total=Sum("duration_hours"))["total"]
        or 0
    )
    future_hours = future_occurrences.aggregate(total=Sum("duration_hours"))["total"] or 0
    future_pto = max(visible_user.pto_balance - future_hours_using_balance, 0)
    # Future Personal: only future approved time off that would add to unpaid (no PTO left).
    future_personal = max(0, future_hours_using_balance - visible_user.pto_balance)

    context = {
        "date": today,
        "past_occurrences": past_occurrences,
        "future_occurrences": future_occurrences,
        "current_pto": visible_user.pto_balance,
        "current_personal": visible_user.personal_time_balance,
        "future_pto": future_pto,
        "future_personal": future_personal,
    }

    return render(request, "attendance/attendance_list.html", context)

def user_can_view_reports(user):
    return user.role in [
        RoleChoices.GROUP_LEAD,
        RoleChoices.SUPERVISOR,
        RoleChoices.MANAGER,
        RoleChoices.EXECUTIVE,
    ]


def user_can_view_payroll(user):
    return user.role == RoleChoices.EXECUTIVE

@login_required
def reports_redirect(request):
    return redirect("attendance:payroll")


@login_required
def payroll_view(request):
    if not user_can_view_payroll(request.user):
        return redirect("attendance:dashboard")

    user = request.user
    form = ReportFilterForm(request.GET or None)
    visible_users = CustomUser.objects.all()
    form.fields["user"].queryset = visible_users.order_by(
        "payroll_lastname",
        "payroll_firstname",
        "last_name",
        "first_name",
        "username",
    )

    occurrences = []
    selected_user = None
    today = localdate()
    payroll_weeks_list = get_recent_saturdays(12)
    current_week_saturday = payroll_weeks_list[0]
    week_ending_param = request.GET.get("week_ending")
    if week_ending_param:
        try:
            selected_saturday = date.fromisoformat(week_ending_param)
            end_of_week = selected_saturday
            start_of_week = end_of_week - timedelta(days=6)
        except (TypeError, ValueError):
            end_of_week = current_week_saturday
            start_of_week = end_of_week - timedelta(days=6)
    else:
        end_of_week = current_week_saturday
        start_of_week = end_of_week - timedelta(days=6)

    ne_payroll_users = sorted(
        visible_users.filter(is_exempt=False).prefetch_related("schedules"),
        key=_payroll_sort_key,
    )
    ne_payroll_ids = [u.id for u in ne_payroll_users]
    entries_by_uid = defaultdict(list)
    if ne_payroll_ids:
        for e in TimeEntry.objects.filter(
            user_id__in=ne_payroll_ids,
            date__range=[start_of_week, end_of_week],
        ):
            entries_by_uid[e.user_id].append(e)

    pto_by_uid = {}
    if ne_payroll_ids:
        for row in (
            Occurrence.objects.filter(
                user_id__in=ne_payroll_ids,
                date__range=[start_of_week, end_of_week],
                pto_applied=True,
            )
            .exclude(subtype=OccurrenceSubtype.HOLIDAY_PAID)
            .values("user_id")
            .annotate(
                pto_sum=Sum("pto_hours_applied"),
                per_sum=Sum("personal_hours_applied"),
            )
        ):
            pto_by_uid[row["user_id"]] = (
                float(row["pto_sum"] or 0),
                float(row["per_sum"] or 0),
            )

    weekly_totals = []
    for u in ne_payroll_users:
        total_actual = 0
        total_reported = 0
        for entry in entries_by_uid[u.id]:
            if entry.clock_in and entry.clock_out:
                total_actual += entry.actual_worked_hours()
                total_reported += entry.reported_worked_hours()
        total_scheduled = _scheduled_hours_for_range(u, start_of_week, end_of_week)
        pto_applied, personal_applied = pto_by_uid.get(u.id, (0.0, 0.0))
        weekly_totals.append((
            u,
            round(total_actual, 2),
            round(total_reported, 2),
            round(total_scheduled, 2),
            round(pto_applied, 2),
            round(personal_applied, 2),
        ))

    alerts = []
    if user.role in [
        RoleChoices.GROUP_LEAD,
        RoleChoices.SUPERVISOR,
        RoleChoices.MANAGER,
        RoleChoices.EXECUTIVE,
    ] or user.is_staff:
        problem_entries = TimeEntry.objects.filter(date__range=[start_of_week, end_of_week])
        for e in problem_entries:
            if e.date < today and e.is_incomplete():
                alerts.append(e)

    if form.is_valid():
        selected_user = form.cleaned_data["user"]
        start_date = form.cleaned_data["start_date"]
        end_date = form.cleaned_data["end_date"]
        occurrences = Occurrence.objects.filter(
            user=selected_user, date__range=(start_date, end_date)
        ).order_by("date")

    payroll_weeks = [d.strftime("%Y-%m-%d") for d in payroll_weeks_list]
    payroll_weeks_display = [(d.strftime("%Y-%m-%d"), d.strftime("%m/%d/%Y")) for d in payroll_weeks_list]
    if end_of_week.strftime("%Y-%m-%d") not in payroll_weeks:
        payroll_weeks_display.insert(0, (end_of_week.strftime("%Y-%m-%d"), end_of_week.strftime("%m/%d/%Y")))
        payroll_weeks.insert(0, end_of_week.strftime("%Y-%m-%d"))

    user_service_dates = {
        str(u.public_slug): u.service_date.isoformat() if u.service_date else None
        for u in visible_users
    }

    payroll_finalized = _is_payroll_week_finalized(end_of_week)

    return render(request, "attendance/payroll.html", {
        "form": form,
        "occurrences": occurrences,
        "selected_user": selected_user,
        "weekly_totals": weekly_totals,
        "alerts": alerts,
        "start_of_week": start_of_week,
        "end_of_week": end_of_week,
        "payroll_weeks": payroll_weeks,
        "payroll_weeks_display": payroll_weeks_display,
        "payroll_finalized": payroll_finalized,
        "user_service_dates_json": json.dumps(user_service_dates),
        "today_iso": today.isoformat(),
    })


@login_required
def payroll_user_breakdown(request):
    """
    JSON endpoint for Payroll user modal: daily time-entry breakdown for a given week.
    """
    if not user_can_view_payroll(request.user):
        return JsonResponse({"error": "forbidden"}, status=403)

    user_slug = request.GET.get("user_slug")
    user_id = request.GET.get("user_id")
    week_ending_param = request.GET.get("week_ending")
    if not week_ending_param or not (user_slug or (user_id and user_id.isdigit())):
        return JsonResponse({"error": "missing params"}, status=400)

    try:
        week_ending = date.fromisoformat(week_ending_param)
    except ValueError:
        return JsonResponse({"error": "invalid week_ending"}, status=400)

    visible_users = CustomUser.objects.all()

    if user_slug:
        target_user = get_object_or_404(visible_users, public_slug=user_slug)
    else:
        target_user = get_object_or_404(visible_users, id=int(user_id))
    week_start = week_ending - timedelta(days=6)

    entries = (
        TimeEntry.objects.filter(user=target_user, date__range=[week_start, week_ending])
        .order_by("date")
    )

    days = []
    for e in entries:
        def fmt(dt):
            if not dt:
                return None
            return django_tz.localtime(dt).strftime("%I:%M %p").lstrip("0")

        days.append(
            {
                "date": e.date.isoformat(),
                "clock_in": fmt(e.clock_in),
                "lunch_out": fmt(e.lunch_out),
                "lunch_in": fmt(e.lunch_in),
                "clock_out": fmt(e.clock_out),
                "actual_hours": round(e.actual_worked_hours(), 2),
                "reported_hours": round(e.reported_worked_hours(), 2),
                "incomplete": e.is_incomplete(),
            }
        )

    return JsonResponse(
        {
            "user": {
                "id": target_user.id,
                "public_slug": target_user.public_slug,
                "name": target_user.payroll_display_name(),
            },
            "week_start": week_start.isoformat(),
            "week_ending": week_ending.isoformat(),
            "days": days,
        }
    )


def _fmt_csv_time(dt):
    if not dt:
        return ""
    return django_tz.localtime(dt).strftime("%H:%M")


def _fmt_time_only(t):
    if not t:
        return ""
    return t.strftime("%H:%M")


def _normalize_payroll_csv_row(row, n=8):
    """Pad with empty strings or trim so spreadsheets that drop trailing empty columns still parse."""
    out = []
    for c in row:
        if c is None:
            cell = ""
        else:
            cell = str(c).strip().strip("\ufeff").strip()
        out.append(cell)
    if len(out) < n:
        out.extend([""] * (n - len(out)))
    elif len(out) > n:
        out = out[:n]
    return out


def _parse_payroll_csv_date(value: str) -> Optional[date]:
    """
    Accept ISO YYYY-MM-DD (from our download) or US M/D/YYYY and M/D/YY (typical Excel CSV).
    Strips Excel datetime suffix (e.g. '4/11/2026 12:00:00').
    """
    s = (value or "").strip().strip("\ufeff")
    if not s:
        return None
    if " " in s:
        s = s.split()[0]
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2})$", s)
    if m:
        month, day, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        year = 2000 + y2 if y2 < 50 else 1900 + y2
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _payroll_redirect_after_csv_upload(request, week_ending_date=None):
    """Keep the week the user was viewing when upload fails; on success pass the imported week."""
    base = reverse("attendance:payroll")
    if week_ending_date is not None:
        if isinstance(week_ending_date, date):
            return redirect(f"{base}?week_ending={week_ending_date.isoformat()}")
        return redirect(f"{base}?week_ending={week_ending_date}")
    ret = (request.POST.get("return_week_ending") or "").strip()
    if ret:
        try:
            date.fromisoformat(ret)
            return redirect(f"{base}?week_ending={ret}")
        except ValueError:
            pass
    return redirect(base)


def _parse_csv_time_cell(value: str):
    if value is None or not str(value).strip():
        return None
    s = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def _make_aware_on_date(d: date, t: time) -> datetime:
    naive = datetime.combine(d, t)
    return django_tz.make_aware(naive, django_tz.get_current_timezone())


def _clock_out_calendar_date(user, work_date: date, clock_in_t: Optional[time], clock_out_t: Optional[time]) -> date:
    if clock_out_t is None or clock_in_t is None:
        return work_date
    if crosses_midnight_for_day(user, work_date) and clock_out_t <= clock_in_t:
        return work_date + timedelta(days=1)
    return work_date


def _delete_time_entry_payroll_import(user, work_date):
    with transaction.atomic():
        u = CustomUser.objects.select_for_update().get(pk=user.pk)
        revert_tardy_occurrences_for_adjust_punch(u, work_date)
    TimeEntry.objects.filter(user=user, date=work_date).delete()


@login_required
def payroll_schedule_csv_download(request):
    """
    CSV: one row per employee per day — week_ending, payroll names, work_date,
    clock_in, lunch_out, lunch_in, clock_out (local HH:MM). Filled from existing
    TimeEntry or from default schedule when no entry exists.
    """
    if not request.user.is_staff:
        return redirect("attendance:dashboard")
    week_ending_param = request.GET.get("week_ending")
    if week_ending_param:
        try:
            week_ending = date.fromisoformat(week_ending_param)
        except ValueError:
            messages.error(request, "Invalid week for payroll.")
            return redirect("attendance:payroll")
    else:
        payroll_weeks_list = get_recent_saturdays(12)
        week_ending = payroll_weeks_list[0] if payroll_weeks_list else _week_ending_for_date(localdate())

    week_start = week_ending - timedelta(days=6)
    dates = [week_start + timedelta(days=i) for i in range(7)]

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="payroll_time_entries_{week_ending.isoformat()}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(
        [
            "week_ending",
            "payroll_lastname",
            "payroll_firstname",
            "work_date",
            "clock_in",
            "lunch_out",
            "lunch_in",
            "clock_out",
        ]
    )
    users = sorted(
        CustomUser.objects.filter(is_active=True, is_exempt=False),
        key=_payroll_sort_key,
    )
    for u in users:
        for d in dates:
            row = [week_ending.isoformat(), u.payroll_lastname, u.payroll_firstname, d.isoformat()]
            e = TimeEntry.objects.filter(user=u, date=d).first()
            if e:
                row += [
                    _fmt_csv_time(e.clock_in),
                    _fmt_csv_time(e.lunch_out),
                    _fmt_csv_time(e.lunch_in),
                    _fmt_csv_time(e.clock_out),
                ]
            else:
                sug = suggested_punch_times_for_day(u, d)
                row += [
                    _fmt_time_only(sug["clock_in"]),
                    _fmt_time_only(sug["lunch_out"]),
                    _fmt_time_only(sug["lunch_in"]),
                    _fmt_time_only(sug["clock_out"]),
                ]
            writer.writerow(row)
    return response


@require_POST
@login_required
def payroll_schedule_csv_upload(request):
    """
    Upload edited CSV: updates TimeEntry rows; empty punch times clears that day’s entry.
    """
    if not request.user.is_staff:
        return redirect("attendance:dashboard")
    f = request.FILES.get("time_entries_csv") or request.FILES.get("schedule_csv")
    if not f:
        messages.error(request, "Choose a CSV file to upload.")
        return _payroll_redirect_after_csv_upload(request)

    try:
        raw = f.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        messages.error(request, "File must be UTF-8.")
        return _payroll_redirect_after_csv_upload(request)

    reader = csv.reader(io.StringIO(raw))
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if len(rows) < 2:
        messages.error(request, "CSV must include a header row and at least one data row.")
        return _payroll_redirect_after_csv_upload(request)

    header = [c.strip().lower().strip("\ufeff") for c in _normalize_payroll_csv_row(rows[0], 8)]
    expected = [
        "week_ending",
        "payroll_lastname",
        "payroll_firstname",
        "work_date",
        "clock_in",
        "lunch_out",
        "lunch_in",
        "clock_out",
    ]
    if header != expected:
        messages.error(
            request,
            "Header must be: week_ending, payroll_lastname, payroll_firstname, work_date, "
            "clock_in, lunch_out, lunch_in, clock_out",
        )
        return _payroll_redirect_after_csv_upload(request)

    week_peek = None
    for row in rows[1:]:
        if len(row) >= 1:
            parsed = _parse_payroll_csv_date(row[0])
            if parsed is not None:
                week_peek = parsed
                break
    if week_peek and _is_payroll_week_finalized(week_peek):
        messages.error(
            request,
            "That payroll week is finalized. Unfinalize before importing time entries.",
        )
        return _payroll_redirect_after_csv_upload(request, week_peek)

    users_list = list(CustomUser.objects.filter(is_active=True, is_exempt=False))

    def _match_user(ln: str, fn: str):
        ln_k = (ln or "").strip().casefold()
        fn_k = (fn or "").strip().casefold()
        matches = []
        for u in users_list:
            u_ln = (u.payroll_lastname or u.last_name or "").strip().casefold()
            u_fn = (u.payroll_firstname or u.first_name or "").strip().casefold()
            if u_ln == ln_k and u_fn == fn_k:
                matches.append(u)
        return matches

    week_ending_ref = None
    applied = 0
    deleted = 0

    with transaction.atomic():
        for row in rows[1:]:
            row = _normalize_payroll_csv_row(row, 8)
            we = _parse_payroll_csv_date(row[0])
            wd = _parse_payroll_csv_date(row[3])
            if we is None or wd is None:
                messages.error(
                    request,
                    "Invalid date in row; use YYYY-MM-DD or M/D/YYYY as exported from Excel. "
                    f"Got week_ending={row[0]!r}, work_date={row[3]!r}",
                )
                transaction.set_rollback(True)
                return _payroll_redirect_after_csv_upload(request)

            if week_ending_ref is None:
                week_ending_ref = we
            elif we != week_ending_ref:
                messages.error(request, "All rows must use the same week_ending.")
                transaction.set_rollback(True)
                return _payroll_redirect_after_csv_upload(request)

            if wd < week_ending_ref - timedelta(days=6) or wd > week_ending_ref:
                messages.error(
                    request,
                    f"work_date {wd} is outside the week ending {week_ending_ref}.",
                )
                transaction.set_rollback(True)
                return _payroll_redirect_after_csv_upload(request)

            matches = _match_user(row[1], row[2])
            if len(matches) != 1:
                messages.error(
                    request,
                    f"No unique active employee for payroll name {row[1]!r}, {row[2]!r}.",
                )
                transaction.set_rollback(True)
                return _payroll_redirect_after_csv_upload(request)
            u = matches[0]

            ci = _parse_csv_time_cell(row[4])
            lo = _parse_csv_time_cell(row[5])
            li = _parse_csv_time_cell(row[6])
            co = _parse_csv_time_cell(row[7])

            if not any([ci, lo, li, co]):
                if TimeEntry.objects.filter(user=u, date=wd).exists():
                    _delete_time_entry_payroll_import(u, wd)
                    deleted += 1
                continue

            if not ci or not co:
                messages.error(
                    request,
                    f"clock_in and clock_out are required when entering any punches on {wd} "
                    f"for {u.payroll_display_name()}.",
                )
                transaction.set_rollback(True)
                return _payroll_redirect_after_csv_upload(request)

            cin = _make_aware_on_date(wd, ci)
            lo_a = _make_aware_on_date(wd, lo) if lo else None
            li_a = _make_aware_on_date(wd, li) if li else None
            co_date = _clock_out_calendar_date(u, wd, ci, co)
            cout = _make_aware_on_date(co_date, co)

            entry, _ = TimeEntry.objects.get_or_create(user=u, date=wd, defaults={})
            entry.clock_in = cin
            entry.lunch_out = lo_a
            entry.lunch_in = li_a
            entry.clock_out = cout
            entry.save()
            sync_tardy_occurrences_for_time_entry(entry)
            applied += 1

    if week_ending_ref is None:
        messages.error(request, "No data rows.")
        return _payroll_redirect_after_csv_upload(request)

    messages.success(
        request,
        f"Imported time entries for week ending {week_ending_ref}: {applied} row(s) saved, "
        f"{deleted} day(s) cleared.",
    )
    return _payroll_redirect_after_csv_upload(request, week_ending_ref)


def _scheduled_hours_for_range(user, week_start, week_ending):
    """Total scheduled hours from weekly_schedule or WorkSchedule for [week_start, week_ending]."""
    return scheduled_hours_for_range(user, week_start, week_ending)


def _scheduled_hours_for_day(user, d):
    """Scheduled hours for a single day from weekly_schedule or WorkSchedule, or 0."""
    return scheduled_duration_hours_for_day(user, d)


def _get_scheduled_start_time(user, d):
    """Return scheduled start time for user on date d from weekly_schedule or WorkSchedule, or None."""
    return get_scheduled_start_for_day(user, d)


def _create_tardy_occurrences_for_week(week_start, week_ending, period=None):
    """
    For each time entry in the week with clock_in and a schedule that day:
    if clock_in is later than scheduled start, create TARDY_IN_GRACE (<=4 min) or
    TARDY_OUT_OF_GRACE (5+ min late, duration = rounded loss). Skip if occurrence already exists.
    If period is given, set payroll_period on created occurrences so they can be reverted on unfinalize.
    Clock-in is compared in local time (schedule is stored as local); avoid UTC vs local mismatch.
    """
    entries = TimeEntry.objects.filter(
        date__range=[week_start, week_ending],
        clock_in__isnull=False,
    ).select_related("user")
    for e in entries:
        scheduled_start = _get_scheduled_start_time(e.user, e.date)
        if not scheduled_start:
            continue
        if not e.clock_in:
            continue
        # Compare in local time: clock_in is stored UTC when USE_TZ=True
        clock_in_local = django_tz.localtime(e.clock_in)
        clock_in_time = clock_in_local.time()
        if clock_in_time <= scheduled_start:
            continue
        delta_minutes = (clock_in_time.hour * 60 + clock_in_time.minute) - (
            scheduled_start.hour * 60 + scheduled_start.minute
        )
        if delta_minutes <= 0:
            continue
        already = Occurrence.objects.filter(
            user=e.user,
            date=e.date,
            subtype__in=[OccurrenceSubtype.TARDY_IN_GRACE, OccurrenceSubtype.TARDY_OUT_OF_GRACE],
        ).exists()
        if already:
            continue
        if delta_minutes <= 4:
            Occurrence.objects.create(
                user=e.user,
                occurrence_type=OccurrenceType.UNPLANNED,
                subtype=OccurrenceSubtype.TARDY_IN_GRACE,
                date=e.date,
                duration_hours=0,
                payroll_period=period,
            )
        else:
            loss_hours = round((delta_minutes / 60.0) * 4) / 4  # round to nearest quarter hour
            if loss_hours <= 0:
                loss_hours = 0.25
            occ = Occurrence.objects.create(
                user=e.user,
                occurrence_type=OccurrenceType.UNPLANNED,
                subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE,
                date=e.date,
                duration_hours=loss_hours,
                payroll_period=period,
            )
            occ.save()


@require_POST
@login_required
def close_payroll(request):
    if not request.user.is_staff:
        return redirect("attendance:dashboard")

    try:
        week_ending = date.fromisoformat(request.POST.get("week_ending"))
    except (TypeError, ValueError):
        messages.error(request, "Invalid week selected.")
        return redirect("attendance:payroll")

    week_start = week_ending - timedelta(days=6)

    # Ensure holiday occurrences exist for this payroll week
    ensure_holiday_occurrences_for_range(week_start, week_ending)

    incomplete_entries = TimeEntry.objects.filter(
        date__range=[week_start, week_ending]
    ).filter(
        Q(clock_in__isnull=True) |
        Q(clock_out__isnull=True) |
        Q(lunch_in__isnull=True) |
        Q(lunch_out__isnull=True)
    )

    if incomplete_entries.exists():
        messages.error(request, "Cannot close payroll. Some time entries are incomplete.")
        return redirect("attendance:payroll")

    period, _ = PayrollPeriod.objects.get_or_create(week_ending=week_ending, defaults={"is_finalized": False})

    if not period.is_finalized:
        # High level: compare each user's schedule to (time entries + approved time off). Shortfall not covered
        # by approved time off gets an auto-generated occurrence; PTO/personal applied per policy; then accrue
        # PTO for 0-2 yr / part-time on time-entry hours only (1 hr per 30 worked, cap 72 for part-time).
        users = sorted(
            CustomUser.objects.filter(is_active=True, is_exempt=False),
            key=_payroll_sort_key,
        )

        # Build total worked (time entries only) and total scheduled per user for the week
        user_total_worked = {}
        user_total_scheduled = {}
        for user in users:
            entries = TimeEntry.objects.filter(user=user, date__range=[week_start, week_ending])
            total_worked_hours = 0
            for e in entries:
                if e.clock_in and e.clock_out:
                    total_worked_hours += e.reported_worked_hours()
            user_total_worked[user.id] = total_worked_hours
            user_total_scheduled[user.id] = _scheduled_hours_for_range(user, week_start, week_ending)

        _create_tardy_occurrences_for_week(week_start, week_ending, period=period)

        # Variance occurrences: only when user has a schedule and reported total falls short.
        # Reported = time entries (punches/manual) + approved time off for the week. No default hours.
        approved_time_off_by_user_date = {}
        for user in users:
            approved_time_off_by_user_date[user.id] = {}
            qs = Occurrence.objects.filter(
                user=user,
                date__range=[week_start, week_ending],
                time_off_request__status=TimeOffRequestStatus.APPROVED,
            )
            for occ in qs.values("date", "duration_hours"):
                d = occ["date"]
                approved_time_off_by_user_date[user.id][d] = (
                    approved_time_off_by_user_date[user.id].get(d, 0) + occ["duration_hours"]
                )

        for user in users:
            total_worked_hours = user_total_worked.get(user.id, 0)
            total_scheduled = _scheduled_hours_for_range(user, week_start, week_ending)
            if total_scheduled <= 0:
                continue  # No schedule: do not create any variance
            current = week_start
            while current <= week_ending:
                scheduled_day = _scheduled_hours_for_day(user, current)
                if scheduled_day <= 0:
                    current += timedelta(days=1)
                    continue
                entries_day = TimeEntry.objects.filter(user=user, date=current)
                worked_day = 0
                for e in entries_day:
                    if e.clock_in and e.clock_out:
                        worked_day += e.reported_worked_hours()
                approved_day = approved_time_off_by_user_date.get(user.id, {}).get(current, 0)
                # Include tardy (and any existing variance) so we don't create duplicate shortfall for same time
                tardy_or_variance_hours = sum(
                    Occurrence.objects.filter(
                        user=user,
                        date=current,
                    )
                    .filter(
                        Q(subtype__in=[OccurrenceSubtype.TARDY_IN_GRACE, OccurrenceSubtype.TARDY_OUT_OF_GRACE])
                        | Q(is_variance_to_schedule=True)
                    )
                    .values_list("duration_hours", flat=True)
                )
                reported_day = worked_day + approved_day + tardy_or_variance_hours
                shortfall_day = max(0, round(scheduled_day - reported_day, 2))
                # Create variance only when shortfall exists and not already covered by approved time off
                # (approved_day is in reported_day, so shortfall is the gap; no occurrence if shortfall is 0)
                if shortfall_day > 0:
                    has_variance = Occurrence.objects.filter(
                        user=user,
                        date=current,
                        is_variance_to_schedule=True,
                    ).exists()
                    if not has_variance:
                        expected_week_hours = user_total_scheduled.get(user.id, total_scheduled)
                        subtype = (
                            OccurrenceSubtype.EXCHANGE
                            if total_worked_hours >= expected_week_hours
                            else OccurrenceSubtype.TIME_OFF
                        )
                        Occurrence.objects.create(
                            user=user,
                            occurrence_type=OccurrenceType.UNPLANNED,
                            subtype=subtype,
                            date=current,
                            duration_hours=round(shortfall_day, 2),
                            pto_applied=False,
                            is_variance_to_schedule=True,
                            payroll_period=period,
                        )
                current += timedelta(days=1)

        # Apply PTO before accrual (use current balance, not hours earned this week).
        # EXCHANGE when user worked 40+ gets no PTO applied.
        week_occurrences = list(
            Occurrence.objects.filter(
                date__range=[week_start, week_ending],
                subtype__in=OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL,
                pto_applied=False,
            )
            .exclude(subtype=OccurrenceSubtype.EXCHANGE)
            .select_related("user")
            .order_by("user_id", "date")
        )
        # Also include EXCHANGE only when user worked < 40 (then apply PTO)
        exchange_occurrences = list(
            Occurrence.objects.filter(
                date__range=[week_start, week_ending],
                subtype=OccurrenceSubtype.EXCHANGE,
                pto_applied=False,
            ).select_related("user").order_by("user_id", "date")
        )
        for occ in exchange_occurrences:
            worked = user_total_worked.get(occ.user_id, 0)
            expected = user_total_scheduled.get(occ.user_id)
            if expected is None:
                expected = _scheduled_hours_for_range(occ.user, week_start, week_ending)
                user_total_scheduled[occ.user_id] = expected
            # Only apply PTO/personal to EXCHANGE when the user did not meet their
            # scheduled hours for the week.
            if worked < expected:
                week_occurrences.append(occ)
        week_occurrences.sort(key=lambda o: (o.user_id, o.date))

        # Apply PTO first (then personal) to all occurrences per policy; no cap so full shortfall is covered
        for occ in week_occurrences:
            occ.apply_pto()

        # Accrue PTO for the week (after applying so balance used is pre-accrual).
        # Policy: FT under 2 yr and part-time accrue 1 hr PTO per 30 hrs worked (time entries only, not PTO from requests).
        # Cap at 40 regular hours so overtime does not accrue.
        for user in users:
            total_worked_hours = user_total_worked.get(user.id, 0)  # from reported (quarter-hour) time entries only
            if total_worked_hours and (user.years_of_service() <= 2 or user.is_part_time):
                user.refresh_from_db()  # use balance after apply_pto so accrual doesn't overwrite
                hours_for_accrual = min(total_worked_hours, 40.0)
                accrued = user.accrue_pto(hours_for_accrual)
                if accrued:
                    PayrollPeriodUserSnapshot.objects.update_or_create(
                        period=period,
                        user=user,
                        defaults={"pto_accrued_hours": round(accrued, 2)},
                    )

        period.is_finalized = True
        period.finalized_at = django_tz.now()
        period.finalized_by = request.user
        period.save()
        messages.success(request, f"Payroll for week ending {week_ending} has been finalized and CSV exported.")
    else:
        messages.info(request, "This payroll period is already finalized. CSV exported for records.")

    response = HttpResponse(content_type='text/csv')
    filename = f"payroll_week_ending_{week_ending.strftime('%Y-%m-%d')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    users = sorted(
        CustomUser.objects.filter(is_active=True, is_exempt=False),
        key=_payroll_sort_key,
    )
    total_worked_all = 0.0
    total_overtime_all = 0.0
    total_pto_all = 0.0
    total_holiday_all = 0.0
    for user in users:
        entries = TimeEntry.objects.filter(user=user, date__range=[week_start, week_ending])
        total_worked_hours = 0
        for e in entries:
            if e.clock_in and e.clock_out:
                total_worked_hours += e.reported_worked_hours()

        pto_occurrences = Occurrence.objects.filter(
            user=user,
            date__range=[week_start, week_ending],
            pto_applied=True,
        ).exclude(subtype=OccurrenceSubtype.HOLIDAY_PAID)
        # "Applied PTO" should only include PTO deducted, not total occurrence hours.
        pto_hours = sum(o.pto_hours_applied for o in pto_occurrences)

        holiday_occurrences = Occurrence.objects.filter(
            user=user,
            date__range=[week_start, week_ending],
            subtype=OccurrenceSubtype.HOLIDAY_PAID,
        )
        holiday_hours = sum(o.duration_hours for o in holiday_occurrences)

        worked_hours_capped = min(total_worked_hours, 40)
        overtime = max(total_worked_hours - 40, 0)
        total_worked_all += worked_hours_capped
        total_overtime_all += overtime
        total_pto_all += pto_hours
        total_holiday_all += holiday_hours
        writer.writerow(
            [
                user.payroll_last_name_for_display() or "",
                user.payroll_first_name_for_display() or "",
                round(worked_hours_capped, 2),
                round(overtime, 2),
                round(pto_hours, 2),
                round(holiday_hours, 2),
            ]
        )

    writer.writerow(
        [
            "",
            "",
            round(total_worked_all, 2),
            round(total_overtime_all, 2),
            round(total_pto_all, 2),
            round(total_holiday_all, 2),
        ]
    )

    return response


def _unfinalize_payroll_revert(period):
    """
    Revert all effects of finalizing this payroll period: PTO accrued, occurrence PTO applied,
    and delete occurrences created at finalize (variance + tardy).
    """
    week_start = period.week_ending - timedelta(days=6)

    # 1. Revert PTO accrued for this period (round so add/subtract cancel exactly)
    for snapshot in period.user_snapshots.select_related("user"):
        u = snapshot.user
        accrued = round(snapshot.pto_accrued_hours, 2)
        u.pto_balance = round(max(0.0, u.pto_balance - accrued), 2)
        u.save()
    period.user_snapshots.all().delete()

    # 2. Refund PTO/personal only for occurrences created at finalize (this period), then delete them
    period_occurrences = Occurrence.objects.filter(
        payroll_period=period,
        pto_applied=True,
    ).exclude(subtype=OccurrenceSubtype.HOLIDAY_PAID).select_related("user")
    # Aggregate refunds by user so we apply correct totals (same user can have variance + tardy)
    refunds = (
        period_occurrences.values("user")
        .annotate(
            pto_refund=Sum("pto_hours_applied"),
            personal_refund=Sum("personal_hours_applied"),
        )
    )
    for r in refunds:
        u = CustomUser.objects.get(pk=r["user"])
        u.pto_balance = round(u.pto_balance + (r["pto_refund"] or 0), 2)
        u.personal_time_balance = round(max(0.0, u.personal_time_balance - (r["personal_refund"] or 0)), 2)
        u.save()
    # Delete occurrences created at finalize so they can be re-created on refinalize
    Occurrence.objects.filter(payroll_period=period).delete()


@require_POST
@login_required
def unfinalize_payroll(request):
    if not request.user.is_staff:
        return redirect("attendance:dashboard")
    try:
        week_ending = date.fromisoformat(request.POST.get("week_ending"))
    except (TypeError, ValueError):
        messages.error(request, "Invalid week selected.")
        return redirect("attendance:payroll")
    period = get_object_or_404(PayrollPeriod, week_ending=week_ending)
    if not period.is_finalized:
        messages.info(request, "That payroll period is not finalized.")
        return redirect("attendance:payroll")
    _unfinalize_payroll_revert(period)
    period.is_finalized = False
    period.finalized_at = None
    period.finalized_by = None
    period.save()
    messages.success(request, f"Payroll for week ending {week_ending} has been unfinalized. PTO accrual and absence PTO have been reverted.")
    return redirect("attendance:payroll")


@login_required
def edit_entry(request, slug):
    entry = get_object_or_404(TimeEntry, slug=slug)

    if request.user.role not in [
        RoleChoices.GROUP_LEAD,
        RoleChoices.SUPERVISOR,
        RoleChoices.MANAGER,
        RoleChoices.EXECUTIVE,
    ]:
        return redirect("attendance:dashboard")

    week_ending = _week_ending_for_date(entry.date)
    _payroll_ok = request.user.role == RoleChoices.EXECUTIVE

    if _is_payroll_week_finalized(week_ending):
        if request.method == "POST":
            messages.error(
                request,
                "This time entry is in a finalized payroll week. Unfinalize payroll for that week to make corrections.",
            )
            return redirect("attendance:payroll" if _payroll_ok else "attendance:dashboard")
        messages.warning(
            request,
            "This week is finalized. Unfinalize payroll to edit.",
        )

    if request.method == "POST":
        form = TimeEntryForm(request.POST, instance=entry)
        if form.is_valid():
            entry = form.save()
            if not _is_payroll_week_finalized(week_ending):
                from timeclock.tardy_sync import sync_tardy_occurrences_for_time_entry

                sync_tardy_occurrences_for_time_entry(entry)
            return redirect("attendance:payroll" if _payroll_ok else "attendance:dashboard")
    else:
        form = TimeEntryForm(instance=entry)

    return render(request, "timeclock/edit_entry.html", {
        "form": form,
        "entry": entry,
        "payroll_finalized": _is_payroll_week_finalized(week_ending),
    })

@login_required
def generate_report_pdf(request):
    if not user_can_view_reports(request.user):
        return redirect("attendance:dashboard")

    form = ReportFilterForm(request.GET)
    if form.is_valid():
        user = form.cleaned_data["user"]
        start_date = form.cleaned_data["start_date"]
        end_date = form.cleaned_data["end_date"]
        occurrences = Occurrence.objects.filter(
            user=user, date__range=(start_date, end_date)
        ).order_by("date")

        # Ensure current balance reflects any occurrences that became due.
        apply_past_due_occurrences(user)
        user.refresh_from_db()

        fmla_st = ABSENCE_REPORT_FMLA_SUBTYPE
        leave_no_personal = ABSENCE_REPORT_LEAVE_AND_NO_PERSONAL_SUBTYPES

        occurrences_fmla = occurrences.filter(subtype=fmla_st)
        occurrences_leave_group = occurrences.filter(subtype__in=leave_no_personal)
        occurrences_main = occurrences.exclude(subtype=fmla_st).exclude(subtype__in=leave_no_personal)

        # PTO/Personal used (headline): main bucket only — excludes FMLA and leave/no-personal subtypes
        pto_using_main = Occurrence.objects.filter(
            user=user,
            date__range=(start_date, end_date),
            pto_applied=True,
        ).exclude(subtype=OccurrenceSubtype.HOLIDAY_PAID).exclude(subtype=fmla_st).exclude(
            subtype__in=leave_no_personal
        )
        pto_used = sum(o.pto_hours_applied for o in pto_using_main)
        personal_used = sum(o.personal_hours_applied for o in pto_using_main)
        legacy_hours = sum(
            o.duration_hours
            for o in pto_using_main
            if o.pto_hours_applied == 0 and o.personal_hours_applied == 0 and o.duration_hours
        )
        if legacy_hours and pto_used == 0 and personal_used == 0:
            pto_used = legacy_hours

        grace_pg = occurrences_main.aggregate(t=Sum("probation_grace_hours_applied"))["t"] or 0
        if grace_pg:
            grace_time_used = float(grace_pg)
        else:
            grace_time_used = float(
                occurrences_main.filter(subtype=OccurrenceSubtype.GRACE_TIME).aggregate(
                    t=Sum("duration_hours")
                )["t"]
                or 0
            )

        fmla_hours_agg = occurrences_fmla.aggregate(t=Sum("duration_hours"))
        fmla_used_hours = float(fmla_hours_agg["t"] or 0)
        fmla_pto_agg = occurrences_fmla.filter(pto_applied=True).aggregate(t=Sum("pto_hours_applied"))
        fmla_pto_applied_total = float(fmla_pto_agg["t"] or 0)

        leave_group_hours_agg = occurrences_leave_group.aggregate(t=Sum("duration_hours"))
        leave_group_total_hours = float(leave_group_hours_agg["t"] or 0)
        leave_group_pto_agg = occurrences_leave_group.filter(pto_applied=True).aggregate(
            t=Sum("pto_hours_applied")
        )
        leave_group_pto_applied_total = float(leave_group_pto_agg["t"] or 0)

        static_dirs = getattr(settings, "STATICFILES_DIRS", []) or []
        static_root = Path(static_dirs[0]) if static_dirs else Path(settings.BASE_DIR) / "static"
        img_dir = static_root / "img"
        logo_uri = None
        # Prefer pdfimage.jpg for report branding, then logo.webp / logo.png
        for name in ("pdfimage.jpg", "logo.webp", "logo.png"):
            logo_path = img_dir / name
            if not logo_path.exists():
                continue
            try:
                raw = logo_path.read_bytes()
                if name.endswith(".webp"):
                    from PIL import Image
                    img = Image.open(BytesIO(raw))
                    buf = BytesIO()
                    img.save(buf, format="PNG")
                    raw = buf.getvalue()
                    mime = "image/png"
                elif name.endswith(".jpg") or name.endswith(".jpeg"):
                    mime = "image/jpeg"
                else:
                    mime = "image/png"
                b64 = base64.b64encode(raw).decode("ascii")
                logo_uri = f"data:{mime};base64,{b64}"
            except Exception:
                logo_uri = None
            break

        template = get_template("attendance/report_pdf_template.html")
        html = template.render({
            "user": user,
            "occurrences_main": occurrences_main,
            "occurrences_fmla": occurrences_fmla,
            "occurrences_leave_group": occurrences_leave_group,
            "start": start_date,
            "end": end_date,
            "logo_uri": logo_uri,
            "pto_used": pto_used,
            "personal_used": personal_used,
            "pto_remaining": user.pto_balance,
            "fmla_used_hours": fmla_used_hours,
            "fmla_pto_applied_total": fmla_pto_applied_total,
            "grace_time_used": grace_time_used,
            "leave_group_total_hours": leave_group_total_hours,
            "leave_group_pto_applied_total": leave_group_pto_applied_total,
            "has_fmla_rows": occurrences_fmla.exists(),
            "has_leave_group_rows": occurrences_leave_group.exists(),
        })
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{user.username}_report.pdf"'
        pisa.CreatePDF(html, dest=response)
        return response
    return redirect("attendance:dashboard")


@login_required
def perfect_attendance_pdf(request):
    """PDF of Perfect Attendance (same company-wide qualifying list as dashboard; hours per executive policy)."""
    request_user = request.user
    today = date.today()

    pa_year_str = request.GET.get("pa_year")
    pa_month_str = request.GET.get("pa_month")
    pa_year = None
    pa_month = None
    if pa_year_str and pa_month_str:
        try:
            py, pm = int(pa_year_str), int(pa_month_str)
            if 1 <= pm <= 12 and 2000 <= py <= 2100:
                pa_year, pa_month = py, pm
        except (ValueError, TypeError):
            pass
    if pa_year is None:
        if today.day == 1:
            if today.month == 1:
                pa_year, pa_month = today.year - 1, 12
            else:
                pa_year, pa_month = today.year, today.month - 1
        else:
            pa_year, pa_month = today.year, today.month

    pa_first = date(pa_year, pa_month, 1)
    pa_last = _last_day_of_month(pa_year, pa_month)
    if pa_first > today:
        messages.error(request, "Choose a month that has already started.")
        return redirect("attendance:dashboard")

    pa_period_end = min(pa_last, today)
    if pa_period_end < pa_last:
        pa_period_description = (
            f"{pa_first:%B %d} – {pa_period_end:%B %d, %Y} (month to date)"
        )
    else:
        pa_period_description = f"{pa_first:%B %Y}"

    perfect_attendance_rows = _perfect_attendance_with_hours(
        _perfect_attendance_candidate_users(), pa_first, pa_period_end
    )
    pa_hours_total = sum(r["total_hours"] for r in perfect_attendance_rows)

    static_dirs = getattr(settings, "STATICFILES_DIRS", []) or []
    static_root = Path(static_dirs[0]) if static_dirs else Path(settings.BASE_DIR) / "static"
    img_dir = static_root / "img"
    logo_uri = None
    for name in ("pdfimage.jpg", "logo.webp", "logo.png"):
        logo_path = img_dir / name
        if not logo_path.exists():
            continue
        try:
            raw = logo_path.read_bytes()
            if name.endswith(".webp"):
                from PIL import Image

                img = Image.open(BytesIO(raw))
                buf = BytesIO()
                img.save(buf, format="PNG")
                raw = buf.getvalue()
                mime = "image/png"
            elif name.endswith(".jpg") or name.endswith(".jpeg"):
                mime = "image/jpeg"
            else:
                mime = "image/png"
            b64 = base64.b64encode(raw).decode("ascii")
            logo_uri = f"data:{mime};base64,{b64}"
        except Exception:
            logo_uri = None
        break

    template = get_template("attendance/perfect_attendance_pdf.html")
    show_pa_hours = request_user.role == RoleChoices.EXECUTIVE
    html = template.render(
        {
            "logo_uri": logo_uri,
            "period_description": pa_period_description,
            "rows": perfect_attendance_rows,
            "pa_hours_total": pa_hours_total,
            "generated_at": today,
            "show_perfect_attendance_hours": show_pa_hours,
        }
    )
    response = HttpResponse(content_type="application/pdf")
    fname = f"perfect_attendance_{pa_year}_{pa_month:02d}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{fname}"'
    pisa.CreatePDF(html, dest=response)
    return response


@login_required
def request_time_off(request):
    """
    Allow a user to request PTO for full scheduled days within a single payroll week.
    """
    user = request.user

    if request.method == "POST":
        form = TimeOffRequestForm(request.POST, request_user=user)
        if form.is_valid():
            tor = form.save(commit=False)
            tor.user = user
            tor.save()
            approval_emails.notify_time_off_submitted(tor)
            messages.success(request, "Time off request submitted.")
            return redirect("attendance:my_time_off_requests")
    else:
        form = TimeOffRequestForm(request_user=user)

    my_requests = TimeOffRequest.objects.filter(user=user).order_by("-created_at")

    return render(
        request,
        "attendance/request_time_off.html",
        {"form": form, "my_requests": my_requests},
    )


@login_required
def my_time_off_requests(request):
    """
    Simple list view for a user to see their own requests and statuses.
    """
    user = request.user
    my_requests = TimeOffRequest.objects.filter(user=user).order_by("-created_at")
    return render(
        request,
        "attendance/my_time_off_requests.html",
        {"my_requests": my_requests},
    )


@login_required
def team_time_off_requests(request):
    """
    For approvers to see pending requests for users they manage.
    Executives see all pending requests.
    """
    approver = request.user

    if approver.role not in [
        RoleChoices.GROUP_LEAD,
        RoleChoices.SUPERVISOR,
        RoleChoices.MANAGER,
        RoleChoices.EXECUTIVE,
    ]:
        return redirect("attendance:dashboard")

    pending = TimeOffRequest.objects.filter(status=TimeOffRequestStatus.PENDING)
    # Filter to only those the current user can approve
    manageable = [r.id for r in pending if can_approve_time_off(approver, r.user)]
    pending = pending.filter(id__in=manageable)

    pending_wtl = WorkThroughLunchRequest.objects.filter(status=TimeOffRequestStatus.PENDING)
    manageable_wtl = [r.id for r in pending_wtl if can_approve_time_off(approver, r.user)]
    pending_wtl = pending_wtl.filter(id__in=manageable_wtl).select_related("user")

    pending_adjust = AdjustPunchRequest.objects.filter(status=TimeOffRequestStatus.PENDING)
    manageable_adj = [r.id for r in pending_adjust if can_approve_time_off(approver, r.user)]
    pending_adjust = pending_adjust.filter(id__in=manageable_adj).select_related("user", "time_entry")

    # For each pending request, surface overlapping approved requests so approvers
    # can see who else is already out in the same timeframe.
    for req in pending:
        overlapping_other = (
            TimeOffRequest.objects.filter(
                status__in=[TimeOffRequestStatus.APPROVED, TimeOffRequestStatus.PENDING],
                start_date__lte=req.end_date,
                end_date__gte=req.start_date,
            )
            .exclude(pk=req.pk)
            .exclude(user=req.user)
            .select_related("user")
            .order_by("start_date", "user__last_name", "user__first_name")
        )
        req.other_requests_display = [
            f"{o.user.payroll_display_name()} ({o.start_date} - {o.end_date}) [{o.status.upper()}]"
            for o in overlapping_other
        ]

    return render(
        request,
        "attendance/team_time_off_requests.html",
        {
            "pending_requests": pending,
            "pending_work_through_lunch": pending_wtl,
            "pending_adjust_punch": pending_adjust,
        },
    )


@require_POST
@login_required
def approve_time_off(request, slug):
    tor = get_object_or_404(TimeOffRequest, slug=slug)
    approver = request.user

    if not can_approve_time_off(approver, tor.user):
        messages.error(request, "You do not have permission to approve this request.")
        return redirect("attendance:team_time_off_requests")

    tor.approve(approver)
    messages.success(request, "Time off request approved and PTO applied.")
    return redirect("attendance:team_time_off_requests")


@require_POST
@login_required
def deny_time_off(request, slug):
    tor = get_object_or_404(TimeOffRequest, slug=slug)
    approver = request.user

    if not can_approve_time_off(approver, tor.user):
        messages.error(request, "You do not have permission to deny this request.")
        return redirect("attendance:team_time_off_requests")

    tor.deny(approver)
    messages.info(request, "Time off request denied.")
    return redirect("attendance:team_time_off_requests")


@require_POST
@login_required
def cancel_time_off(request, slug):
    tor = get_object_or_404(TimeOffRequest, slug=slug)
    if tor.user != request.user:
        messages.error(request, "You can only cancel your own requests.")
        return redirect("attendance:my_time_off_requests")
    if tor.status not in (TimeOffRequestStatus.PENDING, TimeOffRequestStatus.APPROVED):
        messages.error(request, "Only pending or approved requests can be cancelled.")
        return redirect("attendance:my_time_off_requests")
    was_approved = tor.status == TimeOffRequestStatus.APPROVED
    tor.cancel()
    approval_emails.notify_time_off_cancelled(tor, was_approved=was_approved)
    messages.success(
        request,
        "Time off request cancelled." + (" PTO has been credited back." if was_approved else ""),
    )
    next_url = request.POST.get("next") or request.GET.get("next") or request.META.get("HTTP_REFERER")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect("attendance:my_time_off_requests")


@login_required
def request_work_through_lunch(request):
    """Submit a request to work through a scheduled lunch (no automatic lunch deduction)."""
    user = request.user

    if request.method == "POST":
        form = WorkThroughLunchRequestForm(request.POST, request_user=user)
        if form.is_valid():
            wtl = form.save(commit=False)
            wtl.user = user
            wtl.save()
            approval_emails.notify_work_through_lunch_submitted(wtl)
            messages.success(request, "Work-through-lunch request submitted.")
            return redirect("attendance:request_work_through_lunch")
    else:
        form = WorkThroughLunchRequestForm(request_user=user)

    my_requests = WorkThroughLunchRequest.objects.filter(user=user).order_by("-created_at")

    return render(
        request,
        "attendance/request_work_through_lunch.html",
        {"form": form, "my_requests": my_requests},
    )


@require_POST
@login_required
def approve_work_through_lunch(request, slug):
    wtl = get_object_or_404(WorkThroughLunchRequest, slug=slug)
    approver = request.user

    if not can_approve_time_off(approver, wtl.user):
        messages.error(request, "You do not have permission to approve this request.")
        return redirect("attendance:team_time_off_requests")

    wtl.approve(approver)
    entry = TimeEntry.objects.filter(user=wtl.user, date=wtl.work_date).first()
    if entry and entry.clock_in and entry.clock_out:
        scheduled = scheduled_lunch_datetimes_for_entry(entry)
        if scheduled:
            lo, li = scheduled
            if entry.lunch_out == lo and entry.lunch_in == li:
                entry.lunch_out = None
                entry.lunch_in = None
        # Persist so missing_punch_flagged clears when the day is complete (e.g. work-through lunch).
        entry.save()
    messages.success(request, "Work-through-lunch request approved.")
    return redirect("attendance:team_time_off_requests")


@require_POST
@login_required
def deny_work_through_lunch(request, slug):
    wtl = get_object_or_404(WorkThroughLunchRequest, slug=slug)
    approver = request.user

    if not can_approve_time_off(approver, wtl.user):
        messages.error(request, "You do not have permission to deny this request.")
        return redirect("attendance:team_time_off_requests")

    wtl.deny(approver)
    messages.info(request, "Work-through-lunch request denied.")
    return redirect("attendance:team_time_off_requests")


@require_POST
@login_required
def cancel_work_through_lunch(request, slug):
    wtl = get_object_or_404(WorkThroughLunchRequest, slug=slug)
    if wtl.user != request.user:
        messages.error(request, "You can only cancel your own requests.")
        return redirect("attendance:request_work_through_lunch")
    if wtl.status not in (TimeOffRequestStatus.PENDING, TimeOffRequestStatus.APPROVED):
        messages.error(request, "Only pending or approved requests can be cancelled.")
        return redirect("attendance:request_work_through_lunch")
    was_approved = wtl.status == TimeOffRequestStatus.APPROVED
    wtl.cancel()
    approval_emails.notify_work_through_lunch_cancelled(wtl, was_approved=was_approved)
    if was_approved:
        entry = TimeEntry.objects.filter(user=wtl.user, date=wtl.work_date).first()
        if entry:
            entry.save()
    messages.success(request, "Work-through-lunch request cancelled.")
    next_url = request.POST.get("next") or request.GET.get("next") or request.META.get("HTTP_REFERER")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect("attendance:request_work_through_lunch")


@login_required
def request_adjust_punch(request):
    """Submit a request to correct a recorded punch time for a day in the selected week."""
    user = request.user
    today = date.today()
    payroll_weeks_list = get_recent_saturdays(12)
    if not payroll_weeks_list:
        payroll_weeks_list = [_week_ending_for_date(today)]

    week_ending_str = request.GET.get("week_ending")
    if week_ending_str:
        try:
            end_of_week = date.fromisoformat(week_ending_str)
        except ValueError:
            end_of_week = _week_ending_for_date(today)
    else:
        end_of_week = _week_ending_for_date(today)

    week_start = end_of_week - timedelta(days=6)
    entries = TimeEntry.objects.filter(user=user, date__range=[week_start, end_of_week]).order_by("date")

    if request.method == "POST":
        form = AdjustPunchRequestForm(request.POST, request_user=user, time_entry_queryset=entries)
        if form.is_valid():
            entry = form.cleaned_data["_entry"]
            apr = AdjustPunchRequest(
                user=user,
                time_entry=entry,
                punch_field=form.cleaned_data["punch_field"],
                previous_at=getattr(entry, form.cleaned_data["punch_field"]),
                requested_at=form.cleaned_data["requested_at"],
                comments=form.cleaned_data.get("comments") or "",
            )
            apr.save()
            approval_emails.notify_adjust_punch_submitted(apr)
            messages.success(request, "Adjust punch request submitted.")
            return redirect("attendance:request_adjust_punch")
    else:
        form = AdjustPunchRequestForm(request_user=user, time_entry_queryset=entries)

    my_requests = (
        AdjustPunchRequest.objects.filter(user=user)
        .select_related("time_entry")
        .order_by("-created_at")[:50]
    )
    payroll_weeks_display = [(d.strftime("%Y-%m-%d"), d.strftime("%m/%d/%Y")) for d in payroll_weeks_list]
    week_keys = [d.strftime("%Y-%m-%d") for d in payroll_weeks_list]
    if end_of_week.strftime("%Y-%m-%d") not in week_keys:
        payroll_weeks_display.insert(0, (end_of_week.strftime("%Y-%m-%d"), end_of_week.strftime("%m/%d/%Y")))

    return render(
        request,
        "attendance/request_adjust_punch.html",
        {
            "form": form,
            "my_requests": my_requests,
            "week_ending": end_of_week,
            "payroll_weeks_display": payroll_weeks_display,
        },
    )


@login_required
def adjust_punch_my_week_json(request):
    """JSON for adjust-punch page: current user's time entries for the selected week."""
    week_ending_param = request.GET.get("week_ending")
    if not week_ending_param:
        return JsonResponse({"error": "missing week_ending"}, status=400)
    try:
        week_ending = date.fromisoformat(week_ending_param)
    except ValueError:
        return JsonResponse({"error": "invalid week_ending"}, status=400)

    week_start = week_ending - timedelta(days=6)
    user = request.user
    entries = TimeEntry.objects.filter(user=user, date__range=[week_start, week_ending]).order_by("date")

    def iso(dt):
        if not dt:
            return None
        return django_tz.localtime(dt).isoformat()

    def fmt_ampm(dt):
        if not dt:
            return None
        return django_tz.localtime(dt).strftime("%I:%M %p").lstrip("0")

    days = []
    for e in entries:
        days.append(
            {
                "date": e.date.isoformat(),
                "slug": e.slug,
                "clock_in": iso(e.clock_in),
                "lunch_out": iso(e.lunch_out),
                "lunch_in": iso(e.lunch_in),
                "clock_out": iso(e.clock_out),
                "clock_in_display": fmt_ampm(e.clock_in),
                "lunch_out_display": fmt_ampm(e.lunch_out),
                "lunch_in_display": fmt_ampm(e.lunch_in),
                "clock_out_display": fmt_ampm(e.clock_out),
            }
        )

    return JsonResponse(
        {
            "week_start": week_start.isoformat(),
            "week_ending": week_ending.isoformat(),
            "days": days,
        }
    )


@require_POST
@login_required
def approve_adjust_punch(request, slug):
    apr = get_object_or_404(AdjustPunchRequest, slug=slug)
    approver = request.user

    if not can_approve_time_off(approver, apr.user):
        messages.error(request, "You do not have permission to approve this request.")
        return redirect("attendance:team_time_off_requests")

    if apr.status != TimeOffRequestStatus.PENDING:
        messages.error(request, "This request is no longer pending.")
        return redirect("attendance:team_time_off_requests")

    week_ending = _week_ending_for_date(apr.time_entry.date)
    if _is_payroll_week_finalized(week_ending):
        messages.error(
            request,
            "That payroll week is finalized. Unfinalize payroll before approving this adjustment.",
        )
        return redirect("attendance:team_time_off_requests")

    try:
        with transaction.atomic():
            u = CustomUser.objects.select_for_update().get(pk=apr.user_id)
            revert_tardy_occurrences_for_adjust_punch(u, apr.time_entry.date)
            entry = TimeEntry.objects.select_for_update().get(pk=apr.time_entry_id)
            setattr(entry, apr.punch_field, apr.requested_at)
            entry.save()
            if apr.punch_field == AdjustPunchField.CLOCK_IN:
                entry.check_tardy()
            elif apr.punch_field == AdjustPunchField.LUNCH_IN:
                entry.check_lunch_tardy()
            apr.approver = approver
            apr.status = TimeOffRequestStatus.APPROVED
            apr.save()
    except Exception:
        messages.error(request, "Could not apply the punch adjustment. Try again or contact support.")
        return redirect("attendance:team_time_off_requests")

    messages.success(request, "Adjust punch request approved; time entry updated.")
    return redirect("attendance:team_time_off_requests")


@require_POST
@login_required
def deny_adjust_punch(request, slug):
    apr = get_object_or_404(AdjustPunchRequest, slug=slug)
    approver = request.user

    if not can_approve_time_off(approver, apr.user):
        messages.error(request, "You do not have permission to deny this request.")
        return redirect("attendance:team_time_off_requests")

    apr.deny(approver)
    messages.info(request, "Adjust punch request denied.")
    return redirect("attendance:team_time_off_requests")


@require_POST
@login_required
def cancel_adjust_punch(request, slug):
    apr = get_object_or_404(AdjustPunchRequest, slug=slug)
    if apr.user != request.user:
        messages.error(request, "You can only cancel your own requests.")
        return redirect("attendance:request_adjust_punch")
    if apr.status != TimeOffRequestStatus.PENDING:
        messages.error(request, "Only pending requests can be cancelled.")
        return redirect("attendance:request_adjust_punch")
    apr.cancel()
    approval_emails.notify_adjust_punch_cancelled(apr)
    messages.success(request, "Adjust punch request cancelled.")
    next_url = request.POST.get("next") or request.GET.get("next") or request.META.get("HTTP_REFERER")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect("attendance:request_adjust_punch")