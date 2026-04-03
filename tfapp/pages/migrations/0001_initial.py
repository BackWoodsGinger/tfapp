from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="HomeTickerItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "item_type",
                    models.CharField(
                        choices=[
                            ("custom", "Custom message"),
                            ("weather", "Weather (coordinates from site settings)"),
                            ("stock", "Stock quote (US symbol)"),
                        ],
                        default="custom",
                        max_length=20,
                    ),
                ),
                (
                    "custom_text",
                    models.CharField(
                        blank=True,
                        help_text="Shown when type is Custom message.",
                        max_length=500,
                    ),
                ),
                (
                    "stock_symbol",
                    models.CharField(
                        blank=True,
                        help_text="US ticker, e.g. AAPL or MSFT (used when type is Stock quote).",
                        max_length=12,
                    ),
                ),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "Home ticker item",
                "verbose_name_plural": "Home ticker items",
                "ordering": ["sort_order", "id"],
            },
        ),
    ]
