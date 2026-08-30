import uuid

import django.db.models.deletion
from django.db import migrations, models


def migrate_sessions_to_users(apps, schema_editor):
    """Resolve legacy usernames and assign deterministic history metadata."""
    User = apps.get_model("auth", "User")
    Session = apps.get_model("deepseek_api", "Session")
    History = apps.get_model("deepseek_api", "History")

    for session in Session.objects.all().iterator():
        user = User.objects.filter(username=session.user).first()
        if user is None:
            session.delete()
            continue
        Session.objects.filter(pk=session.pk).update(user_fk_id=user.pk)

    for session in Session.objects.all().iterator():
        sequence = 0
        histories = History.objects.filter(session_id=session.pk).order_by("created_at", "id")
        for history in histories.iterator():
            sequence += 1
            History.objects.filter(pk=history.pk).update(
                sequence=sequence,
                message_id=uuid.uuid4(),
            )
        Session.objects.filter(pk=session.pk).update(next_history_sequence=sequence)


class Migration(migrations.Migration):
    dependencies = [
        ("deepseek_api", "0007_session_history_foreign_key"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="user_fk",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="legacy_chat_sessions",
                to="auth.user",
            ),
        ),
        migrations.AddField(
            model_name="session",
            name="next_history_sequence",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="history",
            name="sequence",
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name="history",
            name="message_id",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(migrate_sessions_to_users, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="session",
            name="user_fk",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="chat_sessions",
                to="auth.user",
            ),
        ),
        migrations.AlterField(
            model_name="history",
            name="sequence",
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterField(
            model_name="history",
            name="message_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
        migrations.AlterUniqueTogether(
            name="session",
            unique_together=set(),
        ),
        migrations.RemoveIndex(
            model_name="session",
            name="deepseek_ap_user_8f95e4_idx",
        ),
        migrations.RemoveIndex(
            model_name="history",
            name="deepseek_api_hist_session_idx",
        ),
        migrations.RemoveField(
            model_name="session",
            name="user",
        ),
        migrations.RenameField(
            model_name="session",
            old_name="user_fk",
            new_name="user",
        ),
        migrations.AlterUniqueTogether(
            name="session",
            unique_together={("session_id", "user")},
        ),
        migrations.AddIndex(
            model_name="session",
            index=models.Index(
                fields=["user", "updated_at"],
                name="deepseek_api_user_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="history",
            index=models.Index(
                fields=["session", "created_at", "id"],
                name="deepseek_api_hist_cursor_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="history",
            constraint=models.UniqueConstraint(
                fields=["session", "sequence"],
                name="deepseek_api_hist_seq_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="history",
            constraint=models.UniqueConstraint(
                fields=["session", "message_id"],
                name="deepseek_api_hist_msg_uniq",
            ),
        ),
    ]
