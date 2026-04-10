import math
from decimal import Decimal, ROUND_DOWN
from django.db import models
from django.db.models import JSONField, Q, Sum, UniqueConstraint
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from datetime import date, timedelta, datetime

from .slug_utils import ensure_unique_slug

DAYS_OF_WEEK = [
    (0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"),
    (4, "Friday"), (5, "Saturday"), (6, "Sunday"),
]

class WorkSchedule(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="schedules")
    day = models.IntegerField(choices=DAYS_OF_WEEK)
    start_time = models.TimeField()
    lunch_out = models.TimeField(
        null=True,
        blank=True,
        help_text="Leave blank for half-day or no-lunch shifts (e.g. Friday 6:30–11:00).",
    )
    lunch_in = models.TimeField(
        null=True,
        blank=True,
        help_text="Leave blank when lunch out is blank.",
    )
    end_time = models.TimeField()
    crosses_midnight = models.BooleanField(
        default=False,
        help_text="Set when the shift ends the morning after it starts (e.g. 3:30pm–2:00am).",
    )

    class Meta:
        unique_together = ("user", "day")
        ordering = ["day"]

    def __str__(self):
        return f"{self.user.username} - {DAYS_OF_WEEK[self.day][1]}"
    
class RoleChoices(models.TextChoices):
    EXECUTIVE = "executive", "Executive"
    MANAGER = "manager", "Manager"
    SUPERVISOR = "supervisor", "Supervisor"
    GROUP_LEAD = "group_lead", "Group Lead"
    TEAM_LEAD = "team_lead", "Team Lead"
    USER = "user", "User"


class CustomUser(AbstractUser):
    role = models.CharField(max_length=20, choices=RoleChoices.choices, default=RoleChoices.USER)
    department = models.CharField(max_length=100, blank=True, null=True)
    is_part_time = models.BooleanField(default=False)
    is_exempt = models.BooleanField(default=False)
    hire_date = models.DateField(null=True, blank=True)
    service_date = models.DateField(null=True, blank=True)
    group_lead = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='group_members')
    team_lead = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='team_members')
    pto_balance = models.FloatField(default=0.0)
    personal_time_balance = models.FloatField(default=0.0)
    final_pto_balance = models.FloatField(default=0.0)
    hours_worked = models.FloatField(default=0.0)
    timeclock_login = models.CharField(max_length=4, blank=True, null=True)
    timeclock_pin = models.CharField(max_length=4, blank=True, null=True)
    payroll_lastname = models.CharField(max_length=150, blank=True, default="")
    payroll_firstname = models.CharField(max_length=150, blank=True, default="")
    weekly_schedule = JSONField(
        default=dict,
        blank=True,
        null=True,
        help_text='Per weekday: "start", "end", optional "lunch_out"/"lunch_in" (omit both for no lunch). Optional "crosses_midnight": true.',
    )
    supervisor = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name="supervisees"
    )
    public_slug = models.SlugField(max_length=48, unique=True, editable=False, db_index=True)

    def accrue_pto(self, hours):
        """
        Accrue PTO for years 0-2 or part-time: 1 hour PTO per 30 hours worked (fractional).
        Accrual is rounded down to the hundredth so add/subtract on unfinalize cancels exactly.
        Returns the number of hours added to pto_balance for this call (rounded down to 2 decimals).
        """
        if not (self.is_part_time or self.years_of_service() <= 2):
            return 0.0
        if not hours:
            return 0.0
        # Round down to hundredth so unfinalize subtracts the exact same amount
        raw = hours / 30.0
        earned = math.floor(raw * 100) / 100
        if self.is_part_time:
            self.pto_balance = round(min(self.pto_balance + earned, 72.0), 2)
        else:
            self.pto_balance = round(self.pto_balance + earned, 2)
        self.save()
        return earned

    def years_of_service(self):
        if not self.service_date:
            return 0
        return (date.today() - self.service_date).days // 365

    def payroll_last_name_for_display(self):
        return self.payroll_lastname or self.last_name or self.username

    def payroll_first_name_for_display(self):
        return self.payroll_firstname or self.first_name or ""

    def payroll_display_name(self):
        last_name = self.payroll_last_name_for_display()
        first_name = self.payroll_first_name_for_display()
        if first_name:
            return f"{last_name}, {first_name}"
        return last_name

    def __str__(self):
        return self.payroll_display_name()

    def grace_occurrences_remaining(self):
        if not self.service_date:
            return 0
        days = (date.today() - self.service_date).days
        return max(0, 3 - days // 30) if days <= 90 else 0

    def employment_anchor_date(self):
        """Date of hire for policy windows; prefers hire_date, then service_date."""
        return self.hire_date or self.service_date

    def is_date_in_probation_period(self, d: date) -> bool:
        """True if ``d`` falls in the first 90 calendar days from employment anchor (hire/service)."""
        anchor = self.employment_anchor_date()
        if not anchor or d < anchor:
            return False
        return (d - anchor).days < 90

    def reset_pto_at_service_anniversary(self):
        """
        Reset PTO for a new service year based on tenure.
        This should be called when a user's service anniversary is reached.

        Years-of-service PTO scale (full-time), applied for the
        new service year that starts on the anniversary:
        - Years 1-2: accrual only (handled via accrue_pto)
        - Years 3-4: 80 hours
        - Years 5-8: 100 hours
        - Years 9-14: 120 hours
        - Years 15-20: 140 hours
        - Years 21-24: 160 hours
        - Years 25+: 180 hours
        """
        if self.is_part_time or not self.service_date:
            return  # Skip part-time or users with no service date

        service_years = self.years_of_service()  # completed years as of today

        # Front-loaded PTO is based on the service year being started.
        # Example: completed 14 years -> starting year 15 -> 140 hours.
        starting_service_year = service_years + 1

        # PTO allocation scale
        if 3 <= starting_service_year <= 4:
            pto_alloc = 80
        elif 5 <= starting_service_year <= 8:
            pto_alloc = 100
        elif 9 <= starting_service_year <= 14:
            pto_alloc = 120
        elif 15 <= starting_service_year <= 20:
            pto_alloc = 140
        elif 21 <= starting_service_year <= 24:
            pto_alloc = 160
        elif starting_service_year >= 25:
            pto_alloc = 180
        else:
            pto_alloc = 0  # Service years 1-2 accrue hourly, handled elsewhere

        # Carry out any remaining PTO from the previous year for reporting,
        # then start the new year with the allocated amount and reset unpaid time.
        self.final_pto_balance = self.pto_balance
        self.pto_balance = pto_alloc
        self.personal_time_balance = 0
        self.save()

    def set_pto_to_tenure_baseline(self, clear_personal=True):
        """
        Set PTO balance to the correct amount for the user's current years of service.
        Use in admin after deleting occurrences or to correct balances.
        For years 0-2 (accrual) or part-time: sets PTO and personal to 0 (no tenure allocation yet).
        Optional: clear personal_time_balance (unpaid) when refreshing.
        """
        if not self.service_date:
            return
        if self.is_exempt:
            return
        service_years = self.years_of_service()  # completed years
        if service_years < 2 or self.is_part_time:
            self.pto_balance = 0.0
            self.final_pto_balance = 0.0
            if clear_personal:
                self.personal_time_balance = 0.0
            self.save()
            return

        # Baseline/front-load is keyed to the service year being started.
        starting_service_year = service_years + 1

        if starting_service_year < 5:
            pto_alloc = 80
        elif starting_service_year < 9:
            pto_alloc = 100
        elif starting_service_year < 15:
            pto_alloc = 120
        elif starting_service_year < 21:
            pto_alloc = 140
        elif starting_service_year < 25:
            pto_alloc = 160
        else:
            pto_alloc = 180
        self.pto_balance = pto_alloc
        self.final_pto_balance = pto_alloc
        if clear_personal:
            self.personal_time_balance = 0
        self.save()

    def recalculate_balances(self):
        """
        Ensure PTO / personal time balances respect basic caps.
        This does NOT grant new annual PTO; that is handled by
        reset_pto_at_service_anniversary and the admin action.
        """
        if self.is_exempt:
            self.personal_time_balance = 0
            self.final_pto_balance = self.pto_balance

        elif self.is_part_time:
            # Part-time: PTO accrues up to a hard cap of 72 hours.
            self.pto_balance = min(self.pto_balance, 72)
            self.final_pto_balance = self.pto_balance

        else:
            # For full-time, just mirror current PTO into final_pto_balance.
            self.final_pto_balance = self.pto_balance

    def save(self, *args, **kwargs):
        if not self.public_slug:
            ensure_unique_slug(self, "public_slug", max_length=48)
        # Auto-adjust balances whenever a user is saved,
        # based on service date, tenure, and employment type.
        self.recalculate_balances()
        super().save(*args, **kwargs)


class OccurrenceType(models.TextChoices):
    PLANNED = "Planned", "Planned"
    UNPLANNED = "Unplanned", "Unplanned"

QUARTER_HOUR = Decimal("0.25")


def floor_hours_to_quarter_increment(hours: Decimal) -> Decimal:
    """
    Floor hours to payroll quarter-hour increments (0.25).
    PTO balance may accrue in hundredths (e.g. 1.33 from 40/30 accrual), but only
    whole quarter-hours may be applied toward an absence; the fractional balance remains.
    """
    if hours <= 0:
        return Decimal("0")
    quarters = (hours / QUARTER_HOUR).to_integral_value(rounding=ROUND_DOWN)
    return quarters * QUARTER_HOUR


class OccurrenceSubtype(models.TextChoices):
    TIME_OFF = "Time Off", "Time Off"
    TARDY_IN_GRACE = "Tardy In Grace", "Tardy In Grace"
    TARDY_OUT_OF_GRACE = "Tardy Out of Grace", "Tardy Out of Grace"
    EXCHANGE = "Exchange", "Exchange"
    LAYOFF = "Lay-Off", "Lay-Off"
    FMLA = "FMLA", "Family Medical Leave"
    LEAVE_OF_ABSENCE = "LOA", "Leave of Absence"
    WEATHER_UNPAID = "Weather Unpaid", "Inclement Weather - Unpaid"
    WEATHER_PAID = "Weather Paid", "Inclement Weather - Paid"
    BEREAVEMENT_PAID = "Bereavement Paid", "Bereavement - Paid"
    BEREAVEMENT_UNPAID = "Bereavement Unpaid", "Bereavement - Unpaid"
    JURY_DUTY_PAID = "Jury Duty Paid", "Jury Duty - Paid"
    JURY_DUTY_UNPAID = "Jury Duty Unpaid", "Jury Duty - Unpaid"
    DISCIPLINE = "Discipline", "Discipline"
    WORK_COMP = "Work Comp", "Work Comp"
    DISABILITY = "Disability", "Disability"
    HOLIDAY_PAID = "Holiday Paid", "Holiday - Paid"
    GRACE_TIME = "Grace Time", "Grace Time"


# Subtypes that deduct from PTO (and personal time when PTO is exhausted). Used for balance math.
OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL = [
    OccurrenceSubtype.TIME_OFF,
    OccurrenceSubtype.TARDY_OUT_OF_GRACE,
    OccurrenceSubtype.EXCHANGE,
    OccurrenceSubtype.FMLA,
    OccurrenceSubtype.LEAVE_OF_ABSENCE,
    OccurrenceSubtype.WEATHER_PAID,
    OccurrenceSubtype.BEREAVEMENT_PAID,
    OccurrenceSubtype.JURY_DUTY_PAID,
    OccurrenceSubtype.GRACE_TIME,
]

# First 90 days: up to 30 total hours may be charged to probation grace (no PTO); excess to personal.
PROBATION_GRACE_HOURS_CAP = Decimal("30")

PROBATION_GRACE_ELIGIBLE_SUBTYPES = frozenset(
    {
        OccurrenceSubtype.TIME_OFF,
        OccurrenceSubtype.TARDY_OUT_OF_GRACE,
        OccurrenceSubtype.EXCHANGE,
        OccurrenceSubtype.WEATHER_PAID,
        OccurrenceSubtype.BEREAVEMENT_PAID,
        OccurrenceSubtype.JURY_DUTY_PAID,
    }
)

# Perfect Attendance: any occurrence of these subtypes in the reporting period disqualifies.
PERFECT_ATTENDANCE_DISQUALIFYING_SUBTYPES = frozenset(
    {
        OccurrenceSubtype.LEAVE_OF_ABSENCE,
        OccurrenceSubtype.BEREAVEMENT_PAID,
        OccurrenceSubtype.BEREAVEMENT_UNPAID,
        OccurrenceSubtype.WEATHER_PAID,
        OccurrenceSubtype.WEATHER_UNPAID,
        OccurrenceSubtype.FMLA,
        OccurrenceSubtype.LAYOFF,
        OccurrenceSubtype.WORK_COMP,
        OccurrenceSubtype.DISABILITY,
        OccurrenceSubtype.GRACE_TIME,
    }
)

# Absence report PDF: FMLA in its own table; these subtypes never add to personal time (separate table).
ABSENCE_REPORT_FMLA_SUBTYPE = OccurrenceSubtype.FMLA
ABSENCE_REPORT_LEAVE_AND_NO_PERSONAL_SUBTYPES = frozenset(
    {
        OccurrenceSubtype.LEAVE_OF_ABSENCE,
        OccurrenceSubtype.LAYOFF,
        OccurrenceSubtype.DISCIPLINE,
        OccurrenceSubtype.WORK_COMP,
        OccurrenceSubtype.DISABILITY,
        OccurrenceSubtype.TARDY_IN_GRACE,
        OccurrenceSubtype.BEREAVEMENT_UNPAID,
        OccurrenceSubtype.JURY_DUTY_UNPAID,
        OccurrenceSubtype.WEATHER_UNPAID,
        OccurrenceSubtype.HOLIDAY_PAID,
    }
)


def first_full_month_start_after_hire(anchor: date) -> date:
    """
    First day of the first full calendar month strictly after the month containing the hire date.
    Example: hire June 12 -> July 1 (same year); hire December 5 -> January 1 (next year).
    """
    if anchor.month == 12:
        return date(anchor.year + 1, 1, 1)
    return date(anchor.year, anchor.month + 1, 1)


def user_eligible_for_perfect_attendance_new_hire_month(anchor: date | None, period_first: date) -> bool:
    """
    Employees with a hire/service anchor: eligible only for calendar months on or after their
    first full month after hire (months before that are excluded). No anchor: rule does not apply.
    """
    if not anchor:
        return True
    fms = first_full_month_start_after_hire(anchor)
    period_key = (period_first.year, period_first.month)
    first_full_key = (fms.year, fms.month)
    return period_key >= first_full_key


def _probation_grace_hours_used_before(occ: "Occurrence", anchor: date, probation_end: date) -> Decimal:
    """Probation grace bank hours already allocated to earlier applied occurrences (same user, same window)."""
    total = (
        Occurrence.objects.filter(
            user_id=occ.user_id,
            date__gte=anchor,
            date__lt=probation_end,
            pto_applied=True,
        )
        .filter(Q(date__lt=occ.date) | Q(date=occ.date, pk__lt=occ.pk))
        .aggregate(s=Sum("probation_grace_hours_applied"))
    )["s"]
    return Decimal(str(total or 0)).quantize(Decimal("0.01"))


class PTOBalanceHistory(models.Model):
    """
    Audit trail for PTO/personal balance changes. Use for reconciliation and debugging.
    """
    BALANCE_TYPE_PTO = "pto"
    BALANCE_TYPE_PERSONAL = "personal"
    BALANCE_TYPE_CHOICES = [(BALANCE_TYPE_PTO, "PTO"), (BALANCE_TYPE_PERSONAL, "Personal")]

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="pto_balance_history")
    change = models.DecimalField(max_digits=8, decimal_places=2, help_text="Signed change amount")
    reason = models.CharField(max_length=255)
    balance_after = models.DecimalField(max_digits=8, decimal_places=2, help_text="Balance after this change")
    balance_type = models.CharField(
        max_length=20, choices=BALANCE_TYPE_CHOICES, default=BALANCE_TYPE_PTO
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["user", "timestamp"])]

    def __str__(self):
        return f"{self.user.username} {self.balance_type} {self.change} @ {self.timestamp}"

    @classmethod
    def record(cls, user, change, reason, balance_after, balance_type=BALANCE_TYPE_PTO):
        cls.objects.create(
            user=user,
            change=change,
            reason=reason,
            balance_after=balance_after,
            balance_type=balance_type,
        )


