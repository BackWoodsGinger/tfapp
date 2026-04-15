from decimal import Decimal, ROUND_DOWN
from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta, datetime

from attendance.slug_utils import ensure_unique_slug
from attendance.schedule_utils import (
    get_scheduled_lunch_in_for_day,
    get_scheduled_lunch_out_for_day,
    get_scheduled_start_for_day,
    scheduled_lunch_datetimes_for_entry,
    work_through_lunch_approved_for_day,
)


class TimeEntry(models.Model):
    """One entry per user per day; enforced by unique constraint to prevent duplicates and race double-creation."""
    slug = models.SlugField(max_length=48, unique=True, editable=False, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    clock_in = models.DateTimeField(null=True, blank=True)
    lunch_out = models.DateTimeField(null=True, blank=True)
    lunch_in = models.DateTimeField(null=True, blank=True)
    clock_out = models.DateTimeField(null=True, blank=True)
    date = models.DateField(default=timezone.now)
    missing_punch_flagged = models.BooleanField(
        default=False,
        help_text="Set by nightly job when entry is incomplete; cleared when entry is fixed.",
    )
    missing_punch_flagged_at = models.DateTimeField(null=True, blank=True)
    clock_in_authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="authorized_clock_ins",
        help_text="Manager/supervisor who approved clock-in when unscheduled or more than 15 minutes early.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "date"], name="unique_timeentry_user_date"),
        ]
        indexes = [
            models.Index(fields=["user", "date"]),
            models.Index(fields=["date"]),
        ]

    def is_incomplete(self):
        """Return True if the entry has only some but not all timestamps filled in."""
        fields = [self.clock_in, self.lunch_out, self.lunch_in, self.clock_out]
        if not any(fields):
            return False
        if all(fields):
            return False
        if (
            self.clock_in
            and self.clock_out
            and self.lunch_out is None
            and self.lunch_in is None
            and work_through_lunch_approved_for_day(self.user, self.date)
        ):
            return False
        if (
            self.clock_in
            and self.clock_out
            and self.lunch_out is None
            and self.lunch_in is None
            and scheduled_lunch_datetimes_for_entry(self) is None
        ):
            return False
        return True

    def total_worked_time(self):
        """Return worked hours; uses Decimal for consistent rounding and to avoid float drift."""
        return self.actual_worked_hours()

    def _lunch_deduction_timedelta(self):
        """
        Unpaid lunch duration subtracted from clock_in–clock_out span.
        Uses actual lunch punches when both present (30-minute minimum).
        If both punches are missing and an approved work-through-lunch request exists for this date,
        deducts nothing. Otherwise uses a flat 30 minutes when punches are incomplete.
        """
        if not (self.clock_in and self.clock_out):
            return timedelta(0)
        if self.lunch_out and self.lunch_in:
            lunch = self.lunch_in - self.lunch_out
            if lunch < timedelta(minutes=30):
                lunch = timedelta(minutes=30)
            return lunch
        if work_through_lunch_approved_for_day(self.user, self.date):
            return timedelta(0)
        if scheduled_lunch_datetimes_for_entry(self) is None:
            return timedelta(0)
        return timedelta(minutes=30)

    def _worked_seconds(self):
        """Worked seconds after lunch deduction."""
        if not (self.clock_in and self.clock_out):
            return 0.0
        worked = self.clock_out - self.clock_in
        lunch = self._lunch_deduction_timedelta()
        return max((worked - lunch).total_seconds(), 0)

    def actual_worked_hours(self):
        """Actual hours from punches, rounded to 2 decimals for display."""
        seconds = self._worked_seconds()
        return float(Decimal(str(seconds / 3600)).quantize(Decimal("0.01")))

    def reported_worked_hours(self):
        """
        Payroll-reported hours:
        - clock-in uses schedule grace/tardy policy (<=4 min late = on-time),
          5+ min late rounds up from scheduled start to next 15-min mark.
        - clock-out rounds down to prior 15-min mark.
        - lunch is deducted with a 30-minute minimum using actual lunch punches.
        Keeps actual punches untouched while reporting payroll-compliant hours.
        """
        if not (self.clock_in and self.clock_out):
            return 0.0

        scheduled_start = self._scheduled_start_time_for_date()
        if not scheduled_start:
            # Unscheduled day: only credit early time when an approver override exists.
            fallback_start = self._fallback_scheduled_start_time_for_unscheduled_day()
            if self.clock_in_authorized_by or not fallback_start:
                adjusted_in = timezone.localtime(self.clock_in).replace(second=0, microsecond=0)
            else:
                _minutes_late, adjusted_in = self._tardy_minutes_and_adjusted_start(
                    self.clock_in, fallback_start
                )
        else:
            # Scheduled day: early clock-ins require override to be credited.
            if self.clock_in_authorized_by:
                clock_in_local = timezone.localtime(self.clock_in).replace(second=0, microsecond=0)
                scheduled_local = self._scheduled_local_datetime(scheduled_start)
                if clock_in_local < scheduled_local:
                    adjusted_in = clock_in_local
                else:
                    _minutes_late, adjusted_in = self._tardy_minutes_and_adjusted_start(
                        self.clock_in, scheduled_start
                    )
            else:
                _minutes_late, adjusted_in = self._tardy_minutes_and_adjusted_start(
                    self.clock_in, scheduled_start
                )

        out_local = timezone.localtime(self.clock_out)
        rounded_out_minutes = (out_local.minute // 15) * 15
        adjusted_out = out_local.replace(minute=rounded_out_minutes, second=0, microsecond=0)

        if adjusted_out <= adjusted_in:
            return 0.0

        worked = adjusted_out - adjusted_in
        lunch = self._lunch_deduction_timedelta()

        seconds = max((worked - lunch).total_seconds(), 0)
        hours = Decimal(str(seconds / 3600))
        quarter_hours = (hours * Decimal("4")).to_integral_value(rounding=ROUND_DOWN)
        adjusted = (quarter_hours / Decimal("4")).quantize(Decimal("0.01"))
        return float(adjusted)

    def rounded_start(self):
        if self.clock_in:
            return self.round_to_quarter(self.clock_in)
        return None

    def round_to_quarter(self, dt):
        """
        Round a datetime up to the next 15‑minute interval (ceiling),
        keeping the same date and timezone.
        """
        if not dt:
            return None
        dt = dt.replace(second=0, microsecond=0)
        total_minutes = dt.hour * 60 + dt.minute
        rounded_minutes = ((total_minutes + 14) // 15) * 15  # ceil to next quarter hour
        hours, minutes = divmod(rounded_minutes, 60)
        return dt.replace(hour=hours % 24, minute=minutes)

    def _scheduled_local_datetime(self, scheduled_time):
        """
        Build an aware local datetime for this entry date + scheduled clock time.
        """
        naive_dt = datetime.combine(self.date, scheduled_time)
        return timezone.make_aware(naive_dt, timezone.get_current_timezone())

    def _tardy_minutes_and_adjusted_start(self, punch_dt, scheduled_time):
        """
        Return (minutes_late, adjusted_start_local) using local-time comparison.
        adjusted_start_local equals schedule start for <=4 min late, otherwise
        rounds lateness up to the next 15-minute increment from scheduled start.
        """
        scheduled_local = self._scheduled_local_datetime(scheduled_time)
        punch_local = timezone.localtime(punch_dt)
        delta_seconds = (punch_local - scheduled_local).total_seconds()
        if delta_seconds <= 0:
            return 0, scheduled_local

        minutes_late = int((delta_seconds + 59) // 60)  # ceil partial minutes
        if minutes_late <= 4:
            return minutes_late, scheduled_local

        adjusted_late_minutes = ((minutes_late + 14) // 15) * 15
        adjusted_start = scheduled_local + timedelta(minutes=adjusted_late_minutes)
        return minutes_late, adjusted_start

    def _scheduled_start_time_for_date(self):
        """
        Scheduled start time for this entry date from weekly_schedule or WorkSchedule.
        """
        return get_scheduled_start_for_day(self.user, self.date)

    def _fallback_scheduled_start_time_for_unscheduled_day(self):
        """
        For unscheduled days, infer a reference start from nearby scheduled weekdays.
        Prefers prior days in the same week, then following days.
        """
        for offset in range(1, 7):
            prev_day = self.date - timedelta(days=offset)
            start = get_scheduled_start_for_day(self.user, prev_day)
            if start:
                return start
        for offset in range(1, 7):
            next_day = self.date + timedelta(days=offset)
            start = get_scheduled_start_for_day(self.user, next_day)
            if start:
                return start
        return None

    def check_tardy(self):
        """
        Apply start‑of‑shift tardy rules:
        - <= 4 minutes late: record a 'Tardy In Grace' occurrence with 0 hours.
        - >= 5 minutes late: round actual clock‑in up to next quarter hour and
          create a 'Tardy Out of Grace' occurrence for the lost time, applying PTO.
        """
        from attendance.models import Occurrence, OccurrenceSubtype, OccurrenceType

        start_time = get_scheduled_start_for_day(self.user, self.date)
        if not start_time or not self.clock_in:
            return

        minutes_late, adjusted_start = self._tardy_minutes_and_adjusted_start(
            self.clock_in, start_time
        )

        # Within 4‑minute grace window: mark as in‑grace but do not dock time
        if 0 < minutes_late <= 4:
            Occurrence.objects.get_or_create(
                user=self.user,
                date=self.date,
                occurrence_type=OccurrenceType.UNPLANNED,
                subtype=OccurrenceSubtype.TARDY_IN_GRACE,
                defaults={"duration_hours": 0},
            )
            return

        # 5+ minutes late: round up to next quarter hour and dock time via PTO occurrence
        if minutes_late >= 5:
            scheduled_local = self._scheduled_local_datetime(start_time)
            loss = (adjusted_start - scheduled_local).total_seconds() / 3600
            if loss > 0:
                Occurrence.objects.get_or_create(
                    user=self.user,
                    date=self.date,
                    subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE,
                    defaults={
                        "occurrence_type": OccurrenceType.UNPLANNED,
                        "duration_hours": loss,
                    },
                )

    def check_lunch_tardy(self):
        """
        Apply the same grace/rounding rules when returning from lunch:
        - <= 4 minutes late from scheduled lunch_in: no docking.
        - 5+ minutes late: round up to next quarter hour and create a
          'Tardy Out of Grace' occurrence for the lost time, applying PTO.
        """
        from attendance.models import Occurrence, OccurrenceSubtype, OccurrenceType

        lunch_in_sched = get_scheduled_lunch_in_for_day(self.user, self.date)
        if not lunch_in_sched or not self.lunch_in:
            return

        minutes_late, adjusted_in = self._tardy_minutes_and_adjusted_start(
            self.lunch_in, lunch_in_sched
        )

        # Within 4‑minute grace from lunch: no docking
        if minutes_late <= 4:
            return

        if minutes_late >= 5:
            scheduled_lunch_local = self._scheduled_local_datetime(lunch_in_sched)
            loss = (adjusted_in - scheduled_lunch_local).total_seconds() / 3600
            if loss > 0:
                # Use get_or_create to avoid duplicate if rule runs twice (unique constraint)
                Occurrence.objects.get_or_create(
                    user=self.user,
                    date=self.date,
                    subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE,
                    defaults={
                        "occurrence_type": OccurrenceType.UNPLANNED,
                        "duration_hours": loss,
                    },
                )

    def save(self, *args, **kwargs):
        if not self.slug:
            ensure_unique_slug(self, "slug", max_length=48)
        if (
            self.clock_in
            and self.clock_out
            and self.lunch_out is None
            and self.lunch_in is None
            and not work_through_lunch_approved_for_day(self.user, self.date)
        ):
            scheduled = scheduled_lunch_datetimes_for_entry(self)
            if scheduled:
                self.lunch_out, self.lunch_in = scheduled
        if not self.is_incomplete() and self.missing_punch_flagged:
            self.missing_punch_flagged = False
            self.missing_punch_flagged_at = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.date}"
