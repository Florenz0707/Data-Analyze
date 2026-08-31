from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("deepseek_api", "0010_ratelimit_stable_apikey_fk"),
    ]

    operations = [
        migrations.CreateModel(
            name="RateLimitBucket",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("scope", models.CharField(max_length=64)),
                ("subject", models.CharField(max_length=128)),
                ("window_start", models.BigIntegerField()),
                ("count", models.PositiveIntegerField(default=0)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["scope", "subject", "window_start"],
                        name="deepseek_ap_scope_66f110_idx",
                    ),
                    models.Index(
                        fields=["window_start"],
                        name="deepseek_ap_window__a0d24d_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("scope", "subject", "window_start"),
                        name="unique_rate_limit_bucket",
                    )
                ],
            },
        ),
    ]
