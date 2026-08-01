from django.conf import settings
from django.db import models


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
    brand_color = models.CharField(max_length=7, default='#0F766E')
    conversations_used = models.PositiveIntegerField(default=0)
    tokens_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return self.name

    @property
    def conversation_limit(self):
        return self.plan.conversation_limit if self.plan else 100

    @property
    def token_limit(self):
        return self.plan.token_limit if self.plan else 50_000

    @property
    def usage_percent(self):
        conv = (self.conversations_used / self.conversation_limit) * 100 if self.conversation_limit else 0
        tok = (self.tokens_used / self.token_limit) * 100 if self.token_limit else 0
        return max(conv, tok)

    def is_over_quota(self):
        return self.usage_percent >= 100

    def is_near_quota(self):
        return self.usage_percent >= 80


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
