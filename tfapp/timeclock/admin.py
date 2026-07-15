from django import forms
from django.contrib import admin, messages
from django.db import transaction

from attendance.models import CustomUser, revert_tardy_occurrences_for_adjust_punch
from attendance.payroll_utils import week_ending_for_date, is_payroll_week_finalized

from .models import MAX_TIMECLOCK_KIOSKS, TimeclockKioskIP, TimeEntry
from .tardy_sync import sync_tardy_occurrences_for_time_entry


class TimeclockKioskIPAdminForm(forms.ModelForm):
    class Meta:
        model = TimeclockKioskIP
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        # Enforce max count on create (model.clean also runs via full_clean).
        if self.instance.pk is None:
            if TimeclockKioskIP.objects.count() >= MAX_TIMECLOCK_KIOSKS:
                raise forms.ValidationError(
                    f"At most {MAX_TIMECLOCK_KIOSKS} kiosk IPs can be configured."
                )
        return cleaned


@admin.register(TimeclockKioskIP)
class TimeclockKioskIPAdmin(admin.ModelAdmin):
    form = TimeclockKioskIPAdminForm
    list_display = ("ip_address", "label", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("ip_address", "label")
    ordering = ("ip_address",)

    def has_add_permission(self, request):
        if TimeclockKioskIP.objects.count() >= MAX_TIMECLOCK_KIOSKS:
            return False
        return super().has_add_permission(request)


class TimeEntryAdminForm(forms.ModelForm):
    class Meta:
        model = TimeEntry
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        d = cleaned.get("date")
        if d is None and self.instance.pk:
            d = self.instance.date
        if d and is_payroll_week_finalized(week_ending_for_date(d)):
            raise forms.ValidationError(
                "This payroll week is finalized. Unfinalize payroll before adding or editing "
                "time entries for these dates."
            )
        return cleaned


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    form = TimeEntryAdminForm
    readonly_fields = ("slug", "clock_in_authorized_by")
    list_display = (
        "slug",
        "user",
        "date",
        "clock_in",
        "clock_in_authorized_by",
        "lunch_out",
        "lunch_in",
        "clock_out",
    )
    list_filter = ("date", "user")
    search_fields = ("user__username", "user__first_name", "user__last_name")
    ordering = ("-date",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        sync_tardy_occurrences_for_time_entry(obj)

    def delete_model(self, request, obj):
        if is_payroll_week_finalized(week_ending_for_date(obj.date)):
            self.message_user(
                request,
                "Cannot delete time entries in a finalized payroll week. Unfinalize payroll first.",
                level=messages.ERROR,
            )
            return
        with transaction.atomic():
            u = CustomUser.objects.select_for_update().get(pk=obj.user_id)
            revert_tardy_occurrences_for_adjust_punch(u, obj.date)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        blocked_pks = []
        for obj in queryset:
            if is_payroll_week_finalized(week_ending_for_date(obj.date)):
                blocked_pks.append(obj.pk)
        allowed = queryset.exclude(pk__in=blocked_pks) if blocked_pks else queryset
        if blocked_pks:
            self.message_user(
                request,
                f"Skipped {len(blocked_pks)} time entr(y/ies) in finalized payroll weeks.",
                level=messages.WARNING,
            )
        for obj in allowed:
            with transaction.atomic():
                u = CustomUser.objects.select_for_update().get(pk=obj.user_id)
                revert_tardy_occurrences_for_adjust_punch(u, obj.date)
        allowed.delete()