class Occurrence(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    occurrence_type = models.CharField(
        "Type",
        max_length=20,
        choices=OccurrenceType.choices,
    )
    subtype = models.CharField(max_length=50, choices=OccurrenceSubtype.choices)
    date = models.DateField()
    duration_hours = models.FloatField(default=0.0)
    pto_applied = models.BooleanField(default=False)
    pto_hours_applied = models.FloatField(default=0.0)  # hours deducted from PTO for this occurrence
    personal_hours_applied = models.FloatField(default=0.0)  # hours that went to personal for this occurrence
    probation_grace_hours_applied = models.FloatField(
        default=0.0,
        help_text="Hours absorbed by the 30h probation grace bank (first 90 days); no PTO impact.",
    )
    is_variance_to_schedule = models.BooleanField(default=False)
    time_off_request = models.ForeignKey(
        "TimeOffRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="occurrences",
    )
    payroll_period = models.ForeignKey(
        "PayrollPeriod",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="occurrences_created",
        help_text="Set when this absence was created at payroll finalize (variance or tardy); used to revert on unfinalize.",
    )

    class Meta:
        verbose_name = "Absence"
        verbose_name_plural = "Absences"
        indexes = [
            models.Index(fields=["user", "date"]),
        ]
        constraints = [
            # Prevent duplicate in-grace tardy when user clocks in twice or rule runs twice
            UniqueConstraint(
                fields=["user", "date", "subtype"],
                condition=Q(subtype=OccurrenceSubtype.TARDY_IN_GRACE),
                name="unique_tardy_in_grace_per_user_date",
            ),
            # One TARDY_OUT_OF_GRACE per (user, date) so rule run twice doesn't create two
            UniqueConstraint(
                fields=["user", "date", "subtype"],
                condition=Q(subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE),
                name="unique_tardy_out_of_grace_per_user_date",
            ),
        ]

    def _subtype_uses_pto(self):
        """True if this subtype deducts from user PTO/personal time balance."""
        return self.subtype in OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # When saved (e.g. from admin) with a PTO-using subtype and not yet applied, deduct from user.
        # Skip if created at payroll close (payroll_period set); close_payroll applies with 40-hour cap.
        if not self.pto_applied and self._subtype_uses_pto() and not self.payroll_period_id:
            self.apply_pto()

    def apply_pto(self, max_pto_to_apply=None):
        """
        Deduct from PTO (then personal) for this occurrence. Only if occurrence date has passed.
        PTO is taken only in quarter-hour increments floored from the user's available balance
        (e.g. balance 1.33 allows at most 1.25 toward this absence; remainder stays in PTO).
        If max_pto_to_apply is set (e.g. 40 - worked for the week), cap PTO deduction so
        regular + PTO does not exceed the cap (cap is also floored to quarter hours).

        Probation (first 90 days from hire_date or service_date): eligible absence types do not
        use PTO; up to 30 hours total across the period may be recorded as Grace Time (no balance
        impact); additional hours add to personal time. After 90 days, normal PTO-then-personal
        applies.

        Returns the number of PTO hours deducted.
        Uses Decimal for calculations to avoid float drift; runs in a transaction with row lock.
        """
        from django.db import transaction

        # ``Occurrence.save()`` already calls ``apply_pto`` once; callers that also call
        # ``apply_pto`` after ``create``/``get_or_create`` must not deduct twice.
        if self.pto_applied:
            return float(self.pto_hours_applied or 0.0)

        if self.date > date.today():
            return 0.0

        # Subtypes that do NOT affect balances (company-paid or fully unpaid/ excused)
        if self.subtype in [
            OccurrenceSubtype.LAYOFF,
            OccurrenceSubtype.DISCIPLINE,
            OccurrenceSubtype.WORK_COMP,
            OccurrenceSubtype.DISABILITY,
            OccurrenceSubtype.TARDY_IN_GRACE,
            OccurrenceSubtype.BEREAVEMENT_UNPAID,
            OccurrenceSubtype.JURY_DUTY_UNPAID,
            OccurrenceSubtype.WEATHER_UNPAID,
            OccurrenceSubtype.HOLIDAY_PAID,
        ]:
            return 0.0

        # Subtypes that affect PTO and possibly personal time
        if self.subtype not in [
            OccurrenceSubtype.TIME_OFF,
            OccurrenceSubtype.TARDY_OUT_OF_GRACE,
            OccurrenceSubtype.EXCHANGE,
            OccurrenceSubtype.FMLA,
            OccurrenceSubtype.LEAVE_OF_ABSENCE,
            OccurrenceSubtype.WEATHER_PAID,
            OccurrenceSubtype.BEREAVEMENT_PAID,
            OccurrenceSubtype.JURY_DUTY_PAID,
            OccurrenceSubtype.GRACE_TIME,
        ]:
            return 0.0

        used = Decimal(str(self.duration_hours))
        with transaction.atomic():
            u = CustomUser.objects.select_for_update().get(pk=self.user_id)
            pto_bal = Decimal(str(u.pto_balance)).quantize(Decimal("0.01"))
            personal_bal = Decimal(str(u.personal_time_balance)).quantize(Decimal("0.01"))

            # For FMLA and Leave of Absence: use PTO when available, but do NOT
            # convert any remaining hours into personal/unpaid time. Remaining
            # hours are treated as leave for tracking only.
            if self.subtype in [OccurrenceSubtype.FMLA, OccurrenceSubtype.LEAVE_OF_ABSENCE]:
                pto_usable = floor_hours_to_quarter_increment(pto_bal)
                pto_deducted = min(used, pto_usable)
                if max_pto_to_apply is not None:
                    cap = floor_hours_to_quarter_increment(Decimal(str(max_pto_to_apply)))
                    pto_deducted = min(pto_deducted, cap)
                new_pto = max(Decimal("0"), pto_bal - pto_deducted)
                u.pto_balance = float(new_pto.quantize(Decimal("0.01")))
                u.save()
                if pto_deducted > 0:
                    PTOBalanceHistory.record(
                        user=u,
                        change=float(-pto_deducted.quantize(Decimal("0.01"))),
                        reason=f"Occurrence apply_pto: {self.get_subtype_display()} ({self.date})",
                        balance_after=u.pto_balance,
                    )
                self.pto_hours_applied = float(pto_deducted.quantize(Decimal("0.01")))
                self.personal_hours_applied = 0.0
                self.pto_applied = True
                self.save()
                return float(pto_deducted.quantize(Decimal("0.01")))

            anchor = u.employment_anchor_date()
            probation_end = anchor + timedelta(days=90) if anchor else None
            uses_probation_grace = (
                anchor
                and probation_end
                and u.is_date_in_probation_period(self.date)
                and (
                    self.subtype in PROBATION_GRACE_ELIGIBLE_SUBTYPES
                    or self.subtype == OccurrenceSubtype.GRACE_TIME
                )
            )
            if uses_probation_grace:
                grace_used_prior = _probation_grace_hours_used_before(self, anchor, probation_end)
                grace_remaining = max(Decimal("0"), PROBATION_GRACE_HOURS_CAP - grace_used_prior)
                grace_portion = min(used, grace_remaining)
                personal_portion = used - grace_portion
                new_personal = personal_bal + personal_portion
                u.personal_time_balance = float(new_personal.quantize(Decimal("0.01")))
                u.save()
                if personal_portion > 0:
                    PTOBalanceHistory.record(
                        user=u,
                        change=float(personal_portion.quantize(Decimal("0.01"))),
                        reason=f"Personal time (probation): {self.get_subtype_display()} ({self.date})",
                        balance_after=u.personal_time_balance,
                        balance_type=PTOBalanceHistory.BALANCE_TYPE_PERSONAL,
                    )
                self.pto_hours_applied = 0.0
                self.personal_hours_applied = float(personal_portion.quantize(Decimal("0.01")))
                self.probation_grace_hours_applied = float(grace_portion.quantize(Decimal("0.01")))
                self.pto_applied = True
                if (
                    grace_portion == used
                    and used > 0
                    and personal_portion == 0
                    and self.subtype != OccurrenceSubtype.GRACE_TIME
                ):
                    self.subtype = OccurrenceSubtype.GRACE_TIME
                self.save()
                return 0.0

            # Default behavior: PTO first (quarter-hour increments from balance only), then
            # remaining hours to personal time.
            pto_usable = floor_hours_to_quarter_increment(pto_bal)
            pto_deducted = min(used, pto_usable)
            if max_pto_to_apply is not None:
                cap = floor_hours_to_quarter_increment(Decimal(str(max_pto_to_apply)))
                pto_deducted = min(pto_deducted, cap)
            personal_deducted = used - pto_deducted
            new_pto = max(Decimal("0"), pto_bal - pto_deducted)
            new_personal = personal_bal + personal_deducted
            u.pto_balance = float(new_pto.quantize(Decimal("0.01")))
            u.personal_time_balance = float(new_personal.quantize(Decimal("0.01")))
            u.save()
            PTOBalanceHistory.record(
                user=u,
                change=float(-pto_deducted.quantize(Decimal("0.01"))),
                reason=f"Occurrence apply_pto: {self.get_subtype_display()} ({self.date})",
                balance_after=u.pto_balance,
            )
            if personal_deducted > 0:
                PTOBalanceHistory.record(
                    user=u,
                    change=float(personal_deducted.quantize(Decimal("0.01"))),
                    reason=f"Personal time: {self.get_subtype_display()} ({self.date})",
                    balance_after=u.personal_time_balance,
                    balance_type=PTOBalanceHistory.BALANCE_TYPE_PERSONAL,
                )
            self.pto_hours_applied = float(pto_deducted.quantize(Decimal("0.01")))
            self.personal_hours_applied = float(personal_deducted.quantize(Decimal("0.01")))
            self.pto_applied = True
            self.save()
        return float(pto_deducted.quantize(Decimal("0.01")))


def revert_tardy_occurrences_for_adjust_punch(user, occ_date):
    """
    Before applying an approved punch adjustment, remove tardy occurrences for that calendar day,
    refunding any PTO/personal that was applied for them (same idea as cancelling time off).
    Call inside transaction.atomic(); ``user`` must be the locked CustomUser instance (select_for_update).
    """
    qs = Occurrence.objects.filter(
        user=user,
        date=occ_date,
        subtype__in=[
            OccurrenceSubtype.TARDY_IN_GRACE,
            OccurrenceSubtype.TARDY_OUT_OF_GRACE,
        ],
    )
    if not qs.exists():
        return
    for occ in qs:
        if occ.pto_applied:
            user.pto_balance = round(user.pto_balance + occ.pto_hours_applied, 2)
            user.personal_time_balance = round(
                max(0.0, user.personal_time_balance - occ.personal_hours_applied), 2
            )
            PTOBalanceHistory.record(
                user=user,
                change=float(occ.pto_hours_applied),
                reason=f"Adjust punch: revert {occ.get_subtype_display()} ({occ.date})",
                balance_after=user.pto_balance,
            )
        occ.delete()
    user.save()


def apply_past_due_occurrences(user):
    """
    Apply PTO/personal for any occurrences that are due (date <= today) but not yet applied.
    Call from dashboard or when loading user balance so that when a future approved date passes,
    the balance is updated on next view.
    """
    today = date.today()
    past_due = Occurrence.objects.filter(
        user=user,
        date__lte=today,
        pto_applied=False,
        subtype__in=OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL,
    )
    for occ in past_due:
        occ.apply_pto()


class TimeOffRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    DENIED = "denied", "Denied"
    CANCELLED = "cancelled", "Cancelled"


class TimeOffRequest(models.Model):
    """
    A user-initiated request to use PTO for one or more scheduled work days.
    Approval will create one or more Occurrence records and apply PTO.
    """

    slug = models.SlugField(max_length=48, unique=True, editable=False, db_index=True)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="time_off_requests")
    start_date = models.DateField()
    end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=20,
        choices=TimeOffRequestStatus.choices,
        default=TimeOffRequestStatus.PENDING,
    )
    planned = models.BooleanField(default=False)
    partial_day = models.BooleanField(default=False)
    partial_hours = models.FloatField(null=True, blank=True)
    approver = models.ForeignKey(
        CustomUser,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_time_off_requests",
    )
    subtype = models.CharField(
        max_length=50,
        choices=OccurrenceSubtype.choices,
        default=OccurrenceSubtype.TIME_OFF,
    )
    comments = models.TextField(blank=True)
    reason = models.CharField(max_length=255, blank=True)  # deprecated; use subtype + comments

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} PTO {self.start_date} to {self.end_date} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.slug:
            ensure_unique_slug(self, "slug", max_length=48)
        super().save(*args, **kwargs)

    def compute_requested_hours(self):
        """
        Compute total scheduled hours for this request window from weekly_schedule JSON
        and/or WorkSchedule (including half-day rows with no lunch).
        """
        from .schedule_utils import scheduled_duration_hours_for_day

        if self.partial_day:
            return max(float(self.partial_hours or 0), 0.0)
        total = 0.0
        current = self.start_date
        while current <= self.end_date:
            h = scheduled_duration_hours_for_day(self.user, current)
            if h > 0:
                total += h
            current += timedelta(days=1)
        return total

    def mark_planned_or_unplanned(self):
        """
        Planned if submitted at least one day before the first requested date,
        otherwise unplanned.
        """
        created_date = self.created_at.date() if self.created_at else date.today()
        self.planned = created_date <= (self.start_date - timedelta(days=1))

    def approve(self, approver_user):
        """
        Approve this request, create PTO occurrences, and apply PTO.
        """
        if self.status != TimeOffRequestStatus.PENDING:
            return

        self.approver = approver_user
        self.mark_planned_or_unplanned()
        self.status = TimeOffRequestStatus.APPROVED
        self.save()

        occurrence_type = (
            OccurrenceType.PLANNED if self.planned else OccurrenceType.UNPLANNED
        )

        from .schedule_utils import scheduled_duration_hours_for_day

        current = self.start_date
        while current <= self.end_date:
            if self.partial_day:
                if current != self.start_date:
                    current += timedelta(days=1)
                    continue
                daily_hours = max(float(self.partial_hours or 0), 0.0)
            else:
                daily_hours = scheduled_duration_hours_for_day(self.user, current)

            if daily_hours > 0:
                Occurrence.objects.create(
                    user=self.user,
                    occurrence_type=occurrence_type,
                    subtype=getattr(self, "subtype", OccurrenceSubtype.TIME_OFF),
                    date=current,
                    duration_hours=daily_hours,
                    time_off_request=self,
                )
            current += timedelta(days=1)

    def deny(self, approver_user):
        """
        Deny this request without affecting balances.
        """
        if self.status != TimeOffRequestStatus.PENDING:
            return
        self.approver = approver_user
        self.status = TimeOffRequestStatus.DENIED
        self.save()

    def cancel(self):
        """
        Cancel this request. When PENDING: just set status. When APPROVED: reverse
        PTO (credit user), delete linked occurrences, then set status to CANCELLED.
        Runs in a transaction with row lock on user for safety.
        """
        from django.db import transaction

        if self.status == TimeOffRequestStatus.PENDING:
            self.status = TimeOffRequestStatus.CANCELLED
            self.save()
            return
        if self.status != TimeOffRequestStatus.APPROVED:
            return
        with transaction.atomic():
            u = CustomUser.objects.select_for_update().get(pk=self.user_id)
            for occ in self.occurrences.all():
                if occ.pto_applied:
                    u.pto_balance += occ.pto_hours_applied
                    u.personal_time_balance = max(0, u.personal_time_balance - occ.personal_hours_applied)
                    PTOBalanceHistory.record(
                        user=u,
                        change=occ.pto_hours_applied,
                        reason=f"Time off request cancelled (refund): {self.start_date}–{self.end_date}",
                        balance_after=u.pto_balance,
                    )
                occ.delete()
            u.save()
        self.status = TimeOffRequestStatus.CANCELLED
        self.save()


