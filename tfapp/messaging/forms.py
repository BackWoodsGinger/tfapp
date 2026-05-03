from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()


class MessageComposeForm(forms.Form):
    body = forms.CharField(
        label="Message",
        min_length=1,
        max_length=10000,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Type a message…",
            }
        ),
    )


class GroupConversationForm(forms.Form):
    name = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Group name"}),
    )
    members = forms.ModelMultipleChoiceField(
        label="Members",
        queryset=User.objects.none(),
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 12}),
        help_text="Select at least two other people (Ctrl/Cmd-click to select multiple).",
    )

    def __init__(self, *args, creator=None, **kwargs):
        self._creator = creator
        super().__init__(*args, **kwargs)
        if creator is not None:
            self.fields["members"].queryset = (
                User.objects.filter(is_active=True)
                .exclude(pk=creator.pk)
                .order_by("payroll_lastname", "payroll_firstname", "last_name", "first_name", "username")
            )

    def clean_members(self):
        users = self.cleaned_data.get("members")
        if not users:
            raise forms.ValidationError("Select at least two other people for a group.")
        if users.count() < 2:
            raise forms.ValidationError("Select at least two other people for a group conversation.")
        return users
