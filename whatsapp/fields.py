"""Encrypted text field for access tokens (Fernet, key from FIELD_ENCRYPTION_KEY)."""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet():
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        raise ImproperlyConfigured("Set FIELD_ENCRYPTION_KEY to store access tokens.")
    return Fernet(key.encode() if isinstance(key, str) else key)


class EncryptedTextField(models.TextField):
    """Stores ciphertext in the database; Python code always sees plain text."""

    def get_prep_value(self, value):
        if value in (None, ""):
            return value
        return _fernet().encrypt(str(value).encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ImproperlyConfigured(
                "Cannot decrypt a stored token: FIELD_ENCRYPTION_KEY changed or is wrong."
            ) from exc
