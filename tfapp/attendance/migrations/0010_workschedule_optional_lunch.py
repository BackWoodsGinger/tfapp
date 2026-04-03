from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0009_adjustpunchrequest"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customuser",
            name="weekly_schedule",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Per weekday: "start", "end", optional "lunch_out"/"lunch_in" (omit both for no lunch). Optional "crosses_midnight": true.',
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="workschedule",
            name="lunch_in",
            field=models.TimeField(
                blank=True,
                help_text="Leave blank when lunch out is blank.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="workschedule",
            name="lunch_out",
            field=models.TimeField(
                blank=True,
                help_text="Leave blank for half-day or no-lunch shifts (e.g. Friday 6:30–11:00).",
                null=True,
            ),
        ),
    ]
