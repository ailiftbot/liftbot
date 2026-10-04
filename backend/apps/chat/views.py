import json
import logging
import re
import time
import uuid

import redis
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F, Q
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.employees.models import AIEmployee
from apps.knowledge.models import KnowledgeSource
from apps.workspaces.models import Workspace
from apps.workspaces.views import user_workspace

from .actions import handle_widget_action, process_visitor_turn
from .memory import (
    build_visitor_context_for_prompt,
    get_or_create_profile,
    get_resume_context,
    update_after_exchange,
)
from .models import ChatSession, EmployeeTask, Message, VisitorProfile
from .notify import notify_team_event

logger = logging.getLogger(__name__)

FALLBACK_REPLY = 'I am not sure based on my training materials yet.'


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


VISITOR_ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')


class WidgetError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def widget_endpoint(view):
    """Public widget endpoints always answer JSON, even on errors."""
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except WidgetError as exc:
            return JsonResponse({'error': str(exc)}, status=exc.status)
        except Http404:
            return JsonResponse({'error': 'Not found'}, status=404)
    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


def _rate_limit(request, bucket: str, limit: int, window: int = 60):
    """Fixed-window limiter in Redis, keyed by client IP. Fails open if Redis is down."""
    ip = (request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0].strip()
    key = f'liftbot:rl:{bucket}:{ip}:{int(time.time() // window)}'
    try:
        client = _redis()
        count = client.incr(key)
        if count == 1:
            client.expire(key, window)
    except Exception:  # noqa: BLE001
        return
    if count > limit:
        raise WidgetError('Too many requests. Please slow down.', status=429)


def _clean_visitor_id(value, required=False):
    value = (value or '').strip() if isinstance(value, str) else ''
    if not value:
        if required:
            raise WidgetError('visitor_id required')
        return str(uuid.uuid4())
    if not VISITOR_ID_RE.match(value):
        raise WidgetError('Invalid visitor_id')
    return value


def _to_int(value, default=None):
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise WidgetError('Invalid id') from None


def _visitor_session(employee, session_id, visitor_id):
    """A session is only reachable by the visitor that owns it."""
    return get_object_or_404(ChatSession, pk=_to_int(session_id), employee=employee, visitor_id=visitor_id)


