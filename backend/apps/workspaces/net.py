"""
Outbound-URL safety checks (SSRF protection).

Importable from anywhere with no side effects (no Django model imports):

    from apps.workspaces.net import is_safe_public_url, validate_public_url

``is_safe_public_url(url)`` returns True only when the URL uses an allowed
scheme (https by default) and *every* address its host resolves to is a
public, globally routable IP — loopback, private (RFC1918 / ULA), link-local
(incl. cloud metadata 169.254.169.254), CGNAT, multicast, reserved and
unspecified addresses are rejected.

Note: this checks at validation time. Callers making the request should also
disable redirects (``allow_redirects=False``) and re-check right before
sending, since DNS answers can change.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

__all__ = ['UnsafeURLError', 'is_safe_public_url', 'validate_public_url', 'is_public_ip']


class UnsafeURLError(ValueError):
    """Raised by ``validate_public_url`` with a user-presentable message."""


def is_public_ip(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    try:
        addr = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    except ValueError:
        return False
    # Unwrap IPv4-mapped / 6to4-style IPv6 so ::ffff:127.0.0.1 is caught.
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        elif addr.sixtofour is not None:
            addr = addr.sixtofour
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return False
    # Catches remaining special ranges such as 100.64.0.0/10 (CGNAT).
    return addr.is_global


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def validate_public_url(url: str, *, require_https: bool = True) -> str:
    """
    Return the stripped URL if it is safe to request, else raise UnsafeURLError.
    Set ``require_https=False`` to also allow plain http (e.g. crawling).
    """
    url = (url or '').strip()
    if not url:
        raise UnsafeURLError('Enter a URL.')
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise UnsafeURLError('Enter a valid URL.')

    allowed = {'https'} if require_https else {'https', 'http'}
    scheme = (parts.scheme or '').lower()
    if scheme not in allowed:
        raise UnsafeURLError('The URL must start with https://.' if require_https
                             else 'The URL must start with http:// or https://.')
    host = parts.hostname
    if not host:
        raise UnsafeURLError('Enter a valid URL with a hostname.')
    if parts.username or parts.password:
        raise UnsafeURLError('URLs with embedded credentials are not allowed.')

    port = port or (443 if scheme == 'https' else 80)
    try:
        addresses = _resolve(host, port)
    except (socket.gaierror, UnicodeError, OSError):
        raise UnsafeURLError('We could not resolve that hostname.')
    if not addresses:
        raise UnsafeURLError('We could not resolve that hostname.')
    for address in addresses:
        # Strip IPv6 zone id (fe80::1%eth0).
        if not is_public_ip(address.split('%', 1)[0]):
            raise UnsafeURLError('That URL points to a private or internal address.')
    return url


def is_safe_public_url(url: str, *, require_https: bool = True) -> bool:
    try:
        validate_public_url(url, require_https=require_https)
    except UnsafeURLError:
        return False
    return True
