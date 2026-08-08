import logging
import re
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.leads.models import Lead

from .models import EmployeeTask
from .notify import notify_team_event

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_RE = re.compile(r'(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{4}')

SCHEDULE_KEYWORDS = (
    'schedule', 'book', 'appointment', 'visit', 'meeting', 'callback', 'call back', 'demo',
)
HANDOFF_KEYWORDS = (
    'speak to', 'talk to', 'human', 'sales team', 'your team', 'connect me', 'call me', 'reach out',
)
QUALIFY_KEYWORDS = (
    'interested in', 'looking for', 'budget', 'need', 'want to buy', 'price', 'cost', 'timeline',
)


def extract_contact(text: str) -> dict:
    email = EMAIL_RE.search(text)
    phone = PHONE_RE.search(text)
    return {
        'email': email.group(0) if email else '',
        'phone': phone.group(0).strip() if phone else '',
    }


def detect_intents(text: str) -> list[str]:
    lower = text.lower()
    intents = []
    if any(k in lower for k in SCHEDULE_KEYWORDS):
        intents.append(EmployeeTask.TaskType.SCHEDULE)
    if any(k in lower for k in HANDOFF_KEYWORDS):
        intents.append(EmployeeTask.TaskType.HANDOFF)
    if any(k in lower for k in QUALIFY_KEYWORDS):
        intents.append(EmployeeTask.TaskType.QUALIFY)
    return intents


def upsert_lead_from_profile(workspace, employee, session, profile, message: str):
    contact = extract_contact(message)
    if contact['email']:
        profile.email = contact['email']
    if contact['phone']:
        profile.phone = contact['phone']
    if contact['email'] or contact['phone']:
        profile.save(update_fields=['email', 'phone', 'updated_at'])

    if not (profile.email or profile.phone):
        return None

    lead = Lead.objects.filter(workspace=workspace, session=session).first()
    if not lead and profile.email:
        lead = Lead.objects.filter(workspace=workspace, email=profile.email).order_by('-created_at').first()
    if lead:
        lead.name = profile.name or lead.name
        lead.phone = profile.phone or lead.phone
        lead.email = profile.email or lead.email
        lead.intent_summary = message[:300]
        lead.employee = employee
        lead.save()
        return lead

    return Lead.objects.create(
        workspace=workspace,
        employee=employee,
        session=session,
        name=profile.name,
        email=profile.email,
        phone=profile.phone,
        intent_summary=message[:300],
        source=Lead.Source.CONVERSATION,
    )


def create_task(workspace, employee, session, task_type, title, details, lead=None, scheduled_for=None):
    task = EmployeeTask.objects.create(
        workspace=workspace,
        employee=employee,
        session=session,
        lead=lead,
        task_type=task_type,
        title=title,
        details=details,
        scheduled_for=scheduled_for,
    )
    notify_team_event(workspace, employee, task_type, {
        'task_id': task.id,
        'title': title,
        'details': details,
        'scheduled_for': scheduled_for.isoformat() if scheduled_for else None,
    })
    task.notified_at = timezone.now()
    task.save(update_fields=['notified_at'])
    return task


def create_tasks_for_intents(workspace, employee, session, message: str, profile, lead=None) -> list[EmployeeTask]:
    from apps.chat.constants import CAPABILITY_NOTIFY, CAPABILITY_SCHEDULE, CAPABILITY_QUALIFY

    caps = set(employee.capabilities or [])
    created = []
    for intent in detect_intents(message):
        if intent == EmployeeTask.TaskType.SCHEDULE and CAPABILITY_SCHEDULE not in caps:
            continue
        if intent == EmployeeTask.TaskType.HANDOFF and CAPABILITY_NOTIFY not in caps:
            continue
        if intent == EmployeeTask.TaskType.QUALIFY and CAPABILITY_QUALIFY not in caps:
            continue

        title_map = {
            EmployeeTask.TaskType.SCHEDULE: f'Schedule request via {employee.name}',
            EmployeeTask.TaskType.HANDOFF: f'Team handoff requested — {employee.name}',
            EmployeeTask.TaskType.QUALIFY: f'Qualified visitor — {employee.name}',
        }
        task = create_task(
            workspace, employee, session,
            intent,
            title_map.get(intent, 'Visitor task'),
            {
                'message': message[:500],
                'visitor_id': profile.visitor_id,
                'visitor_name': profile.name,
                'visitor_email': profile.email,
                'visitor_phone': profile.phone,
                'preferences': profile.preferences,
            },
            lead=lead,
        )
        created.append(task)
    return created


