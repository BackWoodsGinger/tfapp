from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django import forms
from django.utils.timezone import now, localdate
from timeclock.models import TimeEntry
from timeclock.forms import TimeEntryForm
from django.db.models import Sum, Q
from datetime import time, timedelta, date, datetime, timezone
from django.http import HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from .models import CustomUser, Occurrence, RoleChoices
from .forms import ReportFilterForm
from django.views.decorators.http import require_POST
import csv

def home(request):
    return render(request, "pages/index.html")

class DateFilterForm(forms.Form):
    date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )

def get_recent_saturdays(count=5):
    today = date.today()
    days_since_saturday = (today.weekday() - 5) % 7  # 5 = Saturday
    last_saturday = today - timedelta(days=days_since_saturday)
    return sorted([last_saturday - timedelta(weeks=i) for i in range(count)], reverse=True)

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

    selected_user_id = request.GET.get("user_id")
    selected_user = get_object_or_404(CustomUser, id=selected_user_id) if selected_user_id and selected_user_id.isdigit() else user

    anniversary = selected_user.service_date.replace(year=today.year) if selected_user.service_date else today
    if today < anniversary:
        anniversary = anniversary.replace(year=today.year - 1)

    past_occurrences = Occurrence.objects.filter(
        user=selected_user, date__gte=anniversary, date__lte=today
    ).order_by('-date')

    future_occurrences = Occurrence.objects.filter(
        user=selected_user, date__gt=today
    ).order_by('date')

    daily_occurrences = Occurrence.objects.filter(
        user__in=visible_users,
        date=selected_date
    ).order_by("user__username", "date")

    future_hours = future_occurrences.aggregate(total=Sum("duration_hours"))["total"] or 0
    future_pto = max(selected_user.pto_balance - future_hours, 0)
    future_personal = selected_user.personal_time_balance + max(0, future_hours - selected_user.pto_balance)

    start_of_week = today - timedelta(days=(today.weekday() + 1) % 7)
    end_of_week = start_of_week + timedelta(days=6)
    weekly_totals = []
    for u in visible_users:
        total = 0
        entries = TimeEntry.objects.filter(user=u, date__range=[start_of_week, end_of_week])
        for e in entries:
            if e.clock_in and e.clock_out:
                lunch = timedelta()
                if e.lunch_in and e.lunch_out:
                    lunch = e.lunch_in - e.lunch_out
                total += (e.clock_out - e.clock_in - lunch).total_seconds() / 3600
        weekly_totals.append((u, round(total, 2)))

    alerts = []
    if user.is_staff:
        problem_entries = TimeEntry.objects.filter(date__range=[start_of_week, end_of_week])
        for e in problem_entries:
            fields = [e.clock_in, e.lunch_out, e.lunch_in, e.clock_out]
            if any(fields) and not all(fields):
                alerts.append(e)

    context = {
        "user_list": visible_users,
        "selected_user": selected_user,
        "selected_date": selected_date,
        "daily_occurrences": daily_occurrences,
        "past_occurrences": past_occurrences,
        "future_occurrences": future_occurrences,
        "current_pto": selected_user.pto_balance,
        "personal_time": selected_user.personal_time_balance,
        "pending_hours": future_hours,
        "balance_after_pending": future_pto,
        "final_year_balance": selected_user.final_pto_balance,
        "future_personal": future_personal,
        "today": today,
        "weekly_totals": weekly_totals,
        "alerts": alerts,
        "start_of_week": start_of_week,
        "end_of_week": end_of_week,
    }
    return render(request, "attendance/dashboard.html", context)

@login_required
def attendance_list(request, filter_by="today"):
    user = request.user
    today = date.today()

    visible_user = user

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

    future_hours = future_occurrences.aggregate(total=Sum("duration_hours"))["total"] or 0
    future_pto = max(visible_user.pto_balance - future_hours, 0)
    future_personal = visible_user.personal_time_balance + max(0, future_hours - visible_user.pto_balance)

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