class WorkThroughLunchRequest(models.Model):
    """
    Request to work through a scheduled lunch period (no lunch break taken).
    When approved, automatic lunch deduction and scheduled lunch punches are skipped for that day.
    """

    slug = models.SlugField(max_length=48, unique=True, editable=False, db_index=True)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="work_through_lunch_requests",
    )
    work_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=20,
        choices=TimeOffRequestStatus.choices,
        default=TimeOffRequestStatus.PENDING,
    )
    approver = models.ForeignKey(
        CustomUser,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_work_through_lunch_requests",
    )
    comments = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} work through lunch {self.work_date} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.slug:
            ensure_unique_slug(self, "slug", max_length=48)
        super().save(*args, **kwargs)

    def approve(self, approver_user):
        if self.status != TimeOffRequestStatus.PENDING:
            return
        self.approver = approver_user
        self.status = TimeOffRequestStatus.APPROVED
        self.save()

    def deny(self, approver_user):
        if self.status != TimeOffRequestStatus.PENDING:
            return
        self.approver = approver_user
        self.status = TimeOffRequestStatus.DENIED
        self.save()

    def cancel(self):
        if self.status == TimeOffRequestStatus.PENDING:
            self.status = TimeOffRequestStatus.CANCELLED
            self.save()
        elif self.status == TimeOffRequestStatus.APPROVED:
            self.status = TimeOffRequestStatus.CANCELLED
            self.save()


