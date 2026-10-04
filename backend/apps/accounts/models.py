from datetime import timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone
import secrets


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    full_name = models.CharField(max_length=150, blank=True)
    email_verified = models.BooleanField(default=False)
    is_verified = models.BooleanField('Verified', default=False)
    email_verify_token = models.CharField(max_length=64, blank=True, db_index=True)
    # Workspace the user is currently working in (users can belong to several
    # via invites). user_workspace() falls back to the oldest membership.
    active_workspace = models.ForeignKey(
        'workspaces.Workspace',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name or self.user.username

    def mark_verified(self):
        self.is_verified = True
        self.email_verified = True
        self.email_verify_token = ''
        self.save(update_fields=['is_verified', 'email_verified', 'email_verify_token'])

    def issue_verify_token(self):
        self.email_verify_token = secrets.token_urlsafe(32)
        self.save(update_fields=['email_verify_token'])
        return self.email_verify_token


class OTP(models.Model):
    class CooldownActive(Exception):
        """Raised when a new OTP is requested before the resend cooldown has elapsed."""
        def __init__(self, seconds_left):
            self.seconds_left = seconds_left
            super().__init__(f'Wait {seconds_left}s before requesting another OTP.')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='otps',
    )
    code = models.CharField(max_length=6)
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = 'OTPs'
        ordering = ['-created_at']
        verbose_name = 'OTP'
        verbose_name_plural = 'OTPs'

    def __str__(self):
        return f'{self.user_id} · {self.code}'

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @classmethod
    def cooldown_remaining(cls, user):
        """Seconds left before another OTP can be issued for this user. 0 means allowed now."""
        cooldown = getattr(settings, 'OTP_RESEND_COOLDOWN_SECONDS', 60)
        last = cls.objects.filter(user=user).order_by('-created_at').first()
        if last is None:
            return 0
        elapsed = (timezone.now() - last.created_at).total_seconds()
        remaining = cooldown - elapsed
        return max(0, int(remaining))

    @classmethod
    def has_valid_pending(cls, user):
        """True if there's already an unused, unexpired OTP waiting for this user."""
        return cls.objects.filter(
            user=user, is_used=False, expires_at__gt=timezone.now()
        ).exists()

    @classmethod
    def issue_for(cls, user):
        minutes = getattr(settings, 'OTP_EXPIRY_MINUTES', 10)
        wait = cls.cooldown_remaining(user)
        if wait > 0:
            raise cls.CooldownActive(wait)
        cls.objects.filter(user=user, is_used=False).update(is_used=True)
        return cls.objects.create(
            user=user,
            code=f'{secrets.randbelow(1_000_000):06d}',
            expires_at=timezone.now() + timedelta(minutes=minutes),
        )

    @classmethod
    def max_attempts(cls):
        return getattr(settings, 'OTP_MAX_ATTEMPTS', 5)

    @classmethod
    def match_for_user(cls, user, code):
        """Return the matching OTP or None. Prefer ``verify_for_user`` (counts attempts)."""
        otp, _ = cls.verify_for_user(user, code)
        return otp

    @classmethod
    def verify_for_user(cls, user, code):
        """
        Check ``code`` against the user's current pending OTP.

        Returns ``(otp, error)``: ``otp`` is the matched (not yet consumed) OTP or
        None; ``error`` is one of None, 'invalid', 'expired', 'locked'. Every
        wrong guess increments ``attempts`` on the active code and the code is
        invalidated after ``OTP_MAX_ATTEMPTS`` (default 5) wrong guesses.
        """
        cleaned = (code or '').strip()
        active = (
            cls.objects.filter(user=user, is_used=False)
            .order_by('-created_at')
            .first()
        )
        if active is None:
            return None, 'expired'
        if active.is_expired:
            return None, 'expired'
        if active.attempts >= cls.max_attempts():
            active.is_used = True
            active.save(update_fields=['is_used'])
            return None, 'locked'
        if len(cleaned) == 6 and cleaned.isdigit() and secrets.compare_digest(active.code, cleaned):
            return active, None

        # Atomic increment so parallel guesses cannot exceed the limit.
        cls.objects.filter(pk=active.pk).update(attempts=models.F('attempts') + 1)
        active.refresh_from_db(fields=['attempts'])
        if active.attempts >= cls.max_attempts():
            active.is_used = True
            active.save(update_fields=['is_used'])
            return None, 'locked'
        return None, 'invalid'

    def consume(self):
        self.is_used = True
        self.save(update_fields=['is_used'])
