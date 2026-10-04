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
