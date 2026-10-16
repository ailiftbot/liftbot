import json
import socket
from unittest import mock

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.mail.backends.locmem import EmailBackend as LocmemBackend
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.chat.models import ChatSession, EmployeeTask, Message
from apps.chat.tests_widget import FakeRedis, make_workspace

from . import mailer
from .forms import EmployeeMailboxForm
from .models import EmployeeMailbox

PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('142.250.4.108', 587))]
PRIVATE_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.5', 587))]


def public_dns(*a, **k):
    return PUBLIC_DNS


def make_mailbox(employee, **kw):
    mb = EmployeeMailbox(
        employee=employee, from_name='Maya at Acme', from_email='hello@acme.com', reply_to='sales@acme.com',
        smtp_host='smtp.acme.com', smtp_port=587, smtp_username='hello@acme.com', **kw,
    )
    mb.set_password('app-pass-123')
    mb.save()
    return mb


class SmtpHostValidationTests(TestCase):
    def test_rejects_private_hosts_and_odd_ports(self):
        with mock.patch('apps.employees.mailer.socket.getaddrinfo', return_value=PRIVATE_DNS):
            with self.assertRaisesMessage(ValidationError, 'private or internal'):
                mailer.validate_smtp_host('db', 587)
        with self.assertRaisesMessage(ValidationError, 'standard SMTP port'):
            mailer.validate_smtp_host('smtp.gmail.com', 3306)
        with mock.patch('apps.employees.mailer.socket.getaddrinfo', side_effect=socket.gaierror):
            with self.assertRaisesMessage(ValidationError, 'could not find'):
                mailer.validate_smtp_host('nope.invalid', 587)

    def test_backend_rechecks_host_before_connecting(self):
        backend = mailer.SafeSMTPBackend(host='internal', port=587, fail_silently=False)
        with mock.patch('apps.employees.mailer.socket.getaddrinfo', return_value=PRIVATE_DNS):
            with self.assertRaises(ValidationError):
                backend.open()


class MailboxModelFormTests(TestCase):
    def setUp(self):
        _, self.ws, self.employee = make_workspace()

    def test_password_is_encrypted_and_kept_when_blank(self):
        data = {
            'enabled': 'on', 'from_name': 'Maya', 'from_email': 'hello@acme.com', 'reply_to': '',
            'smtp_host': 'smtp.gmail.com', 'smtp_port': 587, 'security': 'starttls',
            'smtp_username': 'hello@acme.com', 'smtp_password': 's3cret-app-pass',
            'send_booking_confirmations': 'on',
        }
        with mock.patch('apps.employees.mailer.socket.getaddrinfo', side_effect=public_dns):
            form = EmployeeMailboxForm(data, employee=self.employee)
            self.assertTrue(form.is_valid(), form.errors)
            mb = form.save()
        self.assertNotIn('s3cret', mb.smtp_password_encrypted)
        self.assertEqual(mb.get_password(), 's3cret-app-pass')

        data['smtp_password'] = ''
        data['from_name'] = 'Maya 2'
        with mock.patch('apps.employees.mailer.socket.getaddrinfo', side_effect=public_dns):
            form = EmployeeMailboxForm(data, instance=mb, employee=self.employee)
            self.assertTrue(form.is_valid(), form.errors)
            mb = form.save()
        self.assertEqual(mb.get_password(), 's3cret-app-pass')
        self.assertFalse(mb.send_contact_confirmations)

    def test_username_without_password_is_rejected(self):
        data = {'from_email': 'a@acme.com', 'smtp_host': 'smtp.gmail.com', 'smtp_port': 587,
                'security': 'starttls', 'smtp_username': 'a@acme.com'}
        with mock.patch('apps.employees.mailer.socket.getaddrinfo', side_effect=public_dns):
            form = EmployeeMailboxForm(data, employee=self.employee)
            self.assertFalse(form.is_valid())
        self.assertIn('smtp_password', form.errors)


class DeliveryTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # the daily cap counter lives in the cache
        _, self.ws, self.employee = make_workspace()
        self.employee.handoff_email = 'team@acme.com'
        self.employee.save()

    def test_without_mailbox_uses_platform_sender_with_business_reply_to(self):
        mailer.deliver(self.employee.pk, 'visitor@example.com', 'Hi', 'Body')
        msg = mail.outbox[-1]
        self.assertIn('Maya at Acme', msg.from_email)
        self.assertEqual(msg.reply_to, ['team@acme.com'])

    def test_with_mailbox_sends_from_business_address(self):
        mb = make_mailbox(self.employee)
        with mock.patch('apps.employees.mailer.mailbox_connection', return_value=LocmemBackend()) as conn:
            mailer.deliver(self.employee.pk, 'visitor@example.com', 'Hi', 'Body')
        conn.assert_called_once()
        msg = mail.outbox[-1]
        self.assertEqual(msg.from_email, 'Maya at Acme <hello@acme.com>')
        self.assertEqual(msg.reply_to, ['sales@acme.com'])
        mb.refresh_from_db()
        self.assertTrue(mb.last_test_ok)

    def test_mailbox_failure_falls_back_and_records_error(self):
        mb = make_mailbox(self.employee)
        broken = mock.MagicMock()
        broken.send_messages.side_effect = OSError('connection refused')
        with mock.patch('apps.employees.mailer.mailbox_connection', return_value=broken):
            mailer.deliver(self.employee.pk, 'visitor@example.com', 'Hi', 'Body')
        msg = mail.outbox[-1]
        self.assertNotIn('hello@acme.com', msg.from_email)  # platform sender
        self.assertEqual(msg.reply_to, ['sales@acme.com'])
        mb.refresh_from_db()
        self.assertFalse(mb.last_test_ok)
        self.assertIn('connection refused', mb.last_error)

    def test_team_alerts_can_opt_out_of_mailbox(self):
        make_mailbox(self.employee, use_for_team_alerts=False)
        with mock.patch('apps.employees.mailer.mailbox_connection') as conn:
            mailer.deliver(self.employee.pk, 'team@acme.com', 'Alert', 'Body', team=True)
        conn.assert_not_called()

    def test_daily_cap(self):
        with mock.patch.object(mailer, 'DAILY_VISITOR_EMAIL_CAP', 2):
            results = [mailer.queue(self.employee, 'v@example.com', 's', 't') for _ in range(3)]
        self.assertEqual(results, [True, True, False])


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class VisitorEmailFlowTests(TestCase):
    def setUp(self):
        _, self.ws, self.employee = make_workspace()
        self.token = self.employee.widget_token
        p = mock.patch('apps.chat.views._redis', return_value=FakeRedis())
        p.start()
        self.addCleanup(p.stop)

    def action(self, action, data, session_id=None, visitor='v1'):
        return self.client.post('/api/widget/action/', data=json.dumps({
            'token': self.token, 'action': action, 'visitor_id': visitor, 'session_id': session_id, 'data': data,
        }), content_type='application/json')

    def visitor_mail(self):
        return [m for m in mail.outbox if 'v@example.com' in m.to]

    def test_booking_then_contact_sends_one_confirmation_with_invite(self):
        slot = self.ws.next_available_slots(1)[0]
        r = self.action('schedule', {'slot_id': slot['id'], 'label': slot['label'], 'kind': 'site visit'})
        sid = r.json()['session_id']
        self.assertEqual(self.visitor_mail(), [])  # no email known yet

        r = self.action('collect_contact', {'name': 'Ana', 'email': 'v@example.com'}, session_id=sid)
        self.assertIn('confirmation is on its way', r.json()['message'])
        sent = self.visitor_mail()
        self.assertEqual(len(sent), 1)
        self.assertIn('site visit', sent[0].subject)
        self.assertIn('Hi Ana', sent[0].body)
        name, content, mimetype = sent[0].attachments[0]
        self.assertEqual(mimetype, 'text/calendar')
        self.assertIn('BEGIN:VEVENT', content)

        # Sharing details again does not resend the same confirmation.
        self.action('collect_contact', {'name': 'Ana', 'email': 'v@example.com'}, session_id=sid)
        self.assertEqual(len([m for m in self.visitor_mail() if 'confirmed' in m.subject]), 1)

    def test_contact_without_booking_sends_acknowledgement(self):
        self.action('collect_contact', {'name': 'Ana', 'email': 'v@example.com'})
        sent = self.visitor_mail()
        self.assertEqual(len(sent), 1)
        self.assertIn('Thanks for contacting', sent[0].subject)

    def test_preferences_turn_emails_off(self):
        make_mailbox(self.employee, send_contact_confirmations=False)
        self.action('collect_contact', {'name': 'Ana', 'email': 'v@example.com'})
        self.assertEqual(self.visitor_mail(), [])

    def test_transcript_action(self):
        session = ChatSession.objects.create(employee=self.employee, visitor_id='v1')
        Message.objects.create(session=session, role=Message.Role.VISITOR, content='What are your hours?')
        Message.objects.create(session=session, role=Message.Role.EMPLOYEE, content='9 to 6, Mon-Fri.')
        r = self.action('email_transcript', {'email': 'v@example.com'}, session_id=session.id)
        self.assertEqual(r.status_code, 200, r.content)
        body = self.visitor_mail()[0].body
        self.assertIn('What are your hours?', body)
        self.assertIn('9 to 6', body)

        for _ in range(2):
            self.action('email_transcript', {'email': 'v@example.com'}, session_id=session.id)
        r = self.action('email_transcript', {'email': 'v@example.com'}, session_id=session.id)
        self.assertEqual(r.status_code, 400)

        r = self.action('email_transcript', {'email': 'not-an-email'}, session_id=session.id)
        self.assertEqual(r.status_code, 400)

    def test_config_exposes_transcript_flag(self):
        r = self.client.get('/api/widget/config/', {'token': self.token, 'visitor_id': 'v1'})
        self.assertTrue(r.json()['email_transcript'])


