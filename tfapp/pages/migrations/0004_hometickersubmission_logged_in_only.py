from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def delete_submissions_without_user(apps, schema_editor):
    HomeTickerSubmission = apps.get_model("pages", "HomeTickerSubmission")
    HomeTickerSubmission.objects.filter(submitted_by__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("pages", "0003_hometickersubmission"),
    ]

    operations = [
        migrations.RunPython(delete_submissions_without_user, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="hometickersubmission",
            name="submitted_email",
        ),
        migrations.RemoveField(
            model_name="hometickersubmission",
            name="submitted_name",
        ),
        migrations.AlterField(
            model_name="hometickersubmission",
            name="submitted_by",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="ticker_submissions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
