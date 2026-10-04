from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.employees.models import AIEmployee
from apps.workspaces.views import user_workspace

from .forms import KnowledgeSourceForm
from .models import KnowledgeSource
from .tasks import queue_ingest


@login_required
def knowledge_list(request, employee_id):
    workspace = user_workspace(request.user)
    employee = get_object_or_404(AIEmployee, pk=employee_id, workspace=workspace)
    sources = list(employee.knowledge_sources.all())
    ready = sum(1 for s in sources if s.status == KnowledgeSource.Status.READY)
    chunks = sum(s.chunk_count for s in sources)
    return render(request, 'knowledge/list.html', {
        'employee': employee,
        'sources': sources,
        'source_count': len(sources),
        'ready_count': ready,
        'chunk_total': chunks,
    })


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
            queue_ingest(source)
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


@login_required
@require_POST
def knowledge_retry(request, employee_id, pk):
    workspace = user_workspace(request.user)
    employee = get_object_or_404(AIEmployee, pk=employee_id, workspace=workspace)
    source = get_object_or_404(KnowledgeSource, pk=pk, employee=employee)
    source.status = KnowledgeSource.Status.PENDING
    source.error_message = ''
    source.save(update_fields=['status', 'error_message', 'updated_at'])
    queue_ingest(source)
    messages.success(request, f'Retrying "{source.title}".')
    next_url = request.POST.get('next', '')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect('knowledge_list', employee_id=employee.id)
