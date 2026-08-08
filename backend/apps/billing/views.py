import logging
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.workspaces.views import user_workspace

from .models import BillingPlan, Invoice

logger = logging.getLogger(__name__)


def _stripe():
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


@login_required
def billing_home(request):
    workspace = user_workspace(request.user)
    plans = BillingPlan.objects.filter(is_active=True)
    invoices = Invoice.objects.filter(workspace=workspace)[:20] if workspace else []
    if request.GET.get('checkout') == 'success':
        messages.success(request, 'Checkout complete. Your plan will update shortly.')
    return render(request, 'billing/home.html', {
        'workspace': workspace,
        'plans': plans,
        'invoices': invoices,
        'stripe_enabled': bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_PUBLISHABLE_KEY),
    })


@login_required
@require_POST
def create_checkout(request, plan_slug):
    workspace = user_workspace(request.user)
    plan = get_object_or_404(BillingPlan, slug=plan_slug, is_active=True)
    if not settings.STRIPE_SECRET_KEY:
        workspace.plan = plan
        workspace.save(update_fields=['plan', 'updated_at'])
        messages.success(request, f'Plan set to {plan.name} (manual — add Stripe keys for checkout).')
        return redirect('billing')

    stripe = _stripe()
    try:
        if not workspace.stripe_customer_id:
            customer = stripe.Customer.create(
                email=request.user.email,
                name=workspace.name,
                metadata={'workspace_id': workspace.id},
            )
            workspace.stripe_customer_id = customer.id
            workspace.save(update_fields=['stripe_customer_id', 'updated_at'])

        if plan.stripe_price_id:
            line_items = [{'price': plan.stripe_price_id, 'quantity': 1}]
        else:
            line_items = [{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': f'LiftBot — {plan.name}'},
                    'unit_amount': int(plan.price_monthly * 100),
                    'recurring': {'interval': 'month'},
                },
                'quantity': 1,
            }]

        session = stripe.checkout.Session.create(
            mode='subscription',
            customer=workspace.stripe_customer_id,
            line_items=line_items,
            success_url=request.build_absolute_uri(reverse('billing')) + '?checkout=success',
            cancel_url=request.build_absolute_uri(reverse('billing')) + '?checkout=cancel',
            metadata={'workspace_id': str(workspace.id), 'plan_slug': plan.slug},
        )
        return redirect(session.url)
    except Exception as exc:  # noqa: BLE001
        logger.exception('Stripe checkout failed')
        messages.error(request, f'Checkout failed: {exc}')
        return redirect('billing')


@csrf_exempt
def stripe_webhook(request):
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_WEBHOOK_SECRET:
        return HttpResponse(status=400)
    stripe = _stripe()
    payload = request.body
    sig = request.META.get('HTTP_STRIPE_SIGNATURE', '')
    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.STRIPE_WEBHOOK_SECRET)
    except Exception:  # noqa: BLE001
        return HttpResponse(status=400)

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        meta = session.get('metadata') or {}
        from apps.workspaces.models import Workspace
        workspace = Workspace.objects.filter(id=meta.get('workspace_id')).first()
        plan = BillingPlan.objects.filter(slug=meta.get('plan_slug')).first()
        if workspace and plan:
            workspace.plan = plan
            workspace.stripe_subscription_id = session.get('subscription') or ''
            workspace.save(update_fields=['plan', 'stripe_subscription_id', 'updated_at'])
            today = date.today()
            Invoice.objects.create(
                workspace=workspace,
                plan=plan,
                amount=plan.price_monthly,
                status=Invoice.Status.PAID,
                period_start=today,
                period_end=today + timedelta(days=30),
                stripe_invoice_id=session.get('invoice') or session.get('id', ''),
                notes='Paid via Stripe Checkout',
            )
    return HttpResponse(status=200)


@login_required
@require_POST
def save_webhook(request):
    workspace = user_workspace(request.user)
    if workspace:
        workspace.webhook_url = (request.POST.get('webhook_url') or '').strip()
        workspace.save(update_fields=['webhook_url', 'updated_at'])
        messages.success(request, 'Webhook URL saved.')
    return redirect('settings')
