import logging

from django.contrib import messages
from django.contrib.auth import login, get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView, LogoutView, PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)
from django.core.mail import send_mail
from django.http import JsonResponse
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
    try:
        if hasattr(user, 'profile'):
            name = user.profile.full_name
    except Exception:
        pass
    try:
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
    except Exception:
        logger.exception('send_mail failed for user %s (%s)', user.pk, user.email)
        raise


def issue_and_send_otp(user):
    otp = OTP.issue_for(user)
    try:
        _send_otp_email(user, otp.code)
    except Exception as e:
        # Fallback mechanism: Keep the generated OTP valid in the database so
        # the user can still be assisted / verified even if SMTP connection or delivery failed.
        logger.warning('OTP email delivery failed for user %s (%s): %s. Generated OTP %s remains valid in DB.', user.pk, user.email, e, otp.code)
        raise
    return otp


class SignUpView(View):
    template_name = 'accounts/signup.html'

    def get(self, request):
        try:
            if request.user.is_authenticated:
                return redirect('dashboard')
            return render(request, self.template_name, {'form': SignUpForm()})
        except Exception:
            logger.exception('SignUpView.get crashed')
            return render(request, self.template_name, {'form': SignUpForm()})

    def post(self, request):
        is_json = (
            request.content_type == 'application/json'
            or 'application/json' in request.headers.get('Accept', '')
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        )

        data = request.POST
        if request.content_type == 'application/json':
            import json
            try:
                data = json.loads(request.body.decode('utf-8'))
            except Exception:
                return JsonResponse({'status': 'error', 'message': 'Invalid JSON format'}, status=400)

        # Normalize single 'password' field from API payloads to password1/password2 for UserCreationForm
        if isinstance(data, dict):
            if 'password' in data and 'password1' not in data:
                data = data.copy()
                data['password1'] = data['password']
                data['password2'] = data.get('password2', data['password'])
        elif hasattr(data, 'copy'):
            if 'password' in data and 'password1' not in data:
                data = data.copy()
                data['password1'] = data['password']
                data['password2'] = data.get('password2', data['password'])

        form = SignUpForm(data)
        if form.is_valid():
            try:
                user = form.save()
                plan = BillingPlan.objects.filter(slug='starter').first() or BillingPlan.objects.first()
                if not plan:
                    try:
                        plan = BillingPlan.objects.create(
                            name='Starter',
                            slug='starter',
                            price_monthly=29.00,
                            conversation_limit=1000,
                            token_limit=500000,
                            employee_limit=1,
                            is_active=True,
                        )
                    except Exception:
                        logger.warning('Could not auto-create starter plan')
                        plan = None

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
            except Exception as e:
                logger.exception('SignUpView.post: DB operations failed')
                if is_json:
                    from django.http import JsonResponse
                    return JsonResponse({'status': 'error', 'message': 'Database error creating account. Please try again.'}, status=500)
                form.add_error(None, 'Something went wrong creating your account. Please try again.')
                return render(request, self.template_name, {'form': form})

            request.session['onboarding_user_id'] = user.id

            # Send OTP email with graceful fallback
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

            if is_json:
                from django.http import JsonResponse
                return JsonResponse({
                    'status': 'success',
                    'message': 'Account created successfully.',
                    'redirect_url': reverse('signup_verify_otp'),
                })

            return redirect('signup_verify_otp')

        if is_json:
            from django.http import JsonResponse
            return JsonResponse({'status': 'error', 'errors': form.errors.get_json_data()}, status=400)

        return render(request, self.template_name, {'form': form})


