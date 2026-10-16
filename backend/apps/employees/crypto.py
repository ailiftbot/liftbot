"""Symmetric encryption for secrets stored in the database (e.g. SMTP passwords).

The key comes from FIELD_ENCRYPTION_KEY (a Fernet key) when set, otherwise it is
derived from SECRET_KEY. If the key changes, stored secrets can no longer be
decrypted — decrypt() then returns '' and the user re-enters the password.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', '') or ''
    if not key:
        digest = hashlib.sha256(f'liftbot-field-encryption:{settings.SECRET_KEY}'.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ''


def decrypt(token: str) -> str:
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ''
