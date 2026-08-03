from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction, IntegrityError
from .models import TimeEntry
from .forms import TimeEntryForm
from .kiosk import (
    get_client_ip,
    is_timeclock_kiosk,
    kiosk_auth_method,
    kiosk_token_from_query,
    set_kiosk_cookie,
)
from attendance.models import CustomUser, RoleChoices
from attendance.payroll_utils import week_ending_for_date, is_payroll_week_finalized
from .tardy_sync import sync_tardy_occurrences_for_time_entry
from attendance.schedule_utils import clock_in_requires_approver
from django.utils import timezone
from django.utils.timezone import localtime
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse


def _clock_in_approver_queryset():
    return CustomUser.objects.filter(
        role__in=[
            RoleChoices.EXECUTIVE,
            RoleChoices.MANAGER,
            RoleChoices.SUPERVISOR,
            RoleChoices.GROUP_LEAD,
        ],
        is_active=True,
    ).order_by(
        "payroll_lastname",
        "payroll_firstname",
        "last_name",
        "first_name",
        "username",
    )


def _is_valid_clock_in_approver(u: CustomUser) -> bool:
    if not u or not u.is_active:
        return False
    return u.role in (
        RoleChoices.EXECUTIVE,
        RoleChoices.MANAGER,
        RoleChoices.SUPERVISOR,
        RoleChoices.GROUP_LEAD,
    )


def _resolve_timeclock_user(request, login, pin, *, is_kiosk: bool):
    """
    Resolve employee for a punch.
    Kiosk (registered IP or token): login only (barcode). All others: login + PIN.
    Returns (user, error_message). error_message is set when resolution fails.
    """
    login = (login or "").strip()
    pin = (pin or "").strip()
    if not login:
        return None, "missing_credentials"
    try:
        if is_kiosk:
            user = CustomUser.objects.get(timeclock_login=login, is_active=True)
        else:
            if not pin:
                return None, "missing_credentials"
            user = CustomUser.objects.get(
                timeclock_login=login, timeclock_pin=pin, is_active=True
            )
    except CustomUser.DoesNotExist:
        return None, "invalid_credentials"
    except CustomUser.MultipleObjectsReturned:
        return None, "ambiguous_login"
    return user, None


def _punch_sequence_error(entry, action):
    """
    Message explaining why a punch cannot be recorded, or None when it is allowed.

    Punches must arrive in order (clock in, lunch out, lunch in, clock out) and once per day.
    `entry` may be None when nothing has been punched yet, so a badge scan cannot register a
    clock out for a shift that was never started.
    """
    clock_in = getattr(entry, "clock_in", None)
    lunch_out = getattr(entry, "lunch_out", None)
    lunch_in = getattr(entry, "lunch_in", None)
    clock_out = getattr(entry, "clock_out", None)
    already_out = "You are already clocked out for today. See a supervisor to correct your time."

    if action == "clock_in":
        if clock_in is not None:
            return "You are already clocked in for today."
        return None
    if action == "lunch_out":
        if lunch_out is not None:
            return "Lunch out already recorded for today."
        if clock_in is None:
            return "You are not clocked in for today. Clock in before recording lunch out."
        if clock_out is not None:
            return already_out
        return None
    if action == "lunch_in":
        if lunch_in is not None:
            return "Lunch in already recorded for today."
        if clock_in is None:
            return "You are not clocked in for today. Clock in before recording lunch in."
        if lunch_out is None:
            return "Record lunch out before lunch in."
        if clock_out is not None:
            return already_out
        return None
    if action == "clock_out":
        if clock_out is not None:
            return "You are already clocked out for today."
        if clock_in is None:
            return "You are not clocked in for today. Clock in before clocking out."
        return None
    return "Select a punch type, then try again."


def _credential_error_message(code: str, *, is_kiosk: bool) -> str:
    if code == "ambiguous_login":
        return "Multiple employees share this login. Contact a supervisor."
    if is_kiosk:
        return "Invalid badge / login."
    return "Invalid login or pin."


@require_POST
def check_clock_in(request):
    """
    Returns whether clock-in requires a manager override (unscheduled or >15 min before start).
    Used by the timeclock UI before submitting the clock-in punch.
    """
    is_kiosk = is_timeclock_kiosk(request)
    login = request.POST.get("login")
    pin = request.POST.get("pin")
    user, err = _resolve_timeclock_user(request, login, pin, is_kiosk=is_kiosk)
    if err == "missing_credentials":
        return JsonResponse({"error": "missing_credentials"}, status=400)
    if err:
        return JsonResponse({"error": err}, status=401)

    now = timezone.now()
    today = now.date()
    requires_override, reason = clock_in_requires_approver(user, now, today)
    return JsonResponse(
        {
            "requires_override": requires_override,
            "reason": reason,
        }
    )


