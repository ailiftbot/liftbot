"""
Outgoing email on behalf of an AI Employee.

Each employee can have its own SMTP mailbox (EmployeeMailbox), so visitors get
booking confirmations, contact acknowledgements and transcripts from the
business's address. Without a working mailbox the platform account sends
instead, with Reply-To pointing at the business.

SMTP hosts are customer-supplied, so connections are only allowed to public IPs
on standard mail ports (checked again right before connecting).
"""
import logging
import socket
from datetime import timedelta, timezone as dt_timezone
from email.utils import formataddr, parseaddr

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.smtp import EmailBackend
from django.utils import timezone
from django.utils.html import escape, linebreaks

from apps.workspaces.net import is_public_ip

logger = logging.getLogger(__name__)

ALLOWED_SMTP_PORTS = (25, 465, 587, 2525)
# Visitor-facing emails per employee per day — caps abuse of the public widget.
DAILY_VISITOR_EMAIL_CAP = 300


# ---------------------------------------------------------------- safety

def validate_smtp_host(host: str, port: int):
    """Raise ValidationError unless host:port is a public mail server address."""
    host = (host or '').strip()
    if not host:
        raise ValidationError('Enter the SMTP server, e.g. smtp.gmail.com.')
    if port not in ALLOWED_SMTP_PORTS:
        raise ValidationError(f'Use a standard SMTP port: {", ".join(map(str, ALLOWED_SMTP_PORTS))}.')
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        raise ValidationError(f'We could not find the server "{host}". Check the spelling.') from None
    addresses = {info[4][0].split('%', 1)[0] for info in infos}
    if not addresses or not all(is_public_ip(a) for a in addresses):
        raise ValidationError('That server points to a private or internal address.')


class SafeSMTPBackend(EmailBackend):
    """SMTP backend that re-checks the host right before connecting (DNS can change)."""

    def open(self):
        if self.connection is None:
            validate_smtp_host(self.host, self.port)
        return super().open()


# ---------------------------------------------------------------- sender selection

def get_mailbox(employee):
    from .models import EmployeeMailbox

    try:
        return employee.mailbox
    except EmployeeMailbox.DoesNotExist:
        return None


def prefs(employee) -> dict:
    """Which visitor emails this employee sends. Defaults to all on."""
    mb = get_mailbox(employee)
    return {
        'booking': mb.send_booking_confirmations if mb else True,
        'contact': mb.send_contact_confirmations if mb else True,
        'transcript': mb.send_transcripts if mb else True,
    }


def mailbox_connection(mailbox, timeout=None):
    return SafeSMTPBackend(
        host=mailbox.smtp_host,
        port=mailbox.smtp_port,
        username=mailbox.smtp_username or None,
        password=mailbox.get_password() or None,
        use_tls=mailbox.security == mailbox.Security.STARTTLS,
        use_ssl=mailbox.security == mailbox.Security.SSL,
        timeout=timeout or getattr(settings, 'EMAIL_TIMEOUT', 10),
        fail_silently=False,
    )


def _company(employee) -> str:
    return employee.workspace.name if employee.workspace_id else 'our team'


def _platform_sender(employee):
    platform_addr = parseaddr(settings.DEFAULT_FROM_EMAIL)[1] or settings.DEFAULT_FROM_EMAIL
    from_email = formataddr((f'{employee.name} at {_company(employee)}', platform_addr))
    reply = employee.handoff_email or getattr(employee.workspace.owner, 'email', '')
    return from_email, [reply] if reply else []


def _mailbox_sender(employee, mailbox):
    from_email = formataddr((mailbox.from_name or employee.name, mailbox.from_email))
    return from_email, [mailbox.reply_to] if mailbox.reply_to else []


def _build(from_email, reply_to, to, subject, text, html, attachments, connection=None):
    msg = EmailMultiAlternatives(
        subject=subject, body=text, from_email=from_email, to=to, reply_to=reply_to or None,
        connection=connection,
    )
    if html:
        msg.attach_alternative(html, 'text/html')
    for name, content, mimetype in attachments or ():
        msg.attach(name, content, mimetype)
    return msg


