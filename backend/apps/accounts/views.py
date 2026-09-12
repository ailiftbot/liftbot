import logging

from django.contrib import messages
from django.contrib.auth import login, get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView, LogoutView, PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.conf import settings

from apps.billing.models import BillingPlan, Transaction
from apps.workspaces.models import Workspace, WorkspaceMembership
from apps.workspaces.views import user_workspace

from .forms import LoginForm, SignUpForm, OTPVerifyForm
from .models import OTP, UserProfile

logger = logging.getLogger(__name__)


def _send_otp_email(user, code):
    name = ''
    if hasattr(user, 'profile'):
        name = user.profile.full_name
    send_mail(
        subject='Your LiftBot verification code',
        message=(
            f'Hi {name or user.first_name or "there"},\n\n'
            f'Your LiftBot email verification code is:\n\n'
            f'    {code}\n\n'
            f'This code expires in {getattr(settings, "OTP_EXPIRY_MINUTES", 10)} minutes.\n'
            f'If you did not request this, ignore this email.\n'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@liftbot.ai'),
        recipient_list=[user.email],
        fail_silently=False,
    )


def issue_and_send_otp(user):
    otp = OTP.issue_for(user)
    _send_otp_email(user, otp.code)
    return otp


class SignUpView(View):
    template_name = 'accounts/signup.html'

    def get(self, request):
        if request.user.is_authenticated:
            return redirect('dashboard')
        return render(request, self.template_name, {'form': SignUpForm()})

    def post(self, request):
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            plan = BillingPlan.objects.filter(slug='starter').first()
            workspace = Workspace.objects.create(
                name=form.cleaned_data['company_name'],
                owner=user,
                plan=plan,
                is_active=False,
            )
            WorkspaceMembership.objects.create(
                workspace=workspace,
                user=user,
                role=WorkspaceMembership.Role.OWNER,
            )

            request.session['onboarding_user_id'] = user.id

            # SMTP fail ho toh bhi signup crash na ho — GET pe verify page
            # khud dobara try karega (neeche SignupVerifyOtpView.get dekho).
            try:
                issue_and_send_otp(user)
            except Exception:
                logger.exception('OTP email failed for user %s at signup', user.pk)
                messages.warning(
                    request,
                    'Account created, but we had trouble emailing your code. '
                    'Use "Resend code" on the next screen.',
                )
            else:
                messages.info(request, 'Account created. Enter the code we emailed you to verify your address.')

            return redirect('signup_verify_otp')
        return render(request, self.template_name, {'form': form})


class SignupVerifyOtpView(View):
    """Pre-login email verification, right after signup (session-based, no auth required)."""
    template_name = 'accounts/signup_verify_otp.html'

    def _get_pending_user(self, request):
        user_id = request.session.get('onboarding_user_id')
        if not user_id:
            return None
        return get_user_model().objects.filter(id=user_id).first()

    def get(self, request):
        user = self._get_pending_user(request)
        if not user:
            return redirect('signup')
        if user.profile.is_verified or user.profile.email_verified:
            return self._proceed_to_billing(request, user)

        # Fix: agar koi valid (unexpired, unused) OTP pending nahi hai —
        # jaise login se redirect hua ho, ya purana code expire ho chuka ho —
        # yahin automatically ek naya bhej do. Cooldown respect karta hai.
        if not OTP.has_valid_pending(user) and OTP.cooldown_remaining(user) == 0:
            try:
                issue_and_send_otp(user)
                messages.info(request, f'A verification code was sent to {user.email}.')
            except Exception:
                logger.exception('OTP email failed for user %s', user.pk)
                messages.error(request, 'We could not send the verification code. Try "Resend code" below.')

        return render(request, self.template_name, {'form': OTPVerifyForm(), 'email': user.email})

    def post(self, request):
        user = self._get_pending_user(request)
        if not user:
            return redirect('signup')

        form = OTPVerifyForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form, 'email': user.email})

        otp = OTP.match_for_user(user, form.cleaned_data['code'])
        if otp is None:
            form.add_error('code', 'That code is invalid or has expired.')
            return render(request, self.template_name, {'form': form, 'email': user.email})

        otp.is_used = True
        otp.save(update_fields=['is_used'])
        user.profile.mark_verified()
        return self._proceed_to_billing(request, user)

    def _proceed_to_billing(self, request, user):
        workspace = user_workspace(user)

        txn = workspace.transactions.order_by('-created_at').first()
        if txn is None:
            txn = Transaction.objects.create(
                workspace=workspace,
                plan=workspace.plan,
                amount=workspace.plan.price_monthly if workspace.plan else 0,
                status=Transaction.Status.PENDING,
            )

        request.session.pop('onboarding_user_id', None)
        request.session['onboarding_workspace_id'] = workspace.id
        request.session['onboarding_transaction_id'] = txn.id

        messages.success(request, 'Email verified! Now complete payment to activate your workspace.')
        return redirect('billing_onboarding')


