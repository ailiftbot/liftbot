from django.contrib import admin

from .models import KnowledgeSource


@admin.register(KnowledgeSource)
class KnowledgeSourceAdmin(admin.ModelAdmin):
    list_display = ('title', 'source_type', 'employee', 'status', 'chunk_count', 'created_at')
    list_filter = ('source_type', 'status')
    search_fields = ('title', 'employee__name')
