import json
import logging

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.chat.constants import CAPABILITY_LABELS
from apps.chat.models import ChatSession, EmployeeTask, Message
from apps.knowledge.forms import KnowledgeSourceForm
from apps.knowledge.models import KnowledgeSource
from apps.knowledge.tasks import queue_ingest
from apps.leads.models import Lead
from apps.workspaces.context_processors import HUB_NAV_GROUPS
from apps.workspaces.public_urls import widget_urls
from apps.workspaces.views import user_workspace

from .forms import AIEmployeeForm
from .models import AIEmployee

logger = logging.getLogger(__name__)

# Hub tabs that have a nav item + panel in templates/employees/hub.html.
HUB_TABS = (
    'playground', 'analytics', 'settings', 'capabilities', 'status',
    'knowledge', 'add-material', 'widget-code', 'team',
)
# Legacy / shorthand tab names still linked from elsewhere.
HUB_TAB_ALIASES = {
    'qa': 'knowledge',
    'webpages': 'knowledge',
    'files': 'knowledge',
    'widget': 'widget-code',
    'profile-settings': 'settings',
    'instructions': 'settings',
    'routing': 'settings',
    'activation': 'status',
    'roster': 'team',
    'suggestions': 'analytics',
    'observability': 'analytics',
}

PLAYGROUND_MAX_MESSAGE_CHARS = 2000
PLAYGROUND_HISTORY_LIMIT = 12
PLAYGROUND_FALLBACK_REPLY = "Sorry, I couldn't put together a reply just now. Please try again."
PLAYGROUND_UNAVAILABLE = (
    'Your AI Employee is temporarily unavailable. Please try again in a moment.'
)


def resolve_hub_tab(tab, default='playground'):
    tab = (tab or '').strip()
    tab = HUB_TAB_ALIASES.get(tab, tab)
    return tab if tab in HUB_TABS else default


def _hub_url(employee_or_pk, tab):
    pk = getattr(employee_or_pk, 'pk', employee_or_pk)
    return f"{reverse('employee_hub', kwargs={'pk': pk})}?tab={resolve_hub_tab(tab)}"


def _hub_settings_form(*args, **kwargs):
    """Settings form for the hub, where the capability checkboxes live in a different
    tab panel than <form id="hub-settings-form"> — the `form` attribute ties them back."""
    form = AIEmployeeForm(*args, **kwargs)
    form.fields['capability_choices'].widget.attrs['form'] = 'hub-settings-form'
    return form


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
    return redirect(_hub_url(pk, 'settings'))


@login_required
def employee_fire(request, pk):
    workspace = _workspace_or_redirect(request)
    if not workspace:
        return redirect('dashboard')
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)
    if request.method == 'POST':
        name = employee.name
        employee.delete()
        messages.success(request, f'{name} has been let go.')
        return redirect('employee_list')
    return render(request, 'employees/fire_confirm.html', {'employee': employee})


@login_required
def playground(request, pk):
    return redirect(_hub_url(pk, 'playground'))


@login_required
def playground_history(request, pk):
    workspace = _workspace_or_redirect(request)
    if not workspace:
        return JsonResponse({'messages': []})
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)
    session_id = request.GET.get('session_id')
    session = None
    if session_id and str(session_id).isdigit():
        session = ChatSession.objects.filter(pk=session_id, employee=employee, is_test=True).first()
    if not session:
        return JsonResponse({'messages': []})
    msgs = session.messages.order_by('id').values('role', 'content')
    return JsonResponse({'session_id': session.id, 'messages': list(msgs)})


