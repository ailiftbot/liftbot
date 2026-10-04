import re
import socket
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from apps.accounts.tests import SIGNUP_DATA, code_from_outbox, make_plan, make_user

from .models import Workspace, WorkspaceInvite, WorkspaceMembership
from .net import is_safe_public_url, validate_public_url, UnsafeURLError
from .views import user_workspace

PUBLIC_IP = '93.184.216.34'


def fake_getaddrinfo(mapping):
    real = socket.getaddrinfo

    def _fake(host, port, *args, **kwargs):
        if host in mapping:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (mapping[host], port))]
        return real(host, port, *args, **kwargs)
    return _fake


class WorkspaceTestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.plan = make_plan()
        self.owner = make_user('owner@example.com')
        self.workspace = Workspace.objects.create(name='Acme', owner=self.owner, plan=self.plan, is_active=True)
        WorkspaceMembership.objects.create(workspace=self.workspace, user=self.owner, role='owner')

    def add_member(self, email, role='member'):
        user = make_user(email)
        membership = WorkspaceMembership.objects.create(workspace=self.workspace, user=user, role=role)
        return user, membership


class RoleCheckTests(WorkspaceTestBase):
    def test_member_cannot_change_settings(self):
        member, _ = self.add_member('member@example.com')
        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse('settings')).status_code, 200)
        for data in (
            {'section': 'workspace', 'name': 'Hacked', 'brand_color': '#000000'},
            {'section': 'webhook', 'webhook_url': 'https://example.com/hook'},
            {'section': 'test_webhook'},
            {'section': 'regenerate_workspace_token'},
        ):
            resp = self.client.post(reverse('settings'), data)
            self.assertEqual(resp.status_code, 403, data)
        resp = self.client.post(reverse('team_invite'), {'email': 'x@example.com', 'role': 'admin'})
        self.assertEqual(resp.status_code, 403)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.name, 'Acme')
        self.assertFalse(WorkspaceInvite.objects.exists())

    def test_member_can_update_own_profile(self):
        member, _ = self.add_member('member@example.com')
        self.client.force_login(member)
        resp = self.client.post(reverse('settings'), {'section': 'account', 'full_name': 'Mia Member'})
        self.assertEqual(resp.status_code, 302)
        member.refresh_from_db()
        self.assertEqual(member.profile.full_name, 'Mia Member')

    def test_admin_can_change_settings(self):
        admin, _ = self.add_member('admin@example.com', role='admin')
        self.client.force_login(admin)
        resp = self.client.post(reverse('settings'), {'section': 'workspace', 'name': 'Acme 2', 'brand_color': '#112233'})
        self.assertEqual(resp.status_code, 302)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.name, 'Acme 2')
        self.assertEqual(self.workspace.brand_color, '#112233')

    def test_owner_cannot_be_removed_or_demoted(self):
        admin, _ = self.add_member('admin@example.com', role='admin')
        owner_membership = WorkspaceMembership.objects.get(user=self.owner)
        self.client.force_login(admin)
        self.client.post(reverse('team_member_remove', args=[owner_membership.id]))
        self.client.post(reverse('team_member_role', args=[owner_membership.id]), {'role': 'member'})
        owner_membership.refresh_from_db()
        self.assertEqual(owner_membership.role, 'owner')

    def test_admin_can_change_role_and_remove_member(self):
        member, membership = self.add_member('member@example.com')
        self.client.force_login(self.owner)
        self.client.post(reverse('team_member_role', args=[membership.id]), {'role': 'admin'})
        membership.refresh_from_db()
        self.assertEqual(membership.role, 'admin')
        self.client.post(reverse('team_member_remove', args=[membership.id]))
        self.assertFalse(WorkspaceMembership.objects.filter(pk=membership.id).exists())

    def test_member_cannot_remove_others(self):
        member, _ = self.add_member('member@example.com')
        _, other = self.add_member('other@example.com')
        self.client.force_login(member)
        resp = self.client.post(reverse('team_member_remove', args=[other.id]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(WorkspaceMembership.objects.filter(pk=other.id).exists())


class InviteTests(WorkspaceTestBase):
    def _invite(self, email='new@example.com', role='member'):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse('team_invite'), {'email': email, 'role': role})
        self.assertEqual(resp.status_code, 302)
        self.client.logout()
        invite = WorkspaceInvite.objects.get(email=email)
        self.assertIn(reverse('invite_accept', args=[invite.token]), mail.outbox[-1].body)
        return invite

    def test_logged_in_matching_user_accepts(self):
        invite = self._invite('joiner@example.com', role='admin')
        joiner = make_user('joiner@example.com')
        other_plan_ws = Workspace.objects.create(name='Own', owner=joiner, plan=self.plan, is_active=True)
        WorkspaceMembership.objects.create(workspace=other_plan_ws, user=joiner, role='owner')

        self.client.force_login(joiner)
        page = self.client.get(reverse('invite_accept', args=[invite.token]))
        self.assertContains(page, 'Accept invite')
        resp = self.client.post(reverse('invite_accept', args=[invite.token]))
        self.assertRedirects(resp, reverse('dashboard'), fetch_redirect_response=False)

        membership = WorkspaceMembership.objects.get(workspace=self.workspace, user=joiner)
        self.assertEqual(membership.role, 'admin')
        invite.refresh_from_db()
        self.assertIsNotNone(invite.accepted_at)
        # The joined workspace becomes the active one even though it is not the first membership.
        self.assertEqual(user_workspace(User.objects.get(pk=joiner.pk)), self.workspace)

    def test_mismatched_email_cannot_accept(self):
        invite = self._invite('joiner@example.com')
        stranger = make_user('stranger@example.com')
        self.client.force_login(stranger)
        resp = self.client.post(reverse('invite_accept', args=[invite.token]))
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(WorkspaceMembership.objects.filter(user=stranger, workspace=self.workspace).exists())

    def test_existing_user_accepts_after_login(self):
        invite = self._invite('joiner@example.com')
        make_user('joiner@example.com')
        self.client.get(reverse('invite_accept', args=[invite.token]))
        resp = self.client.post(reverse('login'), {'username': 'joiner@example.com', 'password': 'S3cure-pass-123'})
        self.assertRedirects(resp, reverse('dashboard'), fetch_redirect_response=False)
        self.assertTrue(WorkspaceMembership.objects.filter(
            workspace=self.workspace, user__email='joiner@example.com').exists())

    def test_new_user_accepts_after_signup_and_verify(self):
        invite = self._invite('joiner@example.com')
        page = self.client.get(reverse('invite_accept', args=[invite.token]))
        self.assertContains(page, 'Create an account')

        data = dict(SIGNUP_DATA, email='joiner@example.com')
        data.pop('company_name')
        resp = self.client.post(reverse('signup'), data)
        self.assertRedirects(resp, reverse('signup_verify_otp'), fetch_redirect_response=False)
        joiner = User.objects.get(email='joiner@example.com')
        self.assertFalse(Workspace.objects.filter(owner=joiner).exists())

        resp = self.client.post(reverse('signup_verify_otp'), {'code': code_from_outbox()})
        self.assertRedirects(resp, reverse('dashboard'), fetch_redirect_response=False)
        self.assertTrue(WorkspaceMembership.objects.filter(workspace=self.workspace, user=joiner).exists())
        self.assertEqual(int(self.client.session['_auth_user_id']), joiner.pk)

    def test_expired_or_unknown_invite(self):
        resp = self.client.get(reverse('invite_accept', args=['nope']))
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, 'no longer valid', status_code=404)


