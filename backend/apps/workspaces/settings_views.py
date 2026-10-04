"""
Workspace settings, team management (invites / roles / removal), workspace
switching & creation, and the danger zone (delete workspace / account).
"""
import json
import logging
import re
import secrets

import requests
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import MemberRoleForm, WorkspaceCreateForm, WorkspaceInviteForm
from .invites import (
    PENDING_INVITE_SESSION_KEY, InviteError, accept_invite, get_pending_invite,
    invite_accept_url, send_invite_email,
)
from .models import WorkspaceInvite, WorkspaceMembership, _invite_expiry, _invite_token
from .net import UnsafeURLError, validate_public_url
from .onboarding import create_workspace_for, default_plan, prepare_onboarding_session
from .permissions import (
    ADMIN_ROLES, current_membership, require_workspace_admin,
    require_workspace_owner, set_active_workspace,
)
from .public_urls import widget_urls

logger = logging.getLogger(__name__)

HEX_COLOR_RE = re.compile(r'^#(?:[0-9a-fA-F]{3}){1,2}$')
ADMIN_SECTIONS = {'workspace', 'webhook', 'test_webhook', 'regenerate_workspace_token'}


def _profile(user):
    from apps.accounts.models import UserProfile

    profile = getattr(user, 'profile', None)
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(
            user=user, defaults={'full_name': user.get_full_name() or user.first_name},
        )
    return profile


def _current(request):
    """(workspace, membership) for the logged-in user, or (None, None)."""
    membership = current_membership(request.user)
    return (membership.workspace, membership) if membership else (None, None)


def _no_workspace_redirect(request):
    messages.info(request, 'Create a workspace first.')
    return redirect('workspace_create')


@login_required
def workspace_settings(request):
    workspace, membership = _current(request)
    if workspace is None:
        return _no_workspace_redirect(request)
    profile = _profile(request.user)

    if request.method == 'POST':
        section = request.POST.get('section') or 'workspace'
        if section in ADMIN_SECTIONS:
            require_workspace_admin(request, workspace)

        if section == 'workspace':
            name = (request.POST.get('name') or '').strip()
            brand = (request.POST.get('brand_color') or '').strip()
            if name:
                workspace.name = name[:200]
            if brand and HEX_COLOR_RE.match(brand):
                workspace.brand_color = brand
            elif brand:
                messages.error(request, 'Brand color must be a hex value like #0F766E.')
                return redirect('settings')
            workspace.save(update_fields=['name', 'brand_color', 'updated_at'])
            messages.success(request, 'Workspace settings saved.')

        elif section == 'webhook':
            url = (request.POST.get('webhook_url') or '').strip()
            if url:
                try:
                    url = validate_public_url(url)
                except UnsafeURLError as exc:
                    messages.error(request, f'Webhook not saved: {exc}')
                    return redirect('settings')
                if len(url) > 200:
                    messages.error(request, 'Webhook not saved: the URL is too long (max 200 characters).')
                    return redirect('settings')
            workspace.webhook_url = url
            workspace.save(update_fields=['webhook_url', 'updated_at'])
            messages.success(request, 'Webhook URL saved.' if url else 'Webhook removed.')

        elif section == 'test_webhook':
            _send_test_webhook(request, workspace)

        elif section == 'account':
            full_name = (request.POST.get('full_name') or '').strip()[:150]
            profile.full_name = full_name
            profile.save(update_fields=['full_name'])
            parts = full_name.split(' ', 1)
            request.user.first_name = parts[0][:150] if parts else ''
            request.user.last_name = parts[1][:150] if len(parts) > 1 else ''
            request.user.save(update_fields=['first_name', 'last_name'])
            messages.success(request, 'Profile updated.')

        elif section == 'regenerate_workspace_token':
            workspace.widget_token = secrets.token_urlsafe(24)
            workspace.save(update_fields=['widget_token', 'updated_at'])
            messages.success(request, 'Team widget token regenerated. Update embeds that use the old token.')

        else:
            messages.error(request, 'Unknown settings action.')

        return redirect('settings')

    memberships = (
        WorkspaceMembership.objects
        .filter(workspace=workspace)
        .select_related('user', 'user__profile')
        .order_by('created_at')
    )
    is_admin = membership.role in ADMIN_ROLES
    pending_invites = (
        workspace.invites.filter(accepted_at__isnull=True, expires_at__gt=timezone.now())
        if is_admin else WorkspaceInvite.objects.none()
    )
    my_workspaces = (
        WorkspaceMembership.objects.filter(user=request.user)
        .select_related('workspace').order_by('created_at')
    )

    urls = widget_urls(request)
    return render(request, 'workspaces/settings.html', {
        'workspace': workspace,
        'profile': profile,
        'membership': membership,
        'is_admin': is_admin,
        'is_owner': membership.role == WorkspaceMembership.Role.OWNER,
        'memberships': memberships,
        'pending_invites': pending_invites,
        'my_workspaces': my_workspaces,
        'invite_form': WorkspaceInviteForm(),
        'role_choices': MemberRoleForm.base_fields['role'].choices,
        'team_embed_snippet': workspace.team_embed_snippet(
            widget_url=urls['widget'], api_base=urls['api']
        ),
        'public_app_url': urls['app'],
    })


