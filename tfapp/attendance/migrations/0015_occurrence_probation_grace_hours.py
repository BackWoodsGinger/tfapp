from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0014_alter_occurrence_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="occurrence",
            name="probation_grace_hours_applied",
            field=models.FloatField(
                default=0.0,
                help_text="Hours absorbed by the 30h probation grace bank (first 90 days); no PTO impact.",
            ),
        ),
    ]
