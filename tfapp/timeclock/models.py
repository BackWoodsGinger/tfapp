from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta, datetime


class TimeEntry(models.Model):
    """One entry per user per day; enforced by unique constraint to prevent duplicates and race double-creation."""
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
        return any(fields) and not all(fields)

    def total_worked_time(self):
        """Return worked hours; uses Decimal for consistent rounding and to avoid float drift."""
        if self.clock_in and self.clock_out:
            worked = self.clock_out - self.clock_in
            if self.lunch_out and self.lunch_in:
                lunch = self.lunch_in - self.lunch_out
                if lunch < timedelta(minutes=30):
                    lunch = timedelta(minutes=30)
            else:
                lunch = timedelta(minutes=30)
            seconds = max((worked - lunch).total_seconds(), 0)
            return float(Decimal(str(seconds / 3600)).quantize(Decimal("0.01")))
        return 0.0

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

    def check_tardy(self):
        """
        Apply start‑of‑shift tardy rules:
        - <= 4 minutes late: record a 'Tardy In Grace' occurrence with 0 hours.
        - >= 5 minutes late: round actual clock‑in up to next quarter hour and
          create a 'Tardy Out of Grace' occurrence for the lost time, applying PTO.
        """
        from attendance.models import Occurrence, OccurrenceSubtype, OccurrenceType

        schedule = self.user.schedules.filter(day=self.date.weekday()).first()
        if not schedule or not self.clock_in:
            return

        minutes_late, adjusted_start = self._tardy_minutes_and_adjusted_start(
            self.clock_in, schedule.start_time
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
            scheduled_local = self._scheduled_local_datetime(schedule.start_time)
            loss = (adjusted_start - scheduled_local).total_seconds() / 3600
            if loss > 0:
                occ, created = Occurrence.objects.get_or_create(
                    user=self.user,
                    date=self.date,
                    subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE,
                    defaults={
                        "occurrence_type": OccurrenceType.UNPLANNED,
                        "duration_hours": loss,
                    },
                )
                if created:
                    occ.apply_pto()

    def check_lunch_tardy(self):
        """
        Apply the same grace/rounding rules when returning from lunch:
        - <= 4 minutes late from scheduled lunch_in: no docking.
        - 5+ minutes late: round up to next quarter hour and create a
          'Tardy Out of Grace' occurrence for the lost time, applying PTO.
        """
        from attendance.models import Occurrence, OccurrenceSubtype, OccurrenceType

        schedule = self.user.schedules.filter(day=self.date.weekday()).first()
        if not schedule or not self.lunch_in:
            return

        minutes_late, adjusted_in = self._tardy_minutes_and_adjusted_start(
            self.lunch_in, schedule.lunch_in
        )

        # Within 4‑minute grace from lunch: no docking
        if minutes_late <= 4:
            return

        if minutes_late >= 5:
            scheduled_lunch_local = self._scheduled_local_datetime(schedule.lunch_in)
            loss = (adjusted_in - scheduled_lunch_local).total_seconds() / 3600
            if loss > 0:
                # Use get_or_create to avoid duplicate if rule runs twice (unique constraint)
                occ, created = Occurrence.objects.get_or_create(
                    user=self.user,
                    date=self.date,
                    subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE,
                    defaults={
                        "occurrence_type": OccurrenceType.UNPLANNED,
                        "duration_hours": loss,
                    },
                )
                if created:
                    occ.apply_pto()

    def save(self, *args, **kwargs):
        if not self.is_incomplete() and self.missing_punch_flagged:
            self.missing_punch_flagged = False
            self.missing_punch_flagged_at = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.date}"
