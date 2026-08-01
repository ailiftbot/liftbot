from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.workspaces.views import user_workspace

from .models import Lead


@login_required
def lead_list(request):
    workspace = user_workspace(request.user)
    leads = Lead.objects.filter(workspace=workspace).select_related('employee')[:100]
    return render(request, 'leads/list.html', {'leads': leads, 'workspace': workspace})
