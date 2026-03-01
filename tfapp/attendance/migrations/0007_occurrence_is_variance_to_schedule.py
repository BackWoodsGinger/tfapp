# Generated for variance-to-schedule PTO (Unplanned, Time Off)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0006_add_timeoff_subtype_comments"),
    ]

    operations = [
        migrations.AddField(
            model_name="occurrence",
            name="is_variance_to_schedule",
            field=models.BooleanField(default=False),
        ),
    ]
