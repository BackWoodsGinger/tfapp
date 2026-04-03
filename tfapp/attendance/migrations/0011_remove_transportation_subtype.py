from django.db import migrations, models


def forwards_remap_transportation(apps, schema_editor):
    Occurrence = apps.get_model("attendance", "Occurrence")
    TimeOffRequest = apps.get_model("attendance", "TimeOffRequest")
    Occurrence.objects.filter(subtype="Transportation").update(subtype="Time Off")
    TimeOffRequest.objects.filter(subtype="Transportation").update(subtype="Time Off")


def backwards_noop(apps, schema_editor):
    pass


_SUBTYPE_CHOICES = [
    ("Time Off", "Time Off"),
    ("Tardy In Grace", "Tardy In Grace"),
    ("Tardy Out of Grace", "Tardy Out of Grace"),
    ("Exchange", "Exchange"),
    ("Lay-Off", "Lay-Off"),
    ("FMLA", "Family Medical Leave"),
    ("LOA", "Leave of Absence"),
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
]


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0010_workschedule_optional_lunch"),
    ]

    operations = [
        migrations.RunPython(forwards_remap_transportation, backwards_noop),
        migrations.AlterField(
            model_name="occurrence",
            name="subtype",
            field=models.CharField(choices=_SUBTYPE_CHOICES, max_length=50),
        ),
        migrations.AlterField(
            model_name="timeoffrequest",
            name="subtype",
            field=models.CharField(
                choices=_SUBTYPE_CHOICES,
                default="Time Off",
                max_length=50,
            ),
        ),
    ]
