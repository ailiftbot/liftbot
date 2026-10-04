"""Workspace team invites: lookup, acceptance and email."""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .models import WorkspaceInvite, WorkspaceMembership

logger = logging.getLogger(__name__)

PENDING_INVITE_SESSION_KEY = 'pending_invite_token'


class InviteError(Exception):
    pass


def get_pending_invite(token):
    """A not-yet-accepted, unexpired invite for ``token`` or None."""
    if not token:
        return None
    invite = (
        WorkspaceInvite.objects.select_related('workspace', 'invited_by')
        .filter(token=token)
        .first()
    )
    if invite is None or not invite.is_pending:
        return None
    return invite


def session_invite(request):
    """The pending invite stored in the session (cleans the key if it went stale)."""
    token = request.session.get(PENDING_INVITE_SESSION_KEY)
    if not token:
        return None
    invite = get_pending_invite(token)
    if invite is None:
        request.session.pop(PENDING_INVITE_SESSION_KEY, None)
    return invite


def accept_invite(invite, user):
    """Create (or upgrade) the membership and mark the invite accepted."""
    from .permissions import set_active_workspace

    if (user.email or '').strip().lower() != invite.email.strip().lower():
        raise InviteError('This invite was sent to a different email address.')
    if not invite.is_pending:
        raise InviteError('This invite has expired or was already used.')
    with transaction.atomic():
        try:
            membership, created = WorkspaceMembership.objects.get_or_create(
                workspace=invite.workspace,
                user=user,
                defaults={'role': invite.role},
            )
        except IntegrityError:
            membership = WorkspaceMembership.objects.get(workspace=invite.workspace, user=user)
        invite.accepted_at = timezone.now()
        invite.save(update_fields=['accepted_at'])
        # Accept any other open invites to the same workspace for this email.
        WorkspaceInvite.objects.filter(
            workspace=invite.workspace, email__iexact=invite.email, accepted_at__isnull=True,
        ).update(accepted_at=invite.accepted_at)
    set_active_workspace(user, invite.workspace)
    return membership


def accept_session_invite(request, user):
    """
    Accept the invite stored in the session if it matches ``user``'s email.
    Returns the workspace joined, or None.
    """
    invite = session_invite(request)
    if invite is None:
        return None
    try:
        accept_invite(invite, user)
    except InviteError:
        return None
    request.session.pop(PENDING_INVITE_SESSION_KEY, None)
    return invite.workspace


def invite_accept_url(request, invite):
    path = reverse('invite_accept', args=[invite.token])
    if request is not None:
        return request.build_absolute_uri(path)
    return f"{settings.PUBLIC_APP_URL.rstrip('/')}{path}"


def send_invite_email(request, invite):
    inviter = invite.invited_by
    inviter_name = ''
    if inviter is not None:
        profile = getattr(inviter, 'profile', None)
        inviter_name = (getattr(profile, 'full_name', '') or inviter.get_full_name() or inviter.email)
    link = invite_accept_url(request, invite)
    send_mail(
        subject=f'You have been invited to {invite.workspace.name} on LiftBot',
        message=(
            f'Hi,\n\n'
            f'{inviter_name or "A teammate"} invited you to join the "{invite.workspace.name}" '
            f'workspace on LiftBot as {invite.get_role_display()}.\n\n'
            f'Accept the invite:\n{link}\n\n'
            f'This link expires on {invite.expires_at:%b %d, %Y}.\n'
            f'If you were not expecting this, you can ignore this email.\n'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[invite.email],
        fail_silently=False,
    )
    return link
