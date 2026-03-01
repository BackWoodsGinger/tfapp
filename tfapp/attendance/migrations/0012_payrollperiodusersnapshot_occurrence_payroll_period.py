# Snapshot for unfinalize revert; link occurrences created at finalize to period

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0011_payrollperiod"),
    ]

    operations = [
        migrations.AddField(
            model_name="occurrence",
            name="payroll_period",
            field=models.ForeignKey(
                blank=True,
                help_text="Set when this occurrence was created at payroll finalize (variance or tardy); used to revert on unfinalize.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="occurrences_created",
                to="attendance.payrollperiod",
            ),
        ),
        migrations.CreateModel(
            name="PayrollPeriodUserSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("pto_accrued_hours", models.FloatField(default=0.0)),
                ("period", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="user_snapshots", to="attendance.payrollperiod")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payroll_period_snapshots", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["period", "user"],
                "unique_together": {("period", "user")},
            },
        ),
    ]
