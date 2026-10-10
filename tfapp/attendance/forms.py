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
    TimeOffRequestDay,
    TimeOffRequestKind,
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
    REPORT_MODE_INDIVIDUAL = "individual"
    REPORT_MODE_GROUP = "group"
    REPORT_MODE_CHOICES = [
        (REPORT_MODE_INDIVIDUAL, "Individual employee"),
        (REPORT_MODE_GROUP, "Multiple employees (group analytics)"),
    ]
    REPORT_GROUP_BY_CHOICES = [
        ("department", "Department"),
        ("supervisor", "Supervisor / reporting line"),
        ("group_lead", "Group lead"),
    ]
    PLANNED_FILTER_ALL = ""
    PLANNED_FILTER_PLANNED = "planned"
    PLANNED_FILTER_UNPLANNED = "unplanned"
    PLANNED_FILTER_CHOICES = [
        (PLANNED_FILTER_ALL, "All (planned and unplanned)"),
        (PLANNED_FILTER_PLANNED, "Planned only"),
        (PLANNED_FILTER_UNPLANNED, "Unplanned only"),
    ]

    report_mode = forms.ChoiceField(
        choices=REPORT_MODE_CHOICES,
        initial=REPORT_MODE_INDIVIDUAL,
        required=False,
        widget=forms.RadioSelect,
    )
    report_group_by = forms.ChoiceField(
        choices=REPORT_GROUP_BY_CHOICES,
        initial="department",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    subtype_filter = forms.MultipleChoiceField(
        label="Absence subtypes",
        required=False,
        choices=[],  # set in __init__
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select",
                "style": "max-width: 28rem;",
                "size": "10",
            }
        ),
    )
    planned_filter = forms.ChoiceField(
        label="Planned / unplanned",
        required=False,
        choices=PLANNED_FILTER_CHOICES,
        initial=PLANNED_FILTER_ALL,
        widget=forms.Select(attrs={"class": "form-select", "style": "max-width: 28rem;"}),
    )
    user = forms.ModelChoiceField(
        queryset=CustomUser.objects.all(),
        to_field_name="public_slug",
        label="User",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))

    def __init__(self, *args, **kwargs):
        if args and args[0] is not None:
            data = args[0].copy()
            if not data.get("report_mode"):
                data["report_mode"] = self.REPORT_MODE_INDIVIDUAL
            # Legacy ?subtype_filter=all (bookmarks) — empty multiselect = all types
            if hasattr(data, "getlist") and data.getlist("subtype_filter") == ["all"]:
                data.setlist("subtype_filter", [])
            if not data.get("report_group_by"):
                data["report_group_by"] = "department"
            # Legacy "shift" grouping removed — map to department
            if data.get("report_group_by") == "shift":
                data["report_group_by"] = "department"
            args = (data,) + tuple(args[1:])
        super().__init__(*args, **kwargs)
        self.fields["subtype_filter"].choices = list(OccurrenceSubtype.choices)
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

    def clean_subtype_filter(self):
        vals = self.cleaned_data.get("subtype_filter")
        if vals is None:
            return []
        if not isinstance(vals, (list, tuple)):
            vals = [vals] if vals else []
        valid = {c[0] for c in OccurrenceSubtype.choices}
        out = []
        for v in vals:
            if v in (None, "", "all"):
                continue
            if v in valid and v not in out:
                out.append(v)
        return out

    def clean_planned_filter(self):
        val = self.cleaned_data.get("planned_filter") or ""
        valid = {c[0] for c in self.PLANNED_FILTER_CHOICES}
        return val if val in valid else self.PLANNED_FILTER_ALL

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("report_mode") or self.REPORT_MODE_INDIVIDUAL
        if mode not in (self.REPORT_MODE_INDIVIDUAL, self.REPORT_MODE_GROUP):
            mode = self.REPORT_MODE_INDIVIDUAL
        cleaned["report_mode"] = mode
        if mode == self.REPORT_MODE_INDIVIDUAL:
            if not cleaned.get("user"):
                raise forms.ValidationError(
                    {"user": "Select an employee for an individual report."}
                )
        return cleaned


TIME_OFF_MULTIPLE_DAYS_MAX = 31
_TIME_INPUT_FORMATS = ["%H:%M", "%H:%M:%S"]


def _time_input(**attrs):
    return forms.TimeInput(
        attrs={"type": "time", "class": "form-control", "step": "900", **attrs},
        format="%H:%M",
    )


