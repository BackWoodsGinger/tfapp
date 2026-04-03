from django.contrib import admin

from .models import HomeTickerItem


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
