# Generated manually for TimeOffRequest subtype and comments

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0005_alter_occurrence_subtype_timeoffrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="timeoffrequest",
            name="subtype",
            field=models.CharField(
                choices=[
                    ("Time Off", "Time Off"),
                    ("Tardy In Grace", "Tardy In Grace"),
                    ("Tardy Out of Grace", "Tardy Out of Grace"),
                    ("Exchange", "Exchange"),
                    ("FMLA", "Family Medical Leave"),
                    ("LOA", "Leave of Absence"),
                    ("Transportation", "Transportation"),
                    ("Weather Unpaid", "Inclement Weather - Unpaid"),
                    ("Weather Paid", "Inclement Weather - Paid"),
                    ("Bereavement Paid", "Bereavement - Paid"),
                    ("Bereavement Unpaid", "Bereavement - Unpaid"),
                    ("Jury Duty Paid", "Jury Duty - Paid"),
                    ("Jury Duty Unpaid", "Jury Duty - Unpaid"),
                    ("Discipline", "Discipline"),
                    ("Work Comp", "Work Comp"),
                    ("Disability", "Disability"),
                    ("Holiday Paid", "Holiday - Paid"),
                ],
                default="Time Off",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="timeoffrequest",
            name="comments",
            field=models.TextField(blank=True),
        ),
    ]
