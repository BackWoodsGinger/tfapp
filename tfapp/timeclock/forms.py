from django import forms
from .models import TimeEntry

class TimeEntryForm(forms.ModelForm):
    class Meta:
        model = TimeEntry
        fields = ['clock_in', 'lunch_out', 'lunch_in', 'clock_out']
        widgets = {
            'clock_in': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'lunch_out': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'lunch_in': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'clock_out': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }