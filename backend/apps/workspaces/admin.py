from django.contrib import admin

from .models import Workspace, WorkspaceMembership


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'plan', 'conversations_used', 'tokens_used', 'created_at')
    list_filter = ('plan',)
    search_fields = ('name', 'owner__email')
    raw_id_fields = ('owner', 'plan')


admin.site.register(WorkspaceMembership)
