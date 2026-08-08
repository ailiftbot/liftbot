import json
import logging

import redis
from django.conf import settings
from django.utils import timezone

from .models import ChatSession, Message, VisitorProfile

logger = logging.getLogger(__name__)


def _redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def visitor_profile_key(workspace_id: int, visitor_id: str) -> str:
    return f'liftbot:visitor:{workspace_id}:{visitor_id}'


def get_or_create_profile(workspace, visitor_id: str) -> VisitorProfile:
    profile, _ = VisitorProfile.objects.get_or_create(
        workspace=workspace,
        visitor_id=visitor_id,
        defaults={'preferences': {}},
    )
    return profile


def cache_profile(profile: VisitorProfile):
    try:
        client = _redis()
        key = visitor_profile_key(profile.workspace_id, profile.visitor_id)
        client.setex(
            key,
            60 * 60 * 24 * 30,
            json.dumps({
                'name': profile.name,
                'email': profile.email,
                'phone': profile.phone,
                'summary': profile.conversation_summary,
                'preferences': profile.preferences,
            }),
        )
    except Exception:  # noqa: BLE001
        logger.exception('Failed to cache visitor profile')


def get_resume_context(workspace, visitor_id: str) -> dict:
    profile = VisitorProfile.objects.filter(workspace=workspace, visitor_id=visitor_id).first()
    if not profile or not profile.conversation_summary:
        return {
            'returning_visitor': False,
            'resume_message': '',
            'visitor_name': profile.name if profile else '',
            'last_session_id': None,
        }
    name = profile.name or 'there'
    topic = profile.conversation_summary[:180]
    return {
        'returning_visitor': True,
        'resume_message': (
            f"Welcome back, {name}! Last time we spoke about: {topic}. "
            'Would you like to continue where we left off?'
        ),
        'visitor_name': profile.name,
        'last_session_id': profile.last_session_id,
        'preferences': profile.preferences,
    }


def build_visitor_context_for_prompt(profile: VisitorProfile | None) -> str:
    if not profile:
        return ''
    parts = []
    if profile.name:
        parts.append(f'Visitor name: {profile.name}')
    if profile.email:
        parts.append(f'Email: {profile.email}')
    if profile.phone:
        parts.append(f'Phone: {profile.phone}')
    if profile.conversation_summary:
        parts.append(f'Previous conversation summary: {profile.conversation_summary}')
    if profile.preferences:
        prefs = ', '.join(f'{k}: {v}' for k, v in profile.preferences.items() if v)
        if prefs:
            parts.append(f'Known preferences: {prefs}')
    if not parts:
        return ''
    return 'Returning visitor context:\n' + '\n'.join(parts)


def update_after_exchange(session: ChatSession, visitor_message: str, employee_reply: str, profile: VisitorProfile):
    profile.last_session = session
    profile.last_seen_at = timezone.now()
    snippet = visitor_message.strip()[:220]
    if snippet:
        profile.conversation_summary = snippet
    profile.save(update_fields=['last_session', 'last_seen_at', 'conversation_summary', 'updated_at'])
    cache_profile(profile)


def link_session_messages_to_profile(session: ChatSession, profile: VisitorProfile):
    """Store a short rolling summary from recent visitor messages."""
    recent = (
        Message.objects.filter(session=session, role=Message.Role.VISITOR)
        .order_by('-created_at')[:3]
    )
    if recent:
        profile.conversation_summary = ' | '.join(m.content[:80] for m in reversed(list(recent)))
        profile.save(update_fields=['conversation_summary', 'updated_at'])
        cache_profile(profile)
