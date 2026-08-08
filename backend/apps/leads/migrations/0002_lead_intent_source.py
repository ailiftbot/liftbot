from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leads', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='intent_summary',
            field=models.TextField(blank=True, help_text='What the visitor wanted'),
        ),
        migrations.AddField(
            model_name='lead',
            name='source',
            field=models.CharField(
                choices=[('conversation', 'Conversation'), ('form', 'Form'), ('manual', 'Manual')],
                default='conversation',
                max_length=20,
            ),
        ),
    ]
