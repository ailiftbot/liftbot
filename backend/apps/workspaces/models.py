import secrets
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import models
from django.utils import timezone


class Workspace(models.Model):
    name = models.CharField(max_length=200)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='owned_workspaces')
    plan = models.ForeignKey(
        'billing.BillingPlan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workspaces',
    )
    is_active = models.BooleanField(
        default=False,
        help_text='True once the onboarding payment has succeeded. Dashboard access is gated on this.',
    )
    brand_color = models.CharField(max_length=7, default='#0F766E')
    widget_token = models.CharField(max_length=64, unique=True, editable=False)
    webhook_url = models.URLField(blank=True, help_text='POST JSON when leads/tasks are created')
    timezone = models.CharField(
        max_length=64,
        default='UTC',
        help_text='IANA timezone the office hours below are expressed in, e.g. Asia/Kolkata',
    )
    office_hours = models.JSONField(
        default=dict,
        blank=True,
        help_text='{"mon": [["09:00", "18:00"]], ...}. Empty means the team is always reachable.',
    )
    away_message = models.TextField(
        blank=True,
        help_text='Shown in the widget outside office hours',
    )
    offline_form_enabled = models.BooleanField(
        default=True,
        help_text='Ask offline visitors to leave their details',
    )
    stripe_customer_id = models.CharField(max_length=100, blank=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True)
    conversations_used = models.PositiveIntegerField(default=0)
    tokens_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.widget_token:
            self.widget_token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)

    @property
    def conversation_limit(self):
        return self.plan.conversation_limit if self.plan else 100

    @property
    def token_limit(self):
        return self.plan.token_limit if self.plan else 50_000

    @property
    def employee_limit(self):
        if not self.plan:
            return 1
        return self.plan.employee_limit

    @property
    def usage_percent(self):
        conv = (self.conversations_used / self.conversation_limit) * 100 if self.conversation_limit else 0
        tok = (self.tokens_used / self.token_limit) * 100 if self.token_limit else 0
        return max(conv, tok)

    def is_over_quota(self):
        return self.usage_percent >= 100

    def is_near_quota(self):
        return self.usage_percent >= 80

    WEEKDAY_KEYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')

    DEFAULT_AWAY_MESSAGE = (
        'The team is offline right now. Leave your details and we will get back to you.'
    )

    def tz(self):
        try:
            return ZoneInfo(self.timezone or 'UTC')
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo('UTC')

    @staticmethod
    def _parse_hhmm(value: str):
        try:
            hh, mm = str(value).split(':')
            return time(int(hh), int(mm))
        except (ValueError, AttributeError):
            return None

    def is_within_office_hours(self, when: datetime | None = None) -> bool:
        """True when the team is reachable. No configured hours == always on."""
        hours = self.office_hours or {}
        if not any(hours.get(k) for k in self.WEEKDAY_KEYS):
            return True
        local = (when or timezone.now()).astimezone(self.tz())
        ranges = hours.get(self.WEEKDAY_KEYS[local.weekday()]) or []
        now_t = local.time()
        for window in ranges:
            if not isinstance(window, (list, tuple)) or len(window) != 2:
                continue
            start = self._parse_hhmm(window[0])
            end = self._parse_hhmm(window[1])
            if start is None or end is None:
                continue
            if start <= end:
                if start <= now_t <= end:
                    return True
            elif now_t >= start or now_t <= end:  # window crosses midnight
                return True
        return False

    def availability(self) -> dict:
        online = self.is_within_office_hours()
        return {
            'online': online,
            'away_message': '' if online else (self.away_message or self.DEFAULT_AWAY_MESSAGE),
            'offline_form': bool(not online and self.offline_form_enabled),
            'office_hours': self.office_hours or {},
            'timezone': self.timezone or 'UTC',
        }

    def team_embed_snippet(self, widget_url: str | None = None, api_base: str | None = None) -> str:
        src = widget_url or settings.PUBLIC_WIDGET_URL
        api = api_base or settings.PUBLIC_WIDGET_API_URL
        return (
            f'<script src="{src}" '
            f'data-workspace-token="{self.widget_token}" '
            f'data-api-base="{api}" '
            f'defer></script>'
        )

    def next_available_slots(self, count: int = 6):
        """Simple next-business-day slots for booking (MVP — no calendar sync)."""
        slots = []
        day = timezone.localtime() + timedelta(days=1)
        hours = [10, 12, 15, 17]
        while len(slots) < count:
            if day.weekday() < 5:  # Mon–Fri
                for hour in hours:
                    if len(slots) >= count:
                        break
                    slot_dt = day.replace(hour=hour, minute=0, second=0, microsecond=0)
                    slots.append({
                        'id': slot_dt.isoformat(),
                        'label': slot_dt.strftime('%a %b %d · %I:%M %p'),
                        'starts_at': slot_dt.isoformat(),
                    })
            day += timedelta(days=1)
        return slots


class WorkspaceMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        ADMIN = 'admin', 'Admin'
        MEMBER = 'member', 'Member'

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('workspace', 'user')

    def __str__(self):
        return f'{self.user} @ {self.workspace}'
