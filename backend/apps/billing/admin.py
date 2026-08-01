from django.contrib import admin

from .models import BillingPlan, Invoice


@admin.register(BillingPlan)
class BillingPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'price_monthly', 'conversation_limit', 'token_limit', 'employee_limit', 'is_active')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'workspace', 'plan', 'amount', 'status', 'period_start', 'period_end', 'created_at')
    list_filter = ('status', 'plan')
    search_fields = ('workspace__name',)
