from django import forms

from .models import ResourceEvent


class ResourceEventForm(forms.ModelForm):
    class Meta:
        model = ResourceEvent
        fields = ("title", "event_date", "event_time", "all_day", "details")
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "event_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "event_time": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "all_day": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "details": forms.Textarea(attrs={"class": "form-control", "rows": 6}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("all_day"):
            cleaned["event_time"] = None
        return cleaned
