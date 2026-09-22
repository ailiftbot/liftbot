import json
import logging
import uuid

import redis
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.employees.models import AIEmployee
from apps.knowledge.models import KnowledgeSource
from apps.workspaces.models import Workspace
from apps.workspaces.views import user_workspace

from .actions import handle_widget_action, process_visitor_turn
from .inbox import (
    expand_shortcuts,
    mark_response,
    mark_visitor_message,
    reopen_session,
    resolve_mentions,
    resolve_session,
    suggest_reply,
    visible_messages,
)
from .memory import (
    build_visitor_context_for_prompt,
    get_or_create_profile,
    get_resume_context,
    update_after_exchange,
)
from .models import (
    CannedResponse,
    ChatSession,
    ConversationRating,
    ConversationTag,
    EmployeeTask,
    Message,
    VisitorProfile,
)
from .notify import notify_team_event

logger = logging.getLogger(__name__)


def _redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _memory_key(session_id: int) -> str:
    return f'liftbot:session:{session_id}:messages'


def _push_memory(session_id: int, role: str, content: str):
    client = _redis()
    key = _memory_key(session_id)
    client.rpush(key, json.dumps({'role': role, 'content': content}))
    client.ltrim(key, -8, -1)
    client.expire(key, 60 * 60 * 24)


def _recent_memory(session_id: int):
    client = _redis()
    raw = client.lrange(_memory_key(session_id), -4, -1)
    return [json.loads(item) for item in raw]


def _check_quota(workspace):
    if workspace.is_over_quota():
        return False, 'This workspace has reached its plan limit. Please upgrade to continue.'
    return True, ''


def _get_employee(token: str) -> AIEmployee:
    return get_object_or_404(
        AIEmployee.objects.select_related('workspace', 'workspace__plan', 'workspace__owner'),
        widget_token=token,
        is_active=True,
    )


def _employee_payload(employee: AIEmployee) -> dict:
    color = employee.brand_color or employee.workspace.brand_color
    avatar_url = employee.avatar.url if employee.avatar else ''
    caps = employee.capabilities if employee.capabilities is not None else employee.default_capabilities()
    actions = []
    if 'collect_contact' in caps:
        actions.append({'id': 'collect_contact', 'label': 'Share my details'})
    if 'schedule_handoff' in caps:
        actions.append({'id': 'schedule', 'label': 'Book a time'})
    if 'notify_team' in caps:
        actions.append({'id': 'handoff', 'label': 'Talk to the team'})
    return {
        'token': employee.widget_token,
        'name': employee.name,
        'role': employee.public_role_label,
        'department': employee.department,
        'greeting': employee.greeting_message,
        'brand_color': color,
        'avatar_url': avatar_url,
        'language': employee.language,
        'capabilities': caps,
        'actions': actions,
        'slots': employee.workspace.next_available_slots(6) if 'schedule_handoff' in caps else [],
    }


def _serialize_message(m: Message) -> dict:
    return {
        'id': m.id,
        'role': m.role,
        'content': m.content,
        'created_at': m.created_at.isoformat(),
        'author': m.author.get_full_name() or m.author.email if m.author_id else '',
    }


@require_GET
def widget_roster(request):
    ws_token = request.GET.get('workspace_token', '')
    workspace = get_object_or_404(Workspace, widget_token=ws_token)
    employees = AIEmployee.objects.filter(workspace=workspace, is_active=True).order_by('department', 'name')
    return JsonResponse({
        'workspace': workspace.name,
        'brand_color': workspace.brand_color,
        'availability': workspace.availability(),
        'employees': [_employee_payload(e) for e in employees],
    })


