from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.chat.constants import CAPABILITY_LABELS
from apps.chat.models import ChatSession, EmployeeTask, Message
from apps.knowledge.forms import KnowledgeSourceForm
from apps.knowledge.models import KnowledgeSource
from apps.knowledge.tasks import ingest_knowledge_source
from apps.workspaces.public_urls import widget_urls
from apps.workspaces.views import user_workspace

from .forms import AIEmployeeForm
from .models import AIEmployee


def _workspace_or_redirect(request):
    workspace = user_workspace(request.user)
    if not workspace:
        messages.error(request, 'Create a workspace first.')
        return None
    return workspace


@login_required
def employee_list(request):
    workspace = _workspace_or_redirect(request)
    if not workspace:
        return redirect('dashboard')
    employees = (
        AIEmployee.objects
        .filter(workspace=workspace)
        .annotate(knowledge_count=Count('knowledge_sources'))
    )
    active_count = sum(1 for e in employees if e.is_active)
    return render(request, 'employees/list.html', {
        'employees': employees,
        'workspace': workspace,
        'employee_count': len(employees),
        'active_count': active_count,
    })


@login_required
def employee_hire(request):
    workspace = _workspace_or_redirect(request)
    if not workspace:
        return redirect('dashboard')

    limit = workspace.plan.employee_limit if workspace.plan else 1
    if AIEmployee.objects.filter(workspace=workspace).count() >= limit:
        messages.warning(request, f'Your plan allows {limit} AI Employee(s). Upgrade to hire more.')
        return redirect('billing')

    if request.method == 'POST':
        form = AIEmployeeForm(request.POST, request.FILES)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.workspace = workspace
            if not employee.brand_color:
                employee.brand_color = workspace.brand_color
            employee.save()
            messages.success(request, f'{employee.name} has been hired.')
            return redirect('employee_hub', pk=employee.pk)
    else:
        form = AIEmployeeForm(initial={'brand_color': workspace.brand_color})

    return render(request, 'employees/form.html', {
        'form': form,
        'title': 'Hire an AI Employee',
        'is_hire': True,
        'employee': None,
        'cancel_url': 'employee_list',
    })


@login_required
def employee_detail(request, pk):
    return redirect('employee_hub', pk=pk)


@login_required
def employee_edit(request, pk):
    return redirect(f"{reverse('employee_hub', kwargs={'pk': pk})}?tab=settings")


@login_required
def employee_fire(request, pk):
    workspace = _workspace_or_redirect(request)
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)
    if request.method == 'POST':
        name = employee.name
        employee.delete()
        messages.success(request, f'{name} has been let go.')
        return redirect('employee_list')
    return render(request, 'employees/fire_confirm.html', {'employee': employee})


@login_required
def playground(request, pk):
    return redirect(f"{reverse('employee_hub', kwargs={'pk': pk})}?tab=playground")


@login_required
def playground_history(request, pk):
    workspace = _workspace_or_redirect(request)
    if not workspace:
        return JsonResponse({'messages': []})
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)
    session_id = request.GET.get('session_id')
    session = ChatSession.objects.filter(pk=session_id, employee=employee).first() if session_id else None
    if not session:
        return JsonResponse({'messages': []})
    msgs = session.messages.order_by('id').values('role', 'content')
    return JsonResponse({'session_id': session.id, 'messages': list(msgs)})


