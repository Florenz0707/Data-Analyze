from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('deepseek_api', '0003_api_tokens_and_session_user_username'),
    ]

    operations = [
        # Now that username exists and is unique together with session_id,
        # drop the old FK field 'user' and rename 'username' -> 'user'.
        migrations.RemoveField(
            model_name='conversationsession',
            name='user',
        ),
        migrations.RenameField(
            model_name='conversationsession',
            old_name='username',
            new_name='user',
        ),
        migrations.AlterUniqueTogether(
            name='conversationsession',
            unique_together={('session_id', 'user')},
        ),
    ]
