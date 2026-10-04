from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.workspaces.models import Workspace, WorkspaceMembership

from .models import BillingPlan, Invoice, Transaction
from .views import apply_checkout_session

STRIPE_KEYS = dict(STRIPE_SECRET_KEY='sk_test_x', STRIPE_PUBLISHABLE_KEY='pk_test_x', STRIPE_WEBHOOK_SECRET='whsec_x')


class StripeObj(dict):
    """Minimal stand-in for a StripeObject (dict access + attribute access)."""
    __getattr__ = dict.get


class BillingTestCase(TestCase):
    def setUp(self):
        self.starter = BillingPlan.objects.create(
            name='Starter', slug='starter', price_monthly=19, conversation_limit=500, token_limit=1000,
        )
        self.pro = BillingPlan.objects.create(
            name='Pro', slug='pro', price_monthly=49, conversation_limit=2500, token_limit=5000, employee_limit=3,
        )
        self.user = User.objects.create_user('owner@example.com', 'owner@example.com', 'pw-123456!')
        self.user.profile.is_verified = True
        self.user.profile.save()
        self.ws = Workspace.objects.create(name='Acme', owner=self.user, plan=self.starter, is_active=False)
        WorkspaceMembership.objects.create(workspace=self.ws, user=self.user, role='owner')

    def start_onboarding(self):
        session = self.client.session
        session['onboarding_workspace_id'] = self.ws.id
        session.save()

    def paid_session(self, **overrides):
        txn = self.ws.transactions.order_by('-created_at').first()
        data = StripeObj(
            id='cs_test_1', status='complete', payment_status='paid', subscription='sub_1',
            customer='cus_1', amount_total=4900,
            metadata={'workspace_id': str(self.ws.id), 'plan_slug': 'pro', 'transaction_id': str(txn.id) if txn else ''},
        )
        data.update(overrides)
        return data


class OnboardingTests(BillingTestCase):
    @override_settings(DEBUG=False, STRIPE_SECRET_KEY='', STRIPE_PUBLISHABLE_KEY='')
    def test_cannot_activate_without_payment_gateway_in_production(self):
        self.start_onboarding()
        self.client.post(reverse('billing_onboarding_pay'), {'simulate': 'success'})
        self.ws.refresh_from_db()
        self.assertFalse(self.ws.is_active)

    @override_settings(DEBUG=True, STRIPE_SECRET_KEY='', STRIPE_PUBLISHABLE_KEY='')
    def test_simulated_payment_in_debug_logs_user_in(self):
        self.start_onboarding()
        r = self.client.post(reverse('billing_onboarding_pay'), {'plan': 'pro'})
        self.assertRedirects(r, reverse('dashboard'), fetch_redirect_response=False)
        self.ws.refresh_from_db()
        self.assertTrue(self.ws.is_active)
        self.assertEqual(self.ws.plan, self.pro)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.id)

    @override_settings(**STRIPE_KEYS)
    def test_stripe_checkout_redirect_and_verified_return(self):
        self.start_onboarding()
        self.client.get(reverse('billing_onboarding'))
        fake = mock.MagicMock()
        fake.Customer.create.return_value = StripeObj(id='cus_1')
        fake.checkout.Session.create.return_value = StripeObj(id='cs_test_1', url='https://checkout.stripe.com/x')
        with mock.patch('apps.billing.views._stripe', return_value=fake):
            r = self.client.post(reverse('billing_onboarding_pay'), {'plan': 'pro'})
        self.assertEqual(r['Location'], 'https://checkout.stripe.com/x')
        kwargs = fake.checkout.Session.create.call_args.kwargs
        self.assertEqual(kwargs['metadata']['plan_slug'], 'pro')
        self.ws.refresh_from_db()
        self.assertFalse(self.ws.is_active)  # nothing activates until Stripe confirms

        # Unpaid session → still inactive
        fake.checkout.Session.retrieve.return_value = self.paid_session(payment_status='unpaid', status='open')
        with mock.patch('apps.billing.views._stripe', return_value=fake):
            self.client.get(reverse('billing_onboarding_status'), {'session_id': 'cs_test_1'})
        self.ws.refresh_from_db()
        self.assertFalse(self.ws.is_active)

        fake.checkout.Session.retrieve.return_value = self.paid_session()
        with mock.patch('apps.billing.views._stripe', return_value=fake):
            r = self.client.get(reverse('billing_onboarding_status'), {'session_id': 'cs_test_1'})
        self.assertRedirects(r, reverse('dashboard'), fetch_redirect_response=False)
        self.ws.refresh_from_db()
        self.assertTrue(self.ws.is_active)
        self.assertEqual(self.ws.plan, self.pro)
        self.assertEqual(Transaction.objects.get(workspace=self.ws).status, Transaction.Status.SUCCESS)

    @override_settings(**STRIPE_KEYS)
    def test_session_for_other_workspace_is_ignored(self):
        self.start_onboarding()
        self.client.get(reverse('billing_onboarding'))
        fake = mock.MagicMock()
        fake.checkout.Session.retrieve.return_value = self.paid_session(metadata={'workspace_id': '999', 'plan_slug': 'pro'})
        with mock.patch('apps.billing.views._stripe', return_value=fake):
            self.client.get(reverse('billing_onboarding_status'), {'session_id': 'cs_other'})
        self.ws.refresh_from_db()
        self.assertFalse(self.ws.is_active)


