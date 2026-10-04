from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_profile(sender, instance, created, **kwargs):
    """Every user gets a profile — including createsuperuser / admin-created users."""
    if created:
        UserProfile.objects.get_or_create(
            user=instance,
            defaults={'full_name': instance.get_full_name() or instance.first_name},
        )