@require_GET
def widget_config(request):
    token = request.GET.get('token', '')
    visitor_id = request.GET.get('visitor_id', '')
    employee = _get_employee(token)
    workspace = employee.workspace
    payload = _employee_payload(employee)

    caps_for_config = employee.capabilities if employee.capabilities is not None else employee.default_capabilities()
    if visitor_id and 'remember_visitors' in caps_for_config:
        resume = get_resume_context(workspace, visitor_id)
        payload.update(resume)
        profile = get_or_create_profile(workspace, visitor_id)
        payload['visitor_context'] = build_visitor_context_for_prompt(profile)
    else:
        payload.update({
            'returning_visitor': False,
            'resume_message': '',
            'visitor_name': '',
            'last_session_id': None,
        })

    payload['availability'] = workspace.availability()
    return JsonResponse(payload)


def _article_payload(source: KnowledgeSource) -> dict:
    return {'id': source.id, 'title': source.title, 'content': source.content}


@require_GET
def widget_articles(request):
    """Help-center articles for the Articles tab, backed by ready knowledge sources."""
    token = request.GET.get('token', '')
    employee = _get_employee(token)
    sources = (
        employee.knowledge_sources
        .filter(status=KnowledgeSource.Status.READY)
        .order_by('title')
    )
    return JsonResponse({'articles': [_article_payload(s) for s in sources]})


@require_GET
def widget_search(request):
    """Keyword search across this employee's ready knowledge, for the Search tab."""
    token = request.GET.get('token', '')
    query = (request.GET.get('q') or '').strip()
    employee = _get_employee(token)
    sources = employee.knowledge_sources.filter(
        status=KnowledgeSource.Status.READY,
    )
    if query:
        sources = sources.filter(Q(title__icontains=query) | Q(content__icontains=query))
    sources = sources.order_by('title')[:20]
    return JsonResponse({'query': query, 'results': [_article_payload(s) for s in sources]})


@csrf_exempt
@require_POST
def widget_action(request):
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    token = body.get('token')
    action = body.get('action')
    visitor_id = body.get('visitor_id') or str(uuid.uuid4())
    session_id = body.get('session_id')
    data = body.get('data') or {}

    if not token or not action:
        return JsonResponse({'error': 'token and action required'}, status=400)

    employee = _get_employee(token)
    workspace = employee.workspace
    profile = get_or_create_profile(workspace, visitor_id)

    if session_id:
        session = get_object_or_404(ChatSession, pk=session_id, employee=employee)
    else:
        session = ChatSession.objects.create(employee=employee, visitor_id=visitor_id)

    result = handle_widget_action(workspace, employee, session, profile, action, data)
    if not result.get('ok'):
        return JsonResponse(result, status=400)

    # Persist employee-side confirmation as a message
    reply = result.get('message', '')
    if reply:
        Message.objects.create(session=session, role=Message.Role.EMPLOYEE, content=reply)

    return JsonResponse({
        **result,
        'session_id': session.id,
    })


