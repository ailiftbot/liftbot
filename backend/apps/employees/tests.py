import json
from unittest import mock

import requests
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.billing.models import BillingPlan
from apps.chat.models import ChatSession, Message
from apps.workspaces.models import Workspace, WorkspaceMembership

from .models import AIEmployee


def make_workspace(email='owner@example.com', name='Acme'):
    user = User.objects.create_user(username=email, email=email, password='pw-123456!')
    user.profile.is_verified = True
    user.profile.save()
    plan, _ = BillingPlan.objects.get_or_create(
        slug='starter',
        defaults=dict(name='Starter', price_monthly=19, conversation_limit=500, token_limit=200_000),
    )
    ws = Workspace.objects.create(name=name, owner=user, plan=plan, is_active=True)
    WorkspaceMembership.objects.get_or_create(
        workspace=ws, user=user, defaults={'role': WorkspaceMembership.Role.OWNER},
    )
    employee = AIEmployee.objects.create(workspace=ws, name='Maya', role=AIEmployee.Role.SALES)
    return user, ws, employee


class FakeRag:
    def __init__(self, lines):
        self.lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        return iter(self.lines)


def settings_payload(employee, **overrides):
    data = {
        'action': 'update_settings',
        'name': employee.name,
        'department': employee.department,
        'role': employee.role,
        'personality': employee.personality,
        'language': employee.language,
        'greeting_message': employee.greeting_message,
        'handoff_email': employee.handoff_email,
        'brand_color': employee.brand_color or '#7C3AED',
        'is_active': 'on' if employee.is_active else '',
    }
    data.update(overrides)
    return data


class HubSettingsTests(TestCase):
    def setUp(self):
        self.user, self.ws, self.employee = make_workspace()
        self.employee.capabilities = ['qualify_visitors', 'collect_contact']
        self.employee.save()
        self.client.force_login(self.user)
        self.url = reverse('employee_hub', args=[self.employee.pk])

    def test_capability_checkboxes_are_tied_to_settings_form(self):
        res = self.client.get(self.url + '?tab=capabilities')
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn('name="capability_choices"', html)
        self.assertIn('form="hub-settings-form"', html)
        self.assertIn('name="capability_choices__present"', html)

    def test_settings_save_without_capability_field_preserves_capabilities(self):
        res = self.client.post(self.url, settings_payload(self.employee, name='Maya Two'))
        self.assertEqual(res.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.name, 'Maya Two')
        self.assertEqual(self.employee.capabilities, ['qualify_visitors', 'collect_contact'])

    def test_settings_save_with_capabilities_updates_them(self):
        data = settings_payload(self.employee)
        data['capability_choices__present'] = '1'
        data['capability_choices'] = ['notify_team']
        res = self.client.post(self.url, data)
        self.assertEqual(res.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.capabilities, ['notify_team'])

    def test_settings_save_with_all_capabilities_unchecked_clears_them(self):
        data = settings_payload(self.employee)
        data['capability_choices__present'] = '1'
        self.client.post(self.url, data)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.capabilities, [])

    def test_invalid_settings_keep_bound_form_on_settings_tab(self):
        res = self.client.post(self.url, settings_payload(self.employee, name=''))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['active_tab'], 'settings')
        self.assertTrue(res.context['form'].is_bound)
        self.assertIn('name', res.context['form'].errors)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.name, 'Maya')

    def test_toggle_active_redirects_to_status_tab(self):
        res = self.client.post(self.url, {'action': 'toggle_active'})
        self.assertRedirects(res, self.url + '?tab=status', fetch_redirect_response=False)


