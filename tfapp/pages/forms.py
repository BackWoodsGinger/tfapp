from django import forms

from .models import HomeTickerSubmission


class HomeTickerSubmissionForm(forms.ModelForm):
    class Meta:
        model = HomeTickerSubmission
        fields = ("message",)
        widgets = {
            "message": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "maxlength": 500,
                    "placeholder": "Your announcement (one line; max 500 characters)",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["message"].label = "Message"
