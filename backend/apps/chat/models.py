import uuid

from django.conf import settings
from django.db import models
from django.utils.text import slugify


class ConversationTag(models.Model):
    """Free-form labels a team puts on conversations (Crisp-style tags)."""

    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='conversation_tags')
    name = models.CharField(max_length=40)
    slug = models.SlugField(max_length=50)
    color = models.CharField(max_length=7, default='#6366F1')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('workspace', 'slug')
        ordering = ('name',)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:50]
        super().save(*args, **kwargs)


class ChatSession(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        HUMAN = 'human', 'Human takeover'
        PAUSED = 'paused', 'Paused'
        RESOLVED = 'resolved', 'Resolved'
        CLOSED = 'closed', 'Closed'

    OPEN_STATUSES = (Status.ACTIVE, Status.HUMAN, Status.PAUSED)

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
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_sessions',
        help_text='Teammate who owns this conversation',
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    tags = models.ManyToManyField(ConversationTag, blank=True, related_name='sessions')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_sessions',
    )
    first_visitor_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the first reply (AI or teammate) went out',
    )
    first_response_seconds = models.PositiveIntegerField(null=True, blank=True)
    first_human_response_seconds = models.PositiveIntegerField(null=True, blank=True)
    resolution_seconds = models.PositiveIntegerField(null=True, blank=True)
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

    @property
    def is_resolved(self):
        return self.status in (self.Status.RESOLVED, self.Status.CLOSED)

    @property
    def handled_by_ai_only(self):
        """Deflected: closed out without a teammate ever typing."""
        return self.first_human_response_seconds is None


class Message(models.Model):
    class Role(models.TextChoices):
        VISITOR = 'visitor', 'Visitor'
        EMPLOYEE = 'employee', 'AI Employee'
        HUMAN = 'human', 'Team member'
        SYSTEM = 'system', 'System'
        NOTE = 'note', 'Private note'

    #: Roles the visitor is never allowed to see in the widget.
    INTERNAL_ROLES = (Role.NOTE,)

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
    mentions = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='mentioned_in_messages',
        help_text='Teammates @mentioned in a private note',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at',)

    def __str__(self):
        return f'{self.role}: {self.content[:50]}'

    @property
    def is_internal(self):
        return self.role in self.INTERNAL_ROLES


class CannedResponse(models.Model):
    """Saved replies a teammate inserts by shortcut, e.g. `!refund`."""

    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='canned_responses')
    shortcut = models.CharField(max_length=40, help_text='Typed as !shortcut in the reply box')
    title = models.CharField(max_length=120)
    body = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='canned_responses',
    )
    uses = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('workspace', 'shortcut')
        ordering = ('shortcut',)

    def __str__(self):
        return f'!{self.shortcut}'

    def save(self, *args, **kwargs):
        self.shortcut = slugify(self.shortcut).replace('-', '_')[:40]
        super().save(*args, **kwargs)


class ConversationRating(models.Model):
    """Visitor satisfaction score collected at the end of a conversation."""

    session = models.OneToOneField(ChatSession, on_delete=models.CASCADE, related_name='rating')
    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='ratings')
    employee = models.ForeignKey('employees.AIEmployee', on_delete=models.CASCADE, related_name='ratings')
    score = models.PositiveSmallIntegerField(help_text='1 (bad) to 5 (great)')
    comment = models.TextField(blank=True)
    rated_human = models.BooleanField(
        default=False,
        help_text='True when a teammate had replied in this conversation',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.score}/5 on session {self.session_id}'


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
