from django import forms
from datetime import timedelta, date, datetime
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Submit, Div
from .models import (
    AdjustPunchField,
    AdjustPunchRequest,
    Occurrence,
    OccurrenceSubtype,
    CustomUser,
    PayrollPeriod,
    TimeOffRequest,
    TimeOffRequestStatus,
    WorkThroughLunchRequest,
)
from timeclock.models import TimeEntry

# Absence subtypes not offered on the employee time-off request form (admin/system only).
TIME_OFF_REQUEST_SUBTYPE_EXCLUDE = frozenset(
    {
        OccurrenceSubtype.LAYOFF,
        OccurrenceSubtype.TARDY_IN_GRACE,
        OccurrenceSubtype.TARDY_OUT_OF_GRACE,
        OccurrenceSubtype.WEATHER_UNPAID,
        OccurrenceSubtype.WEATHER_PAID,
        OccurrenceSubtype.DISCIPLINE,
        OccurrenceSubtype.WORK_COMP,
        OccurrenceSubtype.HOLIDAY_PAID,
    }
)


class OccurrenceForm(forms.ModelForm):
    class Meta:
        model = Occurrence
        fields = ["user", "occurrence_type", "date", "duration_hours"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "duration_hours": forms.NumberInput(attrs={"step": 0.25}),
        }


class ReportFilterForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=CustomUser.objects.all(),
        to_field_name="public_slug",
        label="User",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "get"
        self.helper.form_tag = False  # template wraps with form action for PDF
        self.helper.layout = Layout(
            Row(Column("user", css_class="col-md-6 col-lg-4")),
            Row(
                Column("start_date", css_class="col-auto me-2"),
                Column("end_date", css_class="col-auto me-2"),
                css_class="g-2 mb-2",
            ),
            Submit("submit", "Download Report", css_class="btn btn-primary"),
        )


class TimeOffRequestForm(forms.ModelForm):
    """
    User requests full scheduled days off between start_date and end_date.
    Absence type (subtype) and optional comments. Validation enforces:
    - start_date <= end_date
    - all days within a single payroll week
    - PTO hours for that week do not exceed 40.
    """

    class Meta:
        model = TimeOffRequest
        fields = ["start_date", "end_date", "partial_day", "partial_hours", "subtype", "comments"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "partial_day": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "partial_hours": forms.NumberInput(attrs={"step": 0.25, "min": 0.25, "class": "form-control"}),
            "subtype": forms.Select(attrs={"class": "form-select"}),
            "comments": forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "Optional"}),
        }

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop("request_user", None)
        super().__init__(*args, **kwargs)
        excl = {s.value for s in TIME_OFF_REQUEST_SUBTYPE_EXCLUDE}
        self.fields["subtype"].choices = [
            (v, lbl) for v, lbl in OccurrenceSubtype.choices if v not in excl
        ]
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.form_method = "post"
        self.helper.form_class = "form-horizontal"
        self.helper.label_class = "form-label"
        self.helper.field_class = "mb-3"
        self.helper.layout = Layout(
            Row(
                Column("start_date", css_class="col-md-6"),
                Column("end_date", css_class="col-md-6"),
            ),
            Row(
                Column("partial_day", css_class="col-md-3"),
                Column("partial_hours", css_class="col-md-3"),
            ),
            "subtype",
            "comments",
        )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        partial_day = cleaned_data.get("partial_day")
        partial_hours = cleaned_data.get("partial_hours")
        subtype = cleaned_data.get("subtype")

        if subtype and str(subtype) in {s.value for s in TIME_OFF_REQUEST_SUBTYPE_EXCLUDE}:
            self.add_error(
                "subtype",
                "That absence type is not available for employee time off requests.",
            )

        if not start_date or not end_date:
            return cleaned_data

        if start_date > end_date:
            raise forms.ValidationError("Start date cannot be after end date.")

        # Restrict to a single payroll week (Sunday–Saturday) for simplicity
        # to make the 40-hour weekly PTO cap unambiguous.
        start_week_start = start_date - timedelta(days=(start_date.weekday() + 1) % 7)
        end_week_start = end_date - timedelta(days=(end_date.weekday() + 1) % 7)
        if start_week_start != end_week_start:
            raise forms.ValidationError(
                "Time off requests cannot span multiple payroll weeks. "
                "Please submit separate requests for each week."
            )

        if partial_day:
            if start_date != end_date:
                raise forms.ValidationError(
                    "Partial-day requests must be for a single date."
                )
            if partial_hours is None or partial_hours <= 0:
                raise forms.ValidationError(
                    "Enter partial-day hours greater than 0."
                )

        user = self.request_user
        if not user:
            return cleaned_data

        if partial_day:
            # Ensure partial hours do not exceed the scheduled hours for that date.
            temp_request = TimeOffRequest(user=user, start_date=start_date, end_date=end_date)
            scheduled_hours = temp_request.compute_requested_hours()
            if scheduled_hours <= 0:
                raise forms.ValidationError(
                    "You do not have scheduled hours on that date."
                )
            if float(partial_hours) > float(scheduled_hours):
                raise forms.ValidationError(
                    f"Partial-day hours cannot exceed scheduled hours ({scheduled_hours:.2f}) for that date."
                )

        from .models import TimeOffRequestStatus

        week_start = start_week_start
        week_end = week_start + timedelta(days=6)

        # Compute hours for this new request based on the user's schedule
        temp_request = TimeOffRequest(user=user, start_date=start_date, end_date=end_date)
        new_hours = temp_request.compute_requested_hours()

        # Sum hours for existing approved or pending requests in that same week
        existing_requests = TimeOffRequest.objects.filter(
            user=user,
            start_date__lte=week_end,
            end_date__gte=week_start,
            status__in=[
                TimeOffRequestStatus.PENDING,
                TimeOffRequestStatus.APPROVED,
            ],
        )

        existing_hours = 0.0
        for req in existing_requests:
            existing_hours += req.compute_requested_hours()

        if existing_hours + new_hours > 40:
            raise forms.ValidationError(
                "Total PTO requested for this payroll week would exceed 40 hours."
            )

        return cleaned_data


