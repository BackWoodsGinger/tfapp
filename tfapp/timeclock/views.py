from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction, IntegrityError
from .models import TimeEntry
from .forms import TimeEntryForm
from attendance.models import CustomUser, RoleChoices
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


@require_POST
def check_clock_in(request):
    """
    Returns whether clock-in requires a manager override (unscheduled or >15 min before start).
    Used by the timeclock UI before submitting the clock-in punch.
    """
    login = (request.POST.get("login") or "").strip()
    pin = (request.POST.get("pin") or "").strip()
    if not login or not pin:
        return JsonResponse({"error": "missing_credentials"}, status=400)
    try:
        user = CustomUser.objects.get(timeclock_login=login, timeclock_pin=pin)
    except CustomUser.DoesNotExist:
        return JsonResponse({"error": "invalid_credentials"}, status=401)

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

    if request.method == "POST":
        login = request.POST.get("login")
        pin = request.POST.get("pin")
        action = request.POST.get("action")

        try:
            user = CustomUser.objects.get(timeclock_login=login, timeclock_pin=pin)
        except CustomUser.DoesNotExist:
            messages.error(request, "Invalid login or pin.")
            return redirect("timeclock:timeclock_home")

        now = timezone.now()
        today = now.date()
        timestamp_str = localtime(now).strftime("%I:%M %p").lstrip("0")

        try:
            with transaction.atomic():
                entry = TimeEntry.objects.select_for_update().filter(
                    user=user, date=today
                ).first()
                if entry is None:
                    entry = TimeEntry(user=user, date=today)
                    entry.save()

                # Guard: prevent duplicate punches for the same action on the same day
                if action == "clock_in" and entry.clock_in is not None:
                    messages.warning(request, "You are already clocked in for today.")
                    return redirect("timeclock:timeclock_home")
                if action == "lunch_out" and entry.lunch_out is not None:
                    messages.warning(request, "Lunch out already recorded for today.")
                    return redirect("timeclock:timeclock_home")
                if action == "lunch_in" and entry.lunch_in is not None:
                    messages.warning(request, "Lunch in already recorded for today.")
                    return redirect("timeclock:timeclock_home")
                if action == "clock_out" and entry.clock_out is not None:
                    messages.warning(request, "You are already clocked out for today.")
                    return redirect("timeclock:timeclock_home")

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
        elif action == "lunch_in":
            entry.check_lunch_tardy()

        readable_action = action.replace("_", " ").title()
        user_name = user.get_full_name() or user.username
        messages.success(
            request,
            f"{readable_action} recorded for {user_name} at {timestamp_str}.",
        )
        return redirect("timeclock:timeclock_home")

    return render(
        request,
        "timeclock/timeclock_home.html",
        {"clock_in_approvers": clock_in_approvers},
    )


@login_required
def edit_entry(request, slug):
    entry = get_object_or_404(TimeEntry, slug=slug)

    if request.method == "POST":
        form = TimeEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, "Time entry updated successfully.")
            return redirect("attendance:payroll")
    else:
        form = TimeEntryForm(instance=entry)

    return render(request, "timeclock/edit_entry.html", {"form": form})