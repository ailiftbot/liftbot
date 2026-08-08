from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import (
    LoginView, LogoutView, PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.conf import settings

from apps.billing.models import BillingPlan
from apps.workspaces.models import Workspace, WorkspaceMembership

from .forms import LoginForm, SignUpForm
from .models import UserProfile


def _send_verification_email(request, user, profile):
    token = profile.issue_verify_token()
    link = request.build_absolute_uri(reverse('verify_email', args=[token]))
    send_mail(
        subject='Verify your LiftBot email',
        message=(
            f'Hi {profile.full_name or user.first_name},\n\n'
            f'Welcome to LiftBot. Verify your email to secure your workspace:\n\n'
            f'{link}\n\n'
            f'If you did not sign up, ignore this email.\n'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@liftbot.ai'),
        recipient_list=[user.email],
        fail_silently=True,
    )


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
            )
            WorkspaceMembership.objects.create(
                workspace=workspace,
                user=user,
                role=WorkspaceMembership.Role.OWNER,
            )
            profile = user.profile
            _send_verification_email(request, user, profile)
            login(request, user)
            messages.success(request, 'Account created. Check your email to verify (also printed in server logs in MVP).')
            return redirect('dashboard')
        return render(request, self.template_name, {'form': form})


class EmailLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True


class EmailLogoutView(LogoutView):
    next_page = 'home'


class VerifyEmailView(View):
    def get(self, request, token):
        profile = get_object_or_404(UserProfile, email_verify_token=token)
        profile.email_verified = True
        profile.email_verify_token = ''
        profile.save(update_fields=['email_verified', 'email_verify_token'])
        messages.success(request, 'Email verified. Your workspace is secured.')
        if request.user.is_authenticated:
            return redirect('dashboard')
        return redirect('login')


class ResendVerificationView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return redirect('login')
        profile = request.user.profile
        if profile.email_verified:
            messages.info(request, 'Email already verified.')
        else:
            _send_verification_email(request, request.user, profile)
            messages.success(request, 'Verification email resent.')
        return redirect('dashboard')


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
