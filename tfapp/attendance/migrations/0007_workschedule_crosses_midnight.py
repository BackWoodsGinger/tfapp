from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0006_customuser_public_slug"),
    ]

    operations = [
        migrations.AddField(
            model_name="workschedule",
            name="crosses_midnight",
            field=models.BooleanField(
                default=False,
                help_text="Set when the shift ends the morning after it starts (e.g. 3:30pm–2:00am).",
            ),
        ),
    ]
