from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.timesince import timesince

from apps.chat.models import ChatSession, Message, EmployeeTask, VisitorProfile
from apps.employees.models import AIEmployee
from apps.knowledge.models import KnowledgeSource
from apps.leads.models import Lead

from .models import WorkspaceMembership
from .public_urls import widget_urls


def user_workspace(user):
    membership = (
        WorkspaceMembership.objects
        .select_related('workspace', 'workspace__plan')
        .filter(user=user)
        .order_by('created_at')
        .first()
    )
    return membership.workspace if membership else None


def _setup_steps(workspace, employee, knowledge_count):
    has_workspace = bool(workspace and workspace.name)
    has_employee = bool(employee)
    has_knowledge = knowledge_count > 0
    has_personality = bool(employee and employee.greeting_message and employee.personality)
    has_widget = bool(employee and employee.widget_token)

    steps = [
        {'key': 'business', 'label': 'Business Info', 'done': has_workspace},
        {'key': 'knowledge', 'label': 'Knowledge', 'done': has_knowledge},
        {'key': 'replies', 'label': 'Greeting', 'done': has_personality},
        {'key': 'customize', 'label': 'Customize', 'done': has_employee and bool(employee.brand_color)},
        {'key': 'install', 'label': 'Install', 'done': has_widget and has_knowledge},
    ]
    return steps


