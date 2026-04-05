import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0011_remove_transportation_subtype"),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollPeriodScheduleOverride",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                (
                    "scheduled_hours",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Expected paid hours that day for payroll (0 = not scheduled).",
                        max_digits=6,
                    ),
                ),
                (
                    "period",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="schedule_overrides",
                        to="attendance.payrollperiod",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payroll_schedule_overrides",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["period", "user", "date"],
            },
        ),
        migrations.AddConstraint(
            model_name="payrollperiodscheduleoverride",
            constraint=models.UniqueConstraint(
                fields=("period", "user", "date"),
                name="unique_payroll_schedule_override_period_user_date",
            ),
        ),
    ]
