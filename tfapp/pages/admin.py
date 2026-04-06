from django.contrib import admin

from .models import HomeTickerItem, HomeTickerSubmission


@admin.register(HomeTickerSubmission)
class HomeTickerSubmissionAdmin(admin.ModelAdmin):
    list_display = ("message_preview", "status", "created_at", "submitted_by", "reviewed_at")
    list_filter = ("status",)
    readonly_fields = ("created_at", "reviewed_at", "reviewed_by")
    ordering = ("-created_at",)

    @admin.display(description="Message")
    def message_preview(self, obj):
        return (obj.message or "")[:80]


@admin.register(HomeTickerItem)
class HomeTickerItemAdmin(admin.ModelAdmin):
    list_display = ("message_preview", "sort_order", "is_active")
    list_filter = ("is_active",)
    list_editable = ("sort_order", "is_active")
    ordering = ("sort_order", "id")
    fields = ("message", "sort_order", "is_active")

    @admin.display(description="Message")
    def message_preview(self, obj):
        return (obj.message or "")[:80]
