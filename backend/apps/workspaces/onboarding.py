"""Shared helpers for creating a workspace and handing off to onboarding billing."""
from django.db import transaction

from .models import Workspace, WorkspaceMembership


def default_plan():
    """`starter` if it exists and is active, else the first active plan, else None."""
    from apps.billing.models import BillingPlan

    active = BillingPlan.objects.filter(is_active=True)
    return active.filter(slug='starter').first() or active.order_by('price_monthly', 'id').first()


def create_workspace_for(user, name, plan=None):
    """Create an inactive workspace owned by ``user`` (atomic)."""
    from .permissions import set_active_workspace

    with transaction.atomic():
        workspace = Workspace.objects.create(
            name=name[:200],
            owner=user,
            plan=plan or default_plan(),
            is_active=False,
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=user,
            role=WorkspaceMembership.Role.OWNER,
        )
        set_active_workspace(user, workspace)
    return workspace


def prepare_onboarding_session(request, workspace):
    """
    Point the session-based billing onboarding flow at ``workspace``.
    billing's onboarding view creates the pending Transaction itself.
    """
    request.session['onboarding_workspace_id'] = workspace.id
    last_txn = workspace.transactions.order_by('-created_at').first()
    if last_txn is not None:
        request.session['onboarding_transaction_id'] = last_txn.id
    else:
        request.session.pop('onboarding_transaction_id', None)
