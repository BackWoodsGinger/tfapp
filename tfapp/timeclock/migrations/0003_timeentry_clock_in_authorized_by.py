import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("timeclock", "0002_timeentry_slug"),
    ]

    operations = [
        migrations.AddField(
            model_name="timeentry",
            name="clock_in_authorized_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Manager/supervisor who approved clock-in when unscheduled or more than 10 minutes early.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="authorized_clock_ins",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
