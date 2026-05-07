from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_profilecredentialdocument_display_order"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProfileUpdateReviewItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "update_type",
                    models.CharField(
                        choices=[
                            ("profile_photo", "Profile photo updated"),
                            ("credential_upload", "Credential uploaded"),
                        ],
                        db_index=True,
                        max_length=40,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("reviewed", "Reviewed")],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("photo_name_snapshot", models.CharField(blank=True, max_length=300)),
                ("credential_title_snapshot", models.CharField(blank=True, max_length=200)),
                ("credential_name_snapshot", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_notes", models.TextField(blank=True)),
                (
                    "credential_document",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="review_items",
                        to="accounts.profilecredentialdocument",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="review_items",
                        to="accounts.userprofile",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="profile_updates_reviewed",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile_update_review_items",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["status", "created_at"], name="accounts_pr_status_1d68ce_idx")],
            },
        ),
    ]
