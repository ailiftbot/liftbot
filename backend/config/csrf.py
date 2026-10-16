"""Friendly CSRF failure handling.

A stale form (Back button, page left open, logged in/out in another tab) used
to end on Django's bare "CSRF verification failed" 403. On the public auth
pages, send the visitor back to a fresh copy of the page instead — the new GET
sets a valid token. Everything else (dashboard, API / fetch calls) keeps the 403.
"""
import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.csrf import csrf_failure as django_csrf_failure

logger = logging.getLogger(__name__)

FRIENDLY_URL_NAMES = {
    'signup', 'signup_verify_otp', 'signup_resend_otp', 'login',
    'password_reset', 'password_reset_confirm',
}


def _url_name(request):
    try:
        return resolve(request.path_info).url_name
    except Resolver404:
        return None


def csrf_failure(request, reason=''):
    logger.warning('CSRF failure on %s: %s', request.path, reason)
    wants_json = (
        'application/json' in request.headers.get('Accept', '')
        or 'application/json' in (request.content_type or '')
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )
    if wants_json:
        return JsonResponse({'error': 'Your session expired. Refresh the page and try again.'}, status=403)
    if request.method != 'POST' or _url_name(request) not in FRIENDLY_URL_NAMES or not url_has_allowed_host_and_scheme(
        request.path, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return django_csrf_failure(request, reason=reason)
    try:
        messages.warning(request, 'Your form expired — please try again.')
    except Exception:  # noqa: BLE001 — messages need a session; the redirect alone still helps
        pass
    return redirect(request.get_full_path())
