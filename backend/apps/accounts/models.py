from django.db import models
from django.conf import settings
import secrets


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    full_name = models.CharField(max_length=150, blank=True)
    email_verified = models.BooleanField(default=False)
    email_verify_token = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name or self.user.username

    def issue_verify_token(self):
        self.email_verify_token = secrets.token_urlsafe(32)
        self.save(update_fields=['email_verify_token'])
        return self.email_verify_token