class CheckoutSessionTests(BillingTestCase):
    def test_apply_is_idempotent(self):
        Transaction.objects.create(workspace=self.ws, plan=self.starter, amount=19)
        session = self.paid_session()
        apply_checkout_session(session)
        apply_checkout_session(session)
        self.assertEqual(Invoice.objects.filter(workspace=self.ws).count(), 1)
        self.ws.refresh_from_db()
        self.assertTrue(self.ws.is_active)
        self.assertEqual(self.ws.stripe_subscription_id, 'sub_1')

    def test_plan_switch_cancels_old_subscription(self):
        self.ws.is_active = True
        self.ws.stripe_subscription_id = 'sub_old'
        self.ws.save()
        fake = mock.MagicMock()
        with mock.patch('apps.billing.views._stripe', return_value=fake):
            apply_checkout_session(self.paid_session(id='cs_2', subscription='sub_new'))
        fake.Subscription.cancel.assert_called_once_with('sub_old')


@override_settings(**STRIPE_KEYS)
class WebhookTests(BillingTestCase):
    def send(self, event):
        fake = mock.MagicMock()
        fake.Webhook.construct_event.return_value = event
        with mock.patch('apps.billing.views._stripe', return_value=fake):
            return self.client.post(reverse('stripe_webhook'), data=b'{}', content_type='application/json',
                                    HTTP_STRIPE_SIGNATURE='sig')

    def test_bad_signature(self):
        fake = mock.MagicMock()
        fake.Webhook.construct_event.side_effect = ValueError('bad sig')
        with mock.patch('apps.billing.views._stripe', return_value=fake):
            r = self.client.post(reverse('stripe_webhook'), data=b'{}', content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_checkout_completed_activates(self):
        Transaction.objects.create(workspace=self.ws, plan=self.starter, amount=19)
        r = self.send({'type': 'checkout.session.completed', 'data': {'object': self.paid_session()}})
        self.assertEqual(r.status_code, 200)
        self.ws.refresh_from_db()
        self.assertTrue(self.ws.is_active)

    def test_renewal_resets_usage_and_cancellation_deactivates(self):
        self.ws.is_active = True
        self.ws.stripe_subscription_id = 'sub_1'
        self.ws.conversations_used = 400
        self.ws.save()
        invoice = {'id': 'in_1', 'subscription': 'sub_1', 'billing_reason': 'subscription_cycle', 'amount_paid': 1900}
        self.send({'type': 'invoice.paid', 'data': {'object': invoice}})
        self.send({'type': 'invoice.paid', 'data': {'object': invoice}})  # retry
        self.ws.refresh_from_db()
        self.assertEqual(self.ws.conversations_used, 0)
        self.assertEqual(Invoice.objects.filter(stripe_invoice_id='in_1').count(), 1)

        self.send({'type': 'customer.subscription.deleted', 'data': {'object': {'id': 'sub_1'}}})
        self.ws.refresh_from_db()
        self.assertFalse(self.ws.is_active)


class PlanChangeTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.ws.is_active = True
        self.ws.save()

    @override_settings(DEBUG=False, STRIPE_SECRET_KEY='', STRIPE_PUBLISHABLE_KEY='')
    def test_no_free_upgrade_without_stripe(self):
        self.client.force_login(self.user)
        self.client.post(reverse('billing_checkout', args=['pro']))
        self.ws.refresh_from_db()
        self.assertEqual(self.ws.plan, self.starter)

    def test_member_cannot_change_plan(self):
        member = User.objects.create_user('m@example.com', 'm@example.com', 'pw-123456!')
        member.profile.is_verified = True
        member.profile.save()
        WorkspaceMembership.objects.create(workspace=self.ws, user=member, role='member')
        self.client.force_login(member)
        with override_settings(DEBUG=True, STRIPE_SECRET_KEY='', STRIPE_PUBLISHABLE_KEY=''):
            r = self.client.post(reverse('billing_checkout', args=['pro']))
        self.assertEqual(r.status_code, 403)