class HubEmailTabTests(TestCase):
    def setUp(self):
        self.user, self.ws, self.employee = make_workspace()
        self.client.force_login(self.user)
        self.url = reverse('employee_hub', args=[self.employee.pk])

    def post(self, **extra):
        data = {
            'enabled': 'on', 'from_name': 'Maya', 'from_email': 'hello@acme.com', 'reply_to': '',
            'smtp_host': 'smtp.gmail.com', 'smtp_port': 587, 'security': 'starttls',
            'smtp_username': 'hello@acme.com', 'smtp_password': 'app-pass', 'send_booking_confirmations': 'on',
        }
        data.update(extra)
        with mock.patch('apps.employees.mailer.socket.getaddrinfo', side_effect=public_dns):
            return self.client.post(self.url, data)

    def test_tab_renders(self):
        r = self.client.get(self.url + '?tab=email')
        self.assertContains(r, 'Email (SMTP)')
        self.assertContains(r, 'Not set up')

    def test_save_and_test(self):
        with mock.patch('apps.employees.mailer.mailbox_connection', return_value=LocmemBackend()):
            r = self.post(action='test_email')
        self.assertRedirects(r, self.url + '?tab=email', fetch_redirect_response=False)
        mb = EmployeeMailbox.objects.get(employee=self.employee)
        self.assertTrue(mb.last_test_ok)
        self.assertEqual(mail.outbox[-1].to, [self.user.email])
        page = self.client.get(self.url + '?tab=email')
        self.assertContains(page, 'Connected')

    def test_failed_test_shows_friendly_error(self):
        import smtplib
        broken = mock.MagicMock()
        broken.send_messages.side_effect = smtplib.SMTPAuthenticationError(535, b'bad creds')
        with mock.patch('apps.employees.mailer.mailbox_connection', return_value=broken):
            self.post(action='test_email')
        mb = EmployeeMailbox.objects.get(employee=self.employee)
        self.assertFalse(mb.last_test_ok)
        self.assertIn('App Password', mb.last_error)

    def test_other_workspace_cannot_edit(self):
        other_user, _, _ = make_workspace('other@example.com')
        self.client.force_login(other_user)
        r = self.post(action='update_email')
        self.assertEqual(r.status_code, 404)
        self.assertFalse(EmployeeMailbox.objects.exists())

    def test_remove(self):
        make_mailbox(self.employee)
        self.client.post(self.url, {'action': 'remove_email'})
        self.assertFalse(EmployeeMailbox.objects.exists())