class WebhookSSRFTests(WorkspaceTestBase):
    def test_rejects_internal_addresses(self):
        for url in (
            'http://127.0.0.1',
            'https://127.0.0.1/hook',
            'http://169.254.169.254/latest/meta-data/',
            'https://169.254.169.254/',
            'https://10.0.0.5/hook',
            'https://192.168.1.10/hook',
            'https://[::1]/hook',
            'https://[::ffff:127.0.0.1]/hook',
            'https://localhost/hook',
            'https://100.64.0.1/hook',
            'ftp://example.com/x',
            'not a url',
            '',
        ):
            self.assertFalse(is_safe_public_url(url), url)

    def test_requires_https_by_default(self):
        with mock.patch('socket.getaddrinfo', fake_getaddrinfo({'hooks.example.com': PUBLIC_IP})):
            self.assertTrue(is_safe_public_url('https://hooks.example.com/x'))
            self.assertFalse(is_safe_public_url('http://hooks.example.com/x'))
            self.assertTrue(is_safe_public_url('http://hooks.example.com/x', require_https=False))

    def test_hostname_resolving_to_private_ip_rejected(self):
        with mock.patch('socket.getaddrinfo', fake_getaddrinfo({'evil.example.com': '10.1.2.3'})):
            with self.assertRaises(UnsafeURLError):
                validate_public_url('https://evil.example.com/hook')

    def test_settings_refuses_internal_webhook(self):
        self.client.force_login(self.owner)
        for url in ('http://127.0.0.1', 'http://169.254.169.254', 'https://169.254.169.254/latest'):
            self.client.post(reverse('settings'), {'section': 'webhook', 'webhook_url': url})
            self.workspace.refresh_from_db()
            self.assertEqual(self.workspace.webhook_url, '', url)

    def test_settings_saves_public_webhook_and_sends_test(self):
        self.client.force_login(self.owner)
        with mock.patch('socket.getaddrinfo', fake_getaddrinfo({'hooks.example.com': PUBLIC_IP})):
            self.client.post(reverse('settings'), {'section': 'webhook', 'webhook_url': 'https://hooks.example.com/abc'})
            self.workspace.refresh_from_db()
            self.assertEqual(self.workspace.webhook_url, 'https://hooks.example.com/abc')

            with mock.patch('apps.workspaces.settings_views.requests.post') as post:
                post.return_value.status_code = 200
                resp = self.client.post(reverse('settings'), {'section': 'test_webhook'}, follow=True)
            post.assert_called_once()
            self.assertEqual(post.call_args.args[0], 'https://hooks.example.com/abc')
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
            self.assertContains(resp, 'Test webhook delivered')


