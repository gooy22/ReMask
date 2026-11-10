"""Parse safe metadata for authentication-cookie Set-Cookie directives."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re


_AUTH_COOKIE_NAMES = frozenset({"c_user", "xs"})
_MAX_AGE_PATTERN = re.compile(r"[+-]?[0-9]+\Z")


def auth_cookie_directive(raw, *, now=None):
    """Return ``(cookie_name, is_deletion)`` without returning its value.

    A valid Max-Age takes precedence over Expires, as required by cookie
    processing. Empty values and the conventional ``deleted`` value remain
    explicit deletions even if other attributes conflict.
    """
    try:
        header = str(raw or "")
        pair, *attributes = header.split(";")
        name, separator, value = pair.strip().partition("=")
        cookie_name = name.strip().casefold()
        if not separator or cookie_name not in _AUTH_COOKIE_NAMES:
            return None

        value = value.strip().strip('"')
        if not value or value.casefold() == "deleted":
            return cookie_name, True

        max_age_value = None
        max_age_seen = False
        expires_value = None
        expires_seen = False
        for attribute in attributes:
            key, has_value, attribute_value = attribute.strip().partition("=")
            key = key.strip().casefold()
            if key == "max-age":
                max_age_seen = True
                max_age_value = attribute_value.strip() if has_value else ""
            elif key == "expires":
                expires_seen = True
                expires_value = attribute_value.strip() if has_value else ""

        if max_age_seen and _MAX_AGE_PATTERN.fullmatch(max_age_value or ""):
            return cookie_name, int(max_age_value) <= 0

        if expires_seen and expires_value:
            try:
                expires_at = parsedate_to_datetime(expires_value)
            except (TypeError, ValueError, OverflowError):
                expires_at = None
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                current = now or datetime.now(timezone.utc)
                if current.tzinfo is None:
                    current = current.replace(tzinfo=timezone.utc)
                return cookie_name, expires_at.astimezone(timezone.utc) <= current.astimezone(timezone.utc)

        return cookie_name, False
    except (TypeError, ValueError, OverflowError):
        # Malformed metadata must not turn a normal response into a failure.
        return None
