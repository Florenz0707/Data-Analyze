import django.db.models.deletion
from django.db import migrations, models


def attach_history_to_sessions(apps, schema_editor):
    """Attach valid rows and remove rows that cannot be owned by a Session."""
    Session = apps.get_model("deepseek_api", "Session")
    History = apps.get_model("deepseek_api", "History")
    for history in History.objects.all().iterator():
        session = Session.objects.filter(
            session_id=history.session_id,
            user=history.user,
        ).first()
        if session is None:
            history.delete()
        else:
            History.objects.filter(pk=history.pk).update(session_fk_id=session.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("deepseek_api", "0006_history_session"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="title",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="history",
            name="session_fk",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="legacy_histories",
                to="deepseek_api.session",
            ),
        ),
        migrations.RunPython(attach_history_to_sessions, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="history",
            name="session_fk",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="histories",
                to="deepseek_api.session",
            ),
        ),
        migrations.RemoveIndex(
            model_name="history",
            name="deepseek_ap_session_102b96_idx",
        ),
        migrations.RemoveField(
            model_name="history",
            name="session_id",
        ),
        migrations.RemoveField(
            model_name="history",
            name="user",
        ),
        migrations.RenameField(
            model_name="history",
            old_name="session_fk",
            new_name="session",
        ),
        migrations.AddIndex(
            model_name="history",
            index=models.Index(
                fields=["session", "created_at"],
                name="deepseek_api_hist_session_idx",
            ),
        ),
    ]
