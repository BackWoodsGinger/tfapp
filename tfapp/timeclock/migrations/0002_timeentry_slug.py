import secrets

from django.db import migrations, models


def fill_timeentry_slugs(apps, schema_editor):
    TimeEntry = apps.get_model("timeclock", "TimeEntry")
    for entry in TimeEntry.objects.all():
        if entry.slug:
            continue
        for _ in range(32):
            candidate = secrets.token_urlsafe(18)[:48]
            if not TimeEntry.objects.filter(slug=candidate).exclude(pk=entry.pk).exists():
                entry.slug = candidate
                entry.save(update_fields=["slug"])
                break
        else:
            entry.slug = secrets.token_urlsafe(32)[:48]
            entry.save(update_fields=["slug"])


class Migration(migrations.Migration):

    dependencies = [
        ("timeclock", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="timeentry",
            name="slug",
            field=models.SlugField(db_index=True, editable=False, max_length=48, null=True),
        ),
        migrations.RunPython(fill_timeentry_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="timeentry",
            name="slug",
            field=models.SlugField(db_index=True, editable=False, max_length=48, unique=True),
        ),
    ]
