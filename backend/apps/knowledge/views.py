from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.employees.models import AIEmployee
from apps.workspaces.views import user_workspace

from .forms import KnowledgeSourceForm
from .models import KnowledgeSource
from .tasks import ingest_knowledge_source


@login_required
def knowledge_list(request, employee_id):
    workspace = user_workspace(request.user)
    employee = get_object_or_404(AIEmployee, pk=employee_id, workspace=workspace)
    sources = employee.knowledge_sources.all()
    return render(request, 'knowledge/list.html', {'employee': employee, 'sources': sources})


@login_required
def knowledge_add(request, employee_id):
    workspace = user_workspace(request.user)
    employee = get_object_or_404(AIEmployee, pk=employee_id, workspace=workspace)

    if request.method == 'POST':
        form = KnowledgeSourceForm(request.POST, request.FILES)
        if form.is_valid():
            source = form.save(commit=False)
            source.employee = employee
            source.save()
            ingest_knowledge_source.delay(source.id)
            messages.success(request, 'Training material queued. Your AI Employee is learning.')
            return redirect('knowledge_list', employee_id=employee.id)
    else:
        form = KnowledgeSourceForm()

    return render(request, 'knowledge/form.html', {'form': form, 'employee': employee})


@login_required
def knowledge_delete(request, employee_id, pk):
    workspace = user_workspace(request.user)
    employee = get_object_or_404(AIEmployee, pk=employee_id, workspace=workspace)
    source = get_object_or_404(KnowledgeSource, pk=pk, employee=employee)
    if request.method == 'POST':
        source.delete()
        messages.success(request, 'Training material removed.')
        return redirect('knowledge_list', employee_id=employee.id)
    return render(request, 'knowledge/delete_confirm.html', {'source': source, 'employee': employee})
