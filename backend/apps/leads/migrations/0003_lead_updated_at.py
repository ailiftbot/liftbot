from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leads', '0002_lead_intent_source'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
