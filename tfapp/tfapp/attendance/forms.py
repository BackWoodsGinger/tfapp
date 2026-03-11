from django import forms
from .models import Occurrence, CustomUser

class OccurrenceForm(forms.ModelForm):
    class Meta:
        model = Occurrence
        fields = ['user', 'occurrence_type', 'date', 'duration_hours']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'duration_hours': forms.NumberInput(attrs={'step': 0.25}),
        }

class ReportFilterForm(forms.Form):
    user = forms.ModelChoiceField(queryset=CustomUser.objects.all(), label="User")
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))