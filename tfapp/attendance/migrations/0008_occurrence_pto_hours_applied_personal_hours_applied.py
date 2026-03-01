# Track PTO vs personal split when apply_pto runs

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0007_occurrence_is_variance_to_schedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="occurrence",
            name="pto_hours_applied",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="occurrence",
            name="personal_hours_applied",
            field=models.FloatField(default=0.0),
        ),
    ]