def _get_employee(token: str) -> AIEmployee:
    if not token or not isinstance(token, str):
        raise Http404
    return get_object_or_404(
        AIEmployee.objects.select_related('workspace', 'workspace__plan', 'workspace__owner'),
        widget_token=token,
        is_active=True,
        workspace__is_active=True,
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
@widget_endpoint
def widget_roster(request):
    ws_token = request.GET.get('workspace_token', '')
    if not ws_token:
        raise Http404
    workspace = get_object_or_404(Workspace, widget_token=ws_token, is_active=True)
    employees = AIEmployee.objects.filter(workspace=workspace, is_active=True).order_by('department', 'name')
    return JsonResponse({
        'workspace': workspace.name,
        'brand_color': workspace.brand_color,
        'employees': [_employee_payload(e) for e in employees],
    })


@require_GET
@widget_endpoint
def widget_config(request):
    token = request.GET.get('token', '')
    visitor_id = request.GET.get('visitor_id', '')
    if visitor_id and not VISITOR_ID_RE.match(visitor_id):
        visitor_id = ''
    employee = _get_employee(token)
    workspace = employee.workspace
    payload = _employee_payload(employee)

    caps_for_config = employee.capabilities if employee.capabilities is not None else employee.default_capabilities()
    if visitor_id and 'remember_visitors' in caps_for_config:
        resume = get_resume_context(workspace, visitor_id)
        payload.update(resume)
        profile = VisitorProfile.objects.filter(workspace=workspace, visitor_id=visitor_id).first()
        payload['visitor_context'] = build_visitor_context_for_prompt(profile) if profile else ''
    else:
        payload.update({
            'returning_visitor': False,
            'resume_message': '',
            'visitor_name': '',
            'last_session_id': None,
        })

    return JsonResponse(payload)


def _article_payload(source: KnowledgeSource) -> dict:
    return {'id': source.id, 'title': source.title, 'content': source.content}


@require_GET
@widget_endpoint
def widget_articles(request):
    """Help-center articles for the Articles tab — only sources the owner marked public."""
    token = request.GET.get('token', '')
    employee = _get_employee(token)
    sources = (
        employee.knowledge_sources
        .filter(status=KnowledgeSource.Status.READY, is_public=True)
        .order_by('title')
    )
    return JsonResponse({'articles': [_article_payload(s) for s in sources]})


@require_GET
@widget_endpoint
def widget_search(request):
    """Keyword search across this employee's public articles, for the Search tab."""
    token = request.GET.get('token', '')
    query = (request.GET.get('q') or '').strip()[:200]
    _rate_limit(request, 'search', 60)
    employee = _get_employee(token)
    sources = employee.knowledge_sources.filter(
        status=KnowledgeSource.Status.READY,
        is_public=True,
    )
    if query:
        sources = sources.filter(Q(title__icontains=query) | Q(content__icontains=query))
    sources = sources.order_by('title')[:20]
    return JsonResponse({'query': query, 'results': [_article_payload(s) for s in sources]})


def _json_body(request):
    try:
        body = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise WidgetError('Invalid JSON') from None
    if not isinstance(body, dict):
        raise WidgetError('Invalid JSON')
    return body


@csrf_exempt
@require_POST
@widget_endpoint
def widget_action(request):
    body = _json_body(request)
    _rate_limit(request, 'action', 20)

    token = body.get('token')
    action = body.get('action')
    visitor_id = _clean_visitor_id(body.get('visitor_id'))
    session_id = body.get('session_id')
    data = body.get('data') if isinstance(body.get('data'), dict) else {}

    if not token or not action:
        return JsonResponse({'error': 'token and action required'}, status=400)

    employee = _get_employee(token)
    workspace = employee.workspace
    profile = get_or_create_profile(workspace, visitor_id)

    if session_id:
        session = _visitor_session(employee, session_id, visitor_id)
    else:
        session = ChatSession.objects.create(employee=employee, visitor_id=visitor_id)

    result = handle_widget_action(workspace, employee, session, profile, action, data)
    if not result.get('ok'):
        return JsonResponse(result, status=400)

    # Persist employee-side confirmation as a message
    reply = result.get('message', '')
    message_id = None
    if reply:
        message_id = Message.objects.create(session=session, role=Message.Role.EMPLOYEE, content=reply).id

    return JsonResponse({
        **result,
        'session_id': session.id,
        'message_id': message_id,
    })


MAX_MESSAGE_CHARS = 2000


@csrf_exempt
@require_POST
@widget_endpoint
def widget_chat(request):
    body = _json_body(request)
    _rate_limit(request, 'message', 20)

    token = body.get('token')
    message = body.get('message') if isinstance(body.get('message'), str) else ''
    message = message.strip()[:MAX_MESSAGE_CHARS]
    visitor_id = _clean_visitor_id(body.get('visitor_id'))
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
        session = _visitor_session(employee, session_id, visitor_id)
    elif continue_last and profile.last_session_id:
        session = ChatSession.objects.filter(
            pk=profile.last_session_id, employee=employee, visitor_id=visitor_id,
        ).first()
        if not session:
            session = ChatSession.objects.create(employee=employee, visitor_id=visitor_id)
    else:
        session = ChatSession.objects.create(employee=employee, visitor_id=visitor_id)

    Message.objects.create(session=session, role=Message.Role.VISITOR, content=message)
    session.last_message_at = timezone.now()
    session.save(update_fields=['last_message_at'])

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
        'capabilities': employee.capabilities if employee.capabilities is not None else employee.default_capabilities(),
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
    except requests.RequestException:
        logger.exception('RAG chat failed')
        return JsonResponse({'error': 'The AI Employee is temporarily unavailable.'}, status=502)

    def rag_tokens():
        # RAG frames each token as JSON so newlines survive SSE framing.
        try:
            for line in rag.iter_lines(decode_unicode=True):
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
                    yield token_text
        except requests.RequestException:
            logger.exception('RAG stream interrupted')

    def collect_reply():
        return ''.join(rag_tokens()).strip() or FALLBACK_REPLY

    def persist_reply(reply: str):
        first_reply = not session.messages.filter(role=Message.Role.EMPLOYEE).exists()
        msg = Message.objects.create(session=session, role=Message.Role.EMPLOYEE, content=reply)
        try:
            _push_memory(session.id, 'employee', reply)
        except Exception:  # noqa: BLE001
            logger.exception('Redis memory write failed')
        update_after_exchange(session, message, reply, profile)
        # Quota counts conversations, not messages. Use F() so concurrent chats don't clobber each other.
        Workspace.objects.filter(pk=workspace.pk).update(
            conversations_used=F('conversations_used') + (1 if first_reply else 0),
            tokens_used=F('tokens_used') + max(len(message.split()) + len(reply.split()), 1),
        )
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
        for token_text in rag_tokens():
            chunks.append(token_text)
            yield f'data: {json.dumps(token_text)}\n\n'
        reply = ''.join(chunks).strip() or FALLBACK_REPLY
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
@widget_endpoint
def widget_poll(request):
    """Widget polls for new human/employee messages (takeover replies, staff notes)."""
    token = request.GET.get('token', '')
    visitor_id = _clean_visitor_id(request.GET.get('visitor_id'), required=True)
    after_id = _to_int(request.GET.get('after_id'), 0)
    employee = _get_employee(token)
    session = _visitor_session(employee, request.GET.get('session_id'), visitor_id)
    msgs = Message.objects.filter(session=session, id__gt=after_id).exclude(role=Message.Role.VISITOR)
    return JsonResponse({
        'session_id': session.id,
        'human_mode': session.is_human_mode,
        'status': session.status,
        'messages': [_serialize_message(m) for m in msgs[:50]],
    })


@csrf_exempt
@require_POST
@widget_endpoint
def widget_lead(request):
    """Public lead-capture API for custom forms on the customer's site."""
    body = _json_body(request)
    _rate_limit(request, 'lead', 5)

    employee = _get_employee(body.get('token'))
    caps = employee.capabilities if employee.capabilities is not None else employee.default_capabilities()
    if 'collect_contact' not in caps:
        raise WidgetError('Lead capture is disabled for this AI Employee.', status=403)
    workspace = employee.workspace

    def field(name, limit):
        value = body.get(name)
        return value.strip()[:limit] if isinstance(value, str) else ''

    name, email, phone = field('name', 150), field('email', 254), field('phone', 40)
    if email:
        try:
            validate_email(email)
        except ValidationError:
            raise WidgetError('Invalid email address') from None
    if not (email or phone):
        raise WidgetError('email or phone required')

    visitor_id = body.get('visitor_id')
    profile = None
    session = None
    if visitor_id:
        visitor_id = _clean_visitor_id(visitor_id)
        profile = get_or_create_profile(workspace, visitor_id)
        if body.get('session_id'):
            session = ChatSession.objects.filter(
                pk=_to_int(body['session_id']), employee=employee, visitor_id=visitor_id,
            ).first()

    from apps.leads.models import Lead

    if profile:
        profile.name = name or profile.name
        profile.email = email or profile.email
        profile.phone = phone or profile.phone
        profile.save(update_fields=['name', 'email', 'phone', 'updated_at'])

    lead = Lead.objects.create(
        workspace=workspace,
        employee=employee,
        session=session,
        name=name,
        email=email,
        phone=phone,
        intent_summary=field('intent', 500),
        source=Lead.Source.FORM,
    )
    return JsonResponse({'ok': True, 'lead_id': lead.id})


# ---------------------------------------------------------------------------
# Team dashboard views (conversations inbox, tasks)
# ---------------------------------------------------------------------------
from urllib.parse import urlencode  # noqa: E402

from django.core.paginator import Paginator  # noqa: E402
from django.db.models import Exists, OuterRef, Subquery  # noqa: E402
from django.utils.http import url_has_allowed_host_and_scheme  # noqa: E402

DASHBOARD_PAGE_SIZE = 25


def _exclude_test_sessions(qs, prefix=''):
    """Drop playground/test sessions once ChatSession.is_test exists (guarded for rollout)."""
    if any(f.name == 'is_test' for f in ChatSession._meta.get_fields()):
        return qs.exclude(**{f'{prefix}is_test': True})
    return qs


def _clean_filters(request, keys):
    return {k: (request.GET.get(k) or '').strip() for k in keys}


def _querystring(filters):
    return urlencode({k: v for k, v in filters.items() if v})


@login_required
def conversations_list(request):
    from apps.leads.views import _day_bounds, _parse_date

    workspace = user_workspace(request.user)
    filters = _clean_filters(request, ('q', 'employee', 'mode', 'date_from', 'date_to', 'attention'))

    last_msg = Message.objects.filter(session=OuterRef('pk')).order_by('-created_at', '-id')
    visitor = VisitorProfile.objects.filter(
        workspace=OuterRef('employee__workspace'), visitor_id=OuterRef('visitor_id'),
    )
    sessions = (
        _exclude_test_sessions(ChatSession.objects.filter(employee__workspace=workspace))
        .select_related('employee', 'taken_over_by')
        .annotate(
            last_content=Subquery(last_msg.values('content')[:1]),
            last_role=Subquery(last_msg.values('role')[:1]),
            last_created=Subquery(last_msg.values('created_at')[:1]),
            visitor_name=Subquery(visitor.values('name')[:1]),
            visitor_email=Subquery(visitor.values('email')[:1]),
        )
    )

    q = filters['q']
    if q:
        sessions = sessions.filter(
            Q(visitor_id__icontains=q)
            | Q(visitor_name__icontains=q)
            | Q(visitor_email__icontains=q)
            | Exists(Message.objects.filter(session=OuterRef('pk'), content__icontains=q))
        )
    if filters['employee'].isdigit():
        sessions = sessions.filter(employee_id=int(filters['employee']))
    else:
        filters['employee'] = ''
    if filters['mode'] == 'human':
        sessions = sessions.filter(status=ChatSession.Status.HUMAN)
    elif filters['mode'] == 'ai':
        sessions = sessions.exclude(status=ChatSession.Status.HUMAN)
    else:
        filters['mode'] = ''
    if filters['attention'] == '1':
        sessions = sessions.filter(Q(status=ChatSession.Status.HUMAN) | Q(last_role=Message.Role.VISITOR))
    else:
        filters['attention'] = ''
    date_from, date_to = _parse_date(filters['date_from']), _parse_date(filters['date_to'])
    if date_from:
        sessions = sessions.filter(last_message_at__gte=_day_bounds(date_from))
    else:
        filters['date_from'] = ''
    if date_to:
        sessions = sessions.filter(last_message_at__lt=_day_bounds(date_to, end=True))
    else:
        filters['date_to'] = ''

    sessions = sessions.order_by('-last_message_at', '-id')
    page_obj = Paginator(sessions, DASHBOARD_PAGE_SIZE).get_page(request.GET.get('page'))
    for s in page_obj.object_list:
        s.needs_attention = s.is_human_mode or s.last_role == Message.Role.VISITOR
        s.last_role_label = Message.Role(s.last_role).label if s.last_role in Message.Role.values else ''

    return render(request, 'chat/conversations.html', {
        'workspace': workspace,
        'page_obj': page_obj,
        'sessions': page_obj.object_list,
        'filters': filters,
        'is_filtered': any(filters.values()),
        'querystring': _querystring(filters),
        'employees': AIEmployee.objects.filter(workspace=workspace).order_by('name') if workspace else [],
    })


@login_required
def conversation_detail(request, pk):
    workspace = user_workspace(request.user)
    session = get_object_or_404(
        ChatSession.objects.select_related('employee', 'taken_over_by'),
        pk=pk,
        employee__workspace=workspace,
    )
    msgs = session.messages.select_related('author').all()
    profile = VisitorProfile.objects.filter(workspace=workspace, visitor_id=session.visitor_id).first()
    role_labels = dict(Message.Role.choices)
    role_labels[Message.Role.EMPLOYEE] = session.employee.name
    return render(request, 'chat/conversation_detail.html', {
        'session': session,
        'chat_messages': msgs,
        'visitor_profile': profile,
        'session_leads': session.leads.all()[:5],
        'role_labels': role_labels,
        'workspace': workspace,
    })


@login_required
@require_POST
def conversation_takeover(request, pk):
    workspace = user_workspace(request.user)
    session = get_object_or_404(ChatSession, pk=pk, employee__workspace=workspace)
    session.status = ChatSession.Status.HUMAN
    session.taken_over_by = request.user
    session.taken_over_at = timezone.now()
    session.save(update_fields=['status', 'taken_over_by', 'taken_over_at', 'last_message_at'])
    Message.objects.create(
        session=session,
        role=Message.Role.SYSTEM,
        content=f'{(request.user.get_full_name() or request.user.email)} joined the conversation.',
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
        content=f'{(request.user.get_full_name() or request.user.email)} returned control to {session.employee.name}.',
        author=request.user,
    )
    messages.success(request, f'{session.employee.name} is handling replies again.')
    return redirect('conversation_detail', pk=session.pk)


@login_required
@require_POST
def conversation_reply(request, pk):
    workspace = user_workspace(request.user)
    session = get_object_or_404(ChatSession, pk=pk, employee__workspace=workspace)
    content = (request.POST.get('content') or '').strip()
    if not content:
        return JsonResponse({'error': 'Empty message'}, status=400)
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
        'messages': [_serialize_message(m) for m in msgs],
    })


