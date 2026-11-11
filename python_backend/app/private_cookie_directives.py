"""Parse safe metadata for authentication-cookie Set-Cookie directives."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re


_AUTH_COOKIE_NAMES = frozenset({"c_user", "xs"})
_MAX_AGE_PATTERN = re.compile(r"[+-]?[0-9]+\Z")


def auth_cookie_directive(raw, *, now=None):
    """Return ``(cookie_name, is_deletion)`` without returning its value.

    Cookie values such as the empty string or ``deleted`` are not deletion
    directives by themselves. A deletion requires expiry metadata. A valid
    Max-Age takes precedence over Expires, as required by cookie processing.
    """
    try:
        header = str(raw or "")
        pair, *attributes = header.split(";")
        name, separator, value = pair.strip().partition("=")
        cookie_name = name.strip()
        if not separator or cookie_name not in _AUTH_COOKIE_NAMES:
            return None

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


def auth_cookie_deletes_profile_root(raw, *, response_host, response_path, now=None):
    """Whether an expired directive targets an imported .facebook.com/ cookie.

    The stored profile cookies are domain cookies for ``facebook.com`` at the
    root path. Host-only cookies and cookies with narrower domain/path scopes
    are separate cookie records and cannot delete that imported snapshot.
    """
    directive = auth_cookie_directive(raw, now=now)
    if directive is None or not directive[1]:
        return False

    try:
        host = str(response_host or "").strip().casefold().rstrip(".")
        if host != "facebook.com" and not host.endswith(".facebook.com"):
            return False

        attributes = {}
        counts = {}
        for raw_attribute in str(raw or "").split(";")[1:]:
            key, separator, value = raw_attribute.strip().partition("=")
            key = key.strip().casefold()
            if key not in {"domain", "path"}:
                continue
            counts[key] = counts.get(key, 0) + 1
            attributes[key] = value.strip() if separator else ""
        if counts.get("domain") != 1:
            # No Domain means host-only; duplicates are ambiguous.
            return False
        domain = attributes.get("domain", "")
        if domain.startswith("."):
            domain = domain[1:]
        domain = domain.casefold()
        if domain != "facebook.com":
            return False
        if counts.get("path", 0) > 1:
            return False

        path = attributes.get("path") if counts.get("path") else ""
        if not path.startswith("/"):
            path = _default_cookie_path(response_path)
        return path == "/"
    except (TypeError, ValueError, OverflowError):
        return False


def _default_cookie_path(request_path):
    path = str(request_path or "")
    if not path.startswith("/") or path.count("/") <= 1:
        return "/"
    return path.rsplit("/", 1)[0] or "/"
