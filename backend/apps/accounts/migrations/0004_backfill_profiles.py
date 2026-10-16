from django.conf import settings
from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    UserProfile = apps.get_model('accounts', 'UserProfile')
    missing = User.objects.filter(profile__isnull=True)
    profiles = []
    for u in missing:
        # Accounts that were actually used before are trusted; don't lock them out.
        # Never-used ones are half-created / abandoned signups: leave them unverified
        # so the address can be registered again (see stale_unverified_users).
        used = u.last_login is not None or u.is_staff or u.is_superuser
        profiles.append(UserProfile(user=u, full_name=u.first_name, is_verified=used, email_verified=used))
    UserProfile.objects.bulk_create(profiles)


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0003_otp_and_is_verified'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
