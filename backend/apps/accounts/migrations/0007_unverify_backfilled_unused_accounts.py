from datetime import timedelta

from django.conf import settings
from django.db import migrations


def unverify(apps, schema_editor):
    """
    An earlier version of 0004_backfill_profiles marked *every* user without a
    profile as verified — including half-created signups that never worked.
    Those addresses then showed "already in use" forever. Un-verify accounts
    whose profile came from that backfill (created well after the user joined)
    and that were never used (no login, not staff), so the address can be
    registered again. Profiles made at signup are created together with the
    user, so real accounts are not touched.
    """
    UserProfile = apps.get_model('accounts', 'UserProfile')
    candidates = UserProfile.objects.select_related('user').filter(
        is_verified=True,
        user__last_login__isnull=True,
        user__is_staff=False,
        user__is_superuser=False,
    )
    stale = [p.pk for p in candidates if p.created_at - p.user.date_joined > timedelta(hours=1)]
    if stale:
        UserProfile.objects.filter(pk__in=stale).update(is_verified=False, email_verified=False)


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0006_profile_active_workspace'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(unverify, migrations.RunPython.noop)]
