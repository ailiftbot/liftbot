from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workspaces', '0002_workspace_widget_token'),
    ]

    operations = [
        migrations.AddField(
            model_name='workspace',
            name='webhook_url',
            field=models.URLField(blank=True, help_text='POST JSON when leads/tasks are created'),
        ),
        migrations.AddField(
            model_name='workspace',
            name='stripe_customer_id',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='workspace',
            name='stripe_subscription_id',
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
