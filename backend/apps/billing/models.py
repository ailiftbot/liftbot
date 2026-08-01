from django.db import models


class BillingPlan(models.Model):
    name = models.CharField(max_length=50)
    slug = models.SlugField(unique=True)
    price_monthly = models.DecimalField(max_digits=8, decimal_places=2)
    conversation_limit = models.PositiveIntegerField()
    token_limit = models.PositiveIntegerField()
    employee_limit = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('price_monthly',)

    def __str__(self):
        return f'{self.name} (${self.price_monthly}/mo)'


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SENT = 'sent', 'Sent'
        PAID = 'paid', 'Paid'
        VOID = 'void', 'Void'

    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='invoices')
    plan = models.ForeignKey(BillingPlan, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    period_start = models.DateField()
    period_end = models.DateField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'Invoice {self.id} — {self.workspace} ({self.status})'
