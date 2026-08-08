from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='aiemployee',
            name='capabilities',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='aiemployee',
            name='department',
            field=models.CharField(blank=True, help_text='e.g. Sales, Support, Marketing', max_length=80),
        ),
        migrations.AddField(
            model_name='aiemployee',
            name='handoff_email',
            field=models.EmailField(blank=True, help_text='Team inbox for handoffs and scheduled requests'),
        ),
    ]
