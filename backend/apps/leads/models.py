from django.db import models


class Lead(models.Model):
    class Source(models.TextChoices):
        CONVERSATION = 'conversation', 'Conversation'
        FORM = 'form', 'Form'
        MANUAL = 'manual', 'Manual'

    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='leads')
    employee = models.ForeignKey('employees.AIEmployee', on_delete=models.SET_NULL, null=True, related_name='leads')
    session = models.ForeignKey('chat.ChatSession', on_delete=models.SET_NULL, null=True, blank=True, related_name='leads')
    name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    intent_summary = models.TextField(blank=True, help_text='What the visitor wanted')
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.CONVERSATION)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return self.name or self.email or f'Lead {self.id}'
