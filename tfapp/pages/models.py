from django.core.exceptions import ValidationError
from django.db import models


class HomeTickerItem(models.Model):
    """
    One line of text in the home page scrolling ticker (admin-managed announcements only).
    Weather and stock appear separately in the overlay plugin bar (see site settings).
    """

    message = models.CharField(max_length=500)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Home ticker message"
        verbose_name_plural = "Home ticker messages"

    def __str__(self):
        return (self.message or "(empty)")[:60]

    def clean(self):
        super().clean()
        if not (self.message or "").strip():
            raise ValidationError({"message": "Enter message text."})
