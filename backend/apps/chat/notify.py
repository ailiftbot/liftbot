import logging

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


def notify_team_event(workspace, employee, event_type: str, payload: dict):
    """Email + optional webhook when AI Employee does work."""
    _send_email(workspace, employee, event_type, payload)
    _send_webhook(workspace, event_type, payload)


def _send_email(workspace, employee, event_type: str, payload: dict):
    email = getattr(employee, 'handoff_email', '') or getattr(workspace.owner, 'email', None)
    if not email:
        return
    try:
        send_mail(
            subject=f'[LiftBot] {event_type.replace("_", " ").title()} — {employee.name}',
            message=(
                f'AI Employee: {employee.name}\n'
                f'Workspace: {workspace.name}\n'
                f'Event: {event_type}\n\n'
                f'{payload}\n'
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@liftbot.ai'),
            recipient_list=[email],
            fail_silently=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception('Email notify failed')


def _send_webhook(workspace, event_type: str, payload: dict):
    url = getattr(workspace, 'webhook_url', '') or ''
    if not url:
        return
    try:
        requests.post(
            url,
            json={
                'event': event_type,
                'workspace': workspace.name,
                'workspace_id': workspace.id,
                'timestamp': timezone.now().isoformat(),
                'data': payload,
            },
            timeout=8,
        )
    except Exception:  # noqa: BLE001
        logger.exception('Webhook notify failed for %s', url)
