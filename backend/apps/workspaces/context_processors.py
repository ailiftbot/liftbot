import logging

from apps.employees.models import AIEmployee
from apps.workspaces.views import user_workspace

logger = logging.getLogger(__name__)


def current_workspace(request):
    ctx = {'current_workspace': None, 'primary_employee': None}
    try:
        if request.user.is_authenticated:
            workspace = user_workspace(request.user)
            ctx['current_workspace'] = workspace
            if workspace:
                ctx['primary_employee'] = (
                    AIEmployee.objects.filter(workspace=workspace, is_active=True).order_by('created_at').first()
                )
    except Exception:
        logger.exception('current_workspace context processor failed')
    return ctx
