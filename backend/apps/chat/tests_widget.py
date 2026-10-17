import json
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from apps.billing.models import BillingPlan
from apps.employees.models import AIEmployee
from apps.knowledge.models import KnowledgeSource
from apps.leads.models import Lead
from apps.workspaces.models import Workspace, WorkspaceMembership

from .actions import detect_intents, extract_contact
from .constants import ALL_CAPABILITIES
from .models import ChatSession, EmployeeTask, Message


def make_workspace(email='owner@example.com', active=True):
    user = User.objects.create_user(username=email, email=email, password='pw-123456!')
    user.profile.is_verified = True
    user.profile.save()
    plan, _ = BillingPlan.objects.get_or_create(
        slug='starter',
        defaults=dict(name='Starter', price_monthly=19, conversation_limit=500, token_limit=200_000),
    )
    ws = Workspace.objects.create(name='Acme', owner=user, plan=plan, is_active=active)
    WorkspaceMembership.objects.create(workspace=ws, user=user, role=WorkspaceMembership.Role.OWNER)
    employee = AIEmployee.objects.create(workspace=ws, name='Maya', capabilities=list(ALL_CAPABILITIES))
    return user, ws, employee


class FakeRag:
    def __init__(self, tokens):
        self.lines = [f'data: {json.dumps(t)}' for t in tokens] + ['data: [DONE]']

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        return iter(self.lines)


