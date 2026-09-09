from django.db import migrations, models


def backfill_existing_workspaces_active(apps, schema_editor):
    """
    Pehle se existing workspaces already dashboard use kar rahe the —
    unhe is naye billing-gate ki wajah se lock mat karo. Sirf ab se
    naye signups ke liye is_active=False start hoga.
    """
    Workspace = apps.get_model('workspaces', 'Workspace')
    Workspace.objects.update(is_active=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('workspaces', '0004_alter_workspace_widget_token'),
    ]

    operations = [
        migrations.AddField(
            model_name='workspace',
            name='is_active',
            field=models.BooleanField(
                default=False,
                help_text='True once the onboarding payment has succeeded. Dashboard access is gated on this.',
            ),
        ),
        migrations.RunPython(backfill_existing_workspaces_active, noop_reverse),
    ]