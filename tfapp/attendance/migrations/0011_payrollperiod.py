# Payroll period finalized status for locking weekly hours

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0010_occurrence_time_off_request"),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollPeriod",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week_ending", models.DateField(unique=True)),
                ("is_finalized", models.BooleanField(default=False)),
                ("finalized_at", models.DateTimeField(blank=True, null=True)),
                ("finalized_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="finalized_payroll_periods", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-week_ending"],
            },
        ),
    ]