class SignupVerifyOtpView(View):
    """Pre-login email verification, right after signup (session-based, no auth required)."""
    template_name = 'accounts/signup_verify_otp.html'

    def _get_pending_user(self, request):
        user_id = request.session.get('onboarding_user_id')
        if not user_id:
            return None
        try:
            return get_user_model().objects.filter(id=user_id).first()
        except Exception:
            logger.exception('SignupVerifyOtpView._get_pending_user DB error')
            return None

    def get(self, request):
        try:
            user = self._get_pending_user(request)
            if not user:
                return redirect('signup')

            # Safely check profile — may not exist if DB is inconsistent
            profile = getattr(user, 'profile', None)
            if profile and (profile.is_verified or profile.email_verified):
                return self._proceed_to_billing(request, user)

            if not OTP.has_valid_pending(user) and OTP.cooldown_remaining(user) == 0:
                try:
                    issue_and_send_otp(user)
                    messages.info(request, f'A verification code was sent to {user.email}.')
                except Exception:
                    logger.exception('OTP email failed for user %s', user.pk)
                    messages.error(request, 'We could not send the verification code. Try "Resend code" below.')

            return render(request, self.template_name, {'form': OTPVerifyForm(), 'email': user.email})
        except Exception:
            logger.exception('SignupVerifyOtpView.get crashed')
            messages.error(request, 'Something went wrong. Please try signing up again.')
            return redirect('signup')

    def post(self, request):
        is_json = (
            request.content_type == 'application/json'
            or 'application/json' in request.headers.get('Accept', '')
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        )

        try:
            user = self._get_pending_user(request)
            if not user:
                if is_json:
                    from django.http import JsonResponse
                    return JsonResponse({'status': 'error', 'message': 'No pending signup session found. Please sign up first.'}, status=400)
                return redirect('signup')

            data = request.POST
            if request.content_type == 'application/json':
                import json
                try:
                    data = json.loads(request.body.decode('utf-8'))
                except Exception:
                    from django.http import JsonResponse
                    return JsonResponse({'status': 'error', 'message': 'Invalid JSON format'}, status=400)

            form = OTPVerifyForm(data)
            if not form.is_valid():
                if is_json:
                    from django.http import JsonResponse
                    return JsonResponse({'status': 'error', 'errors': form.errors.get_json_data()}, status=400)
                return render(request, self.template_name, {'form': form, 'email': user.email})

            otp = OTP.match_for_user(user, form.cleaned_data['code'])
            if otp is None:
                if is_json:
                    from django.http import JsonResponse
                    return JsonResponse({'status': 'error', 'message': 'That code is invalid or has expired.'}, status=400)
                form.add_error('code', 'That code is invalid or has expired.')
                return render(request, self.template_name, {'form': form, 'email': user.email})

            otp.is_used = True
            otp.save(update_fields=['is_used'])

            profile = getattr(user, 'profile', None)
            if not profile:
                profile, _ = UserProfile.objects.get_or_create(
                    user=user,
                    defaults={'full_name': user.get_full_name() or user.username},
                )
            profile.mark_verified()

            return self._proceed_to_billing(request, user, is_json=is_json)
        except Exception:
            logger.exception('SignupVerifyOtpView.post crashed')
            if is_json:
                from django.http import JsonResponse
                return JsonResponse({'status': 'error', 'message': 'Something went wrong verifying your code.'}, status=500)
            messages.error(request, 'Something went wrong verifying your code. Please try again.')
            return redirect('signup_verify_otp')

    def _proceed_to_billing(self, request, user, is_json=False):
        try:
            workspace = user_workspace(user)
            if not workspace:
                logger.error('User %s has no workspace — redirecting to signup', user.pk)
                if is_json:
                    from django.http import JsonResponse
                    return JsonResponse({'status': 'error', 'message': 'No workspace found. Please sign up again.'}, status=400)
                messages.error(request, 'No workspace found. Please sign up again.')
                return redirect('signup')

            # Ensure plan exists for Transaction NOT NULL constraint
            plan = workspace.plan or BillingPlan.objects.filter(slug='starter').first() or BillingPlan.objects.first()
            if not plan:
                try:
                    plan = BillingPlan.objects.create(
                        name='Starter',
                        slug='starter',
                        price_monthly=29.00,
                        conversation_limit=1000,
                        token_limit=500000,
                        employee_limit=1,
                        is_active=True,
                    )
                except Exception:
                    logger.exception('Could not create fallback starter plan for transaction')

            if not workspace.plan and plan:
                workspace.plan = plan
                workspace.save(update_fields=['plan'])

            txn = workspace.transactions.order_by('-created_at').first()
            if txn is None and plan:
                txn = Transaction.objects.create(
                    workspace=workspace,
                    plan=plan,
                    amount=plan.price_monthly,
                    status=Transaction.Status.PENDING,
                )

            request.session.pop('onboarding_user_id', None)
            request.session['onboarding_workspace_id'] = workspace.id
            if txn:
                request.session['onboarding_transaction_id'] = txn.id

            messages.success(request, 'Email verified! Now complete payment to activate your workspace.')

            if is_json:
                from django.http import JsonResponse
                return JsonResponse({
                    'status': 'success',
                    'message': 'Email verified successfully.',
                    'redirect_url': reverse('billing_onboarding'),
                })

            return redirect('billing_onboarding')
        except Exception:
            logger.exception('_proceed_to_billing failed for user %s', user.pk)
            if is_json:
                from django.http import JsonResponse
                return JsonResponse({'status': 'error', 'message': 'An error occurred proceeding to billing.'}, status=500)
            messages.error(request, 'Something went wrong. Please try again.')
            return redirect('signup')


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