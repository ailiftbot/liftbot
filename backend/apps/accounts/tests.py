import re
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.billing.models import BillingPlan
from apps.workspaces.models import Workspace, WorkspaceMembership

from .models import OTP


def make_plan(slug='starter', price='19.00', **kwargs):
    defaults = dict(
        name=slug.title(), price_monthly=Decimal(price),
        conversation_limit=500, token_limit=100_000, employee_limit=1,
    )
    defaults.update(kwargs)
    return BillingPlan.objects.create(slug=slug, **defaults)


def make_user(email, password='S3cure-pass-123', verified=True):
    user = User.objects.create_user(username=email, email=email, password=password)
    user.profile.is_verified = verified
    user.profile.email_verified = verified
    user.profile.save()
    return user


SIGNUP_DATA = {
    'full_name': 'Ada Lovelace',
    'email': 'Ada@Example.com',
    'company_name': 'Analytical Engines',
    'password1': 'S3cure-pass-123',
    'password2': 'S3cure-pass-123',
    'accept_terms': 'on',
}


def code_from_outbox(index=-1):
    match = re.search(r'\b(\d{6})\b', mail.outbox[index].body)
    assert match, mail.outbox[index].body
    return match.group(1)


class SignupFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.plan = make_plan()
        make_plan('pro', '49.00')

    def test_signup_emails_otp_and_verify_leads_to_onboarding(self):
        resp = self.client.post(reverse('signup'), SIGNUP_DATA)
        self.assertRedirects(resp, reverse('signup_verify_otp'), fetch_redirect_response=False)

        user = User.objects.get(email='ada@example.com')
        self.assertEqual(user.username, 'ada@example.com')
        workspace = Workspace.objects.get(owner=user)
        self.assertEqual(workspace.plan, self.plan)
        self.assertFalse(workspace.is_active)
        self.assertTrue(WorkspaceMembership.objects.filter(
            workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER).exists())

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ada@example.com'])
        code = code_from_outbox()

        page = self.client.get(reverse('signup_verify_otp'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Start over')
        self.assertEqual(len(mail.outbox), 1, 'GET must not resend while a code is pending')

        resp = self.client.post(reverse('signup_verify_otp'), {'code': code})
        self.assertRedirects(resp, reverse('billing_onboarding'), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.profile.is_verified)
        self.assertEqual(self.client.session['onboarding_workspace_id'], workspace.id)

        onboarding = self.client.get(reverse('billing_onboarding'))
        self.assertEqual(onboarding.status_code, 200)

    def test_falls_back_to_first_active_plan_without_starter(self):
        self.plan.delete()
        self.client.post(reverse('signup'), SIGNUP_DATA)
        workspace = Workspace.objects.get(owner__email='ada@example.com')
        self.assertEqual(workspace.plan.slug, 'pro')

    def test_no_plan_shows_error_and_creates_nothing(self):
        BillingPlan.objects.all().delete()
        resp = self.client.post(reverse('signup'), SIGNUP_DATA)
        self.assertEqual(resp.status_code, 503)
        self.assertContains(resp, 'no plan is configured', status_code=503)
        self.assertFalse(User.objects.exists())
        self.assertFalse(Workspace.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_terms_consent_required(self):
        data = dict(SIGNUP_DATA)
        data.pop('accept_terms')
        resp = self.client.post(reverse('signup'), data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Terms of Service')
        self.assertFalse(User.objects.exists())

    def test_signup_page_links_terms_and_privacy(self):
        resp = self.client.get(reverse('signup'))
        self.assertContains(resp, reverse('terms'))
        self.assertContains(resp, reverse('privacy'))

    def test_email_longer_than_150_rejected(self):
        data = dict(SIGNUP_DATA, email='a' * 140 + '@example.com')
        resp = self.client.post(reverse('signup'), data)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_double_submit_does_not_crash_or_duplicate(self):
        self.client.post(reverse('signup'), SIGNUP_DATA)
        resp = self.client.post(reverse('signup'), SIGNUP_DATA)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(User.objects.filter(email='ada@example.com').count(), 1)
        self.assertEqual(Workspace.objects.count(), 1)

    def test_verified_email_cannot_sign_up_again(self):
        make_user('ada@example.com')
        resp = self.client.post(reverse('signup'), SIGNUP_DATA)
        self.assertContains(resp, 'already exists')
        self.assertEqual(User.objects.count(), 1)

    def test_start_over_clears_pending_signup(self):
        self.client.post(reverse('signup'), SIGNUP_DATA)
        resp = self.client.get(reverse('signup') + '?restart=1')
        self.assertRedirects(resp, reverse('signup'), fetch_redirect_response=False)
        self.assertNotIn('onboarding_user_id', self.client.session)


class OTPLockoutTests(TestCase):
    def setUp(self):
        cache.clear()
        make_plan()
        self.client.post(reverse('signup'), SIGNUP_DATA)
        self.user = User.objects.get(email='ada@example.com')
        self.code = code_from_outbox()

    def _wrong(self):
        wrong = '000000' if self.code != '000000' else '111111'
        return self.client.post(reverse('signup_verify_otp'), {'code': wrong})

    def test_code_invalidated_after_five_wrong_attempts(self):
        for _ in range(4):
            resp = self._wrong()
            self.assertContains(resp, 'incorrect')
        resp = self._wrong()
        self.assertContains(resp, 'request a new code')

        otp = OTP.objects.filter(user=self.user).latest('created_at')
        self.assertTrue(otp.is_used)
        self.assertEqual(otp.attempts, 5)

        # The right code no longer works.
        resp = self.client.post(reverse('signup_verify_otp'), {'code': self.code})
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.profile.is_verified)

    def test_correct_code_after_some_wrong_attempts_still_works(self):
        self._wrong()
        self._wrong()
        resp = self.client.post(reverse('signup_verify_otp'), {'code': self.code})
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.profile.is_verified)


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.plan = make_plan()

    def test_unverified_user_is_sent_to_verify(self):
        make_user('bob@example.com', verified=False)
        resp = self.client.post(reverse('login'), {'username': 'BOB@example.com', 'password': 'S3cure-pass-123'})
        self.assertRedirects(resp, reverse('signup_verify_otp'), fetch_redirect_response=False)
        self.assertIn('onboarding_user_id', self.client.session)

    def test_user_without_workspace_is_sent_to_create_one(self):
        make_user('bob@example.com')
        resp = self.client.post(reverse('login'), {'username': 'bob@example.com', 'password': 'S3cure-pass-123'})
        self.assertRedirects(resp, reverse('workspace_create'), fetch_redirect_response=False)
        page = self.client.get(reverse('workspace_create'))
        self.assertEqual(page.status_code, 200)

    def test_user_without_profile_can_log_in(self):
        user = make_user('legacy@example.com')
        user.profile.delete()
        user = User.objects.get(pk=user.pk)
        ws = Workspace.objects.create(name='Legacy', owner=user, plan=self.plan, is_active=True)
        WorkspaceMembership.objects.create(workspace=ws, user=user, role='owner')
        resp = self.client.post(reverse('login'), {'username': 'legacy@example.com', 'password': 'S3cure-pass-123'})
        # Profile is recreated unverified, so the verify step follows (no crash).
        self.assertEqual(resp.status_code, 302)

    def test_active_workspace_goes_to_dashboard(self):
        user = make_user('bob@example.com')
        ws = Workspace.objects.create(name='Bob Co', owner=user, plan=self.plan, is_active=True)
        WorkspaceMembership.objects.create(workspace=ws, user=user, role='owner')
        resp = self.client.post(reverse('login'), {'username': 'bob@example.com', 'password': 'S3cure-pass-123'})
        self.assertRedirects(resp, reverse('dashboard'), fetch_redirect_response=False)

    def test_login_page_has_signup_link_and_no_dead_google_button(self):
        resp = self.client.get(reverse('login'))
        self.assertContains(resp, reverse('signup'))
        self.assertNotContains(resp, 'Continue with Google')


class PasswordChangeTests(TestCase):
    def setUp(self):
        plan = make_plan()
        self.user = make_user('carol@example.com')
        ws = Workspace.objects.create(name='Carol Co', owner=self.user, plan=plan, is_active=True)
        WorkspaceMembership.objects.create(workspace=ws, user=self.user, role='owner')
        self.client.force_login(self.user)

    def test_change_password(self):
        self.assertEqual(self.client.get(reverse('password_change')).status_code, 200)
        resp = self.client.post(reverse('password_change'), {
            'old_password': 'S3cure-pass-123',
            'new_password1': 'An0ther-strong-pass',
            'new_password2': 'An0ther-strong-pass',
        })
        self.assertRedirects(resp, reverse('password_change_done'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('An0ther-strong-pass'))

    def test_wrong_old_password(self):
        resp = self.client.post(reverse('password_change'), {
            'old_password': 'nope',
            'new_password1': 'An0ther-strong-pass',
            'new_password2': 'An0ther-strong-pass',
        })
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('S3cure-pass-123'))