def _rag_reply(employee, message, history):
    """Run one turn through the RAG service and return the collected reply text.

    Raises requests.RequestException if the service is unreachable or errors.
    """
    payload = {
        'employee_id': str(employee.id),
        'system_prompt': employee.build_system_prompt(),
        'message': message,
        'history': history,
        'top_k': 4,
        'capabilities': (
            employee.capabilities if employee.capabilities is not None else employee.default_capabilities()
        ),
    }
    rag = requests.post(
        f'{settings.RAG_SERVICE_URL}/chat',
        json=payload,
        headers={'X-Internal-Token': settings.RAG_INTERNAL_TOKEN},
        timeout=90,
        stream=True,
    )
    rag.raise_for_status()
    tokens = []
    for line in rag.iter_lines(decode_unicode=True):
        if isinstance(line, bytes):
            line = line.decode('utf-8', 'replace')
        if not line or not line.startswith('data: '):
            continue
        data = line[6:]
        if data == '[DONE]':
            break
        try:
            token_text = json.loads(data)
        except json.JSONDecodeError:
            token_text = data
        if isinstance(token_text, str):
            tokens.append(token_text)
    return ''.join(tokens).strip()


@login_required
@require_POST
def playground_message(request, pk):
    """Authenticated test chat for the hub playground and dashboard preview.

    Uses the same RAG pipeline as the website widget, but stores the exchange in an
    is_test session, never counts toward quota, and works for inactive employees.
    """
    workspace = user_workspace(request.user)
    if not workspace:
        raise Http404('No workspace')
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)

    try:
        body = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    message = body.get('message') if isinstance(body.get('message'), str) else ''
    message = message.strip()[:PLAYGROUND_MAX_MESSAGE_CHARS]
    if not message:
        return JsonResponse({'error': 'Type a message to test your AI Employee.'}, status=400)

    session = None
    session_id = body.get('session_id')
    if session_id and str(session_id).isdigit():
        session = ChatSession.objects.filter(pk=session_id, employee=employee, is_test=True).first()
    if session is None:
        session = ChatSession.objects.create(
            employee=employee,
            visitor_id=f'playground-{request.user.pk}',
            is_test=True,
            metadata={'source': 'playground', 'user_id': request.user.pk},
        )

    Message.objects.create(session=session, role=Message.Role.VISITOR, content=message)
    recent = list(session.messages.order_by('-id').values('role', 'content')[:PLAYGROUND_HISTORY_LIMIT])
    history = [{'role': m['role'], 'content': m['content']} for m in reversed(recent)]

    try:
        reply = _rag_reply(employee, message, history)
    except requests.RequestException:
        logger.exception('Playground RAG call failed for employee %s', employee.pk)
        return JsonResponse({'error': PLAYGROUND_UNAVAILABLE, 'session_id': session.id}, status=502)

    reply = reply or PLAYGROUND_FALLBACK_REPLY
    Message.objects.create(session=session, role=Message.Role.EMPLOYEE, content=reply)
    session.last_message_at = timezone.now()
    session.save(update_fields=['last_message_at'])
    return JsonResponse({'reply': reply, 'session_id': session.id})


