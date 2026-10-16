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
_PLACES = (
    r'site|office|store|showroom|property|project|flat|apartment|villa|house|plot|clinic|'
    r'place|location|centre|center|shop|outlet|school|campus|studio|salon|hotel|factory|warehouse'
)
# Order matters: the first kind that matches names the booking.
BOOKING_KINDS = (
    ('site visit', re.compile(
        r'\b(site visit|site tour|viewing|property tour|'
        rf'(visit|see|tour|view|check out) (your|the|this|that) ({_PLACES})|'
        r'(schedule|book|plan|arrange|fix|set up|organi[sz]e) (a |an |my |the )?(site |property )?(visit|tour|viewing)|'
        r'(come|drop) (by|over|in|and see|to see|to visit)|come (and )?visit)\b'
    )),
    ('demo', re.compile(
        r'\b((request|book|schedule|see|get|want|need|arrange|set up) (a |an |the )?(free |quick |live |product )?demo)\b'
    )),
    ('call', re.compile(
        r'\b(call ?back|call me|give me a call|ring me|'
        r'(schedule|book|set up|arrange|fix|have|get on|hop on|jump on) (a |an )?(quick |short |phone |video |zoom |discovery )?call|'
        r'(phone|video|zoom|discovery|quick) call)\b'
    )),
    ('meeting', re.compile(
        r'\b(meeting|meet (you|your team|in person|someone|face to face)|can we meet|'
        r'(book|schedule|get|want|need) (a |an )?consultation)\b'
    )),
    ('appointment', re.compile(
        r'\b(appointment|schedule (a|an)|book (a|an|me)|reserve (a|an) (slot|time))\b'
    )),
)
HANDOFF_RE = re.compile(
    r'\b(speak to|talk to) (a |an |the |your )?(human|person|someone|agent|team|sales)|'
    r'\b(real|actual) (person|human)\b|\bconnect me\b|\breach out to me\b'
)
QUALIFY_RE = re.compile(
    r'\b(interested in (buying|purchasing|your)|want to (buy|purchase|order)|my budget|'
    r'budget (is|of)|ready to (buy|start|sign)|get a quote|pricing for)\b'
)
PACKAGES_RE = re.compile(
    r'\b(packages?|pricing|price list|prices|rates?|tariffs?|plans and|your plans|'
    r'what plans|which plan|subscription|how much|cost(s|ing)?|fees?|charges|'
    r'quotation|offers?|combos?|bundles?|price (of|for)|rate card|menu)\b'
)
PACKAGES_SEARCH_HINT = 'packages plans pricing price cost rates what is included'


def booking_kind(text: str) -> str:
    """'site visit' / 'demo' / 'call' / 'meeting' / 'appointment', or '' if not a booking request."""
    lower = text.lower()
    for kind, pattern in BOOKING_KINDS:
        if pattern.search(lower):
            return kind
    return ''


def wants_packages(text: str) -> bool:
    return bool(PACKAGES_RE.search(text.lower()))


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
    if booking_kind(lower):
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


def _caps(employee) -> set:
    return set(employee.capabilities) if employee.capabilities is not None else set(employee.default_capabilities())


