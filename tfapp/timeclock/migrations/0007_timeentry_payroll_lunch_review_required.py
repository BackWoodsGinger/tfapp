from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("timeclock", "0006_timeentry_clock_in_early_authorized_by_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="timeentry",
            name="payroll_lunch_review_required",
            field=models.BooleanField(
                default=False,
                help_text="Set when payroll CSV import omitted lunch punches on a day with scheduled lunch; "
                "payroll finalization must confirm scheduled lunch or work-through before close.",
            ),
        ),
    ]