@csrf_exempt
@require_POST
def widget_chat(request):
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    token = body.get('token')
    message = (body.get('message') or '').strip()
    visitor_id = body.get('visitor_id') or str(uuid.uuid4())
    session_id = body.get('session_id')
    want_stream = body.get('stream', True)
    continue_last = body.get('continue_last', False)

    if not token or not message:
        return JsonResponse({'error': 'token and message are required'}, status=400)

    employee = _get_employee(token)
    workspace = employee.workspace
    ok, err = _check_quota(workspace)
    if not ok:
        return JsonResponse({'error': err, 'quota_exceeded': True}, status=402)

    profile = get_or_create_profile(workspace, visitor_id)

    if session_id:
        session = get_object_or_404(ChatSession, pk=session_id, employee=employee)
    elif continue_last and profile.last_session_id:
        session = ChatSession.objects.filter(pk=profile.last_session_id, employee=employee).first()
        if not session:
            session = ChatSession.objects.create(employee=employee, visitor_id=visitor_id)
    else:
        session = ChatSession.objects.create(employee=employee, visitor_id=visitor_id)

    Message.objects.create(session=session, role=Message.Role.VISITOR, content=message)
    session.last_message_at = timezone.now()
    session.save(update_fields=['last_message_at'])
    mark_visitor_message(session)

    # A visitor writing back reopens a conversation the team had resolved.
    if session.is_resolved:
        reopen_session(session)

    # Human takeover: store visitor message only — team replies from dashboard
    if session.is_human_mode:
        return JsonResponse({
            'reply': '',
            'session_id': session.id,
            'human_mode': True,
            'message': 'A teammate is helping you. They will reply here shortly.',
            'actions': {},
        })

    action_result = process_visitor_turn(workspace, employee, session, profile, message)

    try:
        _push_memory(session.id, 'visitor', message)
        history = _recent_memory(session.id)
    except Exception:  # noqa: BLE001
        logger.exception('Redis memory unavailable; continuing without cache')
        history = [{'role': 'visitor', 'content': message}]

    system_prompt = employee.build_system_prompt()
    visitor_ctx = build_visitor_context_for_prompt(profile)
    if visitor_ctx:
        system_prompt = f'{system_prompt}\n\n{visitor_ctx}'

    payload = {
        'employee_id': str(employee.id),
        'system_prompt': system_prompt,
        'message': message,
        'history': history,
        'top_k': 4,
        'capabilities': employee.capabilities or [],
        'temperature': employee.reply_temperature,
        'fallback_line': employee.fallback_line,
    }

    try:
        rag = requests.post(
            f'{settings.RAG_SERVICE_URL}/chat',
            json=payload,
            headers={'X-Internal-Token': settings.RAG_INTERNAL_TOKEN},
            timeout=90,
            stream=True,
        )
        rag.raise_for_status()
    except requests.RequestException as exc:
        logger.exception('RAG chat failed')
        return JsonResponse({'error': 'The AI Employee is temporarily unavailable.', 'detail': str(exc)}, status=502)

    def collect_reply():
        chunks = []
        for line in rag.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith('data: '):
                data = line[6:]
                if data == '[DONE]':
                    break
                chunks.append(data)
        return ''.join(chunks).strip() or 'I am not sure based on my training materials yet.'

    def persist_reply(reply: str):
        msg = Message.objects.create(session=session, role=Message.Role.EMPLOYEE, content=reply)
        try:
            _push_memory(session.id, 'employee', reply)
        except Exception:  # noqa: BLE001
            logger.exception('Redis memory write failed')
        mark_response(session, by_human=False)
        update_after_exchange(session, message, reply, profile)
        workspace.conversations_used += 1
        workspace.tokens_used += max(len(message.split()) + len(reply.split()), 1)
        workspace.save(update_fields=['conversations_used', 'tokens_used', 'updated_at'])
        return msg

    if not want_stream:
        reply = collect_reply()
        msg = persist_reply(reply)
        return JsonResponse({
            'reply': reply,
            'session_id': session.id,
            'message_id': msg.id,
            'human_mode': False,
            'actions': action_result,
        })

    def stream():
        chunks = []
        for line in rag.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith('data: '):
                data = line[6:]
                if data == '[DONE]':
                    break
                chunks.append(data)
                yield f'data: {data}\n\n'
        reply = ''.join(chunks).strip() or 'I am not sure based on my training materials yet.'
        msg = persist_reply(reply)
        meta = {
            'session_id': session.id,
            'message_id': msg.id,
            'done': True,
            'actions': action_result,
        }
        yield f'data: {json.dumps(meta)}\n\n'
        yield 'data: [DONE]\n\n'

    response = StreamingHttpResponse(stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@require_GET
def widget_poll(request):
    """Widget polls for new human/employee messages during takeover."""
    token = request.GET.get('token', '')
    session_id = request.GET.get('session_id')
    after_id = int(request.GET.get('after_id') or 0)
    employee = _get_employee(token)
    session = get_object_or_404(ChatSession, pk=session_id, employee=employee)
    msgs = (
        visible_messages(session)
        .filter(id__gt=after_id)
        .exclude(role=Message.Role.VISITOR)
        .select_related('author')
    )
    return JsonResponse({
        'session_id': session.id,
        'human_mode': session.is_human_mode,
        'status': session.status,
        'resolved': session.is_resolved,
        'ask_rating': session.is_resolved and not hasattr(session, 'rating'),
        'messages': [_serialize_message(m) for m in msgs],
    })


@csrf_exempt
@require_POST
def widget_lead(request):
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    token = body.get('token')
    employee = _get_employee(token)
    workspace = employee.workspace
    visitor_id = body.get('visitor_id', '')
    profile = get_or_create_profile(workspace, visitor_id) if visitor_id else None

    session = None
    if body.get('session_id'):
        session = ChatSession.objects.filter(pk=body['session_id'], employee=employee).first()

    from apps.leads.models import Lead

    if profile:
        if body.get('name'):
            profile.name = body['name']
        if body.get('email'):
            profile.email = body['email']
        if body.get('phone'):
            profile.phone = body['phone']
        profile.save(update_fields=['name', 'email', 'phone', 'updated_at'])

    lead = Lead.objects.create(
        workspace=workspace,
        employee=employee,
        session=session,
        name=body.get('name', '') or (profile.name if profile else ''),
        email=body.get('email', '') or (profile.email if profile else ''),
        phone=body.get('phone', '') or (profile.phone if profile else ''),
        intent_summary=body.get('intent', ''),
        source=Lead.Source.FORM,
    )
    return JsonResponse({'ok': True, 'lead_id': lead.id})


@csrf_exempt
@require_POST
def widget_rate(request):
    """Visitor satisfaction rating (CSAT) submitted at the end of a conversation."""
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    employee = _get_employee(body.get('token') or '')
    session = get_object_or_404(ChatSession, pk=body.get('session_id'), employee=employee)

    try:
        score = int(body.get('score'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'score must be 1-5'}, status=400)
    if not 1 <= score <= 5:
        return JsonResponse({'error': 'score must be 1-5'}, status=400)

    rating, _ = ConversationRating.objects.update_or_create(
        session=session,
        defaults={
            'workspace': employee.workspace,
            'employee': employee,
            'score': score,
            'comment': (body.get('comment') or '')[:2000],
            'rated_human': session.first_human_response_seconds is not None,
        },
    )
    notify_team_event(employee.workspace, employee, 'conversation_rated', {
        'session_id': session.id,
        'score': score,
        'comment': rating.comment,
    })
    return JsonResponse({'ok': True, 'score': rating.score})


def _members(workspace):
    from apps.workspaces.models import WorkspaceMembership

    User = get_user_model()
    return User.objects.filter(
        Q(memberships__workspace=workspace) | Q(owned_workspaces=workspace)
    ).distinct().order_by('first_name', 'email')


def _user_label(user) -> str:
    if not user:
        return ''
    return user.get_full_name() or user.get_username() or user.email


@login_required
def conversations_list(request):
    """Team inbox: filter by state, assignee, tag, employee, or free text."""
    workspace = user_workspace(request.user)
    sessions = (
        ChatSession.objects
        .filter(employee__workspace=workspace)
        .select_related('employee', 'taken_over_by', 'assigned_to')
        .prefetch_related('tags', 'messages')
    )

    state = request.GET.get('state') or 'open'
    assignee = request.GET.get('assignee') or ''
    tag_slug = request.GET.get('tag') or ''
    query = (request.GET.get('q') or '').strip()

    if state == 'open':
        sessions = sessions.filter(status__in=ChatSession.OPEN_STATUSES)
    elif state == 'resolved':
        sessions = sessions.filter(status__in=[ChatSession.Status.RESOLVED, ChatSession.Status.CLOSED])
    elif state == 'human':
        sessions = sessions.filter(status=ChatSession.Status.HUMAN)
    elif state == 'unassigned':
        sessions = sessions.filter(assigned_to__isnull=True, status__in=ChatSession.OPEN_STATUSES)

    if assignee == 'me':
        sessions = sessions.filter(assigned_to=request.user)
    elif assignee.isdigit():
        sessions = sessions.filter(assigned_to_id=int(assignee))

    if tag_slug:
        sessions = sessions.filter(tags__slug=tag_slug)

    if query:
        sessions = sessions.filter(
            Q(messages__content__icontains=query)
            | Q(visitor_id__icontains=query)
            | Q(employee__name__icontains=query)
        ).distinct()

    all_sessions = ChatSession.objects.filter(employee__workspace=workspace)
    resolved_states = [ChatSession.Status.RESOLVED, ChatSession.Status.CLOSED]
    inbox_tabs = [
        ('open', 'Open', all_sessions.filter(status__in=ChatSession.OPEN_STATUSES).count()),
        ('unassigned', 'Unassigned', all_sessions.filter(
            assigned_to__isnull=True, status__in=ChatSession.OPEN_STATUSES,
        ).count()),
        ('human', 'With a teammate', all_sessions.filter(status=ChatSession.Status.HUMAN).count()),
        ('resolved', 'Resolved', all_sessions.filter(status__in=resolved_states).count()),
        ('all', 'All', all_sessions.count()),
    ]

    return render(request, 'chat/conversations.html', {
        'sessions': sessions[:50],
        'workspace': workspace,
        'tags': ConversationTag.objects.filter(workspace=workspace),
        'members': _members(workspace),
        'inbox_tabs': inbox_tabs,
        'filters': {'state': state, 'assignee': assignee, 'tag': tag_slug, 'q': query},
    })


@login_required
def conversation_detail(request, pk):
    workspace = user_workspace(request.user)
    session = get_object_or_404(
        ChatSession.objects
        .select_related('employee', 'taken_over_by', 'assigned_to', 'resolved_by')
        .prefetch_related('tags'),
        pk=pk,
        employee__workspace=workspace,
    )
    msgs = session.messages.select_related('author').all()
    visitor = (
        VisitorProfile.objects
        .filter(workspace=workspace, visitor_id=session.visitor_id)
        .first()
    )
    return render(request, 'chat/conversation_detail.html', {
        'session': session,
        'messages': msgs,
        'workspace': workspace,
        'visitor': visitor,
        'members': _members(workspace),
        'all_tags': ConversationTag.objects.filter(workspace=workspace),
        'session_tag_ids': list(session.tags.values_list('id', flat=True)),
        'canned': CannedResponse.objects.filter(workspace=workspace),
        'rating': getattr(session, 'rating', None),
    })


@login_required
@require_POST
def conversation_takeover(request, pk):
    workspace = user_workspace(request.user)
    session = get_object_or_404(ChatSession, pk=pk, employee__workspace=workspace)
    session.status = ChatSession.Status.HUMAN
    session.taken_over_by = request.user
    session.taken_over_at = timezone.now()
    if not session.assigned_to_id:
        session.assigned_to = request.user
        session.assigned_at = timezone.now()
    session.save(update_fields=[
        'status', 'taken_over_by', 'taken_over_at', 'assigned_to', 'assigned_at', 'last_message_at',
    ])
    Message.objects.create(
        session=session,
        role=Message.Role.SYSTEM,
        content=f'{_user_label(request.user)} joined the conversation.',
        author=request.user,
    )
    notify_team_event(workspace, session.employee, 'human_takeover', {
        'session_id': session.id,
        'by': request.user.email,
    })
    messages.success(request, 'You took over this conversation. AI replies are paused.')
    return redirect('conversation_detail', pk=session.pk)


@login_required
@require_POST
def conversation_release(request, pk):
    workspace = user_workspace(request.user)
    session = get_object_or_404(ChatSession, pk=pk, employee__workspace=workspace)
    session.status = ChatSession.Status.ACTIVE
    session.taken_over_by = None
    session.taken_over_at = None
    session.save(update_fields=['status', 'taken_over_by', 'taken_over_at'])
    Message.objects.create(
        session=session,
        role=Message.Role.SYSTEM,
        content=f'{_user_label(request.user)} returned control to {session.employee.name}.',
        author=request.user,
    )
    messages.success(request, f'{session.employee.name} is handling replies again.')
    return redirect('conversation_detail', pk=session.pk)


@login_required
@require_POST
def conversation_reply(request, pk):
    """Send a reply to the visitor, or save a private note for the team."""
    workspace = user_workspace(request.user)
    session = get_object_or_404(ChatSession, pk=pk, employee__workspace=workspace)
    content = (request.POST.get('content') or '').strip()
    is_note = request.POST.get('kind') == 'note'

    if not content:
        return JsonResponse({'error': 'Empty message'}, status=400)

    content = expand_shortcuts(workspace, content)

    if is_note:
        note = Message.objects.create(
            session=session,
            role=Message.Role.NOTE,
            content=content,
            author=request.user,
        )
        mentioned = resolve_mentions(workspace, content)
        if mentioned:
            note.mentions.set(mentioned)
            notify_team_event(workspace, session.employee, 'note_mention', {
                'session_id': session.id,
                'note': content[:500],
                'by': request.user.email,
                'mentioned': [u.email for u in mentioned],
            })
        return JsonResponse({'ok': True, 'message': _serialize_message(note)})

    if not session.is_human_mode:
        session.status = ChatSession.Status.HUMAN
        session.taken_over_by = request.user
        session.taken_over_at = timezone.now()
        session.save(update_fields=['status', 'taken_over_by', 'taken_over_at'])

    msg = Message.objects.create(
        session=session,
        role=Message.Role.HUMAN,
        content=content,
        author=request.user,
    )
    mark_response(session, by_human=True)
    session.last_message_at = timezone.now()
    session.save(update_fields=['last_message_at'])

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
        return JsonResponse({'ok': True, 'message': _serialize_message(msg)})
    return redirect('conversation_detail', pk=session.pk)


@login_required
@require_GET
def conversation_poll(request, pk):
    workspace = user_workspace(request.user)
    session = get_object_or_404(ChatSession, pk=pk, employee__workspace=workspace)
    after_id = int(request.GET.get('after_id') or 0)
    msgs = Message.objects.filter(session=session, id__gt=after_id).select_related('author')
    return JsonResponse({
        'session_id': session.id,
        'status': session.status,
        'human_mode': session.is_human_mode,
        'resolved': session.is_resolved,
        'messages': [_serialize_message(m) for m in msgs],
    })


@login_required
@require_POST
def conversation_assign(request, pk):
    workspace = user_workspace(request.user)
    session = get_object_or_404(ChatSession, pk=pk, employee__workspace=workspace)
    user_id = request.POST.get('user_id') or ''

    if user_id:
        target = _members(workspace).filter(pk=user_id).first()
        if not target:
            return JsonResponse({'error': 'Not a member of this workspace'}, status=400)
        session.assigned_to = target
        session.assigned_at = timezone.now()
        note = f'{_user_label(request.user)} assigned this conversation to {_user_label(target)}.'
    else:
        session.assigned_to = None
        session.assigned_at = None
        note = f'{_user_label(request.user)} unassigned this conversation.'

    session.save(update_fields=['assigned_to', 'assigned_at'])
    Message.objects.create(
        session=session, role=Message.Role.SYSTEM, content=note, author=request.user,
    )
    if session.assigned_to_id and session.assigned_to_id != request.user.id:
        notify_team_event(workspace, session.employee, 'conversation_assigned', {
            'session_id': session.id,
            'assigned_to': session.assigned_to.email,
            'by': request.user.email,
        })
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'assigned_to': _user_label(session.assigned_to)})
    return redirect('conversation_detail', pk=session.pk)


