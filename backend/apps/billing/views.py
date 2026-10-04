import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.workspaces.models import Workspace
from apps.workspaces.permissions import require_workspace_admin
from apps.workspaces.views import user_workspace

from .models import BillingPlan, Invoice, Transaction

logger = logging.getLogger(__name__)


def _stripe():
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def stripe_enabled():
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_PUBLISHABLE_KEY)


@login_required
def billing_home(request):
    workspace = user_workspace(request.user)
    plans = BillingPlan.objects.filter(is_active=True)
    invoices = Invoice.objects.filter(workspace=workspace)[:20] if workspace else []
    if request.GET.get('checkout') == 'success':
        session_id = request.GET.get('session_id', '')
        if session_id and stripe_enabled() and workspace:
            try:
                session = _stripe().checkout.Session.retrieve(session_id)
                if (session.get('metadata') or {}).get('workspace_id') == str(workspace.id):
                    apply_checkout_session(session)
                    workspace.refresh_from_db()
            except Exception:  # noqa: BLE001
                logger.exception('Could not verify checkout session %s', session_id)
        messages.success(request, 'Checkout complete. Your plan has been updated.')
    return render(request, 'billing/home.html', {
        'workspace': workspace,
        'plans': plans,
        'invoices': invoices,
        'stripe_enabled': stripe_enabled(),
        'can_manage_billing': stripe_enabled() and bool(workspace and workspace.stripe_customer_id),
    })


def _plan_line_items(plan):
    if plan.stripe_price_id:
        return [{'price': plan.stripe_price_id, 'quantity': 1}]
    return [{
        'price_data': {
            'currency': 'usd',
            'product_data': {'name': f'LiftBot — {plan.name}'},
            'unit_amount': int(plan.price_monthly * 100),
            'recurring': {'interval': 'month'},
        },
        'quantity': 1,
    }]


def _ensure_customer(stripe, workspace, email):
    if not workspace.stripe_customer_id:
        customer = stripe.Customer.create(
            email=email,
            name=workspace.name,
            metadata={'workspace_id': workspace.id},
        )
        workspace.stripe_customer_id = customer.id
        workspace.save(update_fields=['stripe_customer_id', 'updated_at'])
    return workspace.stripe_customer_id


def _create_checkout_session(request, workspace, plan, success_url, cancel_url, txn=None):
    stripe = _stripe()
    metadata = {'workspace_id': str(workspace.id), 'plan_slug': plan.slug}
    if txn is not None:
        metadata['transaction_id'] = str(txn.id)
    return stripe.checkout.Session.create(
        mode='subscription',
        customer=_ensure_customer(stripe, workspace, workspace.owner.email),
        line_items=_plan_line_items(plan),
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(workspace.id),
        metadata=metadata,
        subscription_data={'metadata': metadata},
    )


@transaction.atomic
def apply_checkout_session(session):
    """
    Activate a workspace from a completed Stripe Checkout Session.
    Called from both the success redirect and the webhook, so it must be idempotent.
    Returns the workspace, or None if the session isn't paid / doesn't match.
    """
    if session.get('status') != 'complete' or session.get('payment_status') not in ('paid', 'no_payment_required'):
        return None
    meta = session.get('metadata') or {}
    workspace = Workspace.objects.select_for_update().filter(id=meta.get('workspace_id')).first()
    plan = BillingPlan.objects.filter(slug=meta.get('plan_slug')).first()
    if not workspace or not plan:
        return None

    session_id = session.get('id', '')
    if Invoice.objects.filter(workspace=workspace, stripe_invoice_id=session_id).exists():
        return workspace  # already applied

    old_subscription = workspace.stripe_subscription_id
    new_subscription = session.get('subscription') or ''
    workspace.plan = plan
    workspace.is_active = True
    workspace.stripe_subscription_id = new_subscription
    if session.get('customer'):
        workspace.stripe_customer_id = session['customer']
    workspace.save(update_fields=[
        'plan', 'is_active', 'stripe_subscription_id', 'stripe_customer_id', 'updated_at',
    ])

    txn_id = meta.get('transaction_id')
    if txn_id:
        txn = Transaction.objects.filter(id=txn_id, workspace=workspace).first()
        if txn and txn.status != Transaction.Status.SUCCESS:
            txn.plan = plan
            txn.gateway = 'stripe'
            txn.save(update_fields=['plan', 'gateway', 'updated_at'])
            txn.mark_success(gateway_reference=session_id)

    today = date.today()
    Invoice.objects.create(
        workspace=workspace,
        plan=plan,
        amount=Decimal(session.get('amount_total') or 0) / 100 if session.get('amount_total') is not None else plan.price_monthly,
        status=Invoice.Status.PAID,
        period_start=today,
        period_end=today + timedelta(days=30),
        stripe_invoice_id=session_id,
        notes='Paid via Stripe Checkout',
    )

    # Switching plans creates a new subscription — stop billing the old one.
    if old_subscription and new_subscription and old_subscription != new_subscription:
        try:
            _stripe().Subscription.cancel(old_subscription)
        except Exception:  # noqa: BLE001
            logger.exception('Could not cancel previous subscription %s', old_subscription)
    return workspace


