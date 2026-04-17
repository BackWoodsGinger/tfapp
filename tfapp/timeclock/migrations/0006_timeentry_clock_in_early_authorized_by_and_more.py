import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("timeclock", "0005_timeentry_clock_in_override_denied"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="timeentry",
            name="clock_in_early_authorized_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Manager/supervisor who approved early-time credit on the clock-in.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="authorized_early_clock_ins",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="timeentry",
            name="clock_in_early_override_denied",
            field=models.BooleanField(
                default=False,
                help_text="Set when payroll review denies early-time credit on an override-required clock-in.",
            ),
        ),
    ]