@login_required
def dashboard(request):
    workspace = user_workspace(request.user)
    employees = list(AIEmployee.objects.filter(workspace=workspace)) if workspace else []
    selected_id = request.GET.get('employee')
    primary = None
    if employees:
        if selected_id:
            primary = next((e for e in employees if str(e.id) == str(selected_id)), employees[0])
        else:
            primary = employees[0]

    knowledge_count = 0
    faq_count = 0
    if primary:
        qs = KnowledgeSource.objects.filter(employee=primary)
        knowledge_count = qs.count()
        faq_count = qs.filter(source_type=KnowledgeSource.SourceType.FAQ).count()

    leads_qs = Lead.objects.filter(workspace=workspace) if workspace else Lead.objects.none()
    leads = list(leads_qs.select_related('employee').order_by('-created_at')[:8])
    lead_count = leads_qs.count() if workspace else 0
    task_count = EmployeeTask.objects.filter(workspace=workspace, status=EmployeeTask.Status.OPEN).count() if workspace else 0

    sessions = []
    conversation_count = 0
    if workspace:
        session_qs = (
            ChatSession.objects
            .filter(employee__workspace=workspace)
            .select_related('employee')
            .prefetch_related('messages')
            .order_by('-last_message_at')
        )
        conversation_count = session_qs.count()
        for s in session_qs[:5]:
            last = s.messages.filter(role=Message.Role.VISITOR).order_by('-created_at').first()
            sessions.append({
                'id': s.id,
                'employee': s.employee.name,
                'visitor': (s.visitor_id or 'Visitor')[:8],
                'snippet': (last.content if last else 'New conversation')[:80],
                'when': f'{timesince(s.last_message_at, timezone.now())} ago',
                'initials': (s.employee.name[:1] + (s.employee.name.split()[-1][:1] if ' ' in s.employee.name else '')).upper(),
            })

    steps = _setup_steps(workspace, primary, knowledge_count)
    completed_steps = sum(1 for s in steps if s['done'])

    urls = widget_urls(request)
    embed_snippet = (
        primary.embed_snippet(widget_url=urls['widget'], api_base=urls['api']) if primary else ''
    )
    team_embed_snippet = (
        workspace.team_embed_snippet(widget_url=urls['widget'], api_base=urls['api'])
        if workspace else ''
    )

    cards = [
        {
            'title': 'Business Information',
            'desc': 'Company profile, brand color, and workspace details.',
            'status': 'Completed' if workspace else 'In Progress',
            'done': bool(workspace),
            'cta': 'Edit',
            'url': 'settings' if workspace else 'dashboard',
            'icon': 'BI',
        },
        {
            'title': 'Knowledge Base',
            'desc': 'Train your AI Employee with PDFs, URLs, FAQs, and text.',
            'status': 'Completed' if knowledge_count else 'In Progress',
            'done': knowledge_count > 0,
            'cta': 'Manage',
            'url': 'knowledge' if primary else 'hire',
            'icon': 'KB',
        },
        {
            'title': 'Greeting & Tone',
            'desc': 'Opening message and personality for website visitors.',
            'status': 'Completed' if primary else 'In Progress',
            'done': bool(primary),
            'cta': 'Manage',
            'url': 'edit' if primary else 'hire',
            'icon': 'GT',
        },
        {
            'title': 'Customize Employee',
            'desc': 'Name, role, avatar, and brand color for the widget.',
            'status': 'Completed' if primary and primary.brand_color else 'In Progress',
            'done': bool(primary and primary.brand_color),
            'cta': 'Customize',
            'url': 'edit' if primary else 'hire',
            'icon': 'CE',
        },
    ]

    quick_setup = [
        {
            'n': 1,
            'title': 'Hire your first AI Employee',
            'desc': 'Give them a name, role, and personality.',
            'done': bool(primary),
            'cta': 'Completed' if primary else 'Hire',
            'href_name': 'employee_detail' if primary else 'employee_hire',
            'pk': primary.pk if primary else None,
        },
        {
            'n': 2,
            'title': 'Train with business knowledge',
            'desc': 'Upload PDFs, crawl URLs, or add FAQs.',
            'done': knowledge_count > 0,
            'cta': 'Completed' if knowledge_count else 'Train',
            'href_name': 'knowledge_list' if primary else 'employee_hire',
            'pk': primary.pk if primary else None,
        },
        {
            'n': 3,
            'title': 'Customize look & greeting',
            'desc': 'Match your brand color and first message.',
            'done': bool(primary and primary.brand_color),
            'cta': 'Completed' if (primary and primary.brand_color) else 'Customize',
            'href_name': 'employee_edit' if primary else 'employee_hire',
            'pk': primary.pk if primary else None,
        },
        {
            'n': 4,
            'title': 'Install on your website',
            'desc': 'Paste the embed snippet on any page.',
            'done': bool(primary and knowledge_count),
            'cta': 'Get Code' if primary else 'Hire first',
            'href_name': 'employee_detail' if primary else 'employee_hire',
            'pk': primary.pk if primary else None,
        },
    ]

    return render(request, 'workspaces/dashboard.html', {
        'workspace': workspace,
        'employees': employees,
        'primary_employee': primary,
        'leads': leads,
        'lead_count': lead_count,
        'conversation_count': conversation_count,
        'knowledge_count': knowledge_count,
        'faq_count': faq_count,
        'sessions': sessions,
        'setup_steps': steps,
        'completed_steps': completed_steps,
        'embed_snippet': embed_snippet,
        'team_embed_snippet': team_embed_snippet,
        'task_count': task_count,
        'cards': cards,
        'quick_setup': quick_setup,
        'usage_percent': workspace.usage_percent if workspace else 0,
        'public_widget_api': urls['api'],
        'public_widget_url': urls['widget'],
    })


def home(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'home.html')


def helloworld(request):
    """Public widget demo page — /helloworld/ or /helloworld/?token=…"""
    urls = widget_urls(request)
    token = (request.GET.get('token') or '').strip()
    if not token and request.user.is_authenticated:
        workspace = user_workspace(request.user)
        if workspace:
            emp = AIEmployee.objects.filter(workspace=workspace, is_active=True).first()
            if emp:
                token = emp.widget_token
    return render(request, 'helloworld.html', {
        'employee_token': token,
        'widget_url': urls['widget'],
        'api_base': urls['api'],
    })