@login_required
@require_POST
def create_checkout(request, plan_slug):
    workspace = user_workspace(request.user)
    if workspace is None:
        return redirect('dashboard')
    require_workspace_admin(request, workspace)
    plan = get_object_or_404(BillingPlan, slug=plan_slug, is_active=True)
    if not stripe_enabled():
        if not (settings.DEBUG or request.user.is_staff):
            messages.error(request, 'Online payments are not configured yet. Please contact support to change plans.')
            return redirect('billing')
        workspace.plan = plan
        workspace.save(update_fields=['plan', 'updated_at'])
        messages.success(request, f'Plan set to {plan.name} (manual — add Stripe keys for checkout).')
        return redirect('billing')

    billing_url = request.build_absolute_uri(reverse('billing'))
    try:
        session = _create_checkout_session(
            request, workspace, plan,
            success_url=billing_url + '?checkout=success&session_id={CHECKOUT_SESSION_ID}',
            cancel_url=billing_url + '?checkout=cancel',
        )
        return redirect(session.url)
    except Exception:  # noqa: BLE001
        logger.exception('Stripe checkout failed')
        messages.error(request, 'We could not start checkout. Please try again in a moment.')
        return redirect('billing')


@login_required
@require_POST
def billing_portal(request):
    workspace = user_workspace(request.user)
    if workspace:
        require_workspace_admin(request, workspace)
    if not stripe_enabled() or not workspace or not workspace.stripe_customer_id:
        messages.error(request, 'No billing account found for this workspace yet.')
        return redirect('billing')
    try:
        portal = _stripe().billing_portal.Session.create(
            customer=workspace.stripe_customer_id,
            return_url=request.build_absolute_uri(reverse('billing')),
        )
        return redirect(portal.url)
    except Exception:  # noqa: BLE001
        logger.exception('Stripe billing portal failed')
        messages.error(request, 'Could not open the billing portal. Please try again.')
        return redirect('billing')


@csrf_exempt
@require_POST
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

    obj = event['data']['object']
    etype = event['type']
    if etype in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
        apply_checkout_session(obj)
    elif etype == 'invoice.paid' and obj.get('billing_reason') == 'subscription_cycle':
        workspace = Workspace.objects.filter(stripe_subscription_id=obj.get('subscription') or '-').first()
        if workspace and workspace.plan and not Invoice.objects.filter(stripe_invoice_id=obj.get('id')).exists():
            today = date.today()
            Invoice.objects.create(
                workspace=workspace,
                plan=workspace.plan,
                amount=Decimal(obj.get('amount_paid') or 0) / 100,
                status=Invoice.Status.PAID,
                period_start=today,
                period_end=today + timedelta(days=30),
                stripe_invoice_id=obj.get('id', ''),
                notes='Subscription renewal',
            )
            # New billing period — reset usage counters.
            workspace.conversations_used = 0
            workspace.tokens_used = 0
            workspace.save(update_fields=['conversations_used', 'tokens_used', 'updated_at'])
    elif etype == 'customer.subscription.deleted':
        workspace = Workspace.objects.filter(stripe_subscription_id=obj.get('id') or '-').first()
        if workspace:
            workspace.is_active = False
            workspace.stripe_subscription_id = ''
            workspace.save(update_fields=['is_active', 'stripe_subscription_id', 'updated_at'])
    return HttpResponse(status=200)


@login_required
@require_POST
def save_webhook(request):
    from apps.workspaces.net import is_safe_public_url

    workspace = user_workspace(request.user)
    if workspace:
        require_workspace_admin(request, workspace)
        url = (request.POST.get('webhook_url') or '').strip()
        if url and not is_safe_public_url(url):
            messages.error(request, 'Webhook URL must be a public https:// address.')
            return redirect('settings')
        workspace.webhook_url = url
        workspace.save(update_fields=['webhook_url', 'updated_at'])
        messages.success(request, 'Webhook URL saved.')
    return redirect('settings')


