from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django import forms
from django.utils import timezone as django_tz
from django.utils.timezone import now, localdate
from timeclock.models import TimeEntry
from timeclock.forms import TimeEntryForm
from django.db import transaction
from django.db.models import Sum, Q
from calendar import month_name, monthrange
from datetime import time, timedelta, date, datetime, timezone
from django.http import HttpResponse
from django.http import JsonResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from .models import (
    CustomUser,
    Occurrence,
    OccurrenceType,
    OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL,
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
)
from .forms import ReportFilterForm, TimeOffRequestForm, WorkThroughLunchRequestForm, AdjustPunchRequestForm
from .schedule_utils import (
    get_scheduled_start_for_day,
    scheduled_duration_hours_for_day,
    scheduled_hours_for_range,
    scheduled_lunch_datetimes_for_entry,
)
from django.views.decorators.http import require_POST
from django.conf import settings
from pathlib import Path
import base64
import csv
import json
from io import BytesIO

def home(request):
    return render(request, "pages/index.html")

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


def _scheduled_but_not_clocked_in(visible_users, on_date: date):
    """Users with a schedule on ``on_date`` who have not clocked in (no entry or no clock_in)."""
    out = []
    for u in visible_users:
        if get_scheduled_start_for_day(u, on_date) is None:
            continue
        entry = TimeEntry.objects.filter(user=u, date=on_date).first()
        if entry is None or entry.clock_in is None:
            out.append(u)
    out.sort(key=_payroll_sort_key)
    return out


def _users_with_no_unplanned_absences(visible_users, first: date, period_end: date):
    """All visible users with zero UNPLANNED absences in [first, period_end]."""
    names = []
    for u in visible_users:
        has_unplanned = Occurrence.objects.filter(
            user=u,
            occurrence_type=OccurrenceType.UNPLANNED,
            date__gte=first,
            date__lte=period_end,
        ).exists()
        if not has_unplanned:
            names.append(u)
    names.sort(key=_payroll_sort_key)
    return names


def _perfect_attendance_with_hours(visible_users, first: date, period_end: date):
    """
    Non-exempt users with no unplanned absences in range who have at least one completed time entry;
    total is sum of reported_worked_hours (payroll-reported time entry hours, not PTO absence hours).
    """
    rows = []
    for u in visible_users.filter(is_exempt=False):
        has_unplanned = Occurrence.objects.filter(
            user=u,
            occurrence_type=OccurrenceType.UNPLANNED,
            date__gte=first,
            date__lte=period_end,
        ).exists()
        if has_unplanned:
            continue
        entries = TimeEntry.objects.filter(user=u, date__gte=first, date__lte=period_end)
        completed = entries.filter(clock_in__isnull=False, clock_out__isnull=False)
        if not completed.exists():
            continue
        total_hours = 0.0
        for e in completed:
            total_hours += e.reported_worked_hours()
        rows.append({"user": u, "total_hours": round(total_hours, 2)})
    rows.sort(key=lambda r: _payroll_sort_key(r["user"]))
    return rows

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

    daily_occurrences = Occurrence.objects.filter(
        user__in=visible_users,
        date=selected_date
    ).order_by("user__username", "date")

    start_of_week = today - timedelta(days=(today.weekday() + 1) % 7)
    end_of_week = start_of_week + timedelta(days=6)
    weekly_totals = []

    for u in visible_users.filter(is_exempt=False):
        total_actual = 0
        total_reported = 0
        total_scheduled = 0

        entries = TimeEntry.objects.filter(user=u, date__range=[start_of_week, end_of_week])
        for entry in entries:
            if entry.clock_in and entry.clock_out:
                total_actual += entry.actual_worked_hours()
                total_reported += entry.reported_worked_hours()

            total_scheduled += scheduled_duration_hours_for_day(u, entry.date)

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
            fields = [e.clock_in, e.lunch_out, e.lunch_in, e.clock_out]
            # Only alert for completed days so active same-day shifts are not flagged.
            if e.date < today and any(fields) and not all(fields):
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
    clock_in_overrides = (
        TimeEntry.objects.filter(
            clock_in_authorized_by__isnull=False,
            user__in=visible_users,
            date__gte=override_cutoff,
        )
        .select_related("user", "clock_in_authorized_by")
        .order_by("-date", "-clock_in")[:75]
    )

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
        no_unplanned_users = []
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
        no_unplanned_users = _users_with_no_unplanned_absences(
            visible_users, pa_first, pa_period_end
        )
        perfect_attendance_rows = _perfect_attendance_with_hours(
            visible_users, pa_first, pa_period_end
        )
        pa_hours_total = sum(r["total_hours"] for r in perfect_attendance_rows)

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
        "scheduled_not_clocked": scheduled_not_clocked,
        "pa_year": pa_year,
        "pa_month": pa_month,
        "pa_first": pa_first,
        "pa_last": pa_last,
        "pa_month_choices": pa_month_choices,
        "pa_year_choices": pa_year_choices,
        "pa_period_description": pa_period_description,
        "pa_period_end": pa_period_end,
        "no_unplanned_users": no_unplanned_users,
        "perfect_attendance_rows": perfect_attendance_rows,
        "pa_hours_total": pa_hours_total,
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

    weekly_totals = []

    for u in sorted(visible_users.filter(is_exempt=False), key=_payroll_sort_key):
        total_actual = 0
        total_reported = 0

        entries = TimeEntry.objects.filter(user=u, date__range=[start_of_week, end_of_week])
        for entry in entries:
            if entry.clock_in and entry.clock_out:
                total_actual += entry.actual_worked_hours()
                total_reported += entry.reported_worked_hours()

        total_scheduled = _scheduled_hours_for_range(u, start_of_week, end_of_week)
        week_pto_personal = Occurrence.objects.filter(
            user=u,
            date__range=[start_of_week, end_of_week],
            pto_applied=True,
        ).exclude(subtype=OccurrenceSubtype.HOLIDAY_PAID)
        pto_applied = sum(o.pto_hours_applied for o in week_pto_personal)
        personal_applied = sum(o.personal_hours_applied for o in week_pto_personal)
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
            fields = [e.clock_in, e.lunch_out, e.lunch_in, e.clock_out]
            # Only alert for completed days so active same-day shifts are not flagged.
            if e.date < today and any(fields) and not all(fields):
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


