from django.contrib import admin

from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'workspace', 'employee', 'created_at')
    search_fields = ('name', 'email', 'phone', 'workspace__name')
