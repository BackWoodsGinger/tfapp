from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.shortcuts import redirect, get_object_or_404
from django.urls import path
from django.contrib import messages
from .models import CustomUser, Occurrence, WorkSchedule, OccurrenceSubtype, PayrollPeriod, PTOBalanceHistory


class WorkScheduleInline(admin.TabularInline):
    model = WorkSchedule
    extra = 0
    ordering = ['day']

@admin.action(description="Recalculate PTO Based on Service Anniversary")
def recalculate_pto(modeladmin, request, queryset):
    updated_count = 0
    for user in queryset:
        if user.service_date and not user.is_part_time:
            user.reset_pto_at_service_anniversary()
            updated_count += 1
    modeladmin.message_user(request, f"✅ PTO reset for {updated_count} user(s).")


@admin.action(description="Refresh PTO balance to tenure baseline (and clear unpaid)")
def refresh_pto_baseline(modeladmin, request, queryset):
    updated_count = 0
    for user in queryset:
        if user.service_date and not user.is_exempt:
            user.set_pto_to_tenure_baseline(clear_personal=True)
            updated_count += 1
    modeladmin.message_user(
        request,
        f"✅ PTO balance set to tenure baseline for {updated_count} user(s). Unpaid (personal) time cleared.",
    )


@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    inlines = [WorkScheduleInline]
    list_display = (
        'username', 'email', 'role', 'department', 'hire_date', 'service_date', 'is_exempt', 'pto_balance'
    )
    actions = [recalculate_pto, refresh_pto_baseline]
    change_form_template = "admin/attendance/customuser/change_form.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/refresh-pto/",
                self.admin_site.admin_view(self.refresh_pto_view),
                name="attendance_customuser_refresh_pto",
            ),
        ]
        return custom + urls

    def refresh_pto_view(self, request, object_id):
        user = get_object_or_404(CustomUser, pk=object_id)
        if request.method != "POST":
            return redirect("admin:attendance_customuser_change", object_id)
        if not user.service_date or user.is_exempt:
            messages.warning(
                request,
                "PTO refresh only applies to non-exempt users with a service date.",
            )
        else:
            user.set_pto_to_tenure_baseline(clear_personal=True)
            messages.success(
                request,
                "PTO balance set to tenure baseline. Unpaid (personal) time cleared.",
            )
        return redirect("admin:attendance_customuser_change", object_id)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        user = get_object_or_404(CustomUser, pk=object_id)
        extra_context["show_refresh_pto"] = bool(
            user.service_date and not user.is_exempt
        )
        return super().change_view(request, object_id, form_url, extra_context)

    fieldsets = (
        (None, {
            'fields': (
                'username', 'password', 'email', 'first_name', 'last_name', 'role',
                'department', 'supervisor', 'group_lead', 'team_lead',
                'hire_date', 'service_date', 'is_part_time', 'is_exempt', 'timeclock_login',
                'timeclock_pin'
            )
        }),
        ('Balances & Hours', {
            'fields': (
                'pto_balance', 'personal_time_balance', 'final_pto_balance', 'hours_worked'
            )
        }),
        ('Permissions', {
            'fields': (
                'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'
            )
        }),
        ('Important Dates', {
            'fields': ('last_login', 'date_joined')
        }),
    )


@admin.register(Occurrence)
class OccurrenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'occurrence_type', 'subtype', 'duration_hours', 'pto_applied')
    list_filter = ('occurrence_type', 'subtype', 'date')
    search_fields = ('user__username', 'subtype', 'occurrence_type')
    readonly_fields = ('pto_applied',)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Only apply PTO if this subtype deducts from balance and we haven't already applied.
        if not obj.pto_applied and obj.subtype in [
            OccurrenceSubtype.TIME_OFF,
            OccurrenceSubtype.TARDY_OUT_OF_GRACE,
            OccurrenceSubtype.EXCHANGE,
            OccurrenceSubtype.FMLA,
            OccurrenceSubtype.LEAVE_OF_ABSENCE,
            OccurrenceSubtype.TRANSPORTATION,
            OccurrenceSubtype.WEATHER_PAID,
            OccurrenceSubtype.JURY_DUTY_PAID,
        ]:
            obj.apply_pto()


@admin.register(PTOBalanceHistory)
class PTOBalanceHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "balance_type", "change", "balance_after", "reason", "timestamp")
    list_filter = ("balance_type", "timestamp")
    search_fields = ("user__username", "reason")
    readonly_fields = ("user", "change", "reason", "balance_after", "balance_type", "timestamp")
    ordering = ("-timestamp",)


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(admin.ModelAdmin):
    list_display = ("week_ending", "is_finalized", "finalized_at", "finalized_by")
    list_filter = ("is_finalized",)
    ordering = ("-week_ending",)