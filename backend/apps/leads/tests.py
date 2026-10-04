import csv
import io
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import BillingPlan
from apps.chat.models import ChatSession
from apps.employees.models import AIEmployee
from apps.workspaces.models import Workspace, WorkspaceMembership

from .models import Lead


def make_account(email, ws_name, plan=None):
    """User + active workspace (owner) + one AI Employee, ready for dashboard access."""
    plan = plan or BillingPlan.objects.get_or_create(
        slug='starter',
        defaults=dict(name='Starter', price_monthly=19, conversation_limit=500, token_limit=100000),
    )[0]
    user = User.objects.create_user(email, email, 'pw-123456!')
    user.profile.is_verified = True
    user.profile.save()
    ws = Workspace.objects.create(name=ws_name, owner=user, plan=plan, is_active=True)
    WorkspaceMembership.objects.create(workspace=ws, user=user, role='owner')
    employee = AIEmployee.objects.create(workspace=ws, name=f'{ws_name} Maya')
    return user, ws, employee


def _csv_rows(response):
    body = b''.join(response.streaming_content).decode('utf-8')
    return list(csv.DictReader(io.StringIO(body)))


class LeadDashboardTests(TestCase):
    def setUp(self):
        self.user_a, self.ws_a, self.emp_a = make_account('a@example.com', 'Acme')
        self.user_b, self.ws_b, self.emp_b = make_account('b@example.com', 'Beta')
        self.session_a = ChatSession.objects.create(employee=self.emp_a, visitor_id='visitor-a')
        self.lead_a = Lead.objects.create(
            workspace=self.ws_a, employee=self.emp_a, session=self.session_a,
            name='Alice Buyer', email='alice@example.com', phone='+15550001',
            intent_summary='Wants a 2 bedroom apartment',
        )
        self.lead_b = Lead.objects.create(
            workspace=self.ws_b, employee=self.emp_b, name='Bob Secret', email='bob@secret.com',
        )
        self.client.force_login(self.user_a)

    # --- list / search / filters / pagination ---------------------------------

    def test_list_is_workspace_scoped_and_links_conversation(self):
        resp = self.client.get(reverse('leads'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Alice Buyer')
        self.assertNotContains(resp, 'Bob Secret')
        self.assertContains(resp, reverse('conversation_detail', args=[self.session_a.pk]))
        self.assertContains(resp, reverse('lead_detail', args=[self.lead_a.pk]))

    def test_new_lead_defaults_to_new_status(self):
        self.assertEqual(self.lead_a.status, Lead.Status.NEW)

    def test_search_matches_name_email_phone_intent(self):
        Lead.objects.create(workspace=self.ws_a, employee=self.emp_a, name='Carl Other', email='carl@x.com')
        for term in ('alice', 'alice@example', '5550001', 'bedroom'):
            resp = self.client.get(reverse('leads'), {'q': term})
            self.assertContains(resp, 'Alice Buyer', msg_prefix=term)
            self.assertNotContains(resp, 'Carl Other', msg_prefix=term)

    def test_filters_status_source_employee_dates(self):
        emp2 = AIEmployee.objects.create(workspace=self.ws_a, name='Second')
        old = Lead.objects.create(
            workspace=self.ws_a, employee=emp2, name='Old Lead', source=Lead.Source.FORM,
            status=Lead.Status.WON,
        )
        Lead.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))

        resp = self.client.get(reverse('leads'), {'status': 'won'})
        self.assertContains(resp, 'Old Lead')
        self.assertNotContains(resp, 'Alice Buyer')

        resp = self.client.get(reverse('leads'), {'source': 'form'})
        self.assertContains(resp, 'Old Lead')
        self.assertNotContains(resp, 'Alice Buyer')

        resp = self.client.get(reverse('leads'), {'employee': emp2.pk})
        self.assertContains(resp, 'Old Lead')
        self.assertNotContains(resp, 'Alice Buyer')

        since = (timezone.localdate() - timedelta(days=7)).isoformat()
        resp = self.client.get(reverse('leads'), {'date_from': since})
        self.assertContains(resp, 'Alice Buyer')
        self.assertNotContains(resp, 'Old Lead')

        until = (timezone.localdate() - timedelta(days=30)).isoformat()
        resp = self.client.get(reverse('leads'), {'date_to': until})
        self.assertContains(resp, 'Old Lead')
        self.assertNotContains(resp, 'Alice Buyer')

    def test_filter_by_other_workspace_employee_shows_nothing(self):
        resp = self.client.get(reverse('leads'), {'employee': self.emp_b.pk})
        self.assertNotContains(resp, 'Bob Secret')
        self.assertContains(resp, 'No leads match these filters')

    def test_pagination_25_per_page(self):
        Lead.objects.bulk_create([
            Lead(workspace=self.ws_a, employee=self.emp_a, name=f'Bulk {i:02d}') for i in range(30)
        ])
        resp = self.client.get(reverse('leads'))
        self.assertEqual(len(resp.context['leads']), 25)
        self.assertEqual(resp.context['page_obj'].paginator.count, 31)
        resp = self.client.get(reverse('leads'), {'page': 2})
        self.assertEqual(len(resp.context['leads']), 6)
        resp = self.client.get(reverse('leads'), {'page': 999})  # out of range -> last page
        self.assertEqual(resp.context['page_obj'].number, 2)

    def test_empty_state(self):
        Lead.objects.filter(workspace=self.ws_a).delete()
        resp = self.client.get(reverse('leads'))
        self.assertContains(resp, 'No leads captured yet')

    # --- CSV export -----------------------------------------------------------

    def test_csv_export_content_and_scope(self):
        resp = self.client.get(reverse('leads_export'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment;', resp['Content-Disposition'])
        rows = _csv_rows(resp)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['name'], 'Alice Buyer')
        self.assertEqual(row['email'], 'alice@example.com')
        self.assertEqual(row['status'], 'new')
        self.assertEqual(row['ai_employee'], self.emp_a.name)
        self.assertEqual(row['conversation_id'], str(self.session_a.pk))

    def test_csv_export_respects_filters(self):
        Lead.objects.create(workspace=self.ws_a, employee=self.emp_a, name='Won Lead', status=Lead.Status.WON)
        rows = _csv_rows(self.client.get(reverse('leads_export'), {'status': 'won'}))
        self.assertEqual([r['name'] for r in rows], ['Won Lead'])
        rows = _csv_rows(self.client.get(reverse('leads_export'), {'q': 'alice'}))
        self.assertEqual([r['name'] for r in rows], ['Alice Buyer'])

    def test_csv_export_neutralises_formulas(self):
        Lead.objects.create(workspace=self.ws_a, employee=self.emp_a, name='=HYPERLINK("x")')
        rows = _csv_rows(self.client.get(reverse('leads_export'), {'q': 'HYPERLINK'}))
        self.assertEqual(rows[0]['name'], '\'=HYPERLINK("x")')

    def test_other_user_export_excludes_foreign_leads(self):
        self.client.force_login(self.user_b)
        rows = _csv_rows(self.client.get(reverse('leads_export')))
        self.assertEqual([r['name'] for r in rows], ['Bob Secret'])

    def test_export_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse('leads_export'))
        self.assertEqual(resp.status_code, 302)

    # --- status update / detail / notes ---------------------------------------

    def test_inline_status_update(self):
        resp = self.client.post(
            reverse('lead_update_status', args=[self.lead_a.pk]), {'status': 'qualified'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'qualified')
        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.status, Lead.Status.QUALIFIED)

    def test_status_update_non_ajax_redirects_safely(self):
        resp = self.client.post(
            reverse('lead_update_status', args=[self.lead_a.pk]),
            {'status': 'contacted', 'next': 'https://evil.example.com/'},
        )
        self.assertRedirects(resp, reverse('leads'), fetch_redirect_response=False)
        resp = self.client.post(
            reverse('lead_update_status', args=[self.lead_a.pk]),
            {'status': 'won', 'next': '/leads/?status=new'},
        )
        self.assertEqual(resp['Location'], '/leads/?status=new')

    def test_status_update_rejects_invalid_value(self):
        resp = self.client.post(
            reverse('lead_update_status', args=[self.lead_a.pk]), {'status': 'bogus'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 400)
        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.status, Lead.Status.NEW)

    def test_status_update_requires_post(self):
        resp = self.client.get(reverse('lead_update_status', args=[self.lead_a.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_status_update_enforces_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user_a)
        resp = client.post(reverse('lead_update_status', args=[self.lead_a.pk]), {'status': 'won'})
        self.assertEqual(resp.status_code, 403)

    def test_detail_and_notes(self):
        resp = self.client.get(reverse('lead_detail', args=[self.lead_a.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Alice Buyer')
        resp = self.client.post(
            reverse('lead_detail', args=[self.lead_a.pk]),
            {'notes': 'Called, wants viewing Friday', 'status': 'contacted'},
        )
        self.assertRedirects(resp, reverse('lead_detail', args=[self.lead_a.pk]), fetch_redirect_response=False)
        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.notes, 'Called, wants viewing Friday')
        self.assertEqual(self.lead_a.status, Lead.Status.CONTACTED)

    # --- cross-workspace isolation -------------------------------------------

    def test_user_b_cannot_view_or_update_user_a_lead(self):
        self.client.force_login(self.user_b)
        self.assertEqual(self.client.get(reverse('lead_detail', args=[self.lead_a.pk])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse('lead_detail', args=[self.lead_a.pk]), {'notes': 'pwned'}).status_code, 404,
        )
        self.assertEqual(
            self.client.post(reverse('lead_update_status', args=[self.lead_a.pk]), {'status': 'lost'}).status_code,
            404,
        )
        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.status, Lead.Status.NEW)
        self.assertEqual(self.lead_a.notes, '')
        resp = self.client.get(reverse('leads'))
        self.assertNotContains(resp, 'Alice Buyer')