def _send_test_webhook(request, workspace):
    url = workspace.webhook_url
    if not url:
        messages.error(request, 'Save a webhook URL first.')
        return
    try:
        validate_public_url(url)
    except UnsafeURLError as exc:
        messages.error(request, f'Test not sent: {exc}')
        return
    body = {
        'event': 'test',
        'workspace': workspace.name,
        'workspace_id': workspace.id,
        'timestamp': timezone.now().isoformat(),
        'data': {'title': 'Test webhook from LiftBot', 'message': 'Your webhook is connected.'},
    }
    try:
        resp = requests.post(
            url,
            data=json.dumps(body),
            headers={'Content-Type': 'application/json', 'User-Agent': 'LiftBot-Webhook/1.0'},
            timeout=8,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        logger.info('Test webhook failed for workspace %s: %s', workspace.id, exc)
        messages.error(request, 'Test webhook failed: could not connect to that URL.')
        return
    if 200 <= resp.status_code < 300:
        messages.success(request, f'Test webhook delivered (HTTP {resp.status_code}).')
    else:
        messages.error(request, f'Test webhook sent, but the endpoint responded with HTTP {resp.status_code}.')


# ---------------------------------------------------------------- team


@login_required
@require_POST
def team_invite(request):
    workspace, _ = _current(request)
    if workspace is None:
        return _no_workspace_redirect(request)
    require_workspace_admin(request, workspace)

    form = WorkspaceInviteForm(request.POST)
    if not form.is_valid():
        first_error = next(iter(form.errors.values()))[0]
        messages.error(request, f'Invite not sent: {first_error}')
        return redirect('settings')

    email = form.cleaned_data['email']
    role = form.cleaned_data['role']
    if WorkspaceMembership.objects.filter(workspace=workspace, user__email__iexact=email).exists():
        messages.info(request, f'{email} is already a member of this workspace.')
        return redirect('settings')

    invite = workspace.invites.filter(email__iexact=email, accepted_at__isnull=True).first()
    if invite is None:
        invite = WorkspaceInvite.objects.create(
            workspace=workspace, email=email, role=role, invited_by=request.user,
        )
    else:
        # Re-invite: fresh token + expiry, latest role wins.
        invite.role = role
        invite.invited_by = request.user
        invite.token = _invite_token()
        invite.expires_at = _invite_expiry()
        invite.save(update_fields=['role', 'invited_by', 'token', 'expires_at'])

    try:
        send_invite_email(request, invite)
    except Exception:  # noqa: BLE001
        logger.exception('Invite email failed for workspace %s', workspace.id)
        messages.warning(
            request,
            f'Invite created, but the email could not be sent. Share this link with {email}: '
            f'{invite_accept_url(request, invite)}',
        )
    else:
        messages.success(request, f'Invite sent to {email}.')
    return redirect('settings')


@login_required
@require_POST
def team_invite_revoke(request, invite_id):
    workspace, _ = _current(request)
    if workspace is None:
        return _no_workspace_redirect(request)
    require_workspace_admin(request, workspace)
    invite = get_object_or_404(WorkspaceInvite, pk=invite_id, workspace=workspace, accepted_at__isnull=True)
    invite.delete()
    messages.success(request, f'Invite for {invite.email} revoked.')
    return redirect('settings')


@login_required
@require_POST
def team_member_remove(request, membership_id):
    workspace, _ = _current(request)
    if workspace is None:
        return _no_workspace_redirect(request)
    require_workspace_admin(request, workspace)
    target = get_object_or_404(WorkspaceMembership, pk=membership_id, workspace=workspace)
    if target.role == WorkspaceMembership.Role.OWNER or target.user_id == workspace.owner_id:
        messages.error(request, 'The workspace owner cannot be removed.')
        return redirect('settings')
    email = target.user.email
    removed_self = target.user_id == request.user.id
    target.delete()
    messages.success(request, 'You left the workspace.' if removed_self else f'{email} was removed from the workspace.')
    return redirect('dashboard' if removed_self else 'settings')


@login_required
@require_POST
def team_member_role(request, membership_id):
    workspace, _ = _current(request)
    if workspace is None:
        return _no_workspace_redirect(request)
    require_workspace_admin(request, workspace)
    target = get_object_or_404(WorkspaceMembership, pk=membership_id, workspace=workspace)
    if target.role == WorkspaceMembership.Role.OWNER or target.user_id == workspace.owner_id:
        messages.error(request, "The owner's role cannot be changed.")
        return redirect('settings')
    form = MemberRoleForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Choose a valid role.')
        return redirect('settings')
    target.role = form.cleaned_data['role']
    target.save(update_fields=['role'])
    messages.success(request, f'{target.user.email} is now {target.get_role_display()}.')
    return redirect('settings')


def invite_accept(request, token):
    """Landing page for /invite/<token>/ links."""
    invite = get_pending_invite(token)
    if invite is None:
        return render(request, 'accounts/invite_accept.html', {'state': 'invalid'}, status=404)

    if not request.user.is_authenticated:
        # Remember the invite; login/signup (+ email verify) will accept it.
        request.session[PENDING_INVITE_SESSION_KEY] = invite.token
        return render(request, 'accounts/invite_accept.html', {'state': 'anonymous', 'invite': invite})

    if (request.user.email or '').lower() != invite.email.lower():
        return render(request, 'accounts/invite_accept.html', {'state': 'mismatch', 'invite': invite}, status=403)

    if request.method == 'POST':
        try:
            accept_invite(invite, request.user)
        except InviteError as exc:
            messages.error(request, str(exc))
            return redirect('dashboard')
        request.session.pop(PENDING_INVITE_SESSION_KEY, None)
        messages.success(request, f'You joined the {invite.workspace.name} workspace.')
        return redirect('dashboard')

    return render(request, 'accounts/invite_accept.html', {'state': 'confirm', 'invite': invite})


# ---------------------------------------------------------------- workspaces


@login_required
@require_POST
def workspace_switch(request):
    membership = WorkspaceMembership.objects.filter(
        user=request.user, workspace_id=request.POST.get('workspace_id') or 0,
    ).select_related('workspace').first()
    if membership is None:
        messages.error(request, 'You are not a member of that workspace.')
        return redirect('settings')
    set_active_workspace(request.user, membership.workspace)
    messages.success(request, f'Switched to {membership.workspace.name}.')
    return redirect('dashboard')


@login_required
def workspace_create(request):
    form = WorkspaceCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        plan = default_plan()
        if plan is None:
            logger.error('Workspace creation blocked: no active BillingPlan configured.')
            form.add_error(None, 'Workspaces cannot be created right now because no plan is configured. Please contact support.')
        else:
            workspace = create_workspace_for(request.user, form.cleaned_data['name'], plan)
            prepare_onboarding_session(request, workspace)
            messages.success(request, 'Workspace created. Complete payment to activate it.')
            return redirect('billing_onboarding')
    return render(request, 'accounts/workspace_create.html', {'form': form})


@login_required
@require_POST
def workspace_delete(request):
    workspace, _ = _current(request)
    if workspace is None:
        return _no_workspace_redirect(request)
    require_workspace_owner(request, workspace)
    confirm = (request.POST.get('confirm_name') or '').strip()
    if confirm != workspace.name.strip():
        messages.error(request, 'Workspace not deleted: type the workspace name exactly to confirm.')
        return redirect('settings')
    name = workspace.name
    with transaction.atomic():
        workspace.delete()
    logger.info('Workspace "%s" deleted by user %s', name, request.user.pk)
    messages.success(request, f'Workspace "{name}" and all its data were deleted.')
    if WorkspaceMembership.objects.filter(user=request.user).exists():
        return redirect('dashboard')
    return redirect('workspace_create')


@login_required
@require_POST
def account_delete(request):
    user = request.user
    password = request.POST.get('password') or ''
    confirm = (request.POST.get('confirm') or '').strip()
    if confirm != 'DELETE' or not user.check_password(password):
        messages.error(request, 'Account not deleted: enter your password and type DELETE to confirm.')
        return redirect('settings')
    owned = list(user.owned_workspaces.values_list('name', flat=True))
    user_id = user.pk
    logout(request)
    with transaction.atomic():
        get_user_model().objects.filter(pk=user_id).delete()
    logger.info('User %s deleted their account (owned workspaces: %s)', user_id, owned)
    messages.success(request, 'Your account has been deleted.')
    return redirect('home')
