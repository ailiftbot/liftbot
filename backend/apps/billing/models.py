import secrets
from django.db import models


class BillingPlan(models.Model):
    name = models.CharField(max_length=50)
    slug = models.SlugField(unique=True)
    price_monthly = models.DecimalField(max_digits=8, decimal_places=2)
    conversation_limit = models.PositiveIntegerField()
    token_limit = models.PositiveIntegerField()
    employee_limit = models.PositiveIntegerField(default=1)
    stripe_price_id = models.CharField(max_length=100, blank=True)
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
    stripe_invoice_id = models.CharField(max_length=100, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'Invoice {self.id} — {self.workspace} ({self.status})'


class Transaction(models.Model):
    """
    Onboarding-time payment record. Kept separate from Invoice (which is
    for recurring billing cycles after a workspace is already active).
    """
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='transactions')
    plan = models.ForeignKey(BillingPlan, on_delete=models.PROTECT, related_name='transactions')
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # Placeholder fields — filled in once a real gateway (Razorpay/Stripe/etc.) is wired up.
    gateway = models.CharField(max_length=50, blank=True, help_text='e.g. razorpay, stripe')
    gateway_reference = models.CharField(max_length=150, blank=True, help_text='Gateway order/payment ID')

    reference = models.CharField(max_length=40, unique=True, editable=False)
    failure_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.reference} · {self.workspace} · {self.status}'

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f'TXN-{secrets.token_hex(8).upper()}'
        super().save(*args, **kwargs)

    def mark_success(self, gateway_reference=''):
        self.status = self.Status.SUCCESS
        if gateway_reference:
            self.gateway_reference = gateway_reference
        self.save(update_fields=['status', 'gateway_reference', 'updated_at'])

    def mark_failed(self, reason=''):
        self.status = self.Status.FAILED
        self.failure_reason = reason
        self.save(update_fields=['status', 'failure_reason', 'updated_at'])