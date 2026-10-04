from unittest import mock

import requests
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.billing.models import BillingPlan
from apps.employees.models import AIEmployee
from apps.workspaces.models import Workspace, WorkspaceMembership

from .forms import KnowledgeSourceForm
from .models import KnowledgeSource
from .tasks import fetch_public_page, ingest_knowledge_source, queue_ingest


class KnowledgeTests(TestCase):
    def setUp(self):
        plan = BillingPlan.objects.create(
            name='Starter', slug='starter', price_monthly=19, conversation_limit=500, token_limit=1000,
        )
        self.user = User.objects.create_user('o@example.com', 'o@example.com', 'pw-123456!')
        self.user.profile.is_verified = True
        self.user.profile.save()
        self.ws = Workspace.objects.create(name='Acme', owner=self.user, plan=plan, is_active=True)
        WorkspaceMembership.objects.create(workspace=self.ws, user=self.user, role='owner')
        self.employee = AIEmployee.objects.create(workspace=self.ws, name='Maya')
        self.client.force_login(self.user)

    def make_source(self, **kw):
        defaults = dict(employee=self.employee, source_type='text', title='Hours', content='We open at 9.')
        defaults.update(kw)
        return KnowledgeSource.objects.create(**defaults)

    def test_ingest_success(self):
        source = self.make_source()
        resp = mock.Mock(**{'json.return_value': {'doc_id': 'd1', 'chunks': 2}})
        with mock.patch('apps.knowledge.tasks.requests.post', return_value=resp) as post:
            ingest_knowledge_source(source.id)
        source.refresh_from_db()
        self.assertEqual(source.status, KnowledgeSource.Status.READY)
        self.assertEqual(source.chunk_count, 2)
        self.assertEqual(post.call_args.kwargs['json']['source_id'], str(source.id))

    def test_ingest_failure_has_friendly_message(self):
        source = self.make_source()
        with mock.patch('apps.knowledge.tasks.requests.post', side_effect=requests.ConnectionError('http://rag:8100')):
            ingest_knowledge_source(source.id)
        source.refresh_from_db()
        self.assertEqual(source.status, KnowledgeSource.Status.FAILED)
        self.assertNotIn('rag:8100', source.error_message)

    def test_queue_falls_back_inline_when_broker_down(self):
        source = self.make_source()
        with mock.patch('apps.knowledge.tasks.ingest_knowledge_source.delay', side_effect=OSError('broker down')), \
                mock.patch('apps.knowledge.tasks.ingest_knowledge_source') as task:
            task.delay.side_effect = OSError('broker down')
            queue_ingest(source)
        task.assert_called_once_with(source.id)

    def test_delete_removes_vectors(self):
        source = self.make_source()
        with mock.patch('apps.knowledge.signals.rag_delete_source') as rag_delete, \
                self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reverse('knowledge_delete', args=[self.employee.id, source.id]))
        self.assertEqual(r.status_code, 302)
        rag_delete.assert_called_once_with(self.employee.id, source.id)

    def test_firing_employee_removes_index(self):
        with mock.patch('apps.knowledge.signals.rag_delete_employee') as rag_delete, \
                mock.patch('apps.knowledge.signals.rag_delete_source'), \
                self.captureOnCommitCallbacks(execute=True):
            emp_id = self.employee.id
            self.employee.delete()
        rag_delete.assert_called_once_with(emp_id)

    def test_retry(self):
        source = self.make_source(status='failed', error_message='boom')
        with mock.patch('apps.knowledge.views.queue_ingest') as q:
            r = self.client.post(reverse('knowledge_retry', args=[self.employee.id, source.id]))
        self.assertEqual(r.status_code, 302)
        q.assert_called_once()
        source.refresh_from_db()
        self.assertEqual(source.status, 'pending')

    def test_other_workspace_cannot_touch_sources(self):
        source = self.make_source()
        other = User.objects.create_user('x@example.com', 'x@example.com', 'pw-123456!')
        other.profile.is_verified = True
        other.profile.save()
        ws2 = Workspace.objects.create(name='Other', owner=other, is_active=True)
        WorkspaceMembership.objects.create(workspace=ws2, user=other, role='owner')
        self.client.force_login(other)
        r = self.client.post(reverse('knowledge_delete', args=[self.employee.id, source.id]))
        self.assertEqual(r.status_code, 404)
        self.assertTrue(KnowledgeSource.objects.filter(pk=source.pk).exists())

    def test_form_rejects_internal_urls(self):
        for url in ('http://127.0.0.1/admin', 'http://169.254.169.254/latest/meta-data', 'http://localhost:8100'):
            form = KnowledgeSourceForm(data={'source_type': 'url', 'title': 'x', 'source_url': url})
            self.assertFalse(form.is_valid(), url)
            self.assertIn('source_url', form.errors)

    def test_fetch_refuses_redirect_to_internal_host(self):
        redirect = mock.Mock(is_redirect=True, headers={'Location': 'http://127.0.0.1/secret'})
        with mock.patch('apps.workspaces.net.socket.getaddrinfo',
                        return_value=[(2, 1, 6, '', ('93.184.216.34', 80))]), \
                mock.patch('apps.knowledge.tasks.requests.get', return_value=redirect):
            with self.assertRaises(ValueError):
                fetch_public_page('http://example.com/')
