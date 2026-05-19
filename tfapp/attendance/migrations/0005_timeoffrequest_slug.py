import secrets

from django.db import migrations, models

from attendance.pg_slug_migration import run_apply_unique_slug, run_drop_orphan_indexes

TIMEOFF_SLUG_INDEX_PREFIX = "attendance_timeoffrequest_slug"


def populate_timeoff_slugs(apps, schema_editor):
    TimeOffRequest = apps.get_model("attendance", "TimeOffRequest")
    for tor in TimeOffRequest.objects.all().iterator():
        if tor.slug:
            continue
        for _ in range(32):
            candidate = secrets.token_urlsafe(18)
            if len(candidate) > 48:
                candidate = candidate[:48]
            if not TimeOffRequest.objects.filter(slug=candidate).exists():
                tor.slug = candidate
                tor.save(update_fields=["slug"])
                break


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0004_timeoffrequest_partial_day_and_more"),
    ]

    operations = [
        migrations.RunPython(
            run_drop_orphan_indexes(TIMEOFF_SLUG_INDEX_PREFIX),
            migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name="timeoffrequest",
            name="slug",
            field=models.SlugField(editable=False, max_length=48, null=True),
        ),
        migrations.RunPython(populate_timeoff_slugs, migrations.RunPython.noop),
        migrations.RunPython(
            run_drop_orphan_indexes(TIMEOFF_SLUG_INDEX_PREFIX),
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            run_apply_unique_slug("attendance_timeoffrequest", "slug"),
            migrations.RunPython.noop,
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="timeoffrequest",
                    name="slug",
                    field=models.SlugField(
                        db_index=True,
                        editable=False,
                        max_length=48,
                        unique=True,
                    ),
                ),
            ],
        ),
    ]
