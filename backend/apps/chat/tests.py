from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.leads.models import Lead
from apps.leads.tests import make_account

from .models import ChatSession, EmployeeTask, Message, VisitorProfile


class ConversationDashboardTests(TestCase):
    def setUp(self):
        self.user_a, self.ws_a, self.emp_a = make_account('a@example.com', 'Acme')
        self.user_b, self.ws_b, self.emp_b = make_account('b@example.com', 'Beta')
        self.session_a = ChatSession.objects.create(employee=self.emp_a, visitor_id='visitor-aaa')
        Message.objects.create(session=self.session_a, role=Message.Role.VISITOR, content='Do you have parking?')
        Message.objects.create(session=self.session_a, role=Message.Role.EMPLOYEE, content='Yes, two spots included.')
        VisitorProfile.objects.create(
            workspace=self.ws_a, visitor_id='visitor-aaa', name='Priya Shah', email='priya@example.com',
        )
        self.session_b = ChatSession.objects.create(employee=self.emp_b, visitor_id='visitor-bbb')
        Message.objects.create(session=self.session_b, role=Message.Role.VISITOR, content='Beta secret message')
        self.client.force_login(self.user_a)

    def test_list_scoped_shows_identity_and_preview(self):
        resp = self.client.get(reverse('conversations'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Priya Shah')
        self.assertContains(resp, 'Yes, two spots included.')  # last message preview
        self.assertNotContains(resp, 'Beta secret message')
        self.assertEqual([s.pk for s in resp.context['sessions']], [self.session_a.pk])

    def test_needs_attention_badge(self):
        unanswered = ChatSession.objects.create(employee=self.emp_a, visitor_id='visitor-new')
        Message.objects.create(session=unanswered, role=Message.Role.VISITOR, content='Hello? Anyone there')
        resp = self.client.get(reverse('conversations'))
        flags = {s.pk: s.needs_attention for s in resp.context['sessions']}
        self.assertTrue(flags[unanswered.pk])
        self.assertFalse(flags[self.session_a.pk])
        ChatSession.objects.filter(pk=self.session_a.pk).update(status=ChatSession.Status.HUMAN)
        resp = self.client.get(reverse('conversations'), {'attention': '1'})
        self.assertEqual({s.pk for s in resp.context['sessions']}, {unanswered.pk, self.session_a.pk})
        self.assertContains(resp, 'Needs attention')

    def test_search_by_message_content_identity_and_visitor_id(self):
        other = ChatSession.objects.create(employee=self.emp_a, visitor_id='visitor-zzz')
        Message.objects.create(session=other, role=Message.Role.VISITOR, content='What are your opening hours?')
        cases = {
            'parking': {self.session_a.pk},
            'opening hours': {other.pk},
            'priya@': {self.session_a.pk},
            'Shah': {self.session_a.pk},
            'visitor-zz': {other.pk},
            'Beta secret': set(),  # other workspace's content never leaks
        }
        for term, expected in cases.items():
            resp = self.client.get(reverse('conversations'), {'q': term})
            self.assertEqual({s.pk for s in resp.context['sessions']}, expected, term)

    def test_filters_mode_employee_and_date(self):
        from apps.employees.models import AIEmployee
        emp2 = AIEmployee.objects.create(workspace=self.ws_a, name='Second')
        human = ChatSession.objects.create(employee=emp2, visitor_id='v-human', status=ChatSession.Status.HUMAN)
        ChatSession.objects.filter(pk=human.pk).update(last_message_at=timezone.now() - timedelta(days=20))

        resp = self.client.get(reverse('conversations'), {'mode': 'human'})
        self.assertEqual([s.pk for s in resp.context['sessions']], [human.pk])
        resp = self.client.get(reverse('conversations'), {'mode': 'ai'})
        self.assertEqual([s.pk for s in resp.context['sessions']], [self.session_a.pk])
        resp = self.client.get(reverse('conversations'), {'employee': emp2.pk})
        self.assertEqual([s.pk for s in resp.context['sessions']], [human.pk])
        since = (timezone.localdate() - timedelta(days=3)).isoformat()
        resp = self.client.get(reverse('conversations'), {'date_from': since})
        self.assertEqual([s.pk for s in resp.context['sessions']], [self.session_a.pk])

    def test_test_sessions_excluded(self):
        ChatSession.objects.create(employee=self.emp_a, visitor_id='playground', is_test=True)
        resp = self.client.get(reverse('conversations'))
        self.assertEqual([s.pk for s in resp.context['sessions']], [self.session_a.pk])

    def test_pagination(self):
        ChatSession.objects.bulk_create([
            ChatSession(employee=self.emp_a, visitor_id=f'bulk-{i}') for i in range(30)
        ])
        resp = self.client.get(reverse('conversations'))
        self.assertEqual(len(resp.context['sessions']), 25)
        resp = self.client.get(reverse('conversations'), {'page': 2})
        self.assertEqual(len(resp.context['sessions']), 6)

    def test_empty_state(self):
        self.client.force_login(make_account('c@example.com', 'Gamma')[0])
        resp = self.client.get(reverse('conversations'))
        self.assertContains(resp, 'No conversations yet')

    def test_detail_renders_role_names_and_visitor(self):
        resp = self.client.get(reverse('conversation_detail', args=[self.session_a.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Priya Shah')
        self.assertContains(resp, 'role-labels')
        self.assertContains(resp, 'Do you have parking?')

    def test_poll_reports_human_mode(self):
        resp = self.client.get(reverse('conversation_poll', args=[self.session_a.pk]), {'after_id': 0})
        data = resp.json()
        self.assertFalse(data['human_mode'])
        self.assertEqual(len(data['messages']), 2)

    def test_user_b_cannot_access_user_a_conversation(self):
        self.client.force_login(self.user_b)
        pk = self.session_a.pk
        self.assertEqual(self.client.get(reverse('conversation_detail', args=[pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('conversation_poll', args=[pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('conversation_takeover', args=[pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('conversation_release', args=[pk])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse('conversation_reply', args=[pk]), {'content': 'hi'}).status_code, 404,
        )
        self.session_a.refresh_from_db()
        self.assertEqual(self.session_a.status, ChatSession.Status.ACTIVE)
        resp = self.client.get(reverse('conversations'))
        self.assertNotContains(resp, 'Priya Shah')


class TaskDashboardTests(TestCase):
    def setUp(self):
        self.user_a, self.ws_a, self.emp_a = make_account('a@example.com', 'Acme')
        self.user_b, self.ws_b, self.emp_b = make_account('b@example.com', 'Beta')
        self.session_a = ChatSession.objects.create(employee=self.emp_a, visitor_id='v-a')
        self.lead_a = Lead.objects.create(workspace=self.ws_a, employee=self.emp_a, name='Alice')
        self.task_a = EmployeeTask.objects.create(
            workspace=self.ws_a, employee=self.emp_a, session=self.session_a, lead=self.lead_a,
            task_type=EmployeeTask.TaskType.HANDOFF, title='Call Alice back',
        )
        self.task_b = EmployeeTask.objects.create(
            workspace=self.ws_b, employee=self.emp_b, task_type=EmployeeTask.TaskType.SCHEDULE,
            title='Beta private task',
        )
        self.client.force_login(self.user_a)

    def test_list_scoped_with_links(self):
        resp = self.client.get(reverse('employee_tasks'))
        self.assertContains(resp, 'Call Alice back')
        self.assertNotContains(resp, 'Beta private task')
        self.assertContains(resp, reverse('lead_detail', args=[self.lead_a.pk]))
        self.assertContains(resp, reverse('conversation_detail', args=[self.session_a.pk]))
        self.assertContains(resp, reverse('task_update_status', args=[self.task_a.pk]))

    def test_filters_status_and_type(self):
        EmployeeTask.objects.create(
            workspace=self.ws_a, employee=self.emp_a, task_type=EmployeeTask.TaskType.QUALIFY,
            title='Qualify Bob', status=EmployeeTask.Status.DONE,
        )
        resp = self.client.get(reverse('employee_tasks'), {'status': 'done'})
        self.assertEqual([t.title for t in resp.context['tasks']], ['Qualify Bob'])
        resp = self.client.get(reverse('employee_tasks'), {'type': 'handoff'})
        self.assertEqual([t.title for t in resp.context['tasks']], ['Call Alice back'])
        resp = self.client.get(reverse('employee_tasks'), {'type': 'contact'})
        self.assertContains(resp, 'No tasks match these filters')

    def test_pagination(self):
        EmployeeTask.objects.bulk_create([
            EmployeeTask(workspace=self.ws_a, employee=self.emp_a, task_type='qualify', title=f'T{i}')
            for i in range(30)
        ])
        resp = self.client.get(reverse('employee_tasks'))
        self.assertEqual(len(resp.context['tasks']), 25)
        resp = self.client.get(reverse('employee_tasks'), {'page': 2})
        self.assertEqual(len(resp.context['tasks']), 6)

    def test_status_update_valid(self):
        resp = self.client.post(
            reverse('task_update_status', args=[self.task_a.pk]), {'status': 'done'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'ok': True, 'status': 'done', 'label': 'Done'})
        self.task_a.refresh_from_db()
        self.assertEqual(self.task_a.status, EmployeeTask.Status.DONE)

    def test_status_update_non_ajax_redirects(self):
        resp = self.client.post(reverse('task_update_status', args=[self.task_a.pk]), {'status': 'in_progress'})
        self.assertRedirects(resp, reverse('employee_tasks'), fetch_redirect_response=False)
        self.task_a.refresh_from_db()
        self.assertEqual(self.task_a.status, EmployeeTask.Status.IN_PROGRESS)

    def test_status_update_rejects_invalid(self):
        for bad in ('', 'archived', 'DONE'):
            resp = self.client.post(
                reverse('task_update_status', args=[self.task_a.pk]), {'status': bad},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )
            self.assertEqual(resp.status_code, 400, bad)
        self.task_a.refresh_from_db()
        self.assertEqual(self.task_a.status, EmployeeTask.Status.OPEN)

    def test_status_update_requires_post(self):
        self.assertEqual(self.client.get(reverse('task_update_status', args=[self.task_a.pk])).status_code, 405)

    def test_user_b_cannot_update_user_a_task(self):
        self.client.force_login(self.user_b)
        resp = self.client.post(reverse('task_update_status', args=[self.task_a.pk]), {'status': 'cancelled'})
        self.assertEqual(resp.status_code, 404)
        self.task_a.refresh_from_db()
        self.assertEqual(self.task_a.status, EmployeeTask.Status.OPEN)
        self.assertNotContains(self.client.get(reverse('employee_tasks')), 'Call Alice back')


class AnalyticsTests(TestCase):
    def setUp(self):
        self.user, self.ws, self.emp = make_account('a@example.com', 'Acme')
        self.client.force_login(self.user)

    def test_excludes_test_sessions_and_range_param(self):
        ChatSession.objects.create(employee=self.emp, visitor_id='real')
        ChatSession.objects.create(employee=self.emp, visitor_id='play', is_test=True)
        old = ChatSession.objects.create(employee=self.emp, visitor_id='old')
        ChatSession.objects.filter(pk=old.pk).update(started_at=timezone.now() - timedelta(days=60))

        resp = self.client.get(reverse('analytics'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['stats']['conv_total'], 2)
        self.assertEqual(resp.context['range_days'], 30)
        self.assertEqual(resp.context['stats']['conv_period'], 1)
        self.assertEqual(len(resp.context['chart_days']), 30)

        resp = self.client.get(reverse('analytics'), {'range': '90'})
        self.assertEqual(resp.context['stats']['conv_period'], 2)
        self.assertEqual(len(resp.context['chart_days']), 90)

        resp = self.client.get(reverse('analytics'), {'range': 'abc'})
        self.assertEqual(resp.context['range_days'], 30)
        resp = self.client.get(reverse('analytics'), {'range': '7'})
        self.assertEqual(len(resp.context['chart_days']), 7)