@login_required
def tasks_list(request):
    workspace = user_workspace(request.user)
    filters = _clean_filters(request, ('status', 'type'))
    tasks = (
        _exclude_test_sessions(EmployeeTask.objects.filter(workspace=workspace), prefix='session__')
        .select_related('employee', 'lead', 'session')
    )
    if filters['status'] in EmployeeTask.Status.values:
        tasks = tasks.filter(status=filters['status'])
    else:
        filters['status'] = ''
    if filters['type'] in EmployeeTask.TaskType.values:
        tasks = tasks.filter(task_type=filters['type'])
    else:
        filters['type'] = ''
    page_obj = Paginator(tasks.order_by('-created_at', '-id'), DASHBOARD_PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'chat/tasks.html', {
        'workspace': workspace,
        'page_obj': page_obj,
        'tasks': page_obj.object_list,
        'filters': filters,
        'is_filtered': any(filters.values()),
        'querystring': _querystring(filters),
        'status_choices': EmployeeTask.Status.choices,
        'type_choices': EmployeeTask.TaskType.choices,
    })


@login_required
@require_POST
def task_update_status(request, pk):
    workspace = user_workspace(request.user)
    task = get_object_or_404(EmployeeTask, pk=pk, workspace=workspace)
    status = request.POST.get('status')
    wants_json = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    if status not in EmployeeTask.Status.values:
        if wants_json:
            return JsonResponse({'ok': False, 'error': 'Invalid status'}, status=400)
        messages.error(request, 'Choose a valid task status.')
        return redirect('employee_tasks')
    task.status = status
    task.save(update_fields=['status', 'updated_at'])
    if wants_json:
        return JsonResponse({'ok': True, 'status': task.status, 'label': task.get_status_display()})
    messages.success(request, f'Task marked as {task.get_status_display().lower()}.')
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect('employee_tasks')