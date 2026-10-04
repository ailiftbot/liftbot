from django.contrib import admin

from .models import Workspace, WorkspaceInvite, WorkspaceMembership


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'plan', 'conversations_used', 'tokens_used', 'created_at')
    list_filter = ('plan',)
    search_fields = ('name', 'owner__email')
    raw_id_fields = ('owner', 'plan')


admin.site.register(WorkspaceMembership)


@admin.register(WorkspaceInvite)
class WorkspaceInviteAdmin(admin.ModelAdmin):
    list_display = ('email', 'workspace', 'role', 'invited_by', 'accepted_at', 'expires_at')
    search_fields = ('email', 'workspace__name')
    raw_id_fields = ('workspace', 'invited_by')
