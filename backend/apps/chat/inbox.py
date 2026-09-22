"""Team-inbox helpers: response timing, private notes, canned replies, AI drafts."""

import logging
import re

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import CannedResponse, ChatSession, Message

logger = logging.getLogger(__name__)

MENTION_RE = re.compile(r'@([\w.+-]+(?:@[\w.-]+)?)')
SHORTCUT_RE = re.compile(r'(?:^|\s)!([a-z0-9_]{1,40})', re.IGNORECASE)

#: How much transcript the AI draft gets to look at.
SUGGEST_HISTORY = 10


def mark_visitor_message(session: ChatSession) -> None:
    """Stamp the first visitor message so response time can be measured from it."""
    if session.first_visitor_at:
        return
    session.first_visitor_at = timezone.now()
    session.save(update_fields=['first_visitor_at'])


def mark_response(session: ChatSession, *, by_human: bool) -> None:
    """Record first-response timings. Safe to call on every outbound message."""
    if not session.first_visitor_at:
        return

    fields = []
    now = timezone.now()
    elapsed = max(int((now - session.first_visitor_at).total_seconds()), 0)

    if not session.first_response_at:
        session.first_response_at = now
        session.first_response_seconds = elapsed
        fields += ['first_response_at', 'first_response_seconds']

    if by_human and session.first_human_response_seconds is None:
        session.first_human_response_seconds = elapsed
        fields.append('first_human_response_seconds')

    if fields:
        session.save(update_fields=fields)


def resolve_session(session: ChatSession, user=None) -> None:
    session.status = ChatSession.Status.RESOLVED
    session.resolved_at = timezone.now()
    session.resolved_by = user
    session.resolution_seconds = max(int((session.resolved_at - session.started_at).total_seconds()), 0)
    session.save(update_fields=['status', 'resolved_at', 'resolved_by', 'resolution_seconds'])


def reopen_session(session: ChatSession) -> None:
    session.status = ChatSession.Status.HUMAN if session.taken_over_by_id else ChatSession.Status.ACTIVE
    session.resolved_at = None
    session.resolved_by = None
    session.resolution_seconds = None
    session.save(update_fields=['status', 'resolved_at', 'resolved_by', 'resolution_seconds'])


def expand_shortcuts(workspace, content: str) -> str:
    """Replace `!shortcut` tokens with their saved canned response body."""
    found = {m.lower() for m in SHORTCUT_RE.findall(content)}
    if not found:
        return content

    saved = {c.shortcut.lower(): c for c in CannedResponse.objects.filter(workspace=workspace, shortcut__in=found)}
    if not saved:
        return content

    def swap(match):
        canned = saved.get(match.group(1).lower())
        if not canned:
            return match.group(0)
        lead = match.group(0)[: match.start(1) - match.start(0) - 1]
        return f'{lead}{canned.body}'

    used = [c for key, c in saved.items() if key in found]
    for canned in used:
        CannedResponse.objects.filter(pk=canned.pk).update(uses=canned.uses + 1)
    return SHORTCUT_RE.sub(swap, content)


def resolve_mentions(workspace, content: str):
    """Return the teammates @mentioned by username or email in a note."""
    handles = {h.lower() for h in MENTION_RE.findall(content)}
    if not handles:
        return []

    User = get_user_model()
    members = User.objects.filter(memberships__workspace=workspace).distinct()
    matched = []
    for user in members:
        username = (user.get_username() or '').lower()
        email = (user.email or '').lower()
        local = email.split('@')[0] if email else ''
        if username in handles or email in handles or (local and local in handles):
            matched.append(user)
    return matched


def visible_messages(session):
    """Messages the visitor is allowed to receive — notes stay internal."""
    return session.messages.exclude(role__in=Message.INTERNAL_ROLES)


def transcript_for_prompt(session, limit: int = SUGGEST_HISTORY) -> str:
    rows = (
        visible_messages(session)
        .exclude(role=Message.Role.SYSTEM)
        .order_by('-created_at')[:limit]
    )
    labels = {
        Message.Role.VISITOR: 'Visitor',
        Message.Role.EMPLOYEE: 'You',
        Message.Role.HUMAN: 'Teammate',
    }
    lines = [f'{labels.get(m.role, m.role)}: {m.content}' for m in reversed(list(rows))]
    return '\n'.join(lines)


def suggest_reply(session) -> str:
    """Draft a reply for the teammate using the employee's own knowledge (MagicReply)."""
    employee = session.employee
    transcript = transcript_for_prompt(session)
    last_visitor = (
        session.messages.filter(role=Message.Role.VISITOR).order_by('-created_at').first()
    )
    if not last_visitor:
        return ''

    system_prompt = (
        f'{employee.build_system_prompt()}\n\n'
        'You are drafting a reply for a human teammate to send to this visitor. '
        'Write only the message body — no greeting boilerplate, no explanations, '
        'no mention of being a draft. Keep it under 80 words, ground it in the '
        'provided context, and write it in the voice described above.\n\n'
        f'Conversation so far:\n{transcript}'
    )

    payload = {
        'employee_id': str(employee.id),
        'system_prompt': system_prompt,
        'message': last_visitor.content,
        'history': [],
        'top_k': 4,
        'capabilities': employee.capabilities or [],
        'temperature': employee.reply_temperature,
        'fallback_line': employee.fallback_line,
    }

    response = requests.post(
        f'{settings.RAG_SERVICE_URL}/chat',
        json=payload,
        headers={'X-Internal-Token': settings.RAG_INTERNAL_TOKEN},
        timeout=60,
        stream=True,
    )
    response.raise_for_status()

    chunks = []
    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith('data: '):
            continue
        data = line[6:]
        if data == '[DONE]':
            break
        chunks.append(data)
    return ''.join(chunks).strip()
