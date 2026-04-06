from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class HomeTickerSubmission(models.Model):
    """
    Logged-in user proposal for a home-page ticker line; executives approve to publish as HomeTickerItem.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    message = models.CharField(max_length=500)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ticker_submissions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ticker_submissions_reviewed",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Home ticker submission"
        verbose_name_plural = "Home ticker submissions"

    def __str__(self):
        return (self.message or "(empty)")[:60]

    def clean(self):
        super().clean()
        if not (self.message or "").strip():
            raise ValidationError({"message": "Enter message text."})


class HomeTickerItem(models.Model):
    """
    One line of text in the home page scrolling ticker (admin-managed announcements only).
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
