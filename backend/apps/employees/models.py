import secrets

from django.db import models


class AIEmployee(models.Model):
    """Named AI Employee deployed on a client's website. Never called a chatbot."""

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
    role = models.CharField(max_length=40, choices=Role.choices, default=Role.SUPPORT)
    personality = models.CharField(max_length=40, choices=Personality.choices, default=Personality.FRIENDLY)
    language = models.CharField(max_length=20, default='en')
    greeting_message = models.TextField(default='Hi! How can I help you today?')
    system_prompt = models.TextField(blank=True)
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
        return f'{self.name} — {self.get_role_display()}'

    def save(self, *args, **kwargs):
        if not self.widget_token:
            self.widget_token = secrets.token_urlsafe(24)
        if not self.system_prompt:
            company = self.workspace.name if self.workspace_id else 'the company'
            self.system_prompt = (
                f'You are {self.name}, a {self.get_role_display()} at {company}. '
                f'Your personality is {self.get_personality_display().lower()}. '
                'Never say you are an AI or a chatbot. '
                'Only answer using the provided context.'
            )
        super().save(*args, **kwargs)

    @property
    def public_role_label(self):
        return self.get_role_display()

    def embed_snippet(self, widget_url: str) -> str:
        return (
            f'<script src="{widget_url}" '
            f'data-employee-token="{self.widget_token}" '
            f'async></script>'
        )
