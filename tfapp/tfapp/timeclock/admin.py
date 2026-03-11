from django.contrib import admin
from .models import TimeEntry


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'date', 'clock_in', 'lunch_out', 'lunch_in', 'clock_out',
    )
    list_filter = ('date', 'user')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    ordering = ('-date',)

    # Optional: Make time fields read-only if they should not be edited
    # readonly_fields = ('clock_in', 'lunch_out', 'lunch_in', 'clock_out')