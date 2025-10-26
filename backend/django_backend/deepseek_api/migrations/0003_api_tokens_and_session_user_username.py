from django.db import migrations, models


def copy_session_username(apps, schema_editor):
    ConversationSession = apps.get_model('deepseek_api', 'ConversationSession')
    APIKey = apps.get_model('deepseek_api', 'APIKey')
    for sess in ConversationSession.objects.all():
        try:
            if getattr(sess, 'user_id', None):
                api_key = APIKey.objects.get(id=sess.user_id)
                setattr(sess, 'username', api_key.user)
                sess.save(update_fields=['username'])
        except APIKey.DoesNotExist:
            setattr(sess, 'username', '')
            sess.save(update_fields=['username'])


def dedupe_sessions_by_username(apps, schema_editor):
    ConversationSession = apps.get_model('deepseek_api', 'ConversationSession')
    # Remove duplicates by (session_id, username), keep the latest updated
    qs = ConversationSession.objects.all().order_by('session_id', 'username', '-updated_at', '-id')
    seen = set()
    for sess in qs:
        key = (sess.session_id, getattr(sess, 'username', ''))
        if key in seen:
            sess.delete()
        else:
            seen.add(key)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('deepseek_api', '0002_userllmpreference'),
    ]

    operations = [
        # APIKey field adjustments
        migrations.AlterField(
            model_name='apikey',
            name='key',
            field=models.CharField(max_length=64, unique=True),
        ),
        migrations.AlterField(
            model_name='apikey',
            name='user',
            field=models.CharField(max_length=100, db_index=True),
        ),
        migrations.AddField(
            model_name='apikey',
            name='refresh_token',
            field=models.CharField(blank=True, max_length=128, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='apikey',
            name='refresh_expiry_time',
            field=models.IntegerField(blank=True, null=True),
        ),

        # Step 1: ConversationSession add 'username' and backfill + dedupe
        migrations.AddField(
            model_name='conversationsession',
            name='username',
            field=models.CharField(db_index=True, default='', max_length=100),
            preserve_default=False,
        ),
        migrations.RunPython(copy_session_username, noop_reverse),
        migrations.RunPython(dedupe_sessions_by_username, noop_reverse),
        # Switch unique_together to (session_id, username) for now
        migrations.AlterUniqueTogether(
            name='conversationsession',
            unique_together={('session_id', 'username')},
        ),
        # IMPORTANT: we do NOT drop old FK or rename in this migration to avoid SQLite table rebuild issues.
    ]
