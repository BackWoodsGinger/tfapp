from django.db import migrations, models


def backfill_display_order(apps, schema_editor):
    ProfileCredentialDocument = apps.get_model("accounts", "ProfileCredentialDocument")
    user_ids = (
        ProfileCredentialDocument.objects.values_list("user_id", flat=True)
        .distinct()
        .order_by("user_id")
    )
    for uid in user_ids:
        docs = list(
            ProfileCredentialDocument.objects.filter(user_id=uid).order_by("-uploaded_at", "-id")
        )
        for i, d in enumerate(docs):
            ProfileCredentialDocument.objects.filter(pk=d.pk).update(display_order=i)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_userprofile_careerrole_and_credentials"),
    ]

    operations = [
        migrations.AddField(
            model_name="profilecredentialdocument",
            name="display_order",
            field=models.PositiveIntegerField(
                db_index=True,
                default=0,
                help_text="Lower numbers appear first; drag to reorder on your profile.",
            ),
        ),
        migrations.RunPython(backfill_display_order, migrations.RunPython.noop),
    ]
