"""
Shared schedule math for WorkSchedule, weekly_schedule JSON, and timeclock validation.
Night shifts that end after midnight use crosses_midnight / "crosses_midnight" in JSON.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from django.utils import timezone

TIME_FMT = "%H:%M"


def _combine_local(d: date, t) -> datetime:
    naive = datetime.combine(d, t)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def get_scheduled_lunch_out_for_day(user, d: date):
    """Scheduled lunch-out time for date d, or None if not in schedule."""
    schedule = user.weekly_schedule or {}
    weekday_str = d.strftime("%A").lower()
    if schedule and weekday_str in schedule:
        try:
            return datetime.strptime(schedule[weekday_str]["lunch_out"], TIME_FMT).time()
        except (KeyError, ValueError, TypeError):
            pass
    sched = user.schedules.filter(day=d.weekday()).first()
    return sched.lunch_out if sched else None


def get_scheduled_lunch_in_for_day(user, d: date):
    """Scheduled lunch return time for date d, or None if not in schedule."""
    schedule = user.weekly_schedule or {}
    weekday_str = d.strftime("%A").lower()
    if schedule and weekday_str in schedule:
        try:
            return datetime.strptime(schedule[weekday_str]["lunch_in"], TIME_FMT).time()
        except (KeyError, ValueError, TypeError):
            pass
    sched = user.schedules.filter(day=d.weekday()).first()
    return sched.lunch_in if sched else None


def scheduled_lunch_datetimes_for_entry(entry) -> tuple[datetime, datetime] | None:
    """
    Build timezone-aware lunch_out and lunch_in datetimes for this entry's date from the user's schedule.
    Returns None if no schedule or times cannot be placed within the shift.
    """
    user = entry.user
    d = entry.date
    lunch_out_t = get_scheduled_lunch_out_for_day(user, d)
    lunch_in_t = get_scheduled_lunch_in_for_day(user, d)
    if not lunch_out_t or not lunch_in_t:
        return None
    if not entry.clock_in or not entry.clock_out:
        return None

    lunch_out_dt = _combine_local(d, lunch_out_t)
    lunch_in_dt = _combine_local(d, lunch_in_t)
    if lunch_in_t <= lunch_out_t:
        lunch_in_dt = _combine_local(d + timedelta(days=1), lunch_in_t)

    ci = timezone.localtime(entry.clock_in)
    co = timezone.localtime(entry.clock_out)
    lo = timezone.localtime(lunch_out_dt)
    li = timezone.localtime(lunch_in_dt)

    if lo < ci or li > co or lo >= li:
        return None
    return (lunch_out_dt, lunch_in_dt)


def get_scheduled_start_for_day(user, d: date):
    """Return scheduled start time for date d, or None if not scheduled."""
    schedule = user.weekly_schedule or {}
    weekday_str = d.strftime("%A").lower()
    if schedule and weekday_str in schedule:
        try:
            return datetime.strptime(schedule[weekday_str]["start"], TIME_FMT).time()
        except (KeyError, ValueError, TypeError):
            pass
    sched = user.schedules.filter(day=d.weekday()).first()
    return sched.start_time if sched else None


def crosses_midnight_for_day(user, d: date) -> bool:
    """
    True if shift end is the next calendar morning after shift start (overnight shift).

    Uses explicit crosses_midnight from JSON or WorkSchedule when set; otherwise infers
    from start/end times: if end_time <= start_time as clock times (e.g. 02:00 vs 15:30),
    the shift crosses midnight. Without this, scheduled hours become negative.
    """
    ws = user.weekly_schedule or {}
    weekday_str = d.strftime("%A").lower()
    if ws and weekday_str in ws:
        try:
            row = ws[weekday_str]
            st = datetime.strptime(row["start"], TIME_FMT).time()
            et = datetime.strptime(row["end"], TIME_FMT).time()
            inferred = et <= st
            if row.get("crosses_midnight") is True:
                return True
            if inferred:
                return True
            if row.get("crosses_midnight") is False:
                return False
            # JSON defines this weekday: same-calendar-day shift (no explicit flag)
            return False
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
    sched = user.schedules.filter(day=d.weekday()).first()
    if sched:
        if getattr(sched, "crosses_midnight", False):
            return True
        if sched.end_time <= sched.start_time:
            return True
    return False


def scheduled_duration_hours_for_day(user, d: date) -> float:
    """Scheduled paid hours for one calendar day (shift anchored on d), or 0 if none."""
    schedule = user.weekly_schedule or {}
    weekday_str = d.strftime("%A").lower()
    if schedule and weekday_str in schedule:
        try:
            sched = schedule[weekday_str]
            start = datetime.strptime(sched["start"], TIME_FMT)
            end = datetime.strptime(sched["end"], TIME_FMT)
            lunch_out = datetime.strptime(sched["lunch_out"], TIME_FMT)
            lunch_in = datetime.strptime(sched["lunch_in"], TIME_FMT)
            cm = crosses_midnight_for_day(user, d)
            return _duration_hours_from_parts(start, end, lunch_out, lunch_in, cm)
        except (KeyError, ValueError, TypeError):
            pass
    sched = user.schedules.filter(day=d.weekday()).first()
    if not sched:
        return 0.0
    d0 = date(2000, 1, 3)
    start = datetime.combine(d0, sched.start_time)
    end = datetime.combine(d0, sched.end_time)
    lunch_out_dt = datetime.combine(d0, sched.lunch_out)
    lunch_in_dt = datetime.combine(d0, sched.lunch_in)
    cm = crosses_midnight_for_day(user, d)
    return _duration_hours_from_parts(start, end, lunch_out_dt, lunch_in_dt, cm)


def _duration_hours_from_parts(start: datetime, end: datetime, lunch_out: datetime, lunch_in: datetime, crosses_midnight: bool) -> float:
    if crosses_midnight:
        end = end + timedelta(days=1)
    return (end - start - (lunch_in - lunch_out)).total_seconds() / 3600


def scheduled_hours_for_range(user, week_start: date, week_ending: date) -> float:
    total = 0.0
    current = week_start
    while current <= week_ending:
        total += scheduled_duration_hours_for_day(user, current)
        current += timedelta(days=1)
    return total


def earliest_clock_in_allowed(user, d: date):
    """
    Earliest moment the user may clock in without manager approval (10 min before scheduled start).
    Returns None if not scheduled that day.
    """
    start = get_scheduled_start_for_day(user, d)
    if not start:
        return None
    scheduled_local = _combine_local(d, start)
    return scheduled_local - timedelta(minutes=10)


def clock_in_requires_approver(user, now, d: date) -> tuple[bool, str | None]:
    """
    Returns (requires_approver, reason) where reason is 'unscheduled' or 'early' or None.
    """
    start = get_scheduled_start_for_day(user, d)
    if not start:
        return True, "unscheduled"
    earliest = earliest_clock_in_allowed(user, d)
    if earliest and now < earliest:
        return True, "early"
    return False, None
