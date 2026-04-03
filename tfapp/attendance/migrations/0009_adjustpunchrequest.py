import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0008_workthroughlunchrequest"),
        ("timeclock", "0004_alter_timeentry_clock_in_authorized_by"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdjustPunchRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(db_index=True, editable=False, max_length=48, unique=True)),
                ("punch_field", models.CharField(choices=[("clock_in", "Clock in"), ("lunch_out", "Lunch out"), ("lunch_in", "Lunch in"), ("clock_out", "Clock out")], max_length=20)),
                ("previous_at", models.DateTimeField(blank=True, help_text="Punch value at time of request (for audit).", null=True)),
                ("requested_at", models.DateTimeField(help_text="Requested corrected date/time for this punch.")),
                ("comments", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("denied", "Denied"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                (
                    "approver",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_adjust_punch_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "time_entry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="adjust_punch_requests",
                        to="timeclock.timeentry",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="adjust_punch_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
