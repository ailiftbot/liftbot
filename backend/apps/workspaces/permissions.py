"""
Workspace role checks.

    from apps.workspaces.permissions import require_workspace_admin

    membership = require_workspace_admin(request)          # current workspace
    membership = require_workspace_admin(request, workspace)

Raises ``django.core.exceptions.PermissionDenied`` (-> 403) unless the
logged-in user is an owner/admin of the workspace.
"""
from django.core.exceptions import PermissionDenied

from .models import WorkspaceMembership

ADMIN_ROLES = (WorkspaceMembership.Role.OWNER, WorkspaceMembership.Role.ADMIN)


def current_membership(user):
    """
    The membership ``user_workspace()`` resolves to: the profile's
    ``active_workspace`` when the user is still a member of it, otherwise the
    oldest membership.
    """
    if not getattr(user, 'is_authenticated', False):
        return None
    qs = (
        WorkspaceMembership.objects
        .select_related('workspace', 'workspace__plan')
        .filter(user=user)
    )
    profile = getattr(user, 'profile', None)
    active_id = getattr(profile, 'active_workspace_id', None)
    if active_id:
        membership = qs.filter(workspace_id=active_id).first()
        if membership is not None:
            return membership
    return qs.order_by('created_at').first()


def set_active_workspace(user, workspace):
    """Make ``workspace`` the one user_workspace() returns for ``user``."""
    from apps.accounts.models import UserProfile

    profile, _ = UserProfile.objects.get_or_create(user=user)
    if profile.active_workspace_id != getattr(workspace, 'id', None):
        profile.active_workspace = workspace
        profile.save(update_fields=['active_workspace'])
    # Drop any cached reverse accessor so later lookups see the change.
    try:
        user.profile = profile
    except Exception:  # noqa: BLE001
        pass


def get_membership(user, workspace):
    if workspace is None or not getattr(user, 'is_authenticated', False):
        return None
    return WorkspaceMembership.objects.filter(user=user, workspace=workspace).first()


def is_workspace_admin(user, workspace) -> bool:
    membership = get_membership(user, workspace)
    return bool(membership and membership.role in ADMIN_ROLES)


def is_workspace_owner(user, workspace) -> bool:
    membership = get_membership(user, workspace)
    return bool(membership and membership.role == WorkspaceMembership.Role.OWNER)


def require_workspace_admin(request, workspace=None):
    """Return the owner/admin membership or raise PermissionDenied (403)."""
    if workspace is None:
        membership = current_membership(request.user)
    else:
        membership = get_membership(request.user, workspace)
    if membership is None or membership.role not in ADMIN_ROLES:
        raise PermissionDenied('Only workspace owners and admins can do that.')
    return membership


def require_workspace_owner(request, workspace=None):
    membership = require_workspace_admin(request, workspace)
    if membership.role != WorkspaceMembership.Role.OWNER:
        raise PermissionDenied('Only the workspace owner can do that.')
    return membership
