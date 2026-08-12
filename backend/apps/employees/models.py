import secrets

from django.conf import settings
from django.db import models

from apps.chat.constants import DEFAULT_CAPABILITIES_BY_ROLE


class AIEmployee(models.Model):
    """Named AI Employee deployed on a client's website — a teammate, not a Q&A bot."""

    class Role(models.TextChoices):
        SALES = 'sales_agent', 'Sales Agent'
        SUPPORT = 'support_specialist', 'Support Specialist'
        BOOKING = 'booking_assistant', 'Booking Assistant'
        LEAD_GEN = 'lead_gen', 'Lead Gen'

    class Personality(models.TextChoices):
        FRIENDLY = 'friendly', 'Friendly'
        PROFESSIONAL = 'professional', 'Professional'
        CASUAL = 'casual', 'Casual'
        ENTHUSIASTIC = 'enthusiastic', 'Enthusiastic'

    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='employees')
    name = models.CharField(max_length=100)
    department = models.CharField(max_length=80, blank=True, help_text='e.g. Sales, Support, Marketing')
    role = models.CharField(max_length=40, choices=Role.choices, default=Role.SUPPORT)
    personality = models.CharField(max_length=40, choices=Personality.choices, default=Personality.FRIENDLY)
    language = models.CharField(max_length=20, default='en')
    greeting_message = models.TextField(default='Hi! How can I help you today?')
    system_prompt = models.TextField(blank=True)
    capabilities = models.JSONField(default=list, blank=True)
    handoff_email = models.EmailField(blank=True, help_text='Team inbox for handoffs and scheduled requests')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    brand_color = models.CharField(max_length=7, blank=True)
    widget_token = models.CharField(max_length=64, unique=True, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = 'AI Employee'
        verbose_name_plural = 'AI Employees'

    def __str__(self):
        dept = f' ({self.department})' if self.department else ''
        return f'{self.name} — {self.get_role_display()}{dept}'

    def default_capabilities(self):
        return list(DEFAULT_CAPABILITIES_BY_ROLE.get(self.role, DEFAULT_CAPABILITIES_BY_ROLE['support_specialist']))

    def build_system_prompt(self) -> str:
        company = self.workspace.name if self.workspace_id else 'the company'
        dept = self.department or self.get_role_display()
        caps = self.capabilities if self.capabilities is not None else self.default_capabilities()

        lines = [
            f'You are {self.name}, an AI Employee working as {dept} at {company}.',
            f'Your personality is {self.get_personality_display().lower()}.',
            'You are a real teammate — not a generic assistant or chatbot.',
            'Never say you are an AI, a bot, or a chatbot.',
            'Use the provided business context to answer accurately.',
            '',
            'Your job is to DO work for the business, not only answer questions:',
        ]

        if 'qualify_visitors' in caps:
            lines.append(
                '- Qualify visitors: ask what they need, timeline, budget or constraints when relevant.'
            )
        if 'collect_contact' in caps:
            lines.append(
                '- Collect contact details (name, email, phone) naturally when they show interest.'
            )
        if 'schedule_handoff' in caps:
            lines.append(
                '- Offer to schedule a visit, demo, or callback when appropriate.'
            )
        if 'notify_team' in caps:
            lines.append(
                '- When they want human help, confirm you will connect them to the team.'
            )
        if 'remember_visitors' in caps:
            lines.append(
                '- If visitor context from a previous conversation is provided, acknowledge it warmly.'
            )

        lines.extend([
            '',
            'Always be helpful, proactive, and action-oriented like a great team member.',
        ])
        return '\n'.join(lines)

    def save(self, *args, **kwargs):
        if not self.widget_token:
            self.widget_token = secrets.token_urlsafe(24)
        if self.capabilities is None:
            self.capabilities = self.default_capabilities()
        if not self.system_prompt:
            self.system_prompt = self.build_system_prompt()
        super().save(*args, **kwargs)

    @property
    def public_role_label(self):
        if self.department:
            return f'{self.get_role_display()} · {self.department}'
        return self.get_role_display()

    def embed_snippet(self, widget_url: str | None = None, api_base: str | None = None) -> str:
        src = widget_url or settings.PUBLIC_WIDGET_URL
        api = api_base or settings.PUBLIC_WIDGET_API_URL
        return (
            f'<script src="{src}" '
            f'data-employee-token="{self.widget_token}" '
            f'data-api-base="{api}" '
            f'defer></script>'
        )