@login_required
def analytics(request):
    workspace = user_workspace(request.user)
    if not workspace:
        messages.info(request, 'Create a workspace first.')
        return redirect('dashboard')

    now = timezone.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    chart_start = day_start - timedelta(days=13)

    sessions = ChatSession.objects.filter(employee__workspace=workspace)
    messages_qs = Message.objects.filter(session__employee__workspace=workspace)
    leads = Lead.objects.filter(workspace=workspace)
    tasks = EmployeeTask.objects.filter(workspace=workspace)
    visitors = VisitorProfile.objects.filter(workspace=workspace)
    employees = list(AIEmployee.objects.filter(workspace=workspace, is_active=True))

    conv_total = sessions.count()
    conv_7d = sessions.filter(started_at__gte=week_ago).count()
    conv_30d = sessions.filter(started_at__gte=month_ago).count()
    conv_today = sessions.filter(started_at__gte=day_start).count()

    msg_total = messages_qs.count()
    msg_visitor = messages_qs.filter(role=Message.Role.VISITOR).count()
    msg_employee = messages_qs.filter(role=Message.Role.EMPLOYEE).count()
    msg_human = messages_qs.filter(role=Message.Role.HUMAN).count()
    tokens_period = messages_qs.filter(created_at__gte=month_ago).aggregate(t=Sum('tokens_used'))['t'] or 0

    lead_total = leads.count()
    lead_7d = leads.filter(created_at__gte=week_ago).count()
    lead_30d = leads.filter(created_at__gte=month_ago).count()

    task_open = tasks.filter(status=EmployeeTask.Status.OPEN).count()
    task_done = tasks.filter(status=EmployeeTask.Status.DONE).count()
    task_handoff = tasks.filter(task_type=EmployeeTask.TaskType.HANDOFF).count()
    task_schedule = tasks.filter(task_type=EmployeeTask.TaskType.SCHEDULE).count()
    human_sessions = sessions.filter(status=ChatSession.Status.HUMAN).count()

    # Last 14 days conversation chart
    by_day = {
        row['day'].date() if hasattr(row['day'], 'date') else row['day']: row['c']
        for row in (
            sessions.filter(started_at__gte=chart_start)
            .annotate(day=TruncDate('started_at'))
            .values('day')
            .annotate(c=Count('id'))
        )
        if row['day']
    }
    leads_by_day = {
        row['day'].date() if hasattr(row['day'], 'date') else row['day']: row['c']
        for row in (
            leads.filter(created_at__gte=chart_start)
            .annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(c=Count('id'))
        )
        if row['day']
    }
    chart_days = []
    max_bar = 1
    for i in range(14):
        d = (chart_start + timedelta(days=i)).date()
        conv_n = by_day.get(d, 0)
        lead_n = leads_by_day.get(d, 0)
        max_bar = max(max_bar, conv_n, lead_n)
        chart_days.append({
            'label': d.strftime('%a'),
            'date': d.strftime('%b %d'),
            'conversations': conv_n,
            'leads': lead_n,
        })
    for day in chart_days:
        day['conv_pct'] = round((day['conversations'] / max_bar) * 100)
        day['lead_pct'] = round((day['leads'] / max_bar) * 100)

    # Per-employee breakdown
    session_counts = dict(
        sessions.values('employee_id').annotate(c=Count('id')).values_list('employee_id', 'c')
    )
    lead_counts = dict(
        leads.values('employee_id').annotate(c=Count('id')).values_list('employee_id', 'c')
    )
    task_counts = dict(
        tasks.filter(status=EmployeeTask.Status.OPEN)
        .values('employee_id')
        .annotate(c=Count('id'))
        .values_list('employee_id', 'c')
    )
    employee_rows = []
    for emp in employees:
        employee_rows.append({
            'employee': emp,
            'conversations': session_counts.get(emp.id, 0),
            'leads': lead_counts.get(emp.id, 0),
            'open_tasks': task_counts.get(emp.id, 0),
        })

    recent_leads = list(leads.select_related('employee').order_by('-created_at')[:6])
    recent_tasks = list(
        tasks.select_related('employee').order_by('-created_at')[:6]
    )

    conv_limit = workspace.conversation_limit or 1
    tok_limit = workspace.token_limit or 1

    return render(request, 'workspaces/analytics.html', {
        'workspace': workspace,
        'stats': {
            'conv_total': conv_total,
            'conv_today': conv_today,
            'conv_7d': conv_7d,
            'conv_30d': conv_30d,
            'msg_total': msg_total,
            'msg_visitor': msg_visitor,
            'msg_employee': msg_employee,
            'msg_human': msg_human,
            'tokens_period': tokens_period,
            'lead_total': lead_total,
            'lead_7d': lead_7d,
            'lead_30d': lead_30d,
            'task_open': task_open,
            'task_done': task_done,
            'task_handoff': task_handoff,
            'task_schedule': task_schedule,
            'human_sessions': human_sessions,
            'visitors': visitors.count(),
            'employees': len(employees),
        },
        'chart_days': chart_days,
        'employee_rows': employee_rows,
        'recent_leads': recent_leads,
        'recent_tasks': recent_tasks,
        'usage': {
            'conversations': workspace.conversations_used,
            'conversation_limit': workspace.conversation_limit,
            'conv_pct': min(100, round((workspace.conversations_used / conv_limit) * 100)),
            'tokens': workspace.tokens_used,
            'token_limit': workspace.token_limit,
            'tok_pct': min(100, round((workspace.tokens_used / tok_limit) * 100)),
            'overall_pct': workspace.usage_percent,
        },
    })