class PlaygroundMessageTests(TestCase):
    def setUp(self):
        self.user, self.ws, self.employee = make_workspace()
        self.url = reverse('playground_message', args=[self.employee.pk])

    def post(self, body):
        return self.client.post(self.url, data=json.dumps(body), content_type='application/json')

    def test_requires_login(self):
        res = self.post({'message': 'Hi'})
        self.assertEqual(res.status_code, 302)
        self.assertFalse(ChatSession.objects.exists())

    def test_other_workspace_gets_404(self):
        other_user, _, _ = make_workspace(email='other@example.com', name='Other Co')
        self.client.force_login(other_user)
        with mock.patch('apps.employees.views.requests.post') as post:
            res = self.post({'message': 'Hi'})
        self.assertEqual(res.status_code, 404)
        post.assert_not_called()

    def test_csrf_enforced(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        res = client.post(self.url, data=json.dumps({'message': 'Hi'}), content_type='application/json')
        self.assertEqual(res.status_code, 403)

    @mock.patch('apps.employees.views.requests.post')
    def test_returns_reply_in_test_session_without_quota(self, post):
        post.return_value = FakeRag(['data: "Hello"', 'data: [DONE]'])
        self.employee.is_active = False  # playground works for inactive employees
        self.employee.save()
        before = Workspace.objects.get(pk=self.ws.pk).conversations_used
        self.client.force_login(self.user)

        res = self.post({'message': 'Who are you?'})

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['reply'], 'Hello')
        session = ChatSession.objects.get(pk=data['session_id'])
        self.assertTrue(session.is_test)
        self.assertEqual(session.employee, self.employee)
        self.assertEqual(
            list(session.messages.values_list('role', 'content')),
            [(Message.Role.VISITOR, 'Who are you?'), (Message.Role.EMPLOYEE, 'Hello')],
        )
        self.assertEqual(Workspace.objects.get(pk=self.ws.pk).conversations_used, before)

        _, kwargs = post.call_args
        payload = kwargs['json']
        self.assertEqual(payload['employee_id'], str(self.employee.id))
        self.assertEqual(payload['message'], 'Who are you?')
        self.assertEqual(payload['top_k'], 4)
        self.assertIn('X-Internal-Token', kwargs['headers'])
        self.assertEqual(payload['history'][-1], {'role': 'visitor', 'content': 'Who are you?'})

    @mock.patch('apps.employees.views.requests.post')
    def test_continues_existing_test_session(self, post):
        post.return_value = FakeRag(['data: "Hi"', 'data: [DONE]'])
        self.client.force_login(self.user)
        first = self.post({'message': 'One'}).json()
        post.return_value = FakeRag(['data: "Again"', 'data: [DONE]'])
        second = self.post({'message': 'Two', 'session_id': first['session_id']}).json()
        self.assertEqual(first['session_id'], second['session_id'])
        self.assertEqual(ChatSession.objects.count(), 1)

    @mock.patch('apps.employees.views.requests.post')
    def test_does_not_reuse_live_visitor_session(self, post):
        post.return_value = FakeRag(['data: "Hi"', 'data: [DONE]'])
        live = ChatSession.objects.create(employee=self.employee)
        self.client.force_login(self.user)
        data = self.post({'message': 'One', 'session_id': live.pk}).json()
        self.assertNotEqual(data['session_id'], live.pk)
        self.assertEqual(live.messages.count(), 0)

    @mock.patch('apps.employees.views.requests.post', side_effect=requests.ConnectionError('down'))
    def test_rag_down_returns_friendly_502(self, post):
        self.client.force_login(self.user)
        res = self.post({'message': 'Hi'})
        self.assertEqual(res.status_code, 502)
        self.assertIn('error', res.json())

    def test_empty_message_rejected(self):
        self.client.force_login(self.user)
        res = self.post({'message': '   '})
        self.assertEqual(res.status_code, 400)


class SidebarTests(TestCase):
    def setUp(self):
        self.user, self.ws, self.employee = make_workspace()
        self.client.force_login(self.user)

    def test_hub_without_tab_marks_only_playground_active(self):
        res = self.client.get(reverse('employee_hub', args=[self.employee.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['hub_nav'], 'playground')

    def test_primary_employee_prefers_viewed_employee(self):
        second = AIEmployee.objects.create(workspace=self.ws, name='Zed', is_active=False)
        res = self.client.get(reverse('employee_hub', args=[second.pk]) + '?tab=knowledge')
        self.assertEqual(res.context['primary_employee'], second)
        self.assertEqual(res.context['hub_nav'], 'knowledge')


class TemplateSmokeTests(TestCase):
    def setUp(self):
        self.user, self.ws, self.employee = make_workspace()
        self.client.force_login(self.user)

    def test_dashboard_links_conversations_and_lists_leads(self):
        from apps.leads.models import Lead

        session = ChatSession.objects.create(employee=self.employee)
        Message.objects.create(session=session, role=Message.Role.VISITOR, content='Hello there')
        Lead.objects.create(workspace=self.ws, employee=self.employee, name='Priya', email='p@example.com')
        res = self.client.get(reverse('dashboard'))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn(reverse('conversation_detail', args=[session.pk]), html)
        self.assertIn('Recent Leads', html)
        self.assertIn('Priya', html)
        self.assertIn(reverse('playground_message', args=[self.employee.pk]), html)
        self.assertNotIn('/api/widget/message/', html)

    def test_hub_has_no_duplicate_ids_and_no_fake_intents(self):
        import re

        res = self.client.get(reverse('employee_hub', args=[self.employee.pk]))
        html = res.content.decode()
        ids = re.findall(r'\sid="([^"]+)"', html)
        dupes = {i for i in ids if ids.count(i) > 1}
        self.assertEqual(dupes, set())
        self.assertNotIn('62%', html)
        self.assertNotIn('sim-home-btn', html)

    def test_fire_cancel_returns_to_hub_status(self):
        res = self.client.get(reverse('employee_fire', args=[self.employee.pk]))
        self.assertContains(res, reverse('employee_hub', args=[self.employee.pk]) + '?tab=status')
