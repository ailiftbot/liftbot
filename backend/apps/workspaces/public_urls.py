"""Resolve public app / widget URLs for embed snippets."""

from django.conf import settings


def public_base_url(request=None) -> str:
    """
    Prefer the current request host so embeds match the URL you are viewing
    (e.g. http://liftbot.brandinglift.com:8001). Fall back to PUBLIC_APP_URL.
    """
    if request is not None:
        return request.build_absolute_uri('/').rstrip('/')
    return settings.PUBLIC_APP_URL.rstrip('/')


def widget_urls(request=None) -> dict:
    base = public_base_url(request)
    return {
        'app': base,
        'widget': f'{base}/static/widget.js',
        'api': f'{base}/api/widget',
    }
