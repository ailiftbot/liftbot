import json
import logging

import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


def notify_team_event(workspace, employee, event_type: str, payload: dict):
    """Email + optional webhook when an AI Employee does work. Sent off the request path."""
    args = (workspace.id, employee.id, event_type, payload)
    try:
        send_team_notification.delay(*args)
    except Exception:  # noqa: BLE001 — broker down: deliver inline rather than drop it
        logger.warning('Celery broker unavailable; sending notification inline')
        send_team_notification(*args)


@shared_task(ignore_result=True)
def send_team_notification(workspace_id, employee_id, event_type: str, payload: dict):
    from apps.employees.models import AIEmployee

    employee = AIEmployee.objects.select_related('workspace', 'workspace__owner').filter(
        pk=employee_id, workspace_id=workspace_id,
    ).first()
    if employee is None:
        return
    _send_email(employee.workspace, employee, event_type, payload)
    _send_webhook(employee.workspace, event_type, payload)


def _send_email(workspace, employee, event_type: str, payload: dict):
    email = getattr(employee, 'handoff_email', '') or getattr(workspace.owner, 'email', None)
    if not email:
        return
    details = payload.get('details') if isinstance(payload.get('details'), dict) else {}
    lines = [
        f'AI Employee: {employee.name}',
        f'Workspace: {workspace.name}',
        f'Event: {event_type.replace("_", " ").title()}',
        '',
        payload.get('title', ''),
    ]
    for key in ('visitor_name', 'visitor_email', 'visitor_phone', 'message'):
        if details.get(key):
            lines.append(f'{key.replace("visitor_", "").replace("_", " ").title()}: {details[key]}')
    if payload.get('scheduled_for'):
        lines.append(f'Requested time: {payload["scheduled_for"]}')
    lines += ['', f'Open your dashboard: {settings.PUBLIC_APP_URL}/chat/tasks/']
    try:
        send_mail(
            subject=f'[LiftBot] {event_type.replace("_", " ").title()} — {employee.name}',
            message='\n'.join(lines),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001
        logger.exception('Email notify failed')


def _send_webhook(workspace, event_type: str, payload: dict):
    from apps.workspaces.net import is_safe_public_url

    url = getattr(workspace, 'webhook_url', '') or ''
    if not url:
        return
    if not is_safe_public_url(url):
        logger.warning('Refusing webhook to non-public URL for workspace %s', workspace.id)
        return
    try:
        requests.post(
            url,
            data=json.dumps({
                'event': event_type,
                'workspace': workspace.name,
                'workspace_id': workspace.id,
                'timestamp': timezone.now().isoformat(),
                'data': payload,
            }, default=str),
            headers={'Content-Type': 'application/json', 'User-Agent': 'LiftBot-Webhook/1.0'},
            timeout=8,
            allow_redirects=False,
        )
    except Exception:  # noqa: BLE001
        logger.exception('Webhook notify failed for workspace %s', workspace.id)