class DangerZoneTests(WorkspaceTestBase):
    def test_workspace_delete_requires_typed_name(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('workspace_delete'), {'confirm_name': 'acme'})
        self.assertTrue(Workspace.objects.filter(pk=self.workspace.pk).exists())
        resp = self.client.post(reverse('workspace_delete'), {'confirm_name': 'Acme'})
        self.assertFalse(Workspace.objects.filter(pk=self.workspace.pk).exists())
        self.assertRedirects(resp, reverse('workspace_create'), fetch_redirect_response=False)

    def test_admin_cannot_delete_workspace(self):
        admin, _ = self.add_member('admin@example.com', role='admin')
        self.client.force_login(admin)
        resp = self.client.post(reverse('workspace_delete'), {'confirm_name': 'Acme'})
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Workspace.objects.filter(pk=self.workspace.pk).exists())

    def test_account_delete(self):
        member, _ = self.add_member('member@example.com')
        self.client.force_login(member)
        self.client.post(reverse('account_delete'), {'password': 'wrong', 'confirm': 'DELETE'})
        self.assertTrue(User.objects.filter(pk=member.pk).exists())
        resp = self.client.post(reverse('account_delete'), {'password': 'S3cure-pass-123', 'confirm': 'DELETE'})
        self.assertRedirects(resp, reverse('home'), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(pk=member.pk).exists())
        self.assertTrue(Workspace.objects.filter(pk=self.workspace.pk).exists())


class PublicPageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_404_page_renders(self):
        resp = self.client.get('/definitely-not-a-page/')
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, 'find that page', status_code=404)

    def test_500_template_is_standalone(self):
        html = render_to_string('500.html')
        self.assertIn('Something went wrong', html)

    def test_pricing_lists_real_plans(self):
        make_plan('starter', '19.00')
        make_plan('pro', '49.00')
        resp = self.client.get(reverse('pricing'))
        self.assertContains(resp, 'Starter')
        self.assertContains(resp, '$49')
        self.assertContains(resp, reverse('signup'))
        self.assertNotContains(resp, 'Pricing is on its way')

    def test_marketing_home_is_the_home_url(self):
        self.assertEqual(reverse('home'), '/')
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_contact_honeypot_and_rate_limit(self):
        data = {'full_name': 'Pat', 'email': 'pat@example.com', 'topic': 'sales', 'message': 'Hello there, LiftBot!'}
        resp = self.client.post(reverse('contact'), dict(data, company_fax='spam'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

        cache.clear()
        for _ in range(5):
            self.assertEqual(self.client.post(reverse('contact'), data).status_code, 200)
        self.assertEqual(len(mail.outbox), 5)
        resp = self.client.post(reverse('contact'), data)
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(len(mail.outbox), 5)


class InviteEmailLinkTests(WorkspaceTestBase):
    def test_invite_email_contains_absolute_link(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('team_invite'), {'email': 'z@example.com', 'role': 'member'})
        self.assertTrue(re.search(r'https?://\S+/invite/\S+/', mail.outbox[-1].body))