def turn_guidance(employee, message: str) -> dict:
    """
    Per-turn steering for the reply, based on what the visitor is asking for.

    Returns {'prompt': extra system-prompt text, 'search_query': retrieval query
    override ('' = use the message), 'suggest': UI hint for the widget or None}.
    ``suggest`` is {'type': 'schedule', 'kind': ...}, {'type': 'collect_contact',
    'title': ...} or {'type': 'chips', 'options': [{'id', 'label'}]}.
    """
    from apps.chat.constants import CAPABILITY_COLLECT, CAPABILITY_NOTIFY, CAPABILITY_SCHEDULE

    caps = _caps(employee)
    notes, search_query, suggest = [], '', None

    kind = booking_kind(message)
    if kind and CAPABILITY_SCHEDULE in caps:
        notes.append(
            f'The visitor wants to book a {kind}. A time picker appears right under your reply. '
            f'In one or two short sentences, happily confirm you can set up the {kind} and ask them to '
            f'pick a time that suits them below. If they mentioned a day or time, acknowledge it. '
            f'Do not list times and do not ask for contact details yet.'
        )
        suggest = {'type': 'schedule', 'kind': kind}
    elif kind and CAPABILITY_COLLECT in caps:
        notes.append(
            f'The visitor wants to book a {kind}. The team arranges these directly. In one or two short '
            f'sentences, say you will get it set up and ask them to share their name and phone or email '
            f'in the form below so the team can confirm a time.'
        )
        suggest = {'type': 'collect_contact', 'title': f'Where can we reach you to set up the {kind}?'}

    if wants_packages(message):
        notes.append(
            'The visitor wants to see packages, plans or pricing. List every package or plan you know '
            'about, one bullet each, as "- **Name** — price: key inclusions". If prices or packages are '
            'not in the business information, say the team will share the latest pricing rather than '
            'guessing. End with one short line offering the next step.'
        )
        search_query = f'{message} {PACKAGES_SEARCH_HINT}'
        if suggest is None:
            options = []
            if CAPABILITY_SCHEDULE in caps:
                options.append({'id': 'schedule', 'label': 'Book a time'})
            if CAPABILITY_COLLECT in caps:
                options.append({'id': 'collect_contact', 'label': 'Get a quote'})
            if CAPABILITY_NOTIFY in caps:
                options.append({'id': 'handoff', 'label': 'Talk to the team'})
            if options:
                suggest = {'type': 'chips', 'options': options}

    prompt = ''
    if notes:
        prompt = '### THIS TURN ###\n' + '\n'.join(f'- {n}' for n in notes)
    return {'prompt': prompt, 'search_query': search_query, 'suggest': suggest}


def process_visitor_turn(workspace, employee, session, profile, message: str) -> dict:
    lead = upsert_lead_from_profile(workspace, employee, session, profile, message)
    tasks = create_tasks_for_intents(workspace, employee, session, message, profile, lead=lead)
    return {
        'lead_id': lead.id if lead else None,
        'tasks_created': [{'id': t.id, 'type': t.task_type, 'title': t.title} for t in tasks],
    }


MAX_TRANSCRIPTS_PER_SESSION = 3


