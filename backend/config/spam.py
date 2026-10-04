"""Light spam protection for public forms: per-IP rate limit + honeypot field."""
from django.conf import settings
from django.core.cache import cache

# Hidden input bots tend to fill. Real users never see it.
HONEYPOT_FIELD = 'company_fax'


def client_ip(request):
    """
    Best-effort client IP. Behind nginx the right-most X-Forwarded-For entry
    is the address nginx itself saw (earlier entries are client-supplied).
    """
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', '') or 'unknown'


def honeypot_triggered(request):
    return bool((request.POST.get(HONEYPOT_FIELD) or '').strip())


def rate_limited(request, scope, limit=None, window=3600):
    """
    Count this request against ``limit`` submissions per ``window`` seconds per IP.
    Returns True once the limit is exceeded.
    """
    if limit is None:
        limit = getattr(settings, 'PUBLIC_FORM_RATE_LIMIT', 5)
    key = f'ratelimit:{scope}:{client_ip(request)}'
    cache.add(key, 0, timeout=window)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, timeout=window)
        count = 1
    return count > limit
