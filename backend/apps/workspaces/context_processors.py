from apps.employees.models import AIEmployee
from apps.workspaces.views import user_workspace


def current_workspace(request):
    ctx = {'current_workspace': None, 'primary_employee': None}
    if request.user.is_authenticated:
        workspace = user_workspace(request.user)
        ctx['current_workspace'] = workspace
        if workspace:
            ctx['primary_employee'] = (
                AIEmployee.objects.filter(workspace=workspace, is_active=True).order_by('created_at').first()
            )
    return ctx