@login_required
def workspace_settings(request):
    workspace = user_workspace(request.user)
    if not workspace:
        messages.info(request, 'Create a workspace first.')
        return redirect('dashboard')

    profile = getattr(request.user, 'profile', None)

    if request.method == 'POST':
        section = request.POST.get('section') or 'workspace'

        if section == 'workspace':
            name = (request.POST.get('name') or '').strip()
            brand = (request.POST.get('brand_color') or '').strip() or '#0F766E'
            if name:
                workspace.name = name[:200]
            if brand.startswith('#') and len(brand) in (4, 7):
                workspace.brand_color = brand
            workspace.save(update_fields=['name', 'brand_color', 'updated_at'])
            messages.success(request, 'Workspace settings saved.')

        elif section == 'webhook':
            workspace.webhook_url = (request.POST.get('webhook_url') or '').strip()
            workspace.save(update_fields=['webhook_url', 'updated_at'])
            messages.success(request, 'Webhook URL saved.')

        elif section == 'account':
            full_name = (request.POST.get('full_name') or '').strip()
            first = (request.POST.get('first_name') or '').strip()
            last = (request.POST.get('last_name') or '').strip()
            request.user.first_name = first[:150]
            request.user.last_name = last[:150]
            request.user.save(update_fields=['first_name', 'last_name'])
            if profile is not None:
                profile.full_name = full_name or f'{first} {last}'.strip()
                profile.save(update_fields=['full_name'])
            messages.success(request, 'Account updated.')

        elif section == 'regenerate_workspace_token':
            import secrets
            workspace.widget_token = secrets.token_urlsafe(24)
            workspace.save(update_fields=['widget_token', 'updated_at'])
            messages.success(request, 'Team widget token regenerated. Update embeds that use the old token.')

        return redirect('settings')

    memberships = (
        WorkspaceMembership.objects
        .filter(workspace=workspace)
        .select_related('user')
        .order_by('created_at')
    )

    urls = widget_urls(request)
    return render(request, 'workspaces/settings.html', {
        'workspace': workspace,
        'profile': profile,
        'memberships': memberships,
        'team_embed_snippet': workspace.team_embed_snippet(
            widget_url=urls['widget'], api_base=urls['api']
        ),
        'public_app_url': urls['app'],
    })