def _confirm_booking_by_email(employee, task, profile) -> bool:
    """Email the visitor their booking confirmation once, if we know their email."""
    from apps.employees import mailer

    details = task.details or {}
    if not profile.email or details.get('confirmation_sent_to') == profile.email:
        return False
    if not mailer.send_booking_confirmation(employee, task, profile.email, profile.name):
        return False
    task.details = {**details, 'confirmation_sent_to': profile.email}
    task.save(update_fields=['details', 'updated_at'])
    return True


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
        if email:
            # Booked a slot before sharing an email? Send that booking's confirmation now.
            booking = EmployeeTask.objects.filter(
                session=session, task_type=EmployeeTask.TaskType.SCHEDULE, scheduled_for__isnull=False,
            ).order_by('-updated_at').first()
            if booking is not None and _confirm_booking_by_email(employee, booking, profile):
                return {
                    'ok': True,
                    'message': f'Thanks{f", {name}" if name else ""}! Your confirmation is on its way to {email}.',
                    'lead_id': lead.id if lead else None,
                    'task_id': task.id,
                }
            from apps.employees import mailer
            mailer.send_contact_confirmation(employee, email, name)
        return {
            'ok': True,
            'message': f'Thanks{f", {name}" if name else ""}! Got your details — someone from the team will be in touch soon.',
            'lead_id': lead.id if lead else None,
            'task_id': task.id,
        }

    if action == 'schedule':
        if CAPABILITY_SCHEDULE not in caps:
            return {'ok': False, 'error': 'This employee cannot schedule.'}
        slot_id = _text(data.get('slot_id') or data.get('starts_at'), 64)
        label = _text(data.get('label'), 120) or slot_id
        kind = _text(data.get('kind'), 40).lower()
        if kind not in {k for k, _ in BOOKING_KINDS}:
            kind = 'appointment'
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
            f'Requested {kind}: {label}',
        )
        details = {
            'kind': kind,
            'slot_id': slot_id,
            'label': label,
            'visitor_id': profile.visitor_id,
            'visitor_name': profile.name,
            'visitor_email': profile.email,
            'visitor_phone': profile.phone,
        }
        title = f'{kind.capitalize()}: {label}'
        # A chat message like "can I book a site visit?" already opened a
        # schedule task without a time — complete that one instead of adding a duplicate.
        task = EmployeeTask.objects.filter(
            session=session, task_type=EmployeeTask.TaskType.SCHEDULE,
            status=EmployeeTask.Status.OPEN, scheduled_for__isnull=True,
        ).first()
        if task:
            task.title = title
            task.details = {**(task.details or {}), **details}
            task.scheduled_for = starts
            task.lead = lead or task.lead
            task.save(update_fields=['title', 'details', 'scheduled_for', 'lead', 'updated_at'])
            notify_team_event(workspace, employee, EmployeeTask.TaskType.SCHEDULE, {
                'task_id': task.id, 'title': title, 'details': details, 'scheduled_for': starts.isoformat(),
            })
        else:
            task = create_task(
                workspace, employee, session,
                EmployeeTask.TaskType.SCHEDULE, title, details,
                lead=lead, scheduled_for=starts,
            )
        result = {
            'ok': True,
            'task_id': task.id,
            'lead_id': lead.id if lead else None,
        }
        emailed = _confirm_booking_by_email(employee, task, profile)
        reach = profile.email or profile.phone
        if emailed:
            result['message'] = f'Done! Your {kind} is booked for **{label}**. A confirmation is on its way to {profile.email}.'
        elif reach:
            result['message'] = f"Done! Your {kind} is booked for **{label}**. We'll send the confirmation to {reach}."
        elif CAPABILITY_COLLECT in caps:
            result['message'] = (
                f"Done! Your {kind} is booked for **{label}**. "
                f"What's the best phone number or email to send the confirmation to?"
            )
            result['suggest'] = {'type': 'collect_contact', 'title': 'Where should we send the confirmation?'}
        else:
            result['message'] = f'Done! Your {kind} is booked for **{label}**. The team will confirm shortly.'
        return result

    if action == 'email_transcript':
        from apps.employees import mailer

        if not mailer.prefs(employee)['transcript']:
            return {'ok': False, 'error': 'Email transcripts are turned off for this chat.'}
        email = _text(data.get('email'), 254) or profile.email
        if not email or not EMAIL_RE.fullmatch(email):
            return {'ok': False, 'error': 'Please enter a valid email address.'}
        meta = session.metadata or {}
        if meta.get('transcripts_sent', 0) >= MAX_TRANSCRIPTS_PER_SESSION:
            return {'ok': False, 'error': 'We have already sent this conversation a few times.'}
        if not mailer.send_transcript(employee, session, email):
            return {'ok': False, 'error': 'There is nothing to send yet — say hello first!'}
        session.metadata = {**meta, 'transcripts_sent': meta.get('transcripts_sent', 0) + 1}
        session.save(update_fields=['metadata'])
        if not profile.email:
            profile.email = email
            profile.save(update_fields=['email', 'updated_at'])
        return {'ok': True, 'message': f'Sent! A copy of this conversation is on its way to {email}.'}

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
            'message': "Sure — I'm bringing in a teammate now. Stay right here, they'll join this chat shortly.",
            'task_id': task.id,
            'request_takeover': True,
        }

    return {'ok': False, 'error': f'Unknown action: {action}'}
