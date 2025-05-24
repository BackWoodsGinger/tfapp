from django.db import models
from django.contrib.auth.models import AbstractUser
from datetime import date, timedelta


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

    supervisor = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name="supervisees"
    )

    def accrue_pto(self, hours):
        if self.is_part_time or self.years_of_service() <= 2:
            self.hours_worked += hours
            earned = self.hours_worked // 30
            if self.is_part_time:
                self.pto_balance = min(self.pto_balance + earned, 72)
            else:
                self.pto_balance += earned
            self.hours_worked %= 30
            self.save()

    def years_of_service(self):
        if not self.service_date:
            return 0
        return (date.today() - self.service_date).days // 365

    def grace_occurrences_remaining(self):
        if not self.service_date:
            return 0
        days = (date.today() - self.service_date).days
        return max(0, 3 - days // 30) if days <= 90 else 0

    def reset_pto_at_service_anniversary(self):
        """Resets PTO annually based on tenure. Applies updated PTO and carries over."""
        if self.is_part_time or not self.service_date:
            return  # Skip part-time or users with no service date

        today = date.today()
        service_years = self.years_of_service()

        # Find the most recent past anniversary
        anniversary_this_year = self.service_date.replace(year=today.year)
        if today < anniversary_this_year:
            last_anniversary = self.service_date.replace(year=today.year - 1)
        else:
            last_anniversary = anniversary_this_year

        if (today - last_anniversary).days < 1:
            return  # Only update on anniversary day

        # PTO allocation scale
        if 3 <= service_years <= 4:
            pto_alloc = 80
        elif 5 <= service_years <= 8:
            pto_alloc = 100
        elif 9 <= service_years <= 14:
            pto_alloc = 120
        elif 15 <= service_years <= 19:
            pto_alloc = 140
        elif 20 <= service_years <= 24:
            pto_alloc = 160
        elif service_years >= 25:
            pto_alloc = 180
        else:
            pto_alloc = 0  # Years 1-2 accrue hourly, handled elsewhere

        self.final_pto_balance = self.pto_balance
        self.pto_balance = pto_alloc
        self.save()

    def recalculate_balances(self):
        today = date.today()
        years = self.years_of_service()

        if self.is_exempt:
            self.personal_time_balance = 0
            self.final_pto_balance = self.pto_balance

        elif self.is_part_time:
            self.pto_balance = min(self.pto_balance, 72)
            self.final_pto_balance = self.pto_balance

        elif self.service_date:
            if years == 2:
                self.final_pto_balance = self.pto_balance
            elif years >= 3:
                if years < 5:
                    self.pto_balance = max(self.pto_balance, 80)
                elif years < 10:
                    self.pto_balance = max(self.pto_balance, 100)
                elif years < 15:
                    self.pto_balance = max(self.pto_balance, 120)
                elif years < 20:
                    self.pto_balance = max(self.pto_balance, 140)
                elif years < 25:
                    self.pto_balance = max(self.pto_balance, 160)
                else:
                    self.pto_balance = max(self.pto_balance, 180)
                self.final_pto_balance = self.pto_balance

        self.save()


class OccurrenceType(models.TextChoices):
    PLANNED = "Planned", "Planned"
    UNPLANNED = "Unplanned", "Unplanned"

class OccurrenceSubtype(models.TextChoices):
    TIME_OFF = "Time Off", "Time Off"
    TARDY_IN_GRACE = "Tardy In Grace", "Tardy In Grace"
    TARDY_OUT_OF_GRACE = "Tardy Out of Grace", "Tardy Out of Grace"
    EXCHANGE = "Exchange", "Exchange"
    FMLA = "FMLA", "Family Medical Leave"
    LEAVE_OF_ABSENCE = "LOA", "Leave of Absence"
    TRANSPORTATION = "Transportation", "Transportation"
    WEATHER_UNPAID = "Weather Unpaid", "Inclement Weather - Unpaid"
    WEATHER_PAID = "Weather Paid", "Inclement Weather - Paid"
    BEREAVEMENT_PAID = "Bereavement Paid", "Bereavement - Paid"
    BEREAVEMENT_UNPAID = "Bereavement Unpaid", "Bereavement - Unpaid"
    JURY_DUTY_PAID = "Jury Duty Paid", "Jury Duty - Paid"
    JURY_DUTY_UNPAID = "Jury Duty Unpaid", "Jury Duty - Unpaid"
    DISCIPLINE = "Discipline", "Discipline"
    WORK_COMP = "Work Comp", "Work Comp"
    DISABILITY = "Disability", "Disability"


class Occurrence(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    occurrence_type = models.CharField(max_length=20, choices=OccurrenceType.choices)
    subtype = models.CharField(max_length=50, choices=OccurrenceSubtype.choices)
    date = models.DateField()
    duration_hours = models.FloatField(default=0.0)
    pto_applied = models.BooleanField(default=False)

    def apply_pto(self):
        u = self.user
        used = self.duration_hours

        # Subtypes that do NOT affect balances
        if self.subtype in [
            OccurrenceSubtype.DISCIPLINE,
            OccurrenceSubtype.WORK_COMP,
            OccurrenceSubtype.DISABILITY,
            OccurrenceSubtype.TARDY_IN_GRACE,
            OccurrenceSubtype.BEREAVEMENT_UNPAID,
            OccurrenceSubtype.JURY_DUTY_UNPAID,
            OccurrenceSubtype.WEATHER_UNPAID,
        ]:
            self.pto_applied = False
            self.save()
            return

        # Subtypes that affect PTO and possibly personal time
        if self.subtype in [
            OccurrenceSubtype.TIME_OFF,
            OccurrenceSubtype.TARDY_OUT_OF_GRACE,
            OccurrenceSubtype.EXCHANGE,
            OccurrenceSubtype.FMLA,
            OccurrenceSubtype.LEAVE_OF_ABSENCE,
            OccurrenceSubtype.TRANSPORTATION,
            OccurrenceSubtype.WEATHER_PAID,
            OccurrenceSubtype.BEREAVEMENT_PAID,
            OccurrenceSubtype.JURY_DUTY_PAID,
        ]:
            if u.pto_balance >= used:
                u.pto_balance -= used
            else:
                remaining = used - u.pto_balance
                u.pto_balance = 0
                u.personal_time_balance += remaining  # personal time increases
            self.pto_applied = True
            u.save()
            self.save()