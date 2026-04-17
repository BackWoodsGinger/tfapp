from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("timeclock", "0004_alter_timeentry_clock_in_authorized_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="timeentry",
            name="clock_in_override_denied",
            field=models.BooleanField(
                default=False,
                help_text="Set when payroll review denies an override-required clock-in; keeps entry from pending-approval queue.",
            ),
        ),
    ]