class AdjustPunchField(models.TextChoices):
    CLOCK_IN = "clock_in", "Clock in"
    LUNCH_OUT = "lunch_out", "Lunch out"
    LUNCH_IN = "lunch_in", "Lunch in"
    CLOCK_OUT = "clock_out", "Clock out"


class AdjustPunchRequest(models.Model):
    """
    Request to correct a recorded punch time. On approval, tardy-related occurrences for that day
    are reverted (PTO/personal refunded), the punch is updated, then tardy rules may re-run.
    """

    slug = models.SlugField(max_length=48, unique=True, editable=False, db_index=True)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="adjust_punch_requests",
    )
    time_entry = models.ForeignKey(
        "timeclock.TimeEntry",
        on_delete=models.CASCADE,
        related_name="adjust_punch_requests",
    )
    punch_field = models.CharField(max_length=20, choices=AdjustPunchField.choices)
    previous_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Punch value at time of request (for audit).",
    )
    requested_at = models.DateTimeField(help_text="Requested corrected date/time for this punch.")
    comments = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=20,
        choices=TimeOffRequestStatus.choices,
        default=TimeOffRequestStatus.PENDING,
    )
    approver = models.ForeignKey(
        CustomUser,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_adjust_punch_requests",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} adjust punch {self.punch_field} {self.time_entry.date} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.slug:
            ensure_unique_slug(self, "slug", max_length=48)
        super().save(*args, **kwargs)

    def deny(self, approver_user):
        if self.status != TimeOffRequestStatus.PENDING:
            return
        self.approver = approver_user
        self.status = TimeOffRequestStatus.DENIED
        self.save()

    def cancel(self):
        if self.status == TimeOffRequestStatus.PENDING:
            self.status = TimeOffRequestStatus.CANCELLED
            self.save()


class PayrollPeriod(models.Model):
    """
    Tracks whether a payroll week (ending Saturday) has been finalized.
    When finalized, time entries for that week are locked; unfinalize to allow corrections.
    """
    week_ending = models.DateField(unique=True)  # Saturday
    is_finalized = models.BooleanField(default=False)
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="finalized_payroll_periods",
    )

    class Meta:
        ordering = ["-week_ending"]

    def __str__(self):
        return f"Payroll {self.week_ending} ({'finalized' if self.is_finalized else 'open'})"


