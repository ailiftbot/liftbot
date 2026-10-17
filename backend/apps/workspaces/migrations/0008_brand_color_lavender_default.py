from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workspaces', '0007_relax_drifted_columns'),
    ]

    operations = [
        migrations.AlterField(
            model_name='workspace',
            name='brand_color',
            field=models.CharField(default='#7444E4', max_length=7),
        ),
    ]