@login_required
@require_POST
def conversation_resolve(request, pk):
    workspace = user_workspace(request.user)
    session = get_object_or_404(ChatSession, pk=pk, employee__workspace=workspace)

    if session.is_resolved:
        reopen_session(session)
        note = f'{_user_label(request.user)} reopened this conversation.'
        messages.success(request, 'Conversation reopened.')
    else:
        resolve_session(session, request.user)
        note = f'{_user_label(request.user)} marked this conversation resolved.'
        messages.success(request, 'Conversation resolved. The visitor can rate it now.')

    Message.objects.create(
        session=session, role=Message.Role.SYSTEM, content=note, author=request.user,
    )
    return redirect('conversation_detail', pk=session.pk)


@login_required
@require_POST
def conversation_tags(request, pk):
    """Attach or detach a tag. Creates the tag on first use."""
    workspace = user_workspace(request.user)
    session = get_object_or_404(ChatSession, pk=pk, employee__workspace=workspace)
    name = (request.POST.get('name') or '').strip()
    remove_id = request.POST.get('remove')

    if remove_id:
        session.tags.remove(*ConversationTag.objects.filter(pk=remove_id, workspace=workspace))
    elif name:
        slug = slugify(name)[:50]
        if slug:
            tag, _ = ConversationTag.objects.get_or_create(
                workspace=workspace, slug=slug, defaults={'name': name[:40]},
            )
            session.tags.add(tag)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'ok': True,
            'tags': [{'id': t.id, 'name': t.name, 'color': t.color} for t in session.tags.all()],
        })
    return redirect('conversation_detail', pk=session.pk)


