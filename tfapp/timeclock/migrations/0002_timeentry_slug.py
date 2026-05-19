import secrets

from django.db import migrations, models


def drop_orphan_slug_indexes(apps, schema_editor):
    """PostgreSQL: remove leftover slug indexes from failed 0002 attempts."""
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


def apply_slug_unique_postgres(apps, schema_editor):
    """Apply NOT NULL + unique on slug without Django AlterField (avoids duplicate *_like index)."""
    if schema_editor.connection.vendor != "postgresql":
        return
    drop_orphan_slug_indexes(apps, schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE timeclock_timeentry ALTER COLUMN slug SET NOT NULL"
        )
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS timeclock_timeentry_slug_key
            ON timeclock_timeentry (slug)
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ("timeclock", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(drop_orphan_slug_indexes, migrations.RunPython.noop),
        migrations.AddField(
            model_name="timeentry",
            name="slug",
            field=models.SlugField(editable=False, max_length=48, null=True),
        ),
        migrations.RunPython(fill_timeentry_slugs, migrations.RunPython.noop),
        migrations.RunPython(drop_orphan_slug_indexes, migrations.RunPython.noop),
        migrations.RunPython(apply_slug_unique_postgres, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="timeentry",
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
