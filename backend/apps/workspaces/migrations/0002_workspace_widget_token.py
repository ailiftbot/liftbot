import secrets

from django.db import migrations, models


def populate_workspace_tokens(apps, schema_editor):
    Workspace = apps.get_model('workspaces', 'Workspace')
    for ws in Workspace.objects.all():
        ws.widget_token = secrets.token_urlsafe(24)
        ws.save(update_fields=['widget_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('workspaces', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='workspace',
            name='widget_token',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.RunPython(populate_workspace_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='workspace',
            name='widget_token',
            field=models.CharField(max_length=64, unique=True),
        ),
    ]