@login_required
def employee_hub(request, pk):
    workspace = _workspace_or_redirect(request)
    if not workspace:
        return redirect('dashboard')
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)

    if request.method == 'POST':
        action = request.POST.get('action', 'update_settings')

        if action == 'update_settings':
            form = AIEmployeeForm(request.POST, request.FILES, instance=employee)
            if form.is_valid():
                form.save()
                messages.success(request, f'{employee.name} settings updated successfully.')
                return redirect(f'{request.path}?tab=settings')
            else:
                messages.error(request, 'Please check the settings form for errors.')

        elif action == 'toggle_active':
            employee.is_active = not employee.is_active
            employee.save(update_fields=['is_active'])
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                return JsonResponse({'is_active': employee.is_active, 'status': 'ok'})
            status_text = 'active' if employee.is_active else 'inactive'
            messages.success(request, f'{employee.name} is now {status_text} on your website.')
            return redirect(f'{request.path}?tab=activation')

        elif action == 'add_knowledge':
            k_form = KnowledgeSourceForm(request.POST, request.FILES)
            if k_form.is_valid():
                source = k_form.save(commit=False)
                source.employee = employee
                source.save()
                ingest_knowledge_source.delay(source.id)
                messages.success(request, f'Training source "{source.title}" queued successfully.')
                return_tab = request.POST.get('return_tab', 'qa')
                return redirect(f'{request.path}?tab={return_tab}')
            else:
                errs = ' '.join([f'{f}: {e[0]}' for f, e in k_form.errors.items()])
                messages.error(request, f'Could not add material: {errs}')
                return_tab = request.POST.get('return_tab', 'qa')
                return redirect(f'{request.path}?tab={return_tab}')

        elif action == 'delete_knowledge':
            source_id = request.POST.get('source_id')
            source = get_object_or_404(KnowledgeSource, pk=source_id, employee=employee)
            title = source.title
            source.delete()
            messages.success(request, f'Training material "{title}" removed.')
            return_tab = request.POST.get('return_tab', 'qa')
            return redirect(f'{request.path}?tab={return_tab}')

    settings_form = AIEmployeeForm(instance=employee)
    knowledge_form = KnowledgeSourceForm()
    urls = widget_urls(request)

    sources = list(employee.knowledge_sources.all().order_by('-created_at'))
    qa_sources = [s for s in sources if s.source_type == KnowledgeSource.SourceType.FAQ]
    web_sources = [s for s in sources if s.source_type == KnowledgeSource.SourceType.URL]
    file_sources = [s for s in sources if s.source_type == KnowledgeSource.SourceType.PDF]
    text_sources = [s for s in sources if s.source_type == KnowledgeSource.SourceType.TEXT]
    ready_count = sum(1 for s in sources if s.status == KnowledgeSource.Status.READY)
    chunk_total = sum(s.chunk_count for s in sources)

    all_employees = AIEmployee.objects.filter(workspace=workspace).order_by('name')

    session_count = ChatSession.objects.filter(employee=employee).count()
    message_count = Message.objects.filter(session__employee=employee).count()
    task_count = EmployeeTask.objects.filter(employee=employee).count()
    open_task_count = EmployeeTask.objects.filter(employee=employee, status__in=['open', 'in_progress']).count()

    recent_sessions = (
        ChatSession.objects.filter(employee=employee)
        .prefetch_related('messages')
        .order_by('-last_message_at')[:8]
    )
    recent_tasks = EmployeeTask.objects.filter(employee=employee).order_by('-created_at')[:6]

    active_tab = request.GET.get('tab', 'playground')

    context = {
        'employee': employee,
        'workspace': workspace,
        'all_employees': all_employees,
        'form': settings_form,
        'knowledge_form': knowledge_form,
        'public_widget_api': urls['api'],
        'widget_url': urls['widget'],
        'embed_snippet': employee.embed_snippet(widget_url=urls['widget'], api_base=urls['api']),
        'team_embed_snippet': workspace.team_embed_snippet(widget_url=urls['widget'], api_base=urls['api']),
        'sources': sources,
        'qa_sources': qa_sources,
        'web_sources': web_sources,
        'file_sources': file_sources,
        'text_sources': text_sources,
        'ready_count': ready_count,
        'source_count': len(sources),
        'chunk_total': chunk_total,
        'session_count': session_count,
        'message_count': message_count,
        'task_count': task_count,
        'open_task_count': open_task_count,
        'recent_sessions': recent_sessions,
        'recent_tasks': recent_tasks,
        'active_tab': active_tab,
        'capability_items': [
            CAPABILITY_LABELS.get(c, c.replace('_', ' ').title())
            for c in (employee.capabilities or [])
        ],
    }
    return render(request, 'employees/hub.html', context)