def _record(mailbox, ok: bool, error: str = ''):
    from .models import EmployeeMailbox

    EmployeeMailbox.objects.filter(pk=mailbox.pk).update(
        last_tested_at=timezone.now(), last_test_ok=ok, last_error=error[:500],
    )


# ---------------------------------------------------------------- sending

@shared_task(ignore_result=True)
def deliver(employee_id, to, subject, text, html='', attachments=None, team=False):
    from .models import AIEmployee

    employee = AIEmployee.objects.select_related('workspace', 'workspace__owner').filter(pk=employee_id).first()
    if employee is None or not to:
        return
    to = [to] if isinstance(to, str) else list(to)
    mailbox = get_mailbox(employee)
    use_mailbox = mailbox is not None and mailbox.is_configured and (not team or mailbox.use_for_team_alerts)

    if use_mailbox:
        from_email, reply_to = _mailbox_sender(employee, mailbox)
        try:
            _build(from_email, reply_to, to, subject, text, html, attachments,
                   connection=mailbox_connection(mailbox)).send(fail_silently=False)
            if mailbox.last_test_ok is not True:
                _record(mailbox, True)
            return
        except Exception as exc:  # noqa: BLE001 — fall back so the visitor still gets the email
            logger.warning('Mailbox send failed for employee %s, using platform sender: %s', employee_id, exc)
            _record(mailbox, False, f'{type(exc).__name__}: {exc}')

    from_email, reply_to = _platform_sender(employee)
    if use_mailbox and mailbox.reply_to:
        reply_to = [mailbox.reply_to]
    elif use_mailbox:
        reply_to = [mailbox.from_email]
    try:
        _build(from_email, reply_to, to, subject, text, html, attachments).send(fail_silently=False)
    except Exception:  # noqa: BLE001
        logger.exception('Email to %s failed for employee %s', to, employee_id)


def _under_daily_cap(employee) -> bool:
    key = f'liftbot:visitor-mail:{employee.pk}:{timezone.localdate().isoformat()}'
    try:
        cache.add(key, 0, timeout=60 * 60 * 26)
        count = cache.incr(key)
    except Exception:  # noqa: BLE001 — cache down: fail open
        return True
    return count <= DAILY_VISITOR_EMAIL_CAP


def queue(employee, to, subject, text, html='', attachments=None, team=False):
    """Send in the background (inline if the broker is down)."""
    if not team and not _under_daily_cap(employee):
        logger.warning('Daily visitor email cap reached for employee %s', employee.pk)
        return False
    args = (employee.pk, to, subject, text, html, attachments or [], team)
    try:
        deliver.delay(*args)
    except Exception:  # noqa: BLE001
        logger.warning('Celery broker unavailable; sending email inline')
        deliver(*args)
    return True


def send_test(mailbox, to):
    """Synchronous test send. Returns (ok, error_message) and records the result."""
    employee = mailbox.employee
    from_email, reply_to = _mailbox_sender(employee, mailbox)
    text = (
        f'This is a test email from {employee.name} on LiftBot.\n\n'
        f'If you are reading this, visitors will receive booking confirmations and '
        f'other emails from {mailbox.from_email}.'
    )
    try:
        _build(from_email, reply_to, [to], f'Test email from {employee.name}', text, '', None,
               connection=mailbox_connection(mailbox, timeout=15)).send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        error = _friendly_error(exc)
        _record(mailbox, False, error)
        return False, error
    _record(mailbox, True)
    return True, ''