def _onboarding_context(request):
    """Workspace + pending transaction for the pre-dashboard payment step (session based)."""
    workspace_id = request.session.get('onboarding_workspace_id')
    if not workspace_id:
        return None, None
    workspace = Workspace.objects.select_related('plan', 'owner').filter(id=workspace_id).first()
    if workspace is None:
        return None, None
    txn = workspace.transactions.order_by('-created_at').first()
    if txn is None or txn.status == Transaction.Status.FAILED:
        plan = workspace.plan or BillingPlan.objects.filter(is_active=True).first()
        txn = Transaction.objects.create(
            workspace=workspace,
            plan=plan,
            amount=plan.price_monthly,
            status=Transaction.Status.PENDING,
        )
    request.session['onboarding_transaction_id'] = txn.id
    return workspace, txn


def _finish_onboarding(request, workspace):
    request.session.pop('onboarding_workspace_id', None)
    request.session.pop('onboarding_transaction_id', None)
    if not request.user.is_authenticated:
        login(request, workspace.owner, backend='django.contrib.auth.backends.ModelBackend')
    elif request.user.pk != workspace.owner_id:
        return redirect('login')
    messages.success(request, 'Payment successful — your workspace is active. Welcome to LiftBot!')
    return redirect('dashboard')


def onboarding_billing_view(request):
    """
    Post-signup billing page. Session-based on purpose — the user may not be
    logged in yet at this point in the flow.
    """
    workspace, txn = _onboarding_context(request)
    if workspace is None:
        return redirect('signup')
    if workspace.is_active:
        return _finish_onboarding(request, workspace)

    return render(request, 'billing/onboarding.html', {
        'workspace': workspace,
        'plan': txn.plan,
        'plans': BillingPlan.objects.filter(is_active=True),
        'transaction': txn,
        'stripe_enabled': stripe_enabled(),
        'simulated': not stripe_enabled() and settings.DEBUG,
    })


@require_POST
def onboarding_initiate_payment(request):
    workspace, txn = _onboarding_context(request)
    if workspace is None:
        return redirect('signup')
    if workspace.is_active:
        return _finish_onboarding(request, workspace)

    plan_slug = request.POST.get('plan')
    if plan_slug:
        plan = BillingPlan.objects.filter(slug=plan_slug, is_active=True).first()
        if plan and plan.id != txn.plan_id:
            txn.plan = plan
            txn.amount = plan.price_monthly
            txn.save(update_fields=['plan', 'amount', 'updated_at'])

    if stripe_enabled():
        status_url = request.build_absolute_uri(reverse('billing_onboarding_status'))
        try:
            session = _create_checkout_session(
                request, workspace, txn.plan,
                success_url=status_url + '?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=request.build_absolute_uri(reverse('billing_onboarding')),
                txn=txn,
            )
        except Exception:  # noqa: BLE001
            logger.exception('Stripe onboarding checkout failed for workspace %s', workspace.pk)
            messages.error(request, 'We could not start checkout. Please try again in a moment.')
            return redirect('billing_onboarding')
        txn.gateway = 'stripe'
        txn.gateway_reference = session.id
        txn.save(update_fields=['gateway', 'gateway_reference', 'updated_at'])
        return redirect(session.url)

    if not settings.DEBUG:
        logger.error('Onboarding payment attempted but Stripe is not configured')
        messages.error(request, 'Online payments are temporarily unavailable. Please contact support.')
        return redirect('billing_onboarding')

    # Local development without Stripe keys: simulate the gateway.
    if request.POST.get('simulate') == 'failed':
        txn.mark_failed(reason='Simulated failure (development mode).')
        messages.error(request, 'Payment failed. Please try again.')
        return redirect('billing_onboarding')
    txn.gateway = 'simulated'
    txn.save(update_fields=['gateway', 'updated_at'])
    txn.mark_success(gateway_reference=f'SIMULATED-{txn.reference}')
    workspace.plan = txn.plan
    workspace.is_active = True
    workspace.save(update_fields=['plan', 'is_active', 'updated_at'])
    return _finish_onboarding(request, workspace)


def onboarding_payment_status(request):
    """Stripe redirects here after checkout. Verify the session server-side before trusting it."""
    workspace, txn = _onboarding_context(request)
    if workspace is None:
        return redirect('signup')

    session_id = request.GET.get('session_id', '')
    if not workspace.is_active and session_id and stripe_enabled():
        try:
            session = _stripe().checkout.Session.retrieve(session_id)
        except Exception:  # noqa: BLE001
            logger.exception('Could not retrieve checkout session %s', session_id)
            session = None
        meta = (session.get('metadata') or {}) if session else {}
        if session and meta.get('workspace_id') == str(workspace.id):
            apply_checkout_session(session)
            workspace.refresh_from_db()

    if workspace.is_active:
        return _finish_onboarding(request, workspace)

    messages.info(request, 'Your payment is still processing. This page will update once it is confirmed.')
    return redirect('billing_onboarding')
