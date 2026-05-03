import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_usersession_accounts_us_user_id_ab8ad3_idx"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CareerRole",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=200, unique=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("phone", models.CharField(blank=True, max_length=40)),
                (
                    "photo",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="profile_photos/%Y/%m/",
                    ),
                ),
                ("bio", models.TextField(blank=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ProfileCredentialDocument",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True,
                        help_text="Optional label (e.g. degree name or issuing body).",
                        max_length=200,
                    ),
                ),
                ("file", models.FileField(upload_to="profile_credentials/%Y/%m/")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="credential_documents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-uploaded_at"],
            },
        ),
        migrations.CreateModel(
            name="UserCareerRoleInterest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="interested_users",
                        to="accounts.careerrole",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="career_role_interests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["role__sort_order", "role__name"],
            },
        ),
        migrations.AddConstraint(
            model_name="usercareerroleinterest",
            constraint=models.UniqueConstraint(
                fields=("user", "role"),
                name="accounts_user_career_role_unique",
            ),
        ),
    ]