def _friendly_error(exc) -> str:
    import smtplib

    if isinstance(exc, ValidationError):
        return ' '.join(exc.messages)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ('The server rejected the username or password. For Gmail, turn on 2-Step Verification '
                'and use an App Password, not your normal password.')
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return 'The server refused the From address. Use an address this account is allowed to send as.'
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return 'Connection timed out. Check the server, port and security setting.'
    if isinstance(exc, (ConnectionRefusedError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
        return 'Could not connect. Check the port and security setting (587 = STARTTLS, 465 = SSL/TLS).'
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return 'The server does not support that security mode. Try STARTTLS on 587 or SSL/TLS on 465.'
    return f'{type(exc).__name__}: {exc}'[:300]


# ---------------------------------------------------------------- visitor emails

def _html(paragraphs_text: str, employee) -> str:
    color = employee.brand_color or getattr(employee.workspace, 'brand_color', '') or '#7C3AED'
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:15px;'
        'line-height:1.55;color:#1f2937;max-width:560px">'
        f'{linebreaks(escape(paragraphs_text))}'
        f'<p style="margin-top:24px;color:#6b7280;font-size:13px;border-top:3px solid {escape(color)};'
        f'padding-top:12px">{escape(employee.name)} · {escape(_company(employee))}</p></div>'
    )


def _ics(employee, uid, starts, minutes, summary, organizer_email):
    fmt = '%Y%m%dT%H%M%SZ'
    start = starts.astimezone(dt_timezone.utc)
    end = start + timedelta(minutes=minutes)

    def esc(value):
        return str(value).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')

    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//LiftBot//Bookings//EN', 'METHOD:PUBLISH',
        'BEGIN:VEVENT',
        f'UID:{uid}@liftbot',
        f'DTSTAMP:{timezone.now().astimezone(dt_timezone.utc).strftime(fmt)}',
        f'DTSTART:{start.strftime(fmt)}',
        f'DTEND:{end.strftime(fmt)}',
        f'SUMMARY:{esc(summary)}',
        f'ORGANIZER;CN={esc(_company(employee))}:mailto:{organizer_email}',
        'END:VEVENT', 'END:VCALENDAR',
    ]
    return '\r\n'.join(lines) + '\r\n'


def send_booking_confirmation(employee, task, to_email, visitor_name=''):
    if not (to_email and prefs(employee)['booking'] and task.scheduled_for):
        return False
    details = task.details or {}
    kind = details.get('kind') or 'appointment'
    label = details.get('label') or timezone.localtime(task.scheduled_for).strftime('%a %b %d · %I:%M %p')
    company = _company(employee)
    hello = f'Hi {visitor_name},' if visitor_name else 'Hi there,'
    text = (
        f'{hello}\n\n'
        f'Your {kind} with {company} is booked for {label}.\n\n'
        f'We have attached a calendar invite. If you need to reschedule, just reply to this email.\n\n'
        f'See you soon,\n{employee.name}\n{company}'
    )
    mailbox = get_mailbox(employee)
    organizer = (mailbox.from_email if mailbox and mailbox.is_configured
                 else parseaddr(settings.DEFAULT_FROM_EMAIL)[1])
    ics = _ics(employee, f'task-{task.pk}', task.scheduled_for, 60, f'{kind.capitalize()} with {company}', organizer)
    return queue(
        employee, to_email, f'Your {kind} with {company} is confirmed — {label}', text, _html(text, employee),
        attachments=[['invite.ics', ics, 'text/calendar']],
    )


def send_contact_confirmation(employee, to_email, visitor_name=''):
    if not (to_email and prefs(employee)['contact']):
        return False
    company = _company(employee)
    hello = f'Hi {visitor_name},' if visitor_name else 'Hi there,'
    text = (
        f'{hello}\n\n'
        f'Thanks for reaching out to {company}. We have your details and someone from the team '
        f'will get back to you shortly.\n\n'
        f'If there is anything else we should know, just reply to this email.\n\n'
        f'{employee.name}\n{company}'
    )
    return queue(employee, to_email, f'Thanks for contacting {company}', text, _html(text, employee))


def send_transcript(employee, session, to_email):
    if not (to_email and prefs(employee)['transcript']):
        return False
    from apps.chat.models import Message

    company = _company(employee)
    labels = {
        Message.Role.VISITOR: 'You',
        Message.Role.EMPLOYEE: employee.name,
        Message.Role.HUMAN: f'{company} team',
    }
    lines = []
    for m in session.messages.exclude(role=Message.Role.SYSTEM).order_by('id')[:200]:
        when = timezone.localtime(m.created_at).strftime('%b %d, %I:%M %p')
        lines.append(f'{labels.get(m.role, m.role)} ({when}):\n{m.content}\n')
    if not lines:
        return False
    text = (
        f'Here is a copy of your conversation with {company}.\n\n' + '\n'.join(lines)
        + f'\nReply to this email if you have more questions.\n\n{employee.name}\n{company}'
    )
    return queue(employee, to_email, f'Your conversation with {company}', text, _html(text, employee))