@login_required
def employee_hub(request, pk):
    workspace = _workspace_or_redirect(request)
    if not workspace:
        return redirect('dashboard')
    employee = get_object_or_404(AIEmployee, pk=pk, workspace=workspace)

    settings_form = None
    active_tab = resolve_hub_tab(request.GET.get('tab'))

    if request.method == 'POST':
        action = request.POST.get('action', 'update_settings')
        return_tab = resolve_hub_tab(request.POST.get('return_tab'), default='knowledge')

        if action == 'update_settings':
            form = _hub_settings_form(request.POST, request.FILES, instance=employee)
            if form.is_valid():
                form.save()
                messages.success(request, f'{employee.name} settings updated successfully.')
                return redirect(_hub_url(employee, resolve_hub_tab(request.POST.get('return_tab'), 'settings')))
            # Keep the bound form (with errors) and stay on the settings tab.
            messages.error(request, 'Please check the settings form for errors.')
            settings_form = form
            active_tab = 'settings'
            # The failed save may have mutated the in-memory instance; reload for display.
            employee.refresh_from_db()

        elif action == 'toggle_active':
            employee.is_active = not employee.is_active
            employee.save(update_fields=['is_active'])
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                return JsonResponse({'is_active': employee.is_active, 'status': 'ok'})
            status_text = 'active' if employee.is_active else 'inactive'
            messages.success(request, f'{employee.name} is now {status_text} on your website.')
            return redirect(_hub_url(employee, 'status'))

        elif action == 'add_knowledge':
            k_form = KnowledgeSourceForm(request.POST, request.FILES)
            if k_form.is_valid():
                source = k_form.save(commit=False)
                source.employee = employee
                source.save()
                queue_ingest(source)
                messages.success(request, f'Training source "{source.title}" queued successfully.')
            else:
                errs = ' '.join([f'{f}: {e[0]}' for f, e in k_form.errors.items()])
                messages.error(request, f'Could not add material: {errs}')
            return redirect(_hub_url(employee, return_tab))

        elif action == 'delete_knowledge':
            source_id = request.POST.get('source_id')
            source = get_object_or_404(KnowledgeSource, pk=source_id, employee=employee)
            title = source.title
            source.delete()
            messages.success(request, f'Training material "{title}" removed.')
            return redirect(_hub_url(employee, return_tab))

    if settings_form is None:
        settings_form = _hub_settings_form(instance=employee)
    knowledge_form = KnowledgeSourceForm()
    urls = widget_urls(request)

    sources = list(employee.knowledge_sources.all().order_by('-created_at'))
    ready_count = sum(1 for s in sources if s.status == KnowledgeSource.Status.READY)
    chunk_total = sum(s.chunk_count for s in sources)

    all_employees = list(
        AIEmployee.objects.filter(workspace=workspace)
        .annotate(knowledge_count=Count('knowledge_sources'))
        .order_by('name')
    )
    active_employee_count = sum(1 for e in all_employees if e.is_active)
    employee_limit = workspace.plan.employee_limit if workspace.plan else 1

    live_sessions = ChatSession.objects.filter(employee=employee, is_test=False)
    session_count = live_sessions.count()
    message_count = Message.objects.filter(session__employee=employee, session__is_test=False).count()
    tasks = EmployeeTask.objects.filter(employee=employee)
    task_count = tasks.count()
    open_task_count = tasks.filter(status__in=[EmployeeTask.Status.OPEN, EmployeeTask.Status.IN_PROGRESS]).count()
    lead_count = Lead.objects.filter(employee=employee).count()

    # Real breakdown of the work this employee has done, by task type.
    type_labels = dict(EmployeeTask.TaskType.choices)
    type_counts = tasks.values('task_type').annotate(c=Count('id')).order_by('-c')
    work_breakdown = [
        {
            'label': type_labels.get(row['task_type'], row['task_type']),
            'count': row['c'],
            'pct': round(row['c'] * 100 / task_count) if task_count else 0,
        }
        for row in type_counts
    ]

    recent_sessions = (
        live_sessions
        .annotate(msg_count=Count('messages'))
        .order_by('-last_message_at')[:8]
    )

    context = {
        'employee': employee,
        'workspace': workspace,
        'all_employees': all_employees,
        'active_employee_count': active_employee_count,
        'employee_limit': employee_limit,
        'form': settings_form,
        'knowledge_form': knowledge_form,
        'public_widget_api': urls['api'],
        'widget_url': urls['widget'],
        'embed_snippet': employee.embed_snippet(widget_url=urls['widget'], api_base=urls['api']),
        'team_embed_snippet': workspace.team_embed_snippet(widget_url=urls['widget'], api_base=urls['api']),
        'sources': sources,
        'ready_count': ready_count,
        'source_count': len(sources),
        'chunk_total': chunk_total,
        'session_count': session_count,
        'message_count': message_count,
        'task_count': task_count,
        'open_task_count': open_task_count,
        'lead_count': lead_count,
        'work_breakdown': work_breakdown,
        'recent_sessions': recent_sessions,
        'active_tab': active_tab,
        'hub_nav': HUB_NAV_GROUPS.get(active_tab, 'employee'),
        'capability_items': [
            CAPABILITY_LABELS.get(c, c.replace('_', ' ').title())
            for c in (employee.capabilities or [])
        ],
    }
    return render(request, 'employees/hub.html', context)
