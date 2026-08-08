import uuid

from django.conf import settings
from django.db import models


class ChatSession(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        HUMAN = 'human', 'Human takeover'
        PAUSED = 'paused', 'Paused'
        CLOSED = 'closed', 'Closed'

    employee = models.ForeignKey('employees.AIEmployee', on_delete=models.CASCADE, related_name='sessions')
    visitor_id = models.CharField(max_length=64, default=uuid.uuid4, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    taken_over_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='taken_over_sessions',
    )
    taken_over_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-last_message_at',)

    def __str__(self):
        return f'Session {self.id} with {self.employee.name}'

    @property
    def is_human_mode(self):
        return self.status == self.Status.HUMAN


class Message(models.Model):
    class Role(models.TextChoices):
        VISITOR = 'visitor', 'Visitor'
        EMPLOYEE = 'employee', 'AI Employee'
        HUMAN = 'human', 'Team member'
        SYSTEM = 'system', 'System'

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    tokens_used = models.PositiveIntegerField(default=0)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chat_messages',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at',)

    def __str__(self):
        return f'{self.role}: {self.content[:50]}'


class VisitorProfile(models.Model):
    """Long-term memory for returning website visitors."""

    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='visitors')
    visitor_id = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    preferences = models.JSONField(default=dict, blank=True)
    conversation_summary = models.TextField(blank=True)
    last_session = models.ForeignKey(
        ChatSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='visitor_profiles',
    )
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('workspace', 'visitor_id')
        ordering = ('-last_seen_at',)

    def __str__(self):
        return self.name or self.email or f'Visitor {self.visitor_id[:8]}'


class EmployeeTask(models.Model):
    """Work items your AI Employee creates — handoffs, schedules, qualifications."""

    class TaskType(models.TextChoices):
        QUALIFY = 'qualify', 'Qualify visitor'
        HANDOFF = 'handoff', 'Team handoff'
        SCHEDULE = 'schedule', 'Schedule / visit'
        FOLLOW_UP = 'follow_up', 'Follow up'
        CONTACT = 'contact', 'Contact collected'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        IN_PROGRESS = 'in_progress', 'In progress'
        DONE = 'done', 'Done'
        CANCELLED = 'cancelled', 'Cancelled'

    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='employee_tasks')
    employee = models.ForeignKey('employees.AIEmployee', on_delete=models.CASCADE, related_name='tasks')
    session = models.ForeignKey(ChatSession, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    lead = models.ForeignKey('leads.Lead', on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    task_type = models.CharField(max_length=20, choices=TaskType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    title = models.CharField(max_length=255)
    details = models.JSONField(default=dict, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.title} ({self.get_status_display()})'
