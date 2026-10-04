from django.conf import settings
from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    UserProfile = apps.get_model('accounts', 'UserProfile')
    missing = User.objects.filter(profile__isnull=True)
    UserProfile.objects.bulk_create([
        # Pre-existing accounts were usable before; don't lock them out.
        UserProfile(user=u, full_name=u.first_name, is_verified=True, email_verified=True)
        for u in missing
    ])


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0003_otp_and_is_verified'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
