# Link Occurrence to TimeOffRequest so we can reverse PTO when cancelling approved requests

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0009_backfill_pto_personal_hours_applied"),
    ]

    operations = [
        migrations.AddField(
            model_name="occurrence",
            name="time_off_request",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="occurrences",
                to="attendance.timeoffrequest",
            ),
        ),
    ]
