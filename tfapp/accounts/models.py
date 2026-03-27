from django.conf import settings
from django.db import models


class UserSession(models.Model):
    """
    Maps Django session keys to users so we can enforce a max concurrent
    session count per user (oldest sessions are invalidated first).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="browser_sessions",
    )
    session_key = models.CharField(max_length=40, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} / {self.session_key[:8]}…"
