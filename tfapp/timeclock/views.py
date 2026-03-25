from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction, IntegrityError
from .models import TimeEntry
from .forms import TimeEntryForm
from attendance.models import CustomUser
from django.utils import timezone
from django.utils.timezone import localtime
from django.contrib.auth.decorators import login_required


def timeclock_home(request):
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

    return render(request, "timeclock/timeclock_home.html")


@login_required
def edit_entry(request, pk):
    entry = get_object_or_404(TimeEntry, pk=pk)

    if request.method == "POST":
        form = TimeEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, "Time entry updated successfully.")
            return redirect("attendance:payroll")
    else:
        form = TimeEntryForm(instance=entry)

    return render(request, "timeclock/edit_entry.html", {"form": form})