@login_required
def reports_view(request):
    if not user_can_view_reports(request.user):
        return redirect("attendance:dashboard")

    form = ReportFilterForm(request.GET or None)
    occurrences = []
    selected_user = None

    user = request.user
    if user.role == RoleChoices.EXECUTIVE:
        visible_users = CustomUser.objects.all()
    elif user.role == RoleChoices.MANAGER:
        visible_users = CustomUser.objects.filter(department=user.department)
    elif user.role == RoleChoices.SUPERVISOR:
        visible_users = CustomUser.objects.filter(Q(supervisor=user) | Q(id=user.id))
    elif user.role == RoleChoices.GROUP_LEAD:
        visible_users = CustomUser.objects.filter(Q(group_lead=user) | Q(id=user.id))
    else:
        visible_users = CustomUser.objects.none()

    today = localdate()
    start_of_week = today - timedelta(days=(today.weekday() + 1) % 7)
    end_of_week = start_of_week + timedelta(days=6)

    weekly_totals = []
    for u in visible_users.filter(is_exempt=False):
        total = 0
        entries = TimeEntry.objects.filter(user=u, date__range=[start_of_week, end_of_week])
        for e in entries:
            if e.clock_in and e.clock_out:
                lunch = timedelta()
                if e.lunch_in and e.lunch_out:
                    lunch = e.lunch_in - e.lunch_out
                total += (e.clock_out - e.clock_in - lunch).total_seconds() / 3600
        weekly_totals.append((u, round(total, 2)))

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
            if any(fields) and not all(fields):
                alerts.append(e)

    if form.is_valid():
        selected_user = form.cleaned_data["user"]
        start_date = form.cleaned_data["start_date"]
        end_date = form.cleaned_data["end_date"]
        occurrences = Occurrence.objects.filter(
            user=selected_user, date__range=(start_date, end_date)
        ).order_by("date")

    payroll_weeks = [d.strftime("%Y-%m-%d") for d in get_recent_saturdays()]

    return render(request, "attendance/reports.html", {
        "form": form,
        "occurrences": occurrences,
        "selected_user": selected_user,
        "weekly_totals": weekly_totals,
        "alerts": alerts,
        "start_of_week": start_of_week,
        "end_of_week": end_of_week,
        "payroll_weeks": payroll_weeks,
    })

@require_POST
@login_required
def close_payroll(request):
    if not request.user.is_staff:
        return redirect("attendance:dashboard")

    try:
        week_ending = date.fromisoformat(request.POST.get("week_ending"))
    except (TypeError, ValueError):
        messages.error(request, "Invalid week selected.")
        return redirect("attendance:reports")

    week_start = week_ending - timedelta(days=6)

    from timeclock.models import TimeEntry
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
        return redirect("attendance:reports")

    response = HttpResponse(content_type='text/csv')
    filename = f"payroll_week_ending_{week_ending.strftime('%Y-%m-%d')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(["LASTNAME", "FIRSTNAME", "TIMEENTRYTOTAL", "PTOTOTAL", "OVERTIMETOTAL"])

    users = CustomUser.objects.filter(is_active=True, is_exempt=False)
    for user in users:
        entries = TimeEntry.objects.filter(user=user, date__range=[week_start, week_ending])
        total_hours = 0
        for e in entries:
            if e.clock_in and e.clock_out:
                lunch = timedelta()
                if e.lunch_in and e.lunch_out:
                    lunch = e.lunch_in - e.lunch_out
                total_hours += (e.clock_out - e.clock_in - lunch).total_seconds() / 3600

        overtime = max(total_hours - 40, 0)
        regular = min(total_hours, 40)
        writer.writerow([user.last_name, user.first_name, round(regular + overtime, 2), round(regular, 2), round(overtime, 2)])

    return response

@login_required
def edit_entry(request, pk):
    entry = get_object_or_404(TimeEntry, pk=pk)

    # Restrict to group lead or higher
    if request.user.role not in [
        RoleChoices.GROUP_LEAD,
        RoleChoices.SUPERVISOR,
        RoleChoices.MANAGER,
        RoleChoices.EXECUTIVE,
    ]:
        return redirect("attendance:dashboard")

    if request.method == "POST":
        form = TimeEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            return redirect("attendance:reports")
    else:
        form = TimeEntryForm(instance=entry)

    return render(request, "timeclock/edit_entry.html", {"form": form, "entry": entry})

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

        template = get_template("attendance/report_pdf_template.html")
        html = template.render({"user": user, "occurrences": occurrences, "start": start_date, "end": end_date})
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{user.username}_report.pdf"'
        pisa.CreatePDF(html, dest=response)
        return response
    return redirect("attendance:reports")