class FakeRedis:
    def __init__(self):
        self.store = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, *a):
        pass

    def rpush(self, *a):
        pass

    def ltrim(self, *a):
        pass

    def lrange(self, *a):
        return []


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class WidgetApiTests(TestCase):
    def setUp(self):
        self.user, self.ws, self.employee = make_workspace()
        self.token = self.employee.widget_token
        redis_patch = mock.patch('apps.chat.views._redis', return_value=FakeRedis())
        redis_patch.start()
        self.addCleanup(redis_patch.stop)

    def post(self, path, body):
        return self.client.post(f'/api/widget/{path}/', data=json.dumps(body), content_type='application/json')

    def chat(self, message, visitor='v1', session_id=None, tokens=('Hello', '\n- point')):
        with mock.patch('apps.chat.views.requests.post', return_value=FakeRag(tokens)):
            return self.post('message', {
                'token': self.token, 'message': message, 'visitor_id': visitor,
                'session_id': session_id, 'stream': False,
            })

    def test_chat_reply_keeps_newlines_and_counts_one_conversation(self):
        r = self.chat('hi there')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['reply'], 'Hello\n- point')
        r2 = self.chat('and again', session_id=data['session_id'])
        self.assertEqual(r2.status_code, 200)
        self.ws.refresh_from_db()
        self.assertEqual(self.ws.conversations_used, 1)

    def test_session_belongs_to_visitor(self):
        sid = self.chat('hi', visitor='alice').json()['session_id']
        r = self.chat('hijack', visitor='mallory', session_id=sid)
        self.assertEqual(r.status_code, 404)
        r = self.client.get('/api/widget/poll/', {
            'token': self.token, 'visitor_id': 'mallory', 'session_id': sid, 'after_id': 0,
        })
        self.assertEqual(r.status_code, 404)
        r = self.client.get('/api/widget/poll/', {
            'token': self.token, 'visitor_id': 'alice', 'session_id': sid, 'after_id': 0,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['messages']), 1)

    def test_poll_requires_visitor_and_valid_ids(self):
        r = self.client.get('/api/widget/poll/', {'token': self.token, 'session_id': 1})
        self.assertEqual(r.status_code, 400)
        r = self.client.get('/api/widget/poll/', {
            'token': self.token, 'visitor_id': 'v1', 'session_id': 'abc',
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn('error', r.json())

    def test_inactive_workspace_and_bad_token_return_json_404(self):
        self.ws.is_active = False
        self.ws.save()
        r = self.client.get('/api/widget/config/', {'token': self.token})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()['error'], 'Not found')
        self.assertEqual(self.client.get('/api/widget/config/', {'token': 'nope'}).status_code, 404)

    def test_rag_down_returns_502_without_internal_details(self):
        import requests
        with mock.patch('apps.chat.views.requests.post', side_effect=requests.ConnectionError('http://rag:8100 refused')):
            r = self.post('message', {'token': self.token, 'message': 'hi', 'visitor_id': 'v1', 'stream': False})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn('rag', json.dumps(r.json()))

    def test_quota_exceeded(self):
        self.ws.conversations_used = 500
        self.ws.save()
        r = self.chat('hi')
        self.assertEqual(r.status_code, 402)

    def test_rate_limit(self):
        for _ in range(20):
            self.assertEqual(self.chat('hi').status_code, 200)
        self.assertEqual(self.chat('hi').status_code, 429)

    def test_rejects_bad_visitor_id(self):
        r = self.post('message', {'token': self.token, 'message': 'hi', 'visitor_id': 'x' * 100})
        self.assertEqual(r.status_code, 400)

    def test_articles_only_public_sources(self):
        KnowledgeSource.objects.create(
            employee=self.employee, source_type='text', title='Internal pricing sheet',
            content='secret margins', status='ready',
        )
        KnowledgeSource.objects.create(
            employee=self.employee, source_type='faq', title='Opening hours',
            content='9 to 5', status='ready', is_public=True,
        )
        with mock.patch('apps.knowledge.signals.rag_delete_source'):
            titles = [a['title'] for a in self.client.get('/api/widget/articles/', {'token': self.token}).json()['articles']]
            self.assertEqual(titles, ['Opening hours'])
            r = self.client.get('/api/widget/search/', {'token': self.token, 'q': 'secret'})
            self.assertEqual(r.json()['results'], [])

    def test_action_returns_message_id_and_validates_contact(self):
        r = self.post('action', {
            'token': self.token, 'action': 'collect_contact', 'visitor_id': 'v1',
            'data': {'name': 'Ana', 'email': 'not-an-email'},
        })
        self.assertEqual(r.status_code, 400)
        r = self.post('action', {
            'token': self.token, 'action': 'collect_contact', 'visitor_id': 'v1',
            'data': {'name': 'Ana', 'email': 'ana@example.com'},
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(Message.objects.filter(pk=data['message_id']).exists())
        self.assertTrue(Lead.objects.filter(workspace=self.ws, email='ana@example.com').exists())

    def test_lead_api_validates_and_respects_capability(self):
        r = self.post('lead', {'token': self.token, 'email': 'bad'})
        self.assertEqual(r.status_code, 400)
        r = self.post('lead', {'token': self.token, 'email': 'lead@example.com', 'name': 'Lee'})
        self.assertEqual(r.status_code, 200)
        self.employee.capabilities = []
        self.employee.save()
        r = self.post('lead', {'token': self.token, 'email': 'lead2@example.com'})
        self.assertEqual(r.status_code, 403)

    def test_intent_tasks_are_deduplicated_per_session(self):
        sid = self.chat('Can I book a demo please?').json()['session_id']
        self.chat('I would like to book a demo for Friday', session_id=sid)
        session = ChatSession.objects.get(pk=sid)
        self.assertEqual(
            EmployeeTask.objects.filter(session=session, task_type=EmployeeTask.TaskType.SCHEDULE).count(), 1,
        )


class IntentAndContactTests(TestCase):
    def test_common_words_do_not_trigger_tasks(self):
        self.assertEqual(detect_intents('I need to know the price of shipping'), [])
        self.assertEqual(detect_intents('Can I visit your blog?'), [])

    def test_real_intents(self):
        self.assertIn(EmployeeTask.TaskType.HANDOFF, detect_intents('Can I talk to a human?'))
        self.assertIn(EmployeeTask.TaskType.SCHEDULE, detect_intents('I want to book an appointment'))
        self.assertIn(EmployeeTask.TaskType.QUALIFY, detect_intents('My budget is 2M, ready to buy'))

    def test_phone_requires_ten_digits(self):
        self.assertEqual(extract_contact('order 2024 2025 please')['phone'], '')
        self.assertEqual(extract_contact('call +1 415 555 0123')['phone'], '+1 415 555 0123')
        self.assertEqual(extract_contact('mail me at a@b.co')['email'], 'a@b.co')


class ReplyFormattingTests(TestCase):
    def test_bullets_bold_and_paragraphs(self):
        from .formatting import reply_to_html
        html = reply_to_html('Here are our packages:\n- **Basic** — ₹999: 1 visit\n- **Pro** — ₹1,999: 3 visits\n\nWant to book?')
        self.assertEqual(
            html,
            '<p>Here are our packages:</p>'
            '<ul><li><strong>Basic</strong> — ₹999: 1 visit</li><li><strong>Pro</strong> — ₹1,999: 3 visits</li></ul>'
            '<p>Want to book?</p>',
        )

    def test_numbered_list_headings_and_line_breaks(self):
        from .formatting import reply_to_html
        self.assertEqual(reply_to_html('1. One\n2. Two'), '<ol><li>One</li><li>Two</li></ol>')
        self.assertEqual(reply_to_html('### Hours\nMon-Fri\nSat'), '<p><strong>Hours</strong><br>Mon-Fri<br>Sat</p>')

    def test_html_is_escaped_and_only_http_links_allowed(self):
        from .formatting import reply_to_html
        html = reply_to_html('<script>alert(1)</script> **<b>x</b>** [click](javascript:alert(1)) [ok](https://a.co/x?y=1&z=2)')
        self.assertNotIn('<script>', html)
        self.assertNotIn('<b>', html)
        self.assertNotIn('href="javascript', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('<a href="https://a.co/x?y=1&amp;z=2" target="_blank" rel="noopener noreferrer nofollow">ok</a>', html)

    def test_bare_urls_and_emails_are_linked(self):
        from .formatting import reply_to_html
        html = reply_to_html('See https://acme.com/pricing. Or mail sales@acme.com')
        self.assertIn('<a href="https://acme.com/pricing"', html)
        self.assertIn('pricing</a>.', html)
        self.assertIn('href="mailto:sales@acme.com"', html)


class BookingAndPackagesIntentTests(TestCase):
    def test_booking_kinds(self):
        from .actions import booking_kind
        self.assertEqual(booking_kind('Can I schedule a site visit this weekend?'), 'site visit')
        self.assertEqual(booking_kind('I want to visit the property'), 'site visit')
        self.assertEqual(booking_kind('can we set up a meeting?'), 'meeting')
        self.assertEqual(booking_kind('Please schedule a call tomorrow'), 'call')
        self.assertEqual(booking_kind('call me back at 5'), 'call')
        self.assertEqual(booking_kind('can I book a demo'), 'demo')
        self.assertEqual(booking_kind('I want to book an appointment'), 'appointment')
        self.assertEqual(booking_kind('Can I visit your blog?'), '')
        self.assertEqual(booking_kind('what are your opening hours'), '')

    def test_packages(self):
        from .actions import wants_packages
        for text in ('show me your packages', 'What is the pricing?', 'how much does it cost', 'price list please'):
            self.assertTrue(wants_packages(text), text)
        self.assertFalse(wants_packages('where is your office?'))

    def test_guidance_respects_capabilities(self):
        from .actions import turn_guidance
        _, _, employee = make_workspace()
        g = turn_guidance(employee, 'can I book a site visit?')
        self.assertEqual(g['suggest'], {'type': 'schedule', 'kind': 'site visit'})
        self.assertIn('site visit', g['prompt'])

        employee.capabilities = ['collect_contact']
        g = turn_guidance(employee, 'can I book a site visit?')
        self.assertEqual(g['suggest']['type'], 'collect_contact')

        employee.capabilities = []
        g = turn_guidance(employee, 'can I book a site visit?')
        self.assertIsNone(g['suggest'])
        self.assertEqual(g['prompt'], '')

    def test_packages_guidance_widens_search_and_offers_chips(self):
        from .actions import turn_guidance
        _, _, employee = make_workspace()
        g = turn_guidance(employee, 'show me the packages for product X')
        self.assertIn('pricing', g['search_query'])
        self.assertIn('- **Name** — price', g['prompt'])
        self.assertEqual(g['suggest']['type'], 'chips')
        self.assertEqual([o['id'] for o in g['suggest']['options']], ['schedule', 'collect_contact', 'handoff'])


class BookingFlowTests(TestCase):
    """Visit / meeting / call requests open the slot picker and end in one scheduled task."""

    setUp = WidgetApiTests.setUp
    post = WidgetApiTests.post
    chat = WidgetApiTests.chat

    def test_site_visit_request_suggests_picker_and_booking_completes_task(self):
        captured = {}

        def fake_post(url, json=None, **kw):
            captured['payload'] = json
            return FakeRag(['Sure! Pick a time below.'])

        with mock.patch('apps.chat.views.requests.post', side_effect=fake_post):
            r = self.post('message', {
                'token': self.token, 'message': 'Can I schedule a site visit?', 'visitor_id': 'v1', 'stream': False,
            })
        data = r.json()
        self.assertEqual(data['suggest'], {'type': 'schedule', 'kind': 'site visit'})
        self.assertEqual(data['reply_html'], '<p>Sure! Pick a time below.</p>')
        self.assertIn('### THIS TURN ###', captured['payload']['system_prompt'])
        self.assertEqual(captured['payload']['search_query'], '')

        slot = self.ws.next_available_slots(1)[0]
        r = self.post('action', {
            'token': self.token, 'action': 'schedule', 'visitor_id': 'v1', 'session_id': data['session_id'],
            'data': {'slot_id': slot['id'], 'label': slot['label'], 'kind': 'site visit'},
        })
        res = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertIn('site visit is booked', res['message'])
        self.assertIn('<strong>', res['message_html'])
        # No contact yet: ask where to send the confirmation.
        self.assertEqual(res['suggest']['type'], 'collect_contact')
        tasks = EmployeeTask.objects.filter(session_id=data['session_id'], task_type=EmployeeTask.TaskType.SCHEDULE)
        self.assertEqual(tasks.count(), 1)
        self.assertIsNotNone(tasks[0].scheduled_for)
        self.assertEqual(tasks[0].details['kind'], 'site visit')

    def test_slots_cover_two_weeks_of_weekdays_for_the_calendar(self):
        slots = self.ws.next_available_slots(40)
        days = {s['date'] for s in slots}
        self.assertEqual(len(days), 10)
        for s in slots:
            self.assertTrue(s['starts_at'].startswith(s['date']))
            self.assertIn(s['time'], s['label'])

    def test_packages_question_sends_search_query(self):
        captured = {}

        def fake_post(url, json=None, **kw):
            captured['payload'] = json
            return FakeRag(['- **Basic** — $10'])

        with mock.patch('apps.chat.views.requests.post', side_effect=fake_post):
            data = self.post('message', {
                'token': self.token, 'message': 'what packages do you have?', 'visitor_id': 'v1', 'stream': False,
            }).json()
        self.assertIn('pricing', captured['payload']['search_query'])
        self.assertEqual(data['reply_html'], '<ul><li><strong>Basic</strong> — $10</li></ul>')
        self.assertEqual(data['suggest']['type'], 'chips')
