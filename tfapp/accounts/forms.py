import re

from django import forms

from .models import ProfileCredentialDocument, UserProfile


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("phone", "bio", "photo")
        widgets = {
            "phone": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. 248-555-0100",
                    "autocomplete": "tel",
                }
            ),
            "bio": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 5,
                    "placeholder": "A short professional bio…",
                }
            ),
            "photo": forms.ClearableFileInput(
                attrs={
                    "class": "form-control form-control-sm",
                }
            ),
        }

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            return ""
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 10:
            raise forms.ValidationError("Enter at least 10 digits for a phone number.")
        return phone


class ProfileCredentialDocumentForm(forms.ModelForm):
    class Meta:
        model = ProfileCredentialDocument
        fields = ("title", "file")
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "placeholder": "Optional (e.g. AS Degree, AWS cert)",
                }
            ),
            "file": forms.ClearableFileInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "accept": ".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/*",
                }
            ),
        }

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if not f:
            raise forms.ValidationError("Choose a file to upload.")
        max_bytes = 15 * 1024 * 1024
        if f.size > max_bytes:
            raise forms.ValidationError("File must be 15 MB or smaller.")
        return f
