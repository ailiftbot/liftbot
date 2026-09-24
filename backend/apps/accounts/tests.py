import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from apps.accounts.models import OTP, UserProfile
from apps.billing.models import BillingPlan
from apps.workspaces.models import Workspace, WorkspaceMembership


User = get_user_model()


class SignUpAndVerifyTests(TestCase):
    def setUp(self):
        self.client = Client()
        # Ensure a starter plan is available
        self.plan = BillingPlan.objects.create(
            name='Starter',
            slug='starter',
            price_monthly=29.00,
            conversation_limit=1000,
            token_limit=500000,
            employee_limit=1,
            is_active=True,
        )

    def test_signup_get(self):
        response = self.client.get(reverse('signup'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/signup.html')

    @patch('apps.accounts.views._send_otp_email')
    def test_signup_post_form_success(self, mock_send_email):
        payload = {
            'email': 'alice@example.com',
            'full_name': 'Alice Smith',
            'company_name': 'Acme Corp',
            'password': 'StrongPassword123!',
        }
        response = self.client.post(reverse('signup'), payload)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('signup_verify_otp'))

        user = User.objects.filter(email='alice@example.com').first()
        self.assertIsNotNone(user)
        self.assertEqual(self.client.session['onboarding_user_id'], user.id)

        workspace = Workspace.objects.filter(owner=user).first()
        self.assertIsNotNone(workspace)
        self.assertEqual(workspace.name, 'Acme Corp')
        self.assertEqual(workspace.plan, self.plan)

        membership = WorkspaceMembership.objects.filter(workspace=workspace, user=user).first()
        self.assertIsNotNone(membership)
        self.assertEqual(membership.role, WorkspaceMembership.Role.OWNER)

        otp = OTP.objects.filter(user=user, is_used=False).first()
        self.assertIsNotNone(otp)

    @patch('apps.accounts.views._send_otp_email')
    def test_signup_post_json_success(self, mock_send_email):
        payload = {
            'email': 'bob@example.com',
            'full_name': 'Bob Jones',
            'company_name': 'Beta LLC',
            'password': 'StrongPassword123!',
        }
        response = self.client.post(
            reverse('signup'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(data.get('redirect_url'), reverse('signup_verify_otp'))

    @patch('apps.accounts.views._send_otp_email', side_effect=Exception('SMTP connection error'))
    def test_signup_email_failure_fallback(self, mock_send_email):
        """Even if SMTP fails, the user and OTP should be created and redirect to verify."""
        payload = {
            'email': 'carol@example.com',
            'full_name': 'Carol Danvers',
            'company_name': 'Carol Ent',
            'password': 'StrongPassword123!',
        }
        response = self.client.post(reverse('signup'), payload)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('signup_verify_otp'))

        user = User.objects.filter(email='carol@example.com').first()
        self.assertIsNotNone(user)
        otp = OTP.objects.filter(user=user, is_used=False).first()
        self.assertIsNotNone(otp)

    @patch('apps.accounts.views._send_otp_email')
    def test_otp_verification_flow(self, mock_send_email):
        # 1. Sign up
        payload = {
            'email': 'david@example.com',
            'full_name': 'David Miller',
            'company_name': 'Miller Co',
            'password': 'StrongPassword123!',
        }
        self.client.post(reverse('signup'), payload)
        user = User.objects.get(email='david@example.com')
        otp = OTP.objects.filter(user=user, is_used=False).first()
        self.assertIsNotNone(otp)

        # 2. Submit wrong OTP
        wrong_res = self.client.post(reverse('signup_verify_otp'), {'code': '000000'})
        self.assertEqual(wrong_res.status_code, 200)
        self.assertContains(wrong_res, 'That code is invalid or has expired.')

        # 3. Submit correct OTP via JSON
        valid_res = self.client.post(
            reverse('signup_verify_otp'),
            data=json.dumps({'code': otp.code}),
            content_type='application/json',
        )
        self.assertEqual(valid_res.status_code, 200)
        json_data = valid_res.json()
        self.assertEqual(json_data.get('status'), 'success')
        self.assertEqual(json_data.get('redirect_url'), reverse('billing_onboarding'))

        # Check DB states
        otp.refresh_from_db()
        self.assertTrue(otp.is_used)

        user.refresh_from_db()
        self.assertTrue(user.profile.is_verified)
        self.assertTrue(user.profile.email_verified)


class ContactFormTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_contact_get(self):
        response = self.client.get(reverse('contact'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'marketing/talk_to_us.html')

    @patch('config.contact.EmailMessage.send')
    def test_contact_post_form_success(self, mock_send):
        payload = {
            'full_name': 'Jane Doe',
            'email': 'jane@example.com',
            'topic': 'sales',
            'message': 'We would like to discuss deploying Liftbot on our site.',
        }
        response = self.client.post(reverse('contact'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'marketing/talk_to_us.html')
        self.assertTrue(mock_send.called)

    @patch('config.contact.EmailMessage.send')
    def test_contact_post_json_success(self, mock_send):
        payload = {
            'full_name': 'Jane Doe',
            'email': 'jane@example.com',
            'topic': 'product',
            'message': 'Can you share details on custom LLM models support?',
        }
        response = self.client.post(
            reverse('api_contact'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertTrue(mock_send.called)

    def test_contact_post_invalid_validation(self):
        payload = {
            'full_name': '',
            'email': 'not-an-email',
            'topic': 'invalid-topic',
            'message': 'too short',
        }
        response = self.client.post(
            reverse('api_contact'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data.get('status'), 'error')
        self.assertIn('errors', data)

    def test_contact_options_cors(self):
        response = self.client.options(reverse('api_contact'), HTTP_ORIGIN='https://example.com')
        self.assertIn(response.status_code, [200, 204])
        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), 'https://example.com')
