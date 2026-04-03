from django.db import migrations, models


def prune_non_custom_messages(apps, schema_editor):
    HomeTickerItem = apps.get_model("pages", "HomeTickerItem")
    for row in list(HomeTickerItem.objects.all()):
        if row.item_type != "custom":
            row.delete()
        elif not (row.custom_text or "").strip():
            row.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(prune_non_custom_messages, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="hometickeritem",
            name="item_type",
        ),
        migrations.RemoveField(
            model_name="hometickeritem",
            name="stock_symbol",
        ),
        migrations.RenameField(
            model_name="hometickeritem",
            old_name="custom_text",
            new_name="message",
        ),
        migrations.AlterField(
            model_name="hometickeritem",
            name="message",
            field=models.CharField(max_length=500),
        ),
        migrations.AlterModelOptions(
            name="hometickeritem",
            options={
                "ordering": ["sort_order", "id"],
                "verbose_name": "Home ticker message",
                "verbose_name_plural": "Home ticker messages",
            },
        ),
    ]