class PayrollPeriodUserSnapshot(models.Model):
    """
    Stores PTO accrued per user when a payroll period is finalized, so we can revert on unfinalize.
    """
    period = models.ForeignKey(
        PayrollPeriod,
        on_delete=models.CASCADE,
        related_name="user_snapshots",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payroll_period_snapshots",
    )
    pto_accrued_hours = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("period", "user")
        ordering = ["period", "user"]

    def __str__(self):
        return f"{self.period.week_ending} / {self.user} accrued {self.pto_accrued_hours}"


def get_company_holidays(year: int):
    """
    Return a dict of {date: name} for company holidays in the given year.
    Holidays:
    - New Year's Day (Jan 1)
    - Memorial Day (last Monday in May)
    - Independence Day (July 4)
    - Labor Day (first Monday in September)
    - Thanksgiving Day (fourth Thursday in November)
    - Christmas Day (Dec 25)
    """
    holidays = {}

    # New Year's Day
    holidays[date(year, 1, 1)] = "New Year's Day"

    # Memorial Day: last Monday in May
    may_last = date(year, 5, 31)
    while may_last.weekday() != 0:  # 0 = Monday
        may_last -= timedelta(days=1)
    holidays[may_last] = "Memorial Day"

    # Independence Day
    holidays[date(year, 7, 4)] = "Independence Day"

    # Labor Day: first Monday in September
    sept_first = date(year, 9, 1)
    while sept_first.weekday() != 0:
        sept_first += timedelta(days=1)
    holidays[sept_first] = "Labor Day"

    # Thanksgiving: fourth Thursday in November
    nov_first = date(year, 11, 1)
    # find first Thursday
    while nov_first.weekday() != 3:  # 3 = Thursday
        nov_first += timedelta(days=1)
    thanksgiving = nov_first + timedelta(weeks=3)
    holidays[thanksgiving] = "Thanksgiving Day"

    # Christmas Day
    holidays[date(year, 12, 25)] = "Christmas Day"

    return holidays


