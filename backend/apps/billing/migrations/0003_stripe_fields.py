from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='billingplan',
            name='stripe_price_id',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='invoice',
            name='stripe_invoice_id',
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
