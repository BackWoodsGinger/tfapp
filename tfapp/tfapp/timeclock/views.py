from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import TimeEntry
from .forms import TimeEntryForm
from attendance.models import CustomUser
from django.utils import timezone
from django.utils.timezone import localtime
from django.contrib.auth.decorators import login_required

def timeclock_home(request):
    if request.method == 'POST':
        login = request.POST.get('login')
        pin = request.POST.get('pin')
        action = request.POST.get('action')

        try:
            user = CustomUser.objects.get(timeclock_login=login, timeclock_pin=pin)
        except CustomUser.DoesNotExist:
            messages.error(request, "Invalid login or pin.")
            return redirect('timeclock:timeclock_home')

        now = timezone.now()
        today = now.date()
        entry, _ = TimeEntry.objects.get_or_create(user=user, date=today)

        timestamp_str = localtime(now).strftime("%I:%M %p").lstrip("0")

        if action == 'clock_in':
            entry.clock_in = now
        elif action == 'lunch_out':
            entry.lunch_out = now
        elif action == 'lunch_in':
            entry.lunch_in = now
        elif action == 'clock_out':
            entry.clock_out = now

        entry.save()
        readable_action = action.replace('_', ' ').title()  # 'Clock In', etc.
        user_name = user.get_full_name() or user.username
        messages.success(request, f"{readable_action} recorded for {user_name} at {timestamp_str}.")
        return redirect('timeclock:timeclock_home')

    return render(request, 'timeclock/timeclock_home.html')

@login_required
def edit_entry(request, pk):
    entry = get_object_or_404(TimeEntry, pk=pk)

    if request.method == 'POST':
        form = TimeEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, "Time entry updated successfully.")
            return redirect('attendance:reports')
    else:
        form = TimeEntryForm(instance=entry)

    return render(request, 'timeclock/edit_entry.html', {'form': form})