def timeclock_home(request):
    clock_in_approvers = _clock_in_approver_queryset()

    # Activate remote kiosk via ?kiosk=<token> → set cookie → clean URL.
    if request.method == "GET" and "kiosk" in request.GET:
        raw = (request.GET.get("kiosk") or "").strip()
        valid = kiosk_token_from_query(request)
        if valid:
            response = redirect("timeclock:timeclock_home")
            set_kiosk_cookie(response, valid, request)
            return response
        if raw:
            messages.error(
                request,
                "Invalid or inactive kiosk token. Using the standard timeclock.",
            )
            return redirect("timeclock:timeclock_home")

    is_kiosk = is_timeclock_kiosk(request)

    if request.method == "POST":
        login = request.POST.get("login")
        pin = request.POST.get("pin")
        action = request.POST.get("action")

        user, err = _resolve_timeclock_user(request, login, pin, is_kiosk=is_kiosk)
        if err:
            messages.error(request, _credential_error_message(err, is_kiosk=is_kiosk))
            return redirect("timeclock:timeclock_home")

        now = timezone.now()
        today = now.date()
        timestamp_str = localtime(now).strftime("%I:%M %p").lstrip("0")

        if is_payroll_week_finalized(week_ending_for_date(today)):
            messages.error(
                request,
                "This payroll week is finalized. Punches cannot be recorded until payroll is unfinalized for this week.",
            )
            return redirect("timeclock:timeclock_home")

        try:
            with transaction.atomic():
                entry = TimeEntry.objects.select_for_update().filter(
                    user=user, date=today
                ).first()

                # Checked before creating the entry so a rejected punch leaves no empty row.
                sequence_error = _punch_sequence_error(entry, action)
                if sequence_error:
                    messages.warning(request, sequence_error)
                    return redirect("timeclock:timeclock_home")

                if entry is None:
                    entry = TimeEntry(user=user, date=today)
                    entry.save()

                if action == "clock_in":
                    requires_approver, _reason = clock_in_requires_approver(user, now, today)
                    approver_id = (request.POST.get("clock_in_approver") or "").strip()
                    if requires_approver:
                        if not approver_id:
                            messages.error(
                                request,
                                "You are not scheduled today or are more than 15 minutes before your "
                                "scheduled start. Select an approving executive, manager, supervisor, or "
                                "group lead, then try Clock In again.",
                            )
                            return redirect("timeclock:timeclock_home")
                        try:
                            approver = CustomUser.objects.get(pk=approver_id)
                        except CustomUser.DoesNotExist:
                            messages.error(request, "Invalid approver selected.")
                            return redirect("timeclock:timeclock_home")
                        if not _is_valid_clock_in_approver(approver):
                            messages.error(request, "The selected user cannot approve this clock-in.")
                            return redirect("timeclock:timeclock_home")
                        entry.clock_in_authorized_by = approver
                    else:
                        entry.clock_in_authorized_by = None
                    entry.clock_in = now
                elif action == "lunch_out":
                    entry.lunch_out = now
                elif action == "lunch_in":
                    entry.lunch_in = now
                elif action == "clock_out":
                    entry.clock_out = now

                entry.save()
        except IntegrityError:
            messages.error(request, "This punch was already recorded (duplicate). Please refresh and try again.")
            return redirect("timeclock:timeclock_home")
        except Exception:
            messages.error(request, "Could not record punch. Please try again.")
            return redirect("timeclock:timeclock_home")

        # Apply business rules after recording the punch (outside lock)
        if action == "clock_in":
            entry.check_tardy()

        readable_action = action.replace("_", " ").title()
        user_name = user.get_full_name() or user.username
        messages.success(
            request,
            f"{readable_action} recorded for {user_name} at {timestamp_str}.",
        )
        return redirect("timeclock:timeclock_home")

    show_client_ip = bool(
        getattr(request.user, "is_authenticated", False)
        and (
            getattr(request.user, "is_staff", False)
            or getattr(request.user, "is_superuser", False)
        )
    )
    return render(
        request,
        "timeclock/timeclock_home.html",
        {
            "clock_in_approvers": clock_in_approvers,
            "is_kiosk": is_kiosk,
            "client_ip": get_client_ip(request) if show_client_ip else None,
            "show_client_ip": show_client_ip,
            "kiosk_auth_method": kiosk_auth_method(request) if show_client_ip else None,
        },
    )


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

    week_ending = week_ending_for_date(entry.date)
    _payroll_ok = request.user.role == RoleChoices.EXECUTIVE

    if is_payroll_week_finalized(week_ending):
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
            if not is_payroll_week_finalized(week_ending):
                sync_tardy_occurrences_for_time_entry(entry)
            messages.success(request, "Time entry updated successfully.")
            if request.user.role == RoleChoices.EXECUTIVE:
                return redirect("attendance:payroll")
            return redirect("attendance:dashboard")
    else:
        form = TimeEntryForm(instance=entry)

    return render(
        request,
        "timeclock/edit_entry.html",
        {
            "form": form,
            "entry": entry,
            "payroll_finalized": is_payroll_week_finalized(week_ending),
        },
    )
