from django.contrib import admin

from .models import (
    CannedResponse,
    ChatSession,
    ConversationRating,
    ConversationTag,
    EmployeeTask,
    Message,
    VisitorProfile,
)


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ('role', 'content', 'created_at')


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'employee', 'visitor_id', 'status', 'assigned_to',
        'first_response_seconds', 'started_at', 'last_message_at',
    )
    list_filter = ('status', 'assigned_to')
    filter_horizontal = ('tags',)
    inlines = [MessageInline]


@admin.register(VisitorProfile)
class VisitorProfileAdmin(admin.ModelAdmin):
    list_display = ('visitor_id', 'workspace', 'name', 'email', 'phone', 'last_seen_at')
    search_fields = ('visitor_id', 'name', 'email')


@admin.register(EmployeeTask)
class EmployeeTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'task_type', 'status', 'employee', 'workspace', 'created_at')
    list_filter = ('task_type', 'status')
    search_fields = ('title', 'workspace__name', 'employee__name')


@admin.register(ConversationTag)
class ConversationTagAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'workspace', 'color')
    search_fields = ('name', 'workspace__name')


@admin.register(CannedResponse)
class CannedResponseAdmin(admin.ModelAdmin):
    list_display = ('shortcut', 'title', 'workspace', 'uses', 'updated_at')
    search_fields = ('shortcut', 'title', 'body')


@admin.register(ConversationRating)
class ConversationRatingAdmin(admin.ModelAdmin):
    list_display = ('score', 'employee', 'workspace', 'rated_human', 'created_at')
    list_filter = ('score', 'rated_human')
