from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.shortcuts import redirect, get_object_or_404
from django.urls import path
from django.contrib import messages
from datetime import date, timedelta
from decimal import Decimal
from .models import (
    CustomUser,
    DailyAttendanceSummary,
    LeaveOfAbsence,
    Occurrence,
    WorkSchedule,
    OccurrenceSubtype,
    PayrollPeriod,
    PTOBalanceHistory,
    WorkThroughLunchRequest,
    AdjustPunchRequest,
)


class WorkScheduleInline(admin.TabularInline):
    """
    Per-weekday rows: start/end required; lunch_out and lunch_in are optional.
    Leave lunch fields blank for half-day / no-lunch shifts (e.g. 6:30–11:00 Friday).
    """

    model = WorkSchedule
    extra = 0
    ordering = ["day"]
    fields = ("day", "start_time", "lunch_out", "lunch_in", "end_time", "crosses_midnight")
    verbose_name = "work day"
    verbose_name_plural = "Work schedules (per weekday)"

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
        'payroll_name', 'email', 'role', 'department', 'hire_date', 'service_date', 'is_exempt', 'pto_balance'
    )
    ordering = ("payroll_lastname", "payroll_firstname", "username")
    search_fields = ("payroll_lastname", "payroll_firstname", "username", "first_name", "last_name", "email")
    actions = [recalculate_pto, refresh_pto_baseline]
    change_form_template = "admin/attendance/customuser/change_form.html"

    @admin.display(description="Employee", ordering="payroll_lastname")
    def payroll_name(self, obj):
        return obj.payroll_display_name()

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

    def get_readonly_fields(self, request, obj=None):
        return list(super().get_readonly_fields(request, obj)) + ["public_slug"]

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        user = get_object_or_404(CustomUser, pk=object_id)
        extra_context["show_refresh_pto"] = bool(
            user.service_date and not user.is_exempt
        )
        return super().change_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        # Record manual balance edits made directly in admin user form.
        old_pto = None
        old_personal = None
        if change and obj.pk:
            original = CustomUser.objects.filter(pk=obj.pk).first()
            if original:
                old_pto = Decimal(str(original.pto_balance)).quantize(Decimal("0.01"))
                old_personal = Decimal(str(original.personal_time_balance)).quantize(Decimal("0.01"))

        super().save_model(request, obj, form, change)

        if old_pto is not None:
            new_pto = Decimal(str(obj.pto_balance)).quantize(Decimal("0.01"))
            delta_pto = new_pto - old_pto
            if delta_pto != 0:
                PTOBalanceHistory.record(
                    user=obj,
                    change=float(delta_pto),
                    reason=f"Manual admin edit by {request.user.username}",
                    balance_after=float(new_pto),
                    balance_type=PTOBalanceHistory.BALANCE_TYPE_PTO,
                )

            new_personal = Decimal(str(obj.personal_time_balance)).quantize(Decimal("0.01"))
            delta_personal = new_personal - old_personal
            if delta_personal != 0:
                PTOBalanceHistory.record(
                    user=obj,
                    change=float(delta_personal),
                    reason=f"Manual admin edit by {request.user.username}",
                    balance_after=float(new_personal),
                    balance_type=PTOBalanceHistory.BALANCE_TYPE_PERSONAL,
                )

    fieldsets = (
        (None, {
            'fields': (
                'username', 'public_slug', 'password', 'email', 'first_name', 'last_name', 'role',
                'department', 'supervisor', 'group_lead', 'team_lead',
                'payroll_lastname', 'payroll_firstname',
                'hire_date', 'service_date', 'is_part_time', 'is_exempt', 'timeclock_login',
                'timeclock_pin'
            )
        }),
        ('Balances & Hours', {
            'fields': (
                'pto_balance', 'personal_time_balance', 'final_pto_balance', 'hours_worked'
            )
        }),
        (
            "Scheduling",
            {
                "fields": ("weekly_schedule",),
                "description": (
                    "Optional JSON schedule (keys: monday … sunday). Each day needs "
                    "<code>start</code> and <code>end</code> (HH:MM). "
                    "<strong>Half-day / no lunch:</strong> omit <code>lunch_out</code> and "
                    "<code>lunch_in</code> for that day (e.g. Friday 06:30–11:00). "
                    "If both this JSON and the work-schedule table below are set, "
                    "<strong>JSON wins</strong> for that weekday."
                ),
            },
        ),
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
    readonly_fields = ("pto_applied", "probation_grace_hours_applied", "leave")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Only apply PTO if this subtype deducts from balance and we haven't already applied.
        if not obj.pto_applied and obj.subtype in [
            OccurrenceSubtype.TIME_OFF,
            OccurrenceSubtype.TARDY_OUT_OF_GRACE,
            OccurrenceSubtype.EXCHANGE,
            OccurrenceSubtype.FMLA,
            OccurrenceSubtype.LEAVE_OF_ABSENCE,
            OccurrenceSubtype.WEATHER_PAID,
            OccurrenceSubtype.BEREAVEMENT_PAID,
            OccurrenceSubtype.JURY_DUTY_PAID,
            OccurrenceSubtype.GRACE_TIME,
        ]:
            obj.apply_pto()


def _leave_sync_message(result) -> str:
    parts = [f"{result.created} absence(s) created", f"{result.removed} removed and refunded"]
    if result.skipped_finalized:
        parts.append(
            f"{result.skipped_finalized} day(s) left unchanged because the payroll week is finalized"
        )
    return "; ".join(parts) + "."


class LeaveOccurrenceInline(admin.TabularInline):
    model = Occurrence
    fk_name = "leave"
    extra = 0
    can_delete = False
    ordering = ("-date",)
    fields = ("date", "subtype", "duration_hours", "pto_hours_applied", "pto_applied")
    readonly_fields = fields
    verbose_name = "generated absence"
    verbose_name_plural = "Generated absences"

    def has_add_permission(self, request, obj=None):
        return False


@admin.action(description="Switch off: employee returns to work today (last leave day is yesterday)")
def end_leave_returning_today(modeladmin, request, queryset):
    from .services.leave_service import LeaveSyncResult, end_leave

    yesterday = date.today() - timedelta(days=1)
    total = LeaveSyncResult()
    ended = 0
    for leave in queryset:
        if leave.end_date is not None and leave.end_date <= yesterday:
            continue
        total = total + end_leave(leave, max(yesterday, leave.start_date), ended_by=request.user)
        ended += 1
    modeladmin.message_user(request, f"Switched off {ended} leave(s): {_leave_sync_message(total)}")


@admin.action(description="Generate any missing daily absences now")
def sync_selected_leaves(modeladmin, request, queryset):
    from .services.leave_service import LeaveSyncResult, sync_leave

    total = LeaveSyncResult()
    for leave in queryset:
        total = total + sync_leave(leave)
    modeladmin.message_user(request, _leave_sync_message(total))


@admin.action(description="Delete leave and refund its generated absences")
def delete_leaves_with_absences(modeladmin, request, queryset):
    from .services.leave_service import delete_leave_with_absences

    for leave in queryset:
        ok, msg = delete_leave_with_absences(leave)
        modeladmin.message_user(request, msg, level=messages.SUCCESS if ok else messages.ERROR)


@admin.register(LeaveOfAbsence)
class LeaveOfAbsenceAdmin(admin.ModelAdmin):
    """
    Start a leave by picking the employee, leave type, and start date. One absence per scheduled
    work day is created automatically until the leave is switched off (end date set).
    """

    list_display = ("user", "subtype", "start_date", "end_date", "status", "absence_count")
    list_filter = ("subtype", ("end_date", admin.EmptyFieldListFilter))
    search_fields = ("user__payroll_lastname", "user__payroll_firstname", "user__username")
    autocomplete_fields = ("user",)
    fields = ("user", "subtype", "start_date", "end_date", "notes", "created_by", "ended_by")
    readonly_fields = ("created_by", "ended_by")
    inlines = [LeaveOccurrenceInline]
    actions = [end_leave_returning_today, sync_selected_leaves, delete_leaves_with_absences]

    @admin.display(description="Status")
    def status(self, obj):
        return "Active" if obj.is_active else "Ended"

    @admin.display(description="Absences")
    def absence_count(self, obj):
        return obj.occurrences.count()

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return self.readonly_fields + ("user", "subtype")
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        # Generated absences are protected; use the "Delete leave and refund" action instead.
        return False

    def save_model(self, request, obj, form, change):
        from .services.leave_service import sync_leave

        if not change:
            obj.created_by = request.user
        else:
            if "end_date" in form.changed_data and obj.end_date is not None:
                obj.ended_by = request.user
            if "start_date" in form.changed_data:
                obj.generated_through = None
        super().save_model(request, obj, form, change)
        result = sync_leave(obj)
        messages.info(request, _leave_sync_message(result))


@admin.register(DailyAttendanceSummary)
class DailyAttendanceSummaryAdmin(admin.ModelAdmin):
    list_display = ("user", "work_date", "status", "scheduled_hours", "worked_hours", "exchange_eligible")
    list_filter = ("status", "work_date")
    search_fields = ("user__username", "user__payroll_lastname")
    raw_id_fields = ("user", "payroll_period")


@admin.register(PTOBalanceHistory)
class PTOBalanceHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "balance_type", "change", "balance_after", "reason", "timestamp")
    list_filter = ("balance_type", "timestamp")
    search_fields = ("user__username", "reason")
    readonly_fields = ("user", "change", "reason", "balance_after", "balance_type", "timestamp")
    ordering = ("-timestamp",)


@admin.register(WorkThroughLunchRequest)
class WorkThroughLunchRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "work_date", "status", "approver", "created_at")
    list_filter = ("status", "work_date")
    search_fields = ("user__username", "user__last_name", "slug")
    readonly_fields = ("slug", "created_at", "updated_at")


@admin.register(AdjustPunchRequest)
class AdjustPunchRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "time_entry", "punch_field", "status", "approver", "created_at")
    list_filter = ("status", "punch_field")
    search_fields = ("user__username", "slug")
    readonly_fields = ("slug", "previous_at", "created_at", "updated_at")


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(admin.ModelAdmin):
    list_display = ("week_ending", "is_finalized", "finalized_at", "finalized_by")
    list_filter = ("is_finalized",)
    ordering = ("-week_ending",)