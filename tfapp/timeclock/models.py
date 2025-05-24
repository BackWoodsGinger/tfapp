from django.db import models
from django.conf import settings
from django.utils import timezone

class TimeEntry(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    clock_in = models.DateTimeField(null=True, blank=True)
    lunch_out = models.DateTimeField(null=True, blank=True)
    lunch_in = models.DateTimeField(null=True, blank=True)
    clock_out = models.DateTimeField(null=True, blank=True)
    date = models.DateField(default=timezone.now)

    def is_incomplete(self):
        """Return True if the entry has only some but not all timestamps filled in."""
        fields = [self.clock_in, self.lunch_out, self.lunch_in, self.clock_out]
        return any(fields) and not all(fields)
    
    def __str__(self):
        return f"{self.user.username} - {self.date}"
