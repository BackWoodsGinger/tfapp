from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import CustomUser, Occurrence


@admin.action(description="Recalculate PTO Based on Service Anniversary")
def recalculate_pto(modeladmin, request, queryset):
    updated_count = 0
    for user in queryset:
        if user.service_date and not user.is_part_time:
            user.reset_pto_at_service_anniversary()
            updated_count += 1
    modeladmin.message_user(request, f"✅ PTO reset for {updated_count} user(s).")


@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    list_display = (
        'username', 'email', 'role', 'department', 'hire_date', 'service_date', 'is_exempt', 'pto_balance'
    )
    actions = [recalculate_pto]  # ✅ Add the custom admin action here

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
        obj.apply_pto()