@login_required
@require_POST
def conversation_suggest(request, pk):
    """MagicReply: draft a reply for the teammate from the employee's knowledge."""
    workspace = user_workspace(request.user)
    session = get_object_or_404(
        ChatSession.objects.select_related('employee'), pk=pk, employee__workspace=workspace,
    )
    try:
        draft = suggest_reply(session)
    except requests.RequestException as exc:
        logger.exception('Reply suggestion failed')
        return JsonResponse({'error': 'Could not draft a reply right now.', 'detail': str(exc)}, status=502)

    if not draft:
        return JsonResponse({'error': 'Nothing to reply to yet.'}, status=400)
    return JsonResponse({'ok': True, 'draft': draft})


@login_required
def canned_responses(request):
    """Manage saved replies teammates insert with !shortcut."""
    workspace = user_workspace(request.user)

    if request.method == 'POST':
        action = request.POST.get('action') or 'save'
        if action == 'delete':
            CannedResponse.objects.filter(pk=request.POST.get('id'), workspace=workspace).delete()
            messages.success(request, 'Saved reply deleted.')
        else:
            shortcut = (request.POST.get('shortcut') or '').strip()
            title = (request.POST.get('title') or '').strip()
            body = (request.POST.get('body') or '').strip()
            if not (shortcut and body):
                messages.error(request, 'Shortcut and message are both required.')
            else:
                existing = CannedResponse.objects.filter(
                    pk=request.POST.get('id') or 0, workspace=workspace,
                ).first()
                if existing:
                    existing.shortcut = shortcut
                    existing.title = title or shortcut
                    existing.body = body
                    existing.save(update_fields=['shortcut', 'title', 'body', 'updated_at'])
                    messages.success(request, 'Saved reply updated.')
                else:
                    CannedResponse.objects.update_or_create(
                        workspace=workspace,
                        shortcut=slugify(shortcut).replace('-', '_')[:40],
                        defaults={'title': title or shortcut, 'body': body, 'created_by': request.user},
                    )
                    messages.success(request, 'Saved reply added.')
        return redirect('canned_responses')

    return render(request, 'chat/canned_responses.html', {
        'workspace': workspace,
        'responses': CannedResponse.objects.filter(workspace=workspace),
    })


@login_required
def tasks_list(request):
    workspace = user_workspace(request.user)
    tasks = (
        EmployeeTask.objects.filter(workspace=workspace)
        .select_related('employee', 'lead', 'session')
        .order_by('-created_at')[:100]
    )
    return render(request, 'chat/tasks.html', {'tasks': tasks, 'workspace': workspace})


@login_required
@require_POST
def task_update_status(request, pk):
    workspace = user_workspace(request.user)
    task = get_object_or_404(EmployeeTask, pk=pk, workspace=workspace)
    status = request.POST.get('status')
    if status in EmployeeTask.Status.values:
        task.status = status
        task.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'ok': True, 'status': task.status})