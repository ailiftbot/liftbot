import json
import logging
import uuid

import redis
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.employees.models import AIEmployee
from apps.workspaces.models import Workspace
from apps.workspaces.views import user_workspace

from .actions import handle_widget_action, process_visitor_turn
from .memory import (
    build_visitor_context_for_prompt,
    get_or_create_profile,
    get_resume_context,
    update_after_exchange,
)
from .models import ChatSession, EmployeeTask, Message
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
    caps = employee.capabilities or employee.default_capabilities()
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
        'employees': [_employee_payload(e) for e in employees],
    })


@require_GET
def widget_config(request):
    token = request.GET.get('token', '')
    visitor_id = request.GET.get('visitor_id', '')
    employee = _get_employee(token)
    workspace = employee.workspace
    payload = _employee_payload(employee)

    if visitor_id and 'remember_visitors' in (employee.capabilities or employee.default_capabilities()):
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

    return JsonResponse(payload)


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
    msgs = Message.objects.filter(session=session, id__gt=after_id).exclude(role=Message.Role.VISITOR)
    return JsonResponse({
        'session_id': session.id,
        'human_mode': session.is_human_mode,
        'status': session.status,
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


@login_required
def conversations_list(request):
    workspace = user_workspace(request.user)
    sessions = (
        ChatSession.objects
        .filter(employee__workspace=workspace)
        .select_related('employee', 'taken_over_by')
        .prefetch_related('messages')[:50]
    )
    return render(request, 'chat/conversations.html', {'sessions': sessions, 'workspace': workspace})


@login_required
def conversation_detail(request, pk):
    workspace = user_workspace(request.user)
    session = get_object_or_404(
        ChatSession.objects.select_related('employee', 'taken_over_by'),
        pk=pk,
        employee__workspace=workspace,
    )
    msgs = session.messages.select_related('author').all()
    return render(request, 'chat/conversation_detail.html', {
        'session': session,
        'messages': msgs,
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
