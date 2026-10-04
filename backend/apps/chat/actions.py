import logging
import re

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.leads.models import Lead

from .models import EmployeeTask
from .notify import notify_team_event

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_RE = re.compile(r'(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{4}')

MIN_PHONE_DIGITS = 10

# Phrase-level patterns: single words like "need" or "price" match almost every
# message and used to flood the team with tasks.
SCHEDULE_RE = re.compile(
    r'\b(schedule (a|an)|book (a|an|me)|appointment|set up a (call|meeting)|'
    r'call ?back|(request|book|schedule) a demo|site visit|viewing)\b'
)
HANDOFF_RE = re.compile(
    r'\b(speak to|talk to) (a |an |the |your )?(human|person|someone|agent|team|sales)|'
    r'\b(real|actual) (person|human)\b|\bconnect me\b|\bcall me\b|\breach out to me\b'
)
QUALIFY_RE = re.compile(
    r'\b(interested in (buying|purchasing|your)|want to (buy|purchase|order)|my budget|'
    r'budget (is|of)|ready to (buy|start|sign)|get a quote|pricing for)\b'
)


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ''


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r'\D', '', raw)
    if len(digits) < MIN_PHONE_DIGITS or len(digits) > 15:
        return ''
    return raw.strip()


def extract_contact(text: str) -> dict:
    email = EMAIL_RE.search(text)
    phone = ''
    for match in PHONE_RE.finditer(text):
        phone = _normalize_phone(match.group(0))
        if phone:
            break
    return {
        'email': email.group(0) if email else '',
        'phone': phone,
    }


def detect_intents(text: str) -> list[str]:
    lower = text.lower()
    intents = []
    if SCHEDULE_RE.search(lower):
        intents.append(EmployeeTask.TaskType.SCHEDULE)
    if HANDOFF_RE.search(lower):
        intents.append(EmployeeTask.TaskType.HANDOFF)
    if QUALIFY_RE.search(lower):
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

    caps = set(employee.capabilities) if employee.capabilities is not None else set(employee.default_capabilities())
    created = []
    for intent in detect_intents(message):
        # One open task per type per conversation is enough.
        if EmployeeTask.objects.filter(
            session=session, task_type=intent, status=EmployeeTask.Status.OPEN,
        ).exists():
            continue
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

    caps = set(employee.capabilities) if employee.capabilities is not None else set(employee.default_capabilities())
    lead = None

    if action == 'collect_contact':
        if CAPABILITY_COLLECT not in caps:
            return {'ok': False, 'error': 'This employee cannot collect contacts.'}
        name = _text(data.get('name'), 150)
        email = _text(data.get('email'), 254)
        phone = _text(data.get('phone'), 40)
        if email and not EMAIL_RE.fullmatch(email):
            return {'ok': False, 'error': 'Please enter a valid email address.'}
        if phone and not _normalize_phone(phone):
            return {'ok': False, 'error': 'Please enter a valid phone number.'}
        if not (email or phone):
            return {'ok': False, 'error': 'Please share an email or phone number so the team can reach you.'}
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
        slot_id = _text(data.get('slot_id') or data.get('starts_at'), 64)
        label = _text(data.get('label'), 120) or slot_id
        try:
            starts = parse_datetime(slot_id) if slot_id else None
        except ValueError:
            starts = None
        if starts is None:
            return {'ok': False, 'error': 'Please pick one of the available times.'}
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
            _text(data.get('note'), 500) or 'Visitor requested team handoff',
        )
        task = create_task(
            workspace, employee, session,
            EmployeeTask.TaskType.HANDOFF,
            f'Team handoff — {employee.name}',
            {
                'note': _text(data.get('note'), 500),
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
