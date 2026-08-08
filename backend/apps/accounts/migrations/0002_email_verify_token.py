from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='email_verify_token',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
    ]
