from django.db import migrations, models
import django.db.models.deletion


def copy_rate_limit_api_key_ids(apps, schema_editor):
    APIKey = apps.get_model("deepseek_api", "APIKey")
    RateLimit = apps.get_model("deepseek_api", "RateLimit")

    for rate_limit in RateLimit.objects.all().iterator():
        api_key = APIKey.objects.filter(key=rate_limit.api_key_id).only("pk").first()
        if api_key is None:
            # The old foreign key should prevent this case, but deleting an
            # orphan is safer than creating a row with an invalid owner.
            rate_limit.delete()
            continue
        RateLimit.objects.filter(pk=rate_limit.pk).update(api_key_fk_id=api_key.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("deepseek_api", "0009_apikey_revoked_at_refreshtoken"),
    ]

    operations = [
        migrations.AddField(
            model_name="ratelimit",
            name="api_key_fk",
            field=models.ForeignKey(
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rate_limits_migration",
                to="deepseek_api.apikey",
            ),
        ),
        migrations.RunPython(copy_rate_limit_api_key_ids, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="ratelimit",
            name="api_key",
        ),
        migrations.RenameField(
            model_name="ratelimit",
            old_name="api_key_fk",
            new_name="api_key",
        ),
        migrations.AlterField(
            model_name="ratelimit",
            name="api_key",
            field=models.ForeignKey(
                db_index=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rate_limits",
                to="deepseek_api.apikey",
            ),
        ),
    ]
