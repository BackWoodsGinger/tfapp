# Generated manually — payroll CSV now writes TimeEntry rows instead of schedule overrides.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0012_payroll_period_schedule_override"),
    ]

    operations = [
        migrations.DeleteModel(name="PayrollPeriodScheduleOverride"),
    ]
