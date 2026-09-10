from django.contrib import messages
from django.shortcuts import redirect
from django.urls import resolve, Resolver404

from .views import user_workspace

# URL names jo bina verified email / bina active workspace ke bhi accessible rehne chahiye
EXEMPT_URL_NAMES = {
    'login', 'logout', 'signup',
    'signup_verify_otp', 'signup_resend_otp',
    'verify_otp', 'send_otp', 'resend_verification', 'verify_email',
    'password_reset', 'password_reset_done', 'password_reset_confirm', 'password_reset_complete',
    'billing_onboarding', 'billing_onboarding_pay', 'billing_onboarding_status',
    'home', 'about', 'contact', 'ai_employees', 'features', 'how_it_works', 'solutions',
    'industries', 'industry_detail', 'use_cases', 'demo', 'pricing', 'customers',
    'resources', 'blog', 'early_access', 'faq', 'guide', 'support', 'security',
    'privacy', 'terms', 'cookies',
}
EXEMPT_PATH_PREFIXES = ('/admin', '/api/widget/', '/static/', '/media/')


class OnboardingGateMiddleware:
    """
    Blocks dashboard/app access until:
      1. The user's email is verified (UserProfile.is_verified), then
      2. The workspace's onboarding payment has succeeded (Workspace.is_active).
    Marketing pages, auth pages, the signup-verify flow, the onboarding
    billing flow, admin, and the public widget API stay open.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.is_staff:
            if not request.path.startswith(EXEMPT_PATH_PREFIXES):
                try:
                    url_name = resolve(request.path_info).url_name
                except Resolver404:
                    url_name = None

                if url_name not in EXEMPT_URL_NAMES:
                    profile = getattr(request.user, 'profile', None)

                    # Gate 1: email verify pehle
                    if profile and not (profile.is_verified or profile.email_verified):
                        request.session['onboarding_user_id'] = request.user.id
                        messages.warning(request, 'Please verify your email to continue.')
                        return redirect('signup_verify_otp')

                    # Gate 2: payment ke baad hi dashboard
                    workspace = user_workspace(request.user)
                    if workspace and not workspace.is_active:
                        request.session['onboarding_workspace_id'] = workspace.id
                        last_txn = workspace.transactions.order_by('-created_at').first()
                        if last_txn:
                            request.session['onboarding_transaction_id'] = last_txn.id
                        messages.warning(request, 'Please complete payment to activate your workspace.')
                        return redirect('billing_onboarding')

        return self.get_response(request)