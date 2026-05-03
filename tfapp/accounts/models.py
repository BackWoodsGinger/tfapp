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


class CareerRole(models.Model):
    """
    Organization roles users can mark as career interests (maintained in admin).
    Distinct from attendance.CustomUser.role (permission tier).
    """

    name = models.CharField(max_length=200, unique=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    """Extended profile fields for the custom user model (OneToOne)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    phone = models.CharField(max_length=40, blank=True)
    photo = models.ImageField(
        upload_to="profile_photos/%Y/%m/",
        blank=True,
        null=True,
    )
    bio = models.TextField(blank=True)

    def __str__(self):
        return f"Profile for {self.user}"


class UserCareerRoleInterest(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="career_role_interests",
    )
    role = models.ForeignKey(
        CareerRole,
        on_delete=models.CASCADE,
        related_name="interested_users",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role"],
                name="accounts_user_career_role_unique",
            )
        ]
        ordering = ["role__sort_order", "role__name"]

    def __str__(self):
        return f"{self.user} → {self.role}"


class ProfileCredentialDocument(models.Model):
    """Degree, certificate, or other credential file uploaded by the user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="credential_documents",
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional label (e.g. degree name or issuing body).",
    )
    file = models.FileField(upload_to="profile_credentials/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        label = self.title or self.file.name
        return f"{self.user_id}: {label}"