def _week_ending_for_date(d):
    """Saturday of the payroll week containing date d."""
    days_until_saturday = (5 - d.weekday()) % 7
    return d + timedelta(days=days_until_saturday)


def _is_payroll_week_finalized(week_ending_date):
    """Return True if the payroll week ending on this Saturday is finalized."""
    return PayrollPeriod.objects.filter(
        week_ending=week_ending_date,
        is_finalized=True,
    ).exists()


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
            form.save()
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

        # PTO/Personal used in this report period (from tracked split per occurrence)
        pto_using = Occurrence.objects.filter(
            user=user,
            date__range=(start_date, end_date),
            pto_applied=True,
        ).exclude(subtype=OccurrenceSubtype.HOLIDAY_PAID)
        pto_used = sum(o.pto_hours_applied for o in pto_using)
        personal_used = sum(o.personal_hours_applied for o in pto_using)
        # Legacy: occurrences applied before we tracked the split (both 0)
        legacy_hours = sum(
            o.duration_hours for o in pto_using
            if o.pto_hours_applied == 0 and o.personal_hours_applied == 0 and o.duration_hours
        )
        if legacy_hours and pto_used == 0 and personal_used == 0:
            pto_used = legacy_hours

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
            "occurrences": occurrences,
            "start": start_date,
            "end": end_date,
            "logo_uri": logo_uri,
            "pto_used": pto_used,
            "personal_used": personal_used,
            "pto_remaining": user.pto_balance,
        })
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{user.username}_report.pdf"'
        pisa.CreatePDF(html, dest=response)
        return response
    return redirect("attendance:dashboard")


@login_required
def perfect_attendance_pdf(request):
    """PDF of Perfect Attendance (reported time-entry hours; same visibility as dashboard)."""
    request_user = request.user
    today = date.today()

    if request_user.role == RoleChoices.EXECUTIVE:
        visible_users = CustomUser.objects.all()
    elif request_user.role == RoleChoices.MANAGER:
        visible_users = CustomUser.objects.filter(department=request_user.department)
    elif request_user.role == RoleChoices.SUPERVISOR:
        visible_users = CustomUser.objects.filter(Q(supervisor=request_user) | Q(id=request_user.id))
    elif request_user.role == RoleChoices.GROUP_LEAD:
        visible_users = CustomUser.objects.filter(Q(group_lead=request_user) | Q(id=request_user.id))
    elif request_user.role == RoleChoices.TEAM_LEAD:
        visible_users = CustomUser.objects.filter(Q(team_lead=request_user) | Q(id=request_user.id))
    else:
        visible_users = CustomUser.objects.filter(id=request_user.id)

    visible_users = visible_users.order_by(
        "payroll_lastname",
        "payroll_firstname",
        "last_name",
        "first_name",
        "username",
    )

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
        visible_users, pa_first, pa_period_end
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
    html = template.render(
        {
            "logo_uri": logo_uri,
            "period_description": pa_period_description,
            "rows": perfect_attendance_rows,
            "pa_hours_total": pa_hours_total,
            "generated_at": today,
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
                entry.save(update_fields=["lunch_out", "lunch_in"])
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
    messages.success(request, "Adjust punch request cancelled.")
    next_url = request.POST.get("next") or request.GET.get("next") or request.META.get("HTTP_REFERER")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect("attendance:request_adjust_punch")