def _week_ending_saturday_for_date(d: date) -> date:
    return d + timedelta(days=(5 - d.weekday()) % 7)


class AdjustPunchRequestForm(forms.Form):
    """Request a correction to one punch on a time entry the user already recorded."""

    time_entry_slug = forms.CharField(
        label="Time entry",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_time_entry_slug"}),
    )
    punch_field = forms.ChoiceField(
        label="Punch to correct",
        choices=AdjustPunchField.choices,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_punch_field"}),
    )
    requested_time = forms.TimeField(
        label="Corrected time",
        help_text="Only the time is set here; the date comes from the selected time entry and punch.",
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "id": "id_requested_time", "step": "60"},
            format="%H:%M",
        ),
        input_formats=["%H:%M", "%H:%M:%S"],
    )
    comments = forms.CharField(
        label="Reason (optional)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "e.g. Forgot to clock in at start of shift"}),
    )

    def __init__(self, *args, request_user=None, time_entry_queryset=None, **kwargs):
        self.request_user = request_user
        super().__init__(*args, **kwargs)
        if time_entry_queryset is not None:
            self.fields["time_entry_slug"].widget = forms.Select(
                choices=[("", "— Select day —")]
                + [(e.slug, f"{e.date} ({e.date.strftime('%A')})") for e in time_entry_queryset],
                attrs={"class": "form-select", "id": "id_time_entry_slug"},
            )

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "time_entry_slug",
            "punch_field",
            "requested_time",
            "comments",
        )

    def clean(self):
        from django.utils import timezone as django_tz

        cleaned_data = super().clean()
        slug = (cleaned_data.get("time_entry_slug") or "").strip()
        punch_field = cleaned_data.get("punch_field")
        requested_time = cleaned_data.get("requested_time")
        user = self.request_user
        if not slug or not user or not punch_field:
            return cleaned_data

        try:
            entry = TimeEntry.objects.get(slug=slug, user=user)
        except TimeEntry.DoesNotExist:
            raise forms.ValidationError("Invalid time entry.")

        current = getattr(entry, punch_field)
        if current is None:
            raise forms.ValidationError(
                "That punch is not recorded for that day. There is nothing to adjust for this field."
            )

        we = _week_ending_saturday_for_date(entry.date)
        if PayrollPeriod.objects.filter(week_ending=we, is_finalized=True).exists():
            raise forms.ValidationError(
                "That week is payroll-finalized. Ask an administrator to unfinalize payroll before punch adjustments."
            )

        if AdjustPunchRequest.objects.filter(
            time_entry=entry,
            status=TimeOffRequestStatus.PENDING,
        ).exists():
            raise forms.ValidationError(
                "You already have a pending adjust-punch request for this day. Cancel it or wait for a decision."
            )

        if requested_time is None:
            return cleaned_data

        # Same calendar date as the existing punch in local time (handles overnight shifts).
        anchor_date = django_tz.localtime(current).date()
        naive_dt = datetime.combine(anchor_date, requested_time)
        cleaned_data["requested_at"] = django_tz.make_aware(
            naive_dt, django_tz.get_current_timezone()
        )

        cleaned_data["_entry"] = entry
        return cleaned_data


class WorkThroughLunchRequestForm(forms.ModelForm):
    """Request to work through a scheduled lunch for a single date."""

    class Meta:
        model = WorkThroughLunchRequest
        fields = ["work_date", "comments"]
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "comments": forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "Optional"}),
        }

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop("request_user", None)
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "work_date",
            "comments",
        )

    def clean(self):
        from .schedule_utils import get_scheduled_lunch_in_for_day, get_scheduled_lunch_out_for_day

        cleaned_data = super().clean()
        work_date = cleaned_data.get("work_date")
        user = self.request_user
        if not work_date or not user:
            return cleaned_data

        if not get_scheduled_lunch_out_for_day(user, work_date) or not get_scheduled_lunch_in_for_day(
            user, work_date
        ):
            raise forms.ValidationError(
                "You do not have a scheduled lunch on that date, so this request does not apply."
            )

        if WorkThroughLunchRequest.objects.filter(
            user=user,
            work_date=work_date,
            status__in=[TimeOffRequestStatus.PENDING, TimeOffRequestStatus.APPROVED],
        ).exists():
            raise forms.ValidationError(
                "You already have a pending or approved work-through-lunch request for that date."
            )

        return cleaned_data