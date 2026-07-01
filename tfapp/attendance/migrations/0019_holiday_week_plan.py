import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0018_merge_20260508_1018"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HolidayWeekPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("year", models.IntegerField()),
                ("holiday_key", models.CharField(max_length=64)),
                ("name", models.CharField(max_length=64)),
                ("actual_holiday_date", models.DateField()),
                ("week_start", models.DateField()),
                ("week_ending", models.DateField()),
                ("is_complete", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_holiday_week_plans",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-actual_holiday_date"],
            },
        ),
        migrations.CreateModel(
            name="HolidayWeekPlanDay",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("the_date", models.DateField()),
                (
                    "template",
                    models.CharField(
                        choices=[("four_day", "4-day"), ("five_day", "5-day")],
                        max_length=16,
                    ),
                ),
                (
                    "work_hours",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Expected work hours that day (0 = not a work day).",
                        max_digits=6,
                    ),
                ),
                (
                    "holiday_pay_hours",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Holiday pay hours that day (0 = none).",
                        max_digits=6,
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="days",
                        to="attendance.holidayweekplan",
                    ),
                ),
            ],
            options={
                "ordering": ["the_date", "template"],
            },
        ),
        migrations.AddConstraint(
            model_name="holidayweekplan",
            constraint=models.UniqueConstraint(
                fields=("year", "holiday_key"),
                name="unique_holiday_week_plan_year_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="holidayweekplanday",
            constraint=models.UniqueConstraint(
                fields=("plan", "the_date", "template"),
                name="unique_holiday_week_plan_day",
            ),
        ),
    ]
