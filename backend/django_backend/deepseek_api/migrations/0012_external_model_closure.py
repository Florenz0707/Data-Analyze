import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def _cipher() -> Fernet:
    configured_key = getattr(settings, "EXTERNAL_API_ENCRYPTION_KEY", "")
    if configured_key:
        return Fernet(configured_key.encode("ascii"))
    seed = str(settings.SECRET_KEY).encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed).digest()))


def encrypt_existing_api_keys(apps, schema_editor):
    ExternalLLMAPI = apps.get_model("deepseek_api", "ExternalLLMAPI")
    cipher = _cipher()
    for item in ExternalLLMAPI.objects.all().iterator():
        ExternalLLMAPI.objects.filter(pk=item.pk).update(
            api_key_encrypted=cipher.encrypt(item.api_key.encode("utf-8")).decode("ascii")
        )


class Migration(migrations.Migration):
    dependencies = [
        ("deepseek_api", "0011_ratelimitbucket"),
    ]

    operations = [
        migrations.AddField(
            model_name="userllmpreference",
            name="external_api",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="preferences",
                to="deepseek_api.externalllmapi",
            ),
        ),
        migrations.AddField(
            model_name="externalllmapi",
            name="api_key_encrypted",
            field=models.CharField(max_length=512, null=True),
        ),
        migrations.RunPython(encrypt_existing_api_keys, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="externalllmapi",
            name="api_key",
        ),
        migrations.AlterField(
            model_name="externalllmapi",
            name="api_key_encrypted",
            field=models.CharField(max_length=512),
        ),
    ]