def _user_met_holiday_attendance_rule(user: CustomUser, holiday_date: date) -> bool:
    """
    Full-time employees only receive holiday pay if they reported
    (worked OR had an approved occurrence) on their last scheduled day
    before AND their next scheduled day after the holiday.
    """
    # Find last scheduled workday before the holiday
    day = holiday_date - timedelta(days=1)
    last_before = None
    while (holiday_date - day).days <= 7 and day < holiday_date:
        if user.schedules.filter(day=day.weekday()).exists():
            last_before = day
            break
        day -= timedelta(days=1)

    # Find next scheduled workday after the holiday
    day = holiday_date + timedelta(days=1)
    next_after = None
    while (day - holiday_date).days <= 7 and day > holiday_date:
        if user.schedules.filter(day=day.weekday()).exists():
            next_after = day
            break
        day += timedelta(days=1)

    if not last_before or not next_after:
        # If we can't find both sides, be conservative and require neither condition
        return True

    from timeclock.models import TimeEntry

    def _has_time_or_approved_occurrence(the_date: date) -> bool:
        # Any time entry counts as reporting
        has_entry = TimeEntry.objects.filter(user=user, date=the_date).exists()
        if has_entry:
            return True
        # Any occurrence (planned/unplanned, paid/unpaid) means they had an approved leave
        return Occurrence.objects.filter(user=user, date=the_date).exists()

    return _has_time_or_approved_occurrence(last_before) and _has_time_or_approved_occurrence(
        next_after
    )


