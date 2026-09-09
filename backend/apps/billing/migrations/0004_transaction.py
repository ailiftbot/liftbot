import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0003_stripe_fields'),
        ('workspaces', '0005_workspace_is_active'),
    ]

    operations = [
        migrations.CreateModel(
            name='Transaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=8)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('success', 'Success'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('gateway', models.CharField(blank=True, help_text='e.g. razorpay, stripe', max_length=50)),
                ('gateway_reference', models.CharField(blank=True, help_text='Gateway order/payment ID', max_length=150)),
                ('reference', models.CharField(editable=False, max_length=40, unique=True)),
                ('failure_reason', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transactions', to='billing.billingplan')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transactions', to='workspaces.workspace')),
            ],
            options={
                'ordering': ('-created_at',),
            },
        ),
    ]