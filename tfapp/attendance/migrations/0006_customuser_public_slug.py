import secrets

from django.db import migrations, models


def fill_public_slugs(apps, schema_editor):
    User = apps.get_model("attendance", "CustomUser")
    for u in User.objects.all():
        if u.public_slug:
            continue
        for _ in range(32):
            candidate = secrets.token_urlsafe(18)[:48]
            if not User.objects.filter(public_slug=candidate).exclude(pk=u.pk).exists():
                u.public_slug = candidate
                u.save(update_fields=["public_slug"])
                break
        else:
            u.public_slug = secrets.token_urlsafe(32)[:48]
            u.save(update_fields=["public_slug"])


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0005_timeoffrequest_slug"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="public_slug",
            field=models.SlugField(db_index=True, editable=False, max_length=48, null=True),
        ),
        migrations.RunPython(fill_public_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="customuser",
            name="public_slug",
            field=models.SlugField(db_index=True, editable=False, max_length=48, unique=True),
        ),
    ]