def ensure_holiday_occurrences_for_range(start_date: date, end_date: date):
    """
    For each active, non-exempt user who is scheduled on a company holiday
    within the given date range, create a HOLIDAY_PAID Occurrence with
    duration equal to their normal scheduled shift, if one does not already exist.
    """
    if start_date > end_date:
        return

    years = set()
    current = start_date
    while current <= end_date:
        years.add(current.year)
        current += timedelta(days=1)

    holiday_dates = {}
    for y in years:
        holiday_dates.update(get_company_holidays(y))

    from .schedule_utils import scheduled_duration_hours_for_day

    users = CustomUser.objects.filter(is_active=True, is_exempt=False, is_part_time=False)

    for user in users:
        current = start_date
        while current <= end_date:
            if current not in holiday_dates:
                current += timedelta(days=1)
                continue

            daily_hours = scheduled_duration_hours_for_day(user, current)
            if daily_hours <= 0:
                current += timedelta(days=1)
                continue

            # If employee did not meet attendance rule (last before & next after),
            # ensure any existing holiday pay is removed and skip granting.
            if not _user_met_holiday_attendance_rule(user, current):
                Occurrence.objects.filter(
                    user=user,
                    date=current,
                    subtype=OccurrenceSubtype.HOLIDAY_PAID,
                ).delete()
                current += timedelta(days=1)
                continue

            # Skip if a holiday occurrence already exists
            if Occurrence.objects.filter(
                user=user,
                date=current,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
            ).exists():
                current += timedelta(days=1)
                continue

            Occurrence.objects.create(
                user=user,
                occurrence_type=OccurrenceType.PLANNED,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
                date=current,
                duration_hours=daily_hours,
            )

            current += timedelta(days=1)