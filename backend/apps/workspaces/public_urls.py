"""Resolve public app / widget URLs for embed snippets."""

from django.conf import settings


def public_base_url(request=None) -> str:
    """
    Prefer PUBLIC_APP_URL so embeds never include :8001.
    The app should be reached via reverse proxy on port 80/443.
    """
    configured = (getattr(settings, 'PUBLIC_APP_URL', None) or '').rstrip('/')
    if configured:
        return configured
    if request is not None:
        return request.build_absolute_uri('/').rstrip('/')
    return 'http://localhost'


def widget_urls(request=None) -> dict:
    base = public_base_url(request)
    return {
        'app': base,
        'widget': f'{base}/static/widget.js',
        'api': f'{base}/api/widget',
    }
