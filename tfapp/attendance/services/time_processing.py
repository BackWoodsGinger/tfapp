"""
Schedule math for WorkSchedule, weekly_schedule JSON, and timeclock validation.
Night shifts that end after midnight use crosses_midnight / "crosses_midnight" in JSON.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from django.utils import timezone

# Fixed Monday used only to read a representative weekday schedule (JSON or WorkSchedule).
_REFERENCE_MONDAY = date(2020, 1, 6)

TIME_FMT = "%H:%M"


def schedule_row_has_lunch(row: dict | None) -> bool:
    """True if JSON weekly_schedule row includes both lunch times (half-day / no-lunch rows omit them)."""
    if not isinstance(row, dict):
        return False
    lo = row.get("lunch_out")
    li = row.get("lunch_in")
    if lo in (None, "") or li in (None, ""):
        return False
    return True


def _combine_local(d: date, t) -> datetime:
    naive = datetime.combine(d, t)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def get_scheduled_lunch_out_for_day(user, d: date):
    """Scheduled lunch-out time for date d, or None if not in schedule or day has no lunch period."""
    schedule = user.weekly_schedule or {}
    weekday_str = d.strftime("%A").lower()
    if schedule and weekday_str in schedule:
        row = schedule[weekday_str]
        if not schedule_row_has_lunch(row):
            return None
        try:
            return datetime.strptime(row["lunch_out"], TIME_FMT).time()
        except (KeyError, ValueError, TypeError):
            return None
    sched = user.schedules.filter(day=d.weekday()).first()
    if sched and sched.lunch_out is not None and sched.lunch_in is not None:
        return sched.lunch_out
    return None


def get_scheduled_lunch_in_for_day(user, d: date):
    """Scheduled lunch return time for date d, or None if not in schedule or day has no lunch period."""
    schedule = user.weekly_schedule or {}
    weekday_str = d.strftime("%A").lower()
    if schedule and weekday_str in schedule:
        row = schedule[weekday_str]
        if not schedule_row_has_lunch(row):
            return None
        try:
            return datetime.strptime(row["lunch_in"], TIME_FMT).time()
        except (KeyError, ValueError, TypeError):
            return None
    sched = user.schedules.filter(day=d.weekday()).first()
    if sched and sched.lunch_out is not None and sched.lunch_in is not None:
        return sched.lunch_in
    return None


def scheduled_lunch_datetimes_for_entry(entry) -> tuple[datetime, datetime] | None:
    """
    Build timezone-aware lunch_out and lunch_in datetimes for this entry's date from the user's schedule.
    Returns None if no schedule or times cannot be placed within the shift.
    """
    user = entry.user
    d = effective_schedule_reference_date(entry)
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


def effective_schedule_reference_date(entry) -> date:
    """
    Which calendar date's schedule template applies to this entry's punches.

    Nominal weekday schedule is used unless the worked span is much longer than
    that day's scheduled hours (e.g. working Monday's 7:00–15:30 shift on Wednesday
    after a schedule exchange). Then pick the weekday template whose end time and
    duration best match the punches.
    """
    user = entry.user
    d = entry.date
    nominal = scheduled_duration_hours_for_day(user, d)
    if not (entry.clock_in and entry.clock_out):
        return d
    span_h = (entry.clock_out - entry.clock_in).total_seconds() / 3600
    if nominal <= 0 or span_h <= nominal + 1.5:
        return d
    out_t = timezone.localtime(entry.clock_out).time()
    best_ref = d
    best_score = None
    for wd in range(7):
        ref = d - timedelta(days=(d.weekday() - wd) % 7)
        sched_h = scheduled_duration_hours_for_day(user, ref)
        end_t = get_scheduled_end_time_for_day(user, ref)
        if sched_h <= 0 or not end_t:
            continue
        end_mins = end_t.hour * 60 + end_t.minute
        out_mins = out_t.hour * 60 + out_t.minute
        score = abs(end_mins - out_mins) + abs(span_h - sched_h) * 30
        if best_score is None or score < best_score:
            best_score = score
            best_ref = ref
    return best_ref


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


def get_scheduled_end_time_for_day(user, d: date):
    """Scheduled end time for the shift on weekday d, or None if not in schedule / unparsable."""
    schedule = user.weekly_schedule or {}
    weekday_str = d.strftime("%A").lower()
    if schedule and weekday_str in schedule:
        try:
            return datetime.strptime(schedule[weekday_str]["end"], TIME_FMT).time()
        except (KeyError, ValueError, TypeError):
            pass
    sched = user.schedules.filter(day=d.weekday()).first()
    return sched.end_time if sched else None


def get_scheduled_shift_end_datetime(user, d: date) -> datetime | None:
    """
    Timezone-aware local datetime when the shift that *starts* on calendar day d ends.
    Overnight shifts (crosses_midnight) end on d+1 at end_time.
    None if not scheduled, or end time cannot be determined.
    """
    if get_scheduled_start_for_day(user, d) is None:
        return None
    end_t = get_scheduled_end_time_for_day(user, d)
    if end_t is None:
        return None
    cm = crosses_midnight_for_day(user, d)
    end_date = d + timedelta(days=1) if cm else d
    return _combine_local(end_date, end_t)


def monday_typical_shift_label(user) -> str:
    """
    Human-readable start–end for the user's scheduled Monday (JSON or WorkSchedule).
    Used for grouping employees by typical shift pattern.
    """
    st = get_scheduled_start_for_day(user, _REFERENCE_MONDAY)
    et = get_scheduled_end_time_for_day(user, _REFERENCE_MONDAY)
    if st and et:
        return f"{st.strftime(TIME_FMT)}–{et.strftime(TIME_FMT)}"
    return "(No Monday schedule)"


def scheduled_duration_hours_for_day(user, d: date) -> float:
    """Scheduled paid hours for one calendar day (shift anchored on d), or 0 if none."""
    schedule = user.weekly_schedule or {}
    weekday_str = d.strftime("%A").lower()
    if schedule and weekday_str in schedule:
        try:
            sched = schedule[weekday_str]
            start = datetime.strptime(sched["start"], TIME_FMT)
            end = datetime.strptime(sched["end"], TIME_FMT)
            cm = crosses_midnight_for_day(user, d)
            lunch_out = lunch_in = None
            if schedule_row_has_lunch(sched):
                lunch_out = datetime.strptime(sched["lunch_out"], TIME_FMT)
                lunch_in = datetime.strptime(sched["lunch_in"], TIME_FMT)
            return _duration_hours_from_parts(start, end, lunch_out, lunch_in, cm)
        except (KeyError, ValueError, TypeError):
            pass
    sched = user.schedules.filter(day=d.weekday()).first()
    if not sched:
        return 0.0
    d0 = date(2000, 1, 3)
    start = datetime.combine(d0, sched.start_time)
    end = datetime.combine(d0, sched.end_time)
    cm = crosses_midnight_for_day(user, d)
    if sched.lunch_out is not None and sched.lunch_in is not None:
        lunch_out_dt = datetime.combine(d0, sched.lunch_out)
        lunch_in_dt = datetime.combine(d0, sched.lunch_in)
        return _duration_hours_from_parts(start, end, lunch_out_dt, lunch_in_dt, cm)
    return _duration_hours_from_parts(start, end, None, None, cm)


def _duration_hours_from_parts(
    start: datetime,
    end: datetime,
    lunch_out: datetime | None,
    lunch_in: datetime | None,
    crosses_midnight: bool,
) -> float:
    if crosses_midnight:
        end = end + timedelta(days=1)
    span = (end - start).total_seconds()
    if lunch_out is not None and lunch_in is not None:
        span -= (lunch_in - lunch_out).total_seconds()
    return max(span, 0) / 3600


def scheduled_hours_for_range(user, week_start: date, week_ending: date) -> float:
    total = 0.0
    current = week_start
    while current <= week_ending:
        total += scheduled_duration_hours_for_day(user, current)
        current += timedelta(days=1)
    return total


def scheduled_duration_hours_for_day_indexed(
    user,
    d: date,
    schedules_by_weekday: dict[int, object],
) -> float:
    """Like scheduled_duration_hours_for_day but uses prefetched schedules (no per-day DB query)."""
    schedule = user.weekly_schedule or {}
    weekday_str = d.strftime("%A").lower()
    if schedule and weekday_str in schedule:
        try:
            sched = schedule[weekday_str]
            start = datetime.strptime(sched["start"], TIME_FMT)
            end = datetime.strptime(sched["end"], TIME_FMT)
            cm = crosses_midnight_for_day(user, d)
            lunch_out = lunch_in = None
            if schedule_row_has_lunch(sched):
                lunch_out = datetime.strptime(sched["lunch_out"], TIME_FMT)
                lunch_in = datetime.strptime(sched["lunch_in"], TIME_FMT)
            return _duration_hours_from_parts(start, end, lunch_out, lunch_in, cm)
        except (KeyError, ValueError, TypeError):
            pass
    sched = schedules_by_weekday.get(d.weekday())
    if not sched:
        return 0.0
    d0 = date(2000, 1, 3)
    start = datetime.combine(d0, sched.start_time)
    end = datetime.combine(d0, sched.end_time)
    cm = crosses_midnight_for_day(user, d)
    if sched.lunch_out is not None and sched.lunch_in is not None:
        lunch_out_dt = datetime.combine(d0, sched.lunch_out)
        lunch_in_dt = datetime.combine(d0, sched.lunch_in)
        return _duration_hours_from_parts(start, end, lunch_out_dt, lunch_in_dt, cm)
    return _duration_hours_from_parts(start, end, None, None, cm)


def build_daily_scheduled_hours_map(
    users,
    span_start: date,
    span_end: date,
) -> tuple[list[date], dict[int, list[float]]]:
    """
    One pass per user over [span_start, span_end]. Callers should prefetch_related('schedules').
    Returns (date list, user_id -> daily hours aligned with dates). Skips exempt users.
    """
    dates: list[date] = []
    d = span_start
    while d <= span_end:
        dates.append(d)
        d += timedelta(days=1)

    schedules_by_user: dict[int, dict[int, object]] = {}
    daily_by_user: dict[int, list[float]] = {}
    for user in users:
        if getattr(user, "is_exempt", False):
            continue
        uid = user.id
        if uid not in schedules_by_user:
            schedules_by_user[uid] = {s.day: s for s in user.schedules.all()}
        by_weekday = schedules_by_user[uid]
        daily_by_user[uid] = [
            scheduled_duration_hours_for_day_indexed(user, day, by_weekday) for day in dates
        ]
    return dates, daily_by_user


def earliest_clock_in_allowed(user, d: date):
    """
    Earliest moment the user may clock in without manager approval (15 min before scheduled start).
    Returns None if not scheduled that day.
    """
    start = get_scheduled_start_for_day(user, d)
    if not start:
        return None
    scheduled_local = _combine_local(d, start)
    return scheduled_local - timedelta(minutes=15)


def clock_in_at_or_after_scheduled_lunch_in(user, d: date, clock_in_dt) -> bool:
    """
    True when the first punch of the day is at or after scheduled lunch return time
    (partial afternoon shift; no lunch break applies to time worked).
    """
    if not clock_in_dt:
        return False
    lunch_in_t = get_scheduled_lunch_in_for_day(user, d)
    if not lunch_in_t:
        return False
    clock_in_local_t = timezone.localtime(clock_in_dt).time()
    return clock_in_local_t >= lunch_in_t


def entry_requires_payroll_lunch_import_review(entry) -> bool:
    """CSV omitted lunch on a scheduled-lunch day and review is still required."""
    if not getattr(entry, "payroll_lunch_review_required", False):
        return False
    if not entry.clock_in:
        return False
    if clock_in_at_or_after_scheduled_lunch_in(entry.user, entry.date, entry.clock_in):
        return False
    return True


def work_through_lunch_approved_for_day(user, d: date) -> bool:
    """
    True if the user has an approved request to work through lunch on date d
    (no automatic scheduled lunch punches; no lunch deduction).
    """
    from attendance.models import TimeOffRequestStatus, WorkThroughLunchRequest

    return WorkThroughLunchRequest.objects.filter(
        user=user,
        work_date=d,
        status=TimeOffRequestStatus.APPROVED,
    ).exists()


def clock_in_requires_approver(user, now, d: date) -> tuple[bool, str | None]:
    """
    Returns (requires_approver, reason) where reason is 'unscheduled' or 'early' or None.
    """
    start = get_scheduled_start_for_day(user, d)
    if not start:
        return True, "unscheduled"
    earliest = earliest_clock_in_allowed(user, d)
    if earliest and now <= earliest:
        return True, "early"
    return False, None


def clock_in_requires_approver_for_entry(entry) -> tuple[bool, str | None]:
    """Like clock_in_requires_approver but uses effective schedule for exchanged shifts."""
    if not entry.clock_in:
        return False, None
    ref = effective_schedule_reference_date(entry)
    clock_in_local = timezone.localtime(entry.clock_in)
    start = get_scheduled_start_for_day(entry.user, ref)
    if not start:
        return True, "unscheduled"
    scheduled_local = _combine_local(entry.date, start)
    earliest = scheduled_local - timedelta(minutes=15)
    if clock_in_local <= earliest:
        return True, "early"
    return False, None


def suggested_punch_times_for_day(user, d: date) -> dict:
    """
    Expected local clock times for payroll CSV export when no TimeEntry exists yet.
    Keys: clock_in, lunch_out, lunch_in, clock_out (time objects or None).
    """
    start = get_scheduled_start_for_day(user, d)
    if start is None:
        return {"clock_in": None, "lunch_out": None, "lunch_in": None, "clock_out": None}
    return {
        "clock_in": start,
        "lunch_out": get_scheduled_lunch_out_for_day(user, d),
        "lunch_in": get_scheduled_lunch_in_for_day(user, d),
        "clock_out": get_scheduled_end_time_for_day(user, d),
    }