class SignupResendOtpView(View):
    """Resend code during the pre-login signup verification step."""

    def post(self, request):
        user_id = request.session.get('onboarding_user_id')
        user = get_user_model().objects.filter(id=user_id).first() if user_id else None
        if not user:
            return redirect('signup')

        wait = OTP.cooldown_remaining(user)
        if wait > 0:
            messages.error(request, f'Please wait {wait} seconds before requesting another code.')
            return redirect('signup_verify_otp')

        try:
            issue_and_send_otp(user)
        except OTP.CooldownActive as exc:
            messages.error(request, f'Please wait {exc.seconds_left} seconds before requesting another code.')
            return redirect('signup_verify_otp')
        except Exception:
            logger.exception('Signup OTP email failed for user %s', user.pk)
            messages.error(request, 'We could not send the verification code. Please try again.')
            return redirect('signup_verify_otp')

        messages.success(request, 'A new verification code was sent to your email.')
        return redirect('signup_verify_otp')


class EmailLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_success_url(self):
        """
        Safety net for direct/bookmarked logins:
        1. Email verify nahi hai -> signup verify page pe bhejo
           (wahan GET handler khud fresh OTP bhej dega agar zaroorat ho).
        2. Verified hai lekin workspace active nahi -> billing pe bhejo.
        3. Dono clear -> normal dashboard redirect.
        """
        user = self.request.user
        profile = getattr(user, 'profile', None)

        if profile and not (profile.is_verified or profile.email_verified):
            self.request.session['onboarding_user_id'] = user.id
            messages.warning(self.request, 'Please verify your email to continue.')
            return reverse('signup_verify_otp')

        workspace = user_workspace(user)
        if workspace and not workspace.is_active:
            self.request.session['onboarding_workspace_id'] = workspace.id
            last_txn = workspace.transactions.order_by('-created_at').first()
            if last_txn:
                self.request.session['onboarding_transaction_id'] = last_txn.id
            messages.warning(self.request, 'Please complete payment to activate your workspace.')
            return reverse('billing_onboarding')

        return super().get_success_url()


class EmailLogoutView(LogoutView):
    next_page = 'home'


class VerifyEmailView(View):
    """Legacy magic-link from older verification emails."""

    def get(self, request, token):
        profile = get_object_or_404(UserProfile, email_verify_token=token)
        profile.mark_verified()
        OTP.objects.filter(user=profile.user, is_used=False).update(is_used=True)
        messages.success(request, 'Email verified. Your workspace is secured.')
        if request.user.is_authenticated:
            return redirect('dashboard')
        return redirect('login')


class SendOtpView(LoginRequiredMixin, View):
    def post(self, request):
        profile = request.user.profile
        if profile.is_verified or profile.email_verified:
            messages.info(request, 'Email already verified.')
            return redirect('dashboard')

        wait = OTP.cooldown_remaining(request.user)
        if wait > 0:
            messages.error(request, f'Please wait {wait} seconds before requesting another code.')
            return redirect('verify_otp')

        try:
            issue_and_send_otp(request.user)
        except OTP.CooldownActive as exc:
            messages.error(request, f'Please wait {exc.seconds_left} seconds before requesting another code.')
            return redirect('verify_otp')
        except Exception:
            logger.exception('OTP email failed for user %s', request.user.pk)
            messages.error(request, 'We could not send the verification code. Please try again.')
            return redirect('dashboard')

        messages.success(request, 'A verification code was sent to your email.')
        return redirect('verify_otp')


class VerifyOtpView(LoginRequiredMixin, View):
    """Legacy in-dashboard verify flow — kept for users who signed up before this gate existed."""
    template_name = 'accounts/verify_otp.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = request.user.profile
            if profile.is_verified or profile.email_verified:
                messages.info(request, 'Email already verified.')
                return redirect('dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, {'form': OTPVerifyForm()})

    def post(self, request):
        form = OTPVerifyForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})
        otp = OTP.match_for_user(request.user, form.cleaned_data['code'])
        if otp is None:
            form.add_error('code', 'That code is invalid or has expired.')
            return render(request, self.template_name, {'form': form})
        otp.is_used = True
        otp.save(update_fields=['is_used'])
        request.user.profile.mark_verified()
        messages.success(request, 'Email verified. Your workspace is secured.')
        return redirect('dashboard')


class ResendVerificationView(SendOtpView):
    """Same as SendOtpView — kept so existing form actions still work."""


class LiftbotPasswordResetView(PasswordResetView):
    template_name = 'accounts/password_reset.html'
    email_template_name = 'accounts/password_reset_email.txt'
    subject_template_name = 'accounts/password_reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None)


class LiftbotPasswordResetDoneView(PasswordResetDoneView):
    template_name = 'accounts/password_reset_done.html'


class LiftbotPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')


class LiftbotPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'accounts/password_reset_complete.html'