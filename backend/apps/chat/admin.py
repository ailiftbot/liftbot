from django.contrib import admin

from .models import ChatSession, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ('role', 'content', 'tokens_used', 'created_at')


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'employee', 'visitor_id', 'started_at', 'last_message_at')
    search_fields = ('visitor_id', 'employee__name')
    inlines = [MessageInline]


admin.site.register(Message)
