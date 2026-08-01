from django.contrib import admin

from .models import AIEmployee


@admin.register(AIEmployee)
class AIEmployeeAdmin(admin.ModelAdmin):
    list_display = ('name', 'role', 'workspace', 'language', 'is_active', 'widget_token', 'created_at')
    list_filter = ('role', 'is_active', 'language')
    search_fields = ('name', 'workspace__name', 'widget_token')
    readonly_fields = ('widget_token', 'system_prompt')
