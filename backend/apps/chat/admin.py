from django.contrib import admin

from .models import VisitorProfile, ChatSession, Message, EmployeeTask


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ('role', 'content', 'created_at')


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'employee', 'visitor_id', 'status', 'started_at', 'last_message_at')
    list_filter = ('status',)
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
