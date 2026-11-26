"""Phone numbers are stored as WhatsApp IDs: digits only, with country code (e.g. 919876543210)."""

import re

from django.conf import settings


class InvalidPhoneNumber(ValueError):
    pass


def normalize(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):  # 09876543210 (Indian trunk prefix)
        digits = digits[1:]
    if len(digits) == 10:
        digits = settings.DEFAULT_COUNTRY_CODE + digits
    if not 8 <= len(digits) <= 15:
        raise InvalidPhoneNumber(f"Not a usable phone number: {raw!r}")
    return digits