def _payroll_week_start(d: date) -> date:
    """Sunday that starts the payroll week (Sunday–Saturday) containing ``d``."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def weekly_schedule_for_time_off_form(user) -> dict:
    """
    The user's default shift per weekday (Monday=0), for pre-filling time selectors:
    ``{"0": {"start": "07:00", "end": "15:30", "lunch_out": "11:00", "lunch_in": "11:30",
    "hours": 8.0}, ...}``. Lunch keys are null on no-lunch days; unscheduled weekdays are omitted.
    """
    from .schedule_utils import (
        get_scheduled_end_time_for_day,
        get_scheduled_lunch_in_for_day,
        get_scheduled_lunch_out_for_day,
        get_scheduled_start_for_day,
        scheduled_duration_hours_for_day,
    )

    def _fmt(t):
        return t.strftime("%H:%M") if t else None

    reference_monday = date(2020, 1, 6)
    out = {}
    for wd in range(7):
        d = reference_monday + timedelta(days=wd)
        start = get_scheduled_start_for_day(user, d)
        end = get_scheduled_end_time_for_day(user, d)
        hours = scheduled_duration_hours_for_day(user, d)
        if start and end and hours > 0:
            out[str(wd)] = {
                "start": _fmt(start),
                "end": _fmt(end),
                "lunch_out": _fmt(get_scheduled_lunch_out_for_day(user, d)),
                "lunch_in": _fmt(get_scheduled_lunch_in_for_day(user, d)),
                "hours": round(hours, 2),
            }
    return out


class TimeOffRequestForm(forms.ModelForm):
    """
    Time off request in one of three shapes, chosen first via ``request_kind``:

    - Partial day: one date plus from/to times inside the scheduled shift.
    - Full day: one date; the whole scheduled shift.
    - Multiple days: a date range. Each scheduled day in the range can be left out or narrowed
      to partial times (posted as ``day_<YYYY-MM-DD>_include`` / ``_start`` / ``_end``).

    Absence type and comments apply to the whole request. Requested hours per payroll week
    (including other pending/approved requests) may not exceed 40.
    """

    request_kind = forms.ChoiceField(
        label="What kind of time off is this?",
        choices=TimeOffRequestKind.choices,
        widget=forms.RadioSelect,
    )
    partial_date = forms.DateField(
        label="Date", required=False, widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )
    partial_start = forms.TimeField(
        label="From", required=False, input_formats=_TIME_INPUT_FORMATS, widget=_time_input()
    )
    partial_end = forms.TimeField(
        label="To", required=False, input_formats=_TIME_INPUT_FORMATS, widget=_time_input()
    )
    full_date = forms.DateField(
        label="Date", required=False, widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )
    multi_start = forms.DateField(
        label="First day", required=False, widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )
    multi_end = forms.DateField(
        label="Last day", required=False, widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )

    class Meta:
        model = TimeOffRequest
        fields = ["subtype", "comments"]
        labels = {"subtype": "Absence type"}
        widgets = {
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
        self.day_rows: list[dict] = []

    def submitted_day_inputs(self) -> dict:
        """Per-day values posted for a multiple-day request, keyed by ISO date (for re-rendering)."""
        out = {}
        if not self.is_bound:
            return out
        for key in self.data:
            if key.startswith("day_") and key.count("_") == 2:
                _, iso, part = key.split("_")
                out.setdefault(iso, {})[part] = self.data.get(key)
        return out

    def _parse_time(self, raw):
        if not raw:
            return None
        try:
            return forms.TimeField(input_formats=_TIME_INPUT_FORMATS).to_python(raw)
        except forms.ValidationError:
            return None

    def _timed_day(self, d: date, from_t, to_t) -> dict | None:
        """Validate from/to times on ``d`` against the shift and return a day row, or add an error."""
        from .schedule_utils import (
            requested_window_for_day,
            scheduled_hours_between,
            scheduled_shift_window_for_day,
        )

        label = d.strftime("%a %b %d")
        window = scheduled_shift_window_for_day(self.request_user, d)
        if window is None:
            self.add_error(None, f"{label}: you are not scheduled to work that day.")
            return None
        if from_t is None or to_t is None:
            self.add_error(None, f"{label}: enter both a from and a to time.")
            return None
        req_start, req_end = requested_window_for_day(window, d, from_t, to_t)
        if req_start < window["start"] or req_end > window["end"]:
            shift = f"{window['start'].strftime('%H:%M')}–{window['end'].strftime('%H:%M')}"
            self.add_error(None, f"{label}: times must fall within your scheduled shift ({shift}).")
            return None
        hours = scheduled_hours_between(self.request_user, d, from_t, to_t)
        if hours <= 0:
            self.add_error(None, f"{label}: the selected times do not include any scheduled work hours.")
            return None
        return {"date": d, "start_time": from_t, "end_time": to_t, "hours": hours}

    def _full_day(self, d: date) -> dict | None:
        from .schedule_utils import scheduled_duration_hours_for_day

        hours = scheduled_duration_hours_for_day(self.request_user, d)
        if hours <= 0:
            return None
        return {"date": d, "start_time": None, "end_time": None, "hours": round(hours, 2)}

    def _build_partial(self, cd):
        d = cd.get("partial_date")
        if not d:
            self.add_error("partial_date", "Choose the date.")
            return
        if not cd.get("partial_start"):
            self.add_error("partial_start", "Choose a start time.")
        if not cd.get("partial_end"):
            self.add_error("partial_end", "Choose an end time.")
        if not (cd.get("partial_start") and cd.get("partial_end")):
            return
        row = self._timed_day(d, cd["partial_start"], cd["partial_end"])
        if row:
            self.day_rows = [row]

    def _build_full(self, cd):
        d = cd.get("full_date")
        if not d:
            self.add_error("full_date", "Choose the date.")
            return
        row = self._full_day(d)
        if row is None:
            self.add_error("full_date", "You are not scheduled to work that day.")
            return
        self.day_rows = [row]

    def _build_multiple(self, cd):
        first, last = cd.get("multi_start"), cd.get("multi_end")
        if not first:
            self.add_error("multi_start", "Choose the first day.")
        if not last:
            self.add_error("multi_end", "Choose the last day.")
        if not (first and last):
            return
        if first > last:
            self.add_error("multi_end", "The last day cannot be before the first day.")
            return
        if (last - first).days + 1 > TIME_OFF_MULTIPLE_DAYS_MAX:
            self.add_error(
                "multi_end",
                f"Requests are limited to {TIME_OFF_MULTIPLE_DAYS_MAX} days. "
                "For longer leave, contact your supervisor.",
            )
            return

        per_day = self.submitted_day_inputs()
        days_rendered = bool(self.data.get("days_rendered"))
        rows = []
        d = first
        while d <= last:
            full = self._full_day(d)
            if full is not None:
                posted = per_day.get(d.isoformat(), {})
                include = (not days_rendered) or posted.get("include") == "on"
                if include:
                    from_t = self._parse_time(posted.get("start"))
                    to_t = self._parse_time(posted.get("end"))
                    if from_t is None and to_t is None:
                        rows.append(full)
                    else:
                        row = self._timed_day(d, from_t, to_t)
                        if row:
                            rows.append(row)
            d += timedelta(days=1)
        if not rows and not self.errors:
            self.add_error(None, "None of the selected dates are scheduled work days.")
        self.day_rows = rows

    def _check_weekly_cap(self):
        from .models import TimeOffRequestStatus

        new_by_week: dict[date, float] = {}
        for row in self.day_rows:
            ws = _payroll_week_start(row["date"])
            new_by_week[ws] = new_by_week.get(ws, 0.0) + row["hours"]
        for week_start, new_hours in sorted(new_by_week.items()):
            week_end = week_start + timedelta(days=6)
            existing = TimeOffRequest.objects.filter(
                user=self.request_user,
                start_date__lte=week_end,
                end_date__gte=week_start,
                status__in=[TimeOffRequestStatus.PENDING, TimeOffRequestStatus.APPROVED],
            )
            existing_hours = sum(r.requested_hours_in_range(week_start, week_end) for r in existing)
            if existing_hours + new_hours > 40:
                self.add_error(
                    None,
                    f"Total time off requested for the payroll week of {week_start:%b %d} "
                    f"would be {existing_hours + new_hours:.2f} hours, over the 40-hour weekly limit.",
                )

    def clean(self):
        cleaned_data = super().clean()
        subtype = cleaned_data.get("subtype")
        if subtype and str(subtype) in {s.value for s in TIME_OFF_REQUEST_SUBTYPE_EXCLUDE}:
            self.add_error("subtype", "That absence type is not available for employee time off requests.")

        kind = cleaned_data.get("request_kind")
        if not kind or not self.request_user:
            return cleaned_data
        if kind == TimeOffRequestKind.PARTIAL_DAY:
            self._build_partial(cleaned_data)
        elif kind == TimeOffRequestKind.FULL_DAY:
            self._build_full(cleaned_data)
        else:
            self._build_multiple(cleaned_data)
        if self.day_rows and not self.errors:
            self._check_weekly_cap()

        if self.day_rows:
            inst = self.instance
            inst.request_kind = kind
            inst.start_date = self.day_rows[0]["date"]
            inst.end_date = self.day_rows[-1]["date"]
            inst.partial_day = kind == TimeOffRequestKind.PARTIAL_DAY
            inst.partial_hours = self.day_rows[0]["hours"] if inst.partial_day else None
        return cleaned_data

    def save_days(self, time_off_request):
        """Create the per-day rows; call after the request itself has been saved."""
        TimeOffRequestDay.objects.bulk_create(
            [TimeOffRequestDay(request=time_off_request, **row) for row in self.day_rows]
        )


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