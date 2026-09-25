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


from django.http import JsonResponse


def _is_json_request(request):
    accept = request.headers.get('Accept', '')
    x_req = request.headers.get('X-Requested-With', '')
    content_type = getattr(request, 'content_type', '') or ''
    return 'application/json' in accept or x_req == 'XMLHttpRequest' or 'application/json' in content_type


def _send_otp_email(user, code):
    name = ''
    profile = getattr(user, 'profile', None)
    if profile:
        name = profile.full_name
    send_mail(
        subject='Your LiftBot verification code',
        message=(
            f'Hi {name or user.first_name or "there"},\n\n'
            f'Your LiftBot email verification code is:\n\n'
            f'    {code}\n\n'
            f'This code expires in {getattr(settings, "OTP_EXPIRY_MINUTES", 10)} minutes.\n'
            f'If you did not request this, ignore this email.\n'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@liftbot.app'),
        recipient_list=[user.email],
        fail_silently=False,
    )


def issue_and_send_otp(user):
    """
    Issue an OTP and attempt delivery.
    Fallback: The OTP record remains valid in the database even if email
    delivery encounters a network timeout or SMTP error, allowing retry.
    """
    otp = OTP.issue_for(user)
    try:
        _send_otp_email(user, otp.code)
    except Exception as exc:
        logger.exception('Failed to email OTP code to user %s (%s): %s', user.pk, user.email, exc)
        # Keep OTP in database so retry/fallback succeeds
        raise
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
            try:
                user = form.save()
                plan = BillingPlan.objects.filter(slug='starter').first()
                if plan is None:
                    plan = BillingPlan.objects.filter(is_active=True).first()
                if plan is None:
                    logger.warning('Signup: No BillingPlan found. Creating default starter plan.')
                    plan = BillingPlan.objects.create(
                        name='1 AI Employee',
                        slug='starter',
                        price_monthly=49,
                        employees_included=1,
                        is_active=True,
                    )

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

                email_sent = True
                try:
                    issue_and_send_otp(user)
                except Exception:
                    email_sent = False
                    logger.exception('OTP email delivery failed for user %s at signup', user.pk)
                    messages.warning(
                        request,
                        'Account created, but we had trouble emailing your code. '
                        'Please click "Resend code" on the verification screen.',
                    )
                else:
                    messages.info(request, 'Account created. Enter the code we emailed you to verify your address.')

                if _is_json_request(request):
                    return JsonResponse({
                        'status': 'success',
                        'email_sent': email_sent,
                        'redirect_url': reverse('signup_verify_otp'),
                    })

                return redirect('signup_verify_otp')
            except Exception as exc:
                logger.exception('Unexpected error during signup for email=%s: %s', form.cleaned_data.get('email', '?'), exc)
                error_msg = 'Something went wrong while creating your account. Please try again.'
                if _is_json_request(request):
                    return JsonResponse({'status': 'error', 'message': error_msg}, status=500)
                messages.error(request, error_msg)
                return render(request, self.template_name, {'form': form})

        if _is_json_request(request):
            return JsonResponse({'status': 'error', 'errors': form.errors}, status=400)
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

        profile = getattr(user, 'profile', None)
        if profile and (profile.is_verified or profile.email_verified):
            return self._proceed_to_billing(request, user)

        if not OTP.has_valid_pending(user) and OTP.cooldown_remaining(user) == 0:
            try:
                issue_and_send_otp(user)
                messages.info(request, f'A verification code was sent to {user.email}.')
            except Exception:
                logger.exception('OTP email failed for user %s on verify page load', user.pk)
                messages.error(request, 'We could not send the verification code. Try "Resend code" below.')

        return render(request, self.template_name, {'form': OTPVerifyForm(), 'email': user.email})

    def post(self, request):
        user = self._get_pending_user(request)
        if not user:
            if _is_json_request(request):
                return JsonResponse({'status': 'error', 'message': 'Session expired. Please sign up again.'}, status=400)
            return redirect('signup')

        form = OTPVerifyForm(request.POST)
        if not form.is_valid():
            if _is_json_request(request):
                return JsonResponse({'status': 'error', 'errors': form.errors}, status=400)
            return render(request, self.template_name, {'form': form, 'email': user.email})

        try:
            otp = OTP.match_for_user(user, form.cleaned_data['code'])
            if otp is None:
                form.add_error('code', 'That code is invalid or has expired.')
                if _is_json_request(request):
                    return JsonResponse({'status': 'error', 'message': 'That code is invalid or has expired.'}, status=400)
                return render(request, self.template_name, {'form': form, 'email': user.email})

            otp.is_used = True
            otp.save(update_fields=['is_used'])

            profile = getattr(user, 'profile', None)
            if profile is None:
                profile, _ = UserProfile.objects.get_or_create(
                    user=user,
                    defaults={'full_name': user.first_name or user.username}
                )
            profile.mark_verified()

            return self._proceed_to_billing(request, user)
        except Exception as exc:
            logger.exception('OTP verification crashed for user %s: %s', user.pk, exc)
            err = 'An unexpected error occurred during verification. Please try again.'
            if _is_json_request(request):
                return JsonResponse({'status': 'error', 'message': err}, status=500)
            messages.error(request, err)
            return render(request, self.template_name, {'form': form, 'email': user.email})

    def _proceed_to_billing(self, request, user):
        workspace = user_workspace(user)

        if workspace is None:
            logger.error('_proceed_to_billing: no workspace found for user %s', user.pk)
            err = 'We could not find your workspace. Please contact support.'
            if _is_json_request(request):
                return JsonResponse({'status': 'error', 'message': err}, status=404)
            messages.error(request, err)
            return redirect('signup')

        try:
            txn = workspace.transactions.order_by('-created_at').first()
            if txn is None:
                txn = Transaction.objects.create(
                    workspace=workspace,
                    plan=workspace.plan,
                    amount=workspace.plan.price_monthly if workspace.plan else 0,
                    status=Transaction.Status.PENDING,
                )
        except Exception:
            logger.exception('_proceed_to_billing: transaction creation failed for workspace %s', workspace.pk)
            err = 'We could not set up billing. Please try again.'
            if _is_json_request(request):
                return JsonResponse({'status': 'error', 'message': err}, status=500)
            messages.error(request, err)
            return redirect('signup')

        request.session.pop('onboarding_user_id', None)
        request.session['onboarding_workspace_id'] = workspace.id
        request.session['onboarding_transaction_id'] = txn.id

        if _is_json_request(request):
            return JsonResponse({
                'status': 'success',
                'message': 'Email verified! Proceed to billing.',
                'redirect_url': reverse('billing_onboarding'),
            })

        messages.success(request, 'Email verified! Now complete payment to activate your workspace.')
        return redirect('billing_onboarding')


class SignupResendOtpView(View):
    """Resend code during the pre-login signup verification step."""

    def post(self, request):
        user_id = request.session.get('onboarding_user_id')
        user = get_user_model().objects.filter(id=user_id).first() if user_id else None
        if not user:
            if _is_json_request(request):
                return JsonResponse({'status': 'error', 'message': 'Session expired. Please sign up again.'}, status=400)
            return redirect('signup')

        wait = OTP.cooldown_remaining(user)
        if wait > 0:
            msg = f'Please wait {wait} seconds before requesting another code.'
            if _is_json_request(request):
                return JsonResponse({'status': 'error', 'message': msg, 'cooldown': wait}, status=429)
            messages.error(request, msg)
            return redirect('signup_verify_otp')

        try:
            issue_and_send_otp(user)
        except OTP.CooldownActive as exc:
            msg = f'Please wait {exc.seconds_left} seconds before requesting another code.'
            if _is_json_request(request):
                return JsonResponse({'status': 'error', 'message': msg, 'cooldown': exc.seconds_left}, status=429)
            messages.error(request, msg)
            return redirect('signup_verify_otp')
        except Exception:
            logger.exception('Signup OTP email failed for user %s', user.pk)
            err = 'We could not send the verification code. Please try again.'
            if _is_json_request(request):
                return JsonResponse({'status': 'error', 'message': err}, status=500)
            messages.error(request, err)
            return redirect('signup_verify_otp')

        success_msg = 'A new verification code was sent to your email.'
        if _is_json_request(request):
            return JsonResponse({'status': 'success', 'message': success_msg})

        messages.success(request, success_msg)
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