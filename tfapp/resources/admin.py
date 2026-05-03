from django.contrib import admin

from attendance.models import RoleChoices

from .models import EmployeeHandbook, EventAttachment, Policy, ResourceEvent


def _resources_admin_access(request):
    return (
        request.user.is_authenticated
        and request.user.is_staff
        and (request.user.is_superuser or getattr(request.user, "role", None) == RoleChoices.EXECUTIVE)
    )


class ExecutiveResourcesAdminMixin:
    """Handbook, policies, and events are manageable by superusers and Executive role."""

    def has_module_permission(self, request):
        return _resources_admin_access(request)

    def has_view_permission(self, request, obj=None):
        return _resources_admin_access(request)

    def has_add_permission(self, request):
        return _resources_admin_access(request)

    def has_change_permission(self, request, obj=None):
        return _resources_admin_access(request)

    def has_delete_permission(self, request, obj=None):
        return _resources_admin_access(request)


@admin.register(EmployeeHandbook)
class EmployeeHandbookAdmin(ExecutiveResourcesAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "updated_at")
    readonly_fields = ("updated_at",)

    def has_delete_permission(self, request, obj=None):
        return False


class EventAttachmentInline(admin.TabularInline):
    model = EventAttachment
    extra = 1


@admin.register(ResourceEvent)
class ResourceEventAdmin(ExecutiveResourcesAdminMixin, admin.ModelAdmin):
    list_display = ("title", "event_date", "event_time", "all_day", "created_by", "created_at")
    list_filter = ("event_date", "all_day")
    search_fields = ("title", "details")
    date_hierarchy = "event_date"
    inlines = [EventAttachmentInline]
    readonly_fields = ("created_at", "created_by")

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ("created_at", "created_by")
        return ("created_at",)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Policy)
class PolicyAdmin(ExecutiveResourcesAdminMixin, admin.ModelAdmin):
    list_display = ("title", "slug", "updated_at")
    search_fields = ("title", "body")
    fields = ("title", "body")
    readonly_fields = ("created_at", "updated_at", "slug")

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ("created_at", "updated_at", "slug")
        return ("created_at", "updated_at")
