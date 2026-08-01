import json
import logging
import uuid

import redis
import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.employees.models import AIEmployee
from apps.leads.models import Lead
from apps.workspaces.views import user_workspace

from .models import ChatSession, Message

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


@require_GET
def widget_config(request):
    token = request.GET.get('token', '')
    employee = get_object_or_404(AIEmployee, widget_token=token, is_active=True)
    color = employee.brand_color or employee.workspace.brand_color
    avatar_url = employee.avatar.url if employee.avatar else ''
    return JsonResponse({
        'name': employee.name,
        'role': employee.public_role_label,
        'greeting': employee.greeting_message,
        'brand_color': color,
        'avatar_url': avatar_url,
        'language': employee.language,
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

    if not token or not message:
        return JsonResponse({'error': 'token and message are required'}, status=400)

    employee = get_object_or_404(
        AIEmployee.objects.select_related('workspace', 'workspace__plan'),
        widget_token=token,
        is_active=True,
    )
    workspace = employee.workspace
    ok, err = _check_quota(workspace)
    if not ok:
        return JsonResponse({'error': err, 'quota_exceeded': True}, status=402)

    if session_id:
        session = get_object_or_404(ChatSession, pk=session_id, employee=employee)
    else:
        session = ChatSession.objects.create(employee=employee, visitor_id=visitor_id)

    Message.objects.create(session=session, role=Message.Role.VISITOR, content=message)
    try:
        _push_memory(session.id, 'visitor', message)
        history = _recent_memory(session.id)
    except Exception:  # noqa: BLE001
        logger.exception('Redis memory unavailable; continuing without cache')
        history = [{'role': 'visitor', 'content': message}]

    payload = {
        'employee_id': str(employee.id),
        'system_prompt': employee.system_prompt,
        'message': message,
        'history': history,
        'top_k': 4,
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
        return ''.join(chunks).strip() or 'I am not sure based on my training materials.'

    def persist_reply(reply: str):
        msg = Message.objects.create(session=session, role=Message.Role.EMPLOYEE, content=reply)
        try:
            _push_memory(session.id, 'employee', reply)
        except Exception:  # noqa: BLE001
            logger.exception('Redis memory write failed')
        workspace.conversations_used += 1
        workspace.tokens_used += max(len(message.split()) + len(reply.split()), 1)
        workspace.save(update_fields=['conversations_used', 'tokens_used', 'updated_at'])
        return msg

    # Playground / simple clients: return JSON (avoids SSE + gunicorn stream issues)
    if not want_stream:
        reply = collect_reply()
        msg = persist_reply(reply)
        return JsonResponse({
            'reply': reply,
            'session_id': session.id,
            'message_id': msg.id,
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
        reply = ''.join(chunks).strip() or 'I am not sure based on my training materials.'
        msg = persist_reply(reply)
        yield f'data: {json.dumps({"session_id": session.id, "message_id": msg.id, "done": True})}\n\n'
        yield 'data: [DONE]\n\n'

    response = StreamingHttpResponse(stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@csrf_exempt
@require_POST
def widget_lead(request):
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    token = body.get('token')
    employee = get_object_or_404(AIEmployee, widget_token=token, is_active=True)
    session = None
    if body.get('session_id'):
        session = ChatSession.objects.filter(pk=body['session_id'], employee=employee).first()
    lead = Lead.objects.create(
        workspace=employee.workspace,
        employee=employee,
        session=session,
        name=body.get('name', ''),
        email=body.get('email', ''),
        phone=body.get('phone', ''),
    )
    return JsonResponse({'ok': True, 'lead_id': lead.id})


@login_required
def conversations_list(request):
    workspace = user_workspace(request.user)
    sessions = (
        ChatSession.objects
        .filter(employee__workspace=workspace)
        .select_related('employee')
        .prefetch_related('messages')[:50]
    )
    return render(request, 'chat/conversations.html', {'sessions': sessions, 'workspace': workspace})
