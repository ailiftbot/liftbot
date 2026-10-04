import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView, LogoutView, PasswordChangeDoneView, PasswordChangeView,
    PasswordResetCompleteView, PasswordResetConfirmView, PasswordResetDoneView,
    PasswordResetView,
)
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View

from apps.workspaces.invites import accept_session_invite, session_invite
from apps.workspaces.onboarding import create_workspace_for, default_plan, prepare_onboarding_session
from apps.workspaces.views import user_workspace

from .forms import LoginForm, OTPVerifyForm, SignUpForm, stale_unverified_users
from .models import OTP, UserProfile

logger = logging.getLogger(__name__)

MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'

OTP_ERROR_MESSAGES = {
    'invalid': 'That code is incorrect. Check the latest email and try again.',
    'expired': 'That code has expired. Request a new code below.',
    'locked': 'Too many incorrect attempts. That code has been disabled — request a new code below.',
}


def get_profile(user):
    """user.profile, created on the fly for legacy users that never got one."""
    profile = getattr(user, 'profile', None)
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={'full_name': user.get_full_name() or user.first_name},
        )
    return profile


def is_email_verified(user):
    profile = get_profile(user)
    return bool(profile.is_verified or profile.email_verified)


def _send_otp_email(user, code):
    name = get_profile(user).full_name
    send_mail(
        subject='Your LiftBot verification code',
        message=(
            f'Hi {name or user.first_name or "there"},\n\n'
            f'Your LiftBot email verification code is:\n\n'
            f'    {code}\n\n'
            f'This code expires in {getattr(settings, "OTP_EXPIRY_MINUTES", 10)} minutes.\n'
            f'If you did not request this, ignore this email.\n'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def issue_and_send_otp(user):
    otp = OTP.issue_for(user)
    try:
        _send_otp_email(user, otp.code)
    except Exception:
        # Do not leave a code active when the email provider rejected it.
        otp.delete()
        raise
    return otp


def _post_auth_redirect(request, user):
    """
    Where a verified user goes next: accept a pending invite, else create a
    workspace if they have none, else onboarding billing if the workspace is
    unpaid, else None (caller's default, usually the dashboard).
    """
    joined = accept_session_invite(request, user)
    if joined is not None:
        messages.success(request, f'You joined the {joined.name} workspace.')
        return reverse('dashboard')

    workspace = user_workspace(user)
    if workspace is None:
        messages.info(request, 'You are not part of a workspace yet. Create one to continue.')
        return reverse('workspace_create')

    if not workspace.is_active:
        prepare_onboarding_session(request, workspace)
        messages.warning(request, 'Please complete payment to activate your workspace.')
        return reverse('billing_onboarding')
    return None


class SignUpView(View):
    template_name = 'accounts/signup.html'

    def _render(self, request, form, invite, status=200):
        return render(request, self.template_name, {'form': form, 'invite': invite}, status=status)

    def get(self, request):
        if request.GET.get('restart'):
            # "Start over" from the verify step: forget the pending signup.
            request.session.pop('onboarding_user_id', None)
            if request.user.is_authenticated:
                logout(request)
            return redirect('signup')
        if request.user.is_authenticated:
            return redirect('dashboard')
        invite = session_invite(request)
        return self._render(request, SignUpForm(invite=invite), invite)

    def post(self, request):
        invite = session_invite(request)
        form = SignUpForm(request.POST, invite=invite)
        if not form.is_valid():
            return self._render(request, form, invite)

        plan = None
        if invite is None:
            plan = default_plan()
            if plan is None:
                logger.error('Signup blocked: no active BillingPlan configured (run seed_plans).')
                form.add_error(
                    None,
                    'Sign-ups are temporarily unavailable because no plan is configured. '
                    'Please contact support.',
                )
                return self._render(request, form, invite, status=503)

        email = form.cleaned_data['email']
        try:
            with transaction.atomic():
                # Replace abandoned, never-verified signups for this address.
                stale_unverified_users(email).delete()
                user = form.save()
                if invite is None:
                    create_workspace_for(user, form.cleaned_data['company_name'], plan)
        except IntegrityError:
            # Double submit / race: the account was created by a parallel request.
            logger.warning('Signup IntegrityError for %s (double submit?)', email)
            form.add_error('email', 'An account with this email already exists. Log in instead.')
            return self._render(request, form, invite)

        request.session['onboarding_user_id'] = user.id

        # Don't fail the signup if SMTP is down — the verify page retries on GET.
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


class SignupVerifyOtpView(View):
    """Pre-login email verification, right after signup (session-based, no auth required)."""
    template_name = 'accounts/signup_verify_otp.html'

    def _get_pending_user(self, request):
        user_id = request.session.get('onboarding_user_id')
        if not user_id:
            return None
        return get_user_model().objects.filter(id=user_id).first()

    def _render(self, request, form, user):
        return render(request, self.template_name, {'form': form, 'email': user.email})

    def get(self, request):
        user = self._get_pending_user(request)
        if not user:
            return redirect('signup')
        if is_email_verified(user):
            return self._proceed_to_billing(request, user)

        # No valid pending code (came from login, or the old one expired /
        # was locked) — send a fresh one, respecting the resend cooldown.
        if not OTP.has_valid_pending(user) and OTP.cooldown_remaining(user) == 0:
            try:
                issue_and_send_otp(user)
                messages.info(request, f'A verification code was sent to {user.email}.')
            except Exception:
                logger.exception('OTP email failed for user %s', user.pk)
                messages.error(request, 'We could not send the verification code. Try "Resend code" below.')

        return self._render(request, OTPVerifyForm(), user)

    def post(self, request):
        user = self._get_pending_user(request)
        if not user:
            return redirect('signup')
        if is_email_verified(user):
            return self._proceed_to_billing(request, user)

        form = OTPVerifyForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, user)

        otp, error = OTP.verify_for_user(user, form.cleaned_data['code'])
        if otp is None:
            form.add_error('code', OTP_ERROR_MESSAGES.get(error, OTP_ERROR_MESSAGES['invalid']))
            return self._render(request, form, user)

        otp.consume()
        get_profile(user).mark_verified()
        return self._proceed_to_billing(request, user)

    def _proceed_to_billing(self, request, user):
        request.session.pop('onboarding_user_id', None)

        workspace = user_workspace(user)
        if session_invite(request) is None and workspace is not None and not workspace.is_active:
            # Normal signup: pay first; billing logs the owner in on success.
            prepare_onboarding_session(request, workspace)
            messages.success(request, 'Email verified! Now complete payment to activate your workspace.')
            return redirect('billing_onboarding')

        # Invite / no workspace / already-paid workspace: no payment step.
        login(request, user, backend=MODEL_BACKEND)
        next_url = _post_auth_redirect(request, user)
        if next_url is None:
            messages.success(request, 'Email verified. Welcome to LiftBot!')
            return redirect('dashboard')
        return redirect(next_url)


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
        1. Email not verified -> signup verify page (its GET sends a fresh code).
        2. Pending invite in session -> join that workspace.
        3. No workspace -> create one.  Unpaid workspace -> onboarding billing.
        4. Otherwise the normal ?next= / dashboard redirect.
        """
        user = self.request.user
        if not is_email_verified(user):
            self.request.session['onboarding_user_id'] = user.id
            messages.warning(self.request, 'Please verify your email to continue.')
            return reverse('signup_verify_otp')

        next_url = _post_auth_redirect(self.request, user)
        if next_url is not None:
            return next_url
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
        if is_email_verified(request.user):
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
        if request.user.is_authenticated and is_email_verified(request.user):
            messages.info(request, 'Email already verified.')
            return redirect('dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, {'form': OTPVerifyForm()})

    def post(self, request):
        form = OTPVerifyForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})
        otp, error = OTP.verify_for_user(request.user, form.cleaned_data['code'])
        if otp is None:
            form.add_error('code', OTP_ERROR_MESSAGES.get(error, OTP_ERROR_MESSAGES['invalid']))
            return render(request, self.template_name, {'form': form})
        otp.consume()
        get_profile(request.user).mark_verified()
        messages.success(request, 'Email verified. Your workspace is secured.')
        return redirect('dashboard')


class ResendVerificationView(SendOtpView):
    """Same as SendOtpView — kept so existing form actions still work."""


class LiftbotPasswordResetView(PasswordResetView):
    template_name = 'accounts/password_reset.html'
    email_template_name = 'accounts/password_reset_email.txt'
    subject_template_name = 'accounts/password_reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')

    @property
    def from_email(self):
        return settings.DEFAULT_FROM_EMAIL


class LiftbotPasswordResetDoneView(PasswordResetDoneView):
    template_name = 'accounts/password_reset_done.html'


class LiftbotPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')


class LiftbotPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'accounts/password_reset_complete.html'


class LiftbotPasswordChangeView(PasswordChangeView):
    template_name = 'accounts/password_change_form.html'
    success_url = reverse_lazy('password_change_done')


class LiftbotPasswordChangeDoneView(PasswordChangeDoneView):
    template_name = 'accounts/password_change_done.html'
