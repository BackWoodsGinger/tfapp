from django import forms
from datetime import timedelta
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Submit, Div
from .models import Occurrence, CustomUser, TimeOffRequest


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