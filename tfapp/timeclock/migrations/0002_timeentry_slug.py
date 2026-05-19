import secrets

from django.db import migrations, models


def drop_orphan_slug_indexes(apps, schema_editor):
    """PostgreSQL: failed 0002 runs can leave *_like indexes before AlterField completes."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('i', 'I')
              AND n.nspname = 'public'
              AND c.relname LIKE 'timeclock_timeentry_slug%'
            """
        )
        for (indexname,) in cursor.fetchall():
            cursor.execute(f'DROP INDEX IF EXISTS public."{indexname}"')


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
        # Add without db_index first; AlterField below adds unique+index once (avoids
        # duplicate PostgreSQL *_like indexes from AddField + AlterField both indexing).
        migrations.AddField(
            model_name="timeentry",
            name="slug",
            field=models.SlugField(editable=False, max_length=48, null=True),
        ),
        migrations.RunPython(fill_timeentry_slugs, migrations.RunPython.noop),
        migrations.RunPython(drop_orphan_slug_indexes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="timeentry",
            name="slug",
            field=models.SlugField(db_index=True, editable=False, max_length=48, unique=True),
        ),
    ]
