import uuid

from django.db import models


class ChatSession(models.Model):
    employee = models.ForeignKey('employees.AIEmployee', on_delete=models.CASCADE, related_name='sessions')
    visitor_id = models.CharField(max_length=64, default=uuid.uuid4, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-last_message_at',)

    def __str__(self):
        return f'Session {self.id} with {self.employee.name}'


class Message(models.Model):
    class Role(models.TextChoices):
        VISITOR = 'visitor', 'Visitor'
        EMPLOYEE = 'employee', 'AI Employee'
        SYSTEM = 'system', 'System'

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    tokens_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at',)

    def __str__(self):
        return f'{self.role}: {self.content[:50]}'
