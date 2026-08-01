from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from django.utils.timesince import timesince

from apps.chat.models import ChatSession, Message
from apps.employees.models import AIEmployee
from apps.knowledge.models import KnowledgeSource
from apps.leads.models import Lead

from .models import WorkspaceMembership


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

    embed_snippet = ''
    if primary:
        embed_snippet = primary.embed_snippet(settings.PUBLIC_WIDGET_URL)

    cards = [
        {
            'title': 'Business Information',
            'desc': 'Company profile, brand color, and workspace details.',
            'status': 'Completed' if workspace else 'In Progress',
            'done': bool(workspace),
            'cta': 'Edit',
            'url': 'billing' if workspace else 'dashboard',
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
        'cards': cards,
        'quick_setup': quick_setup,
        'usage_percent': workspace.usage_percent if workspace else 0,
    })


def home(request):
    if request.user.is_authenticated:
        from django.shortcuts import redirect
        return redirect('dashboard')
    return render(request, 'home.html')