def process_visitor_turn(workspace, employee, session, profile, message: str) -> dict:
    lead = upsert_lead_from_profile(workspace, employee, session, profile, message)
    tasks = create_tasks_for_intents(workspace, employee, session, message, profile, lead=lead)
    return {
        'lead_id': lead.id if lead else None,
        'tasks_created': [{'id': t.id, 'type': t.task_type, 'title': t.title} for t in tasks],
    }


def handle_widget_action(workspace, employee, session, profile, action: str, data: dict) -> dict:
    """Structured widget actions: collect_contact, schedule, handoff, request_human."""
    from apps.chat.constants import (
        CAPABILITY_COLLECT, CAPABILITY_NOTIFY, CAPABILITY_SCHEDULE,
    )

    caps = set(employee.capabilities or employee.default_capabilities())
    lead = None

    if action == 'collect_contact':
        if CAPABILITY_COLLECT not in caps:
            return {'ok': False, 'error': 'This employee cannot collect contacts.'}
        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip()
        phone = (data.get('phone') or '').strip()
        if name:
            profile.name = name
        if email:
            profile.email = email
        if phone:
            profile.phone = phone
        profile.save(update_fields=['name', 'email', 'phone', 'updated_at'])
        lead = upsert_lead_from_profile(
            workspace, employee, session, profile,
            f'Contact shared: {name} {email} {phone}'.strip(),
        )
        task = create_task(
            workspace, employee, session,
            EmployeeTask.TaskType.CONTACT,
            f'Contact collected by {employee.name}',
            {'name': name, 'email': email, 'phone': phone, 'visitor_id': profile.visitor_id},
            lead=lead,
        )
        return {
            'ok': True,
            'message': f'Thanks{f", {name}" if name else ""}! I have your details and will connect you with the team.',
            'lead_id': lead.id if lead else None,
            'task_id': task.id,
        }

    if action == 'schedule':
        if CAPABILITY_SCHEDULE not in caps:
            return {'ok': False, 'error': 'This employee cannot schedule.'}
        slot_id = data.get('slot_id') or data.get('starts_at')
        label = data.get('label') or slot_id
        starts = parse_datetime(slot_id) if slot_id else None
        if starts and timezone.is_naive(starts):
            starts = timezone.make_aware(starts)
        lead = upsert_lead_from_profile(
            workspace, employee, session, profile,
            f'Requested schedule: {label}',
        )
        task = create_task(
            workspace, employee, session,
            EmployeeTask.TaskType.SCHEDULE,
            f'Scheduled: {label}',
            {
                'slot_id': slot_id,
                'label': label,
                'visitor_id': profile.visitor_id,
                'visitor_name': profile.name,
                'visitor_email': profile.email,
                'visitor_phone': profile.phone,
            },
            lead=lead,
            scheduled_for=starts,
        )
        return {
            'ok': True,
            'message': f"You're booked for {label}. The team will confirm shortly.",
            'task_id': task.id,
            'lead_id': lead.id if lead else None,
        }

    if action in ('handoff', 'request_human'):
        if CAPABILITY_NOTIFY not in caps and action == 'handoff':
            return {'ok': False, 'error': 'This employee cannot hand off yet.'}
        lead = upsert_lead_from_profile(
            workspace, employee, session, profile,
            data.get('note') or 'Visitor requested team handoff',
        )
        task = create_task(
            workspace, employee, session,
            EmployeeTask.TaskType.HANDOFF,
            f'Team handoff — {employee.name}',
            {
                'note': data.get('note', ''),
                'visitor_id': profile.visitor_id,
                'visitor_name': profile.name,
                'visitor_email': profile.email,
                'visitor_phone': profile.phone,
            },
            lead=lead,
        )
        return {
            'ok': True,
            'message': 'I am connecting you with a teammate now. Please stay here — someone will join shortly.',
            'task_id': task.id,
            'request_takeover': True,
        }

    return {'ok': False, 'error': f'Unknown action: {action}'}
