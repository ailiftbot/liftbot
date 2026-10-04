from apps.employees.models import AIEmployee
from apps.workspaces.views import user_workspace

# Sidebar groups for the AI Employee hub tabs (see apps.employees.views.HUB_TABS).
HUB_NAV_GROUPS = {
    'playground': 'playground',
    'knowledge': 'knowledge',
    'add-material': 'knowledge',
    'settings': 'personality',
    'capabilities': 'personality',
    'status': 'personality',
    'widget-code': 'widget',
    'analytics': 'employee',
    'team': 'employee',
}


def _viewed_employee_pk(request):
    """pk of the AI Employee the current page is about, if any."""
    match = getattr(request, 'resolver_match', None)
    if not match:
        return None
    name = match.url_name or ''
    if name.startswith('employee_') or name.startswith('playground_'):
        pk = match.kwargs.get('pk')
    elif name.startswith('knowledge_'):
        pk = match.kwargs.get('employee_id')
    else:
        pk = None
    try:
        return int(pk) if pk is not None else None
    except (TypeError, ValueError):
        return None


def current_workspace(request):
    ctx = {'current_workspace': None, 'primary_employee': None, 'hub_nav': ''}
    if not request.user.is_authenticated:
        return ctx

    workspace = user_workspace(request.user)
    ctx['current_workspace'] = workspace
    if workspace:
        employees = AIEmployee.objects.filter(workspace=workspace)
        primary = None
        viewed_pk = _viewed_employee_pk(request)
        if viewed_pk:
            primary = employees.filter(pk=viewed_pk).first()
        if primary is None:
            # Prefer a live employee, but fall back to any so the sidebar never dead-ends.
            primary = employees.order_by('-is_active', 'created_at').first()
        ctx['primary_employee'] = primary

    match = getattr(request, 'resolver_match', None)
    if match and match.url_name == 'employee_hub':
        from apps.employees.views import resolve_hub_tab

        ctx['hub_nav'] = HUB_NAV_GROUPS.get(resolve_hub_tab(request.GET.get('tab')), 'employee')
    return ctx
