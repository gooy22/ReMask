"""Pure response parsing shared by the Ads Manager HTTP transport.

This module has no profile, browser, or network dependency. Request-shaped
fields are stripped before Ads Manager bootstrap data is inspected.
"""
from __future__ import annotations

import html
import json
import math
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, parse_qs
from typing import Any


_JSON_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_REQUEST_KEYS = {"variables", "params", "request", "input", "query", "preloadparams"}


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _reject_json_constant(_value):
    raise ValueError("Non-finite JSON number")


def _finite_json_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _strict_json_decoder():
    return json.JSONDecoder(object_pairs_hook=_unique_json_object,
                            parse_constant=_reject_json_constant,
                            parse_float=_finite_json_float)


def _strict_json(raw):
    # json.loads accepts both HTTP response bytes and document strings;
    # JSONDecoder.decode only accepts str. Keep identical strict hooks.
    return json.loads(raw, object_pairs_hook=_unique_json_object,
                      parse_constant=_reject_json_constant,
                      parse_float=_finite_json_float)


def _response_only(value: Any, depth: int = 0, decode_string: bool = False) -> Any:
    if depth > 64:
        return None
    if isinstance(value, dict):
        return {
            key: _response_only(child, depth + 1, str(key).replace("_", "").lower()
                in {"result", "response", "payload", "data", "json"})
            for key, child in value.items()
            if str(key).replace("_", "").lower() not in _REQUEST_KEYS
        }
    if isinstance(value, list):
        return [_response_only(child, depth + 1, decode_string) for child in value]
    if decode_string and isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            decoded = _strict_json(value)
        except (ValueError, RecursionError):
            return value
        if isinstance(decoded, (dict, list)):
            return _response_only(decoded, depth + 1)
    return value


def _auth_gate(url: str, body: str) -> str:
    folded_url = str(url or "").casefold()
    folded_body = str(body or "").casefold()
    if "/checkpoint" in folded_url:
        return "CHECKPOINT_REQUIRED"
    login_form = bool(re.search(r"<form\b[^>]*(?:\bid\s*=\s*['\"]login_form['\"]|\baction\s*=\s*['\"][^'\"]*/login)", folded_body))
    if "/login" in folded_url or login_form:
        if "business.facebook.com/business/loginpage" in folded_url:
            return "BUSINESS_LOGIN_GATE"
        return "SESSION_EXPIRED"
    return ""


def business_login_continuation(url: str, body: str) -> str | None:
    """Read an explicit, credential-free Facebook continuation anchor.

    This is document navigation, never password login or an OAuth grant.
    JavaScript-only gates without an inspectable native link remain blocked.
    """
    page = urlsplit(url)
    if (page.scheme != 'https' or page.netloc != 'business.facebook.com'
            or page.path.rstrip('/') != '/business/loginpage'):
        return None

    class Links(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.links = []
            self.current = None
            self.credentials = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'input' and (attrs.get('type', '').lower() in {'password', 'email'}
                    or attrs.get('name', '').lower() in {'email', 'pass', 'password', 'approvals_code'}):
                self.credentials = True
            if tag == 'a':
                self.current = [attrs.get('href', ''), '', attrs.get('aria-label', '')]

        def handle_data(self, data):
            if self.current is not None:
                self.current[1] += data

        def handle_endtag(self, tag):
            if tag == 'a' and self.current is not None:
                self.links.append((self.current[0], self.current[1] or self.current[2]))
                self.current = None

    parser = Links()
    try:
        parser.feed(body)
    except (ValueError, RecursionError):
        return None
    if parser.credentials:
        return None
    labels = {'continue with facebook', 'log in with facebook',
              'войти через facebook', 'увійти через facebook'}
    candidates = []
    for href, label in parser.links:
        if ' '.join(label.split()).casefold() not in labels or not href:
            continue
        target = urljoin(url, href)
        parsed = urlsplit(target)
        routes = {'www.facebook.com': {'/login', '/login.php'},
                  'business.facebook.com': {'/business/login'}}
        if (parsed.scheme != 'https' or parsed.netloc not in routes or parsed.fragment
                or parsed.path.rstrip('/') not in routes[parsed.netloc]):
            continue
        params = parse_qs(parsed.query)
        if set(params) & {'access_token', 'client_id', 'scope', 'password', 'pass'}:
            continue
        safe = True
        for key in ('next', 'redirect', 'redirect_uri'):
            for value in params.get(key, []):
                destination = urlsplit(urljoin(target, value))
                if (destination.scheme != 'https' or destination.netloc not in
                        {'www.facebook.com', 'business.facebook.com', 'adsmanager.facebook.com'}):
                    safe = False
        if safe:
            candidates.append(target)
    return candidates[0] if len(candidates) == 1 else None


def _json_payloads(body: str) -> list[Any]:
    payloads: list[Any] = []
    document = str(body or "").strip()
    fragments = [match.group(1).strip() for match in _JSON_SCRIPT_RE.finditer(document)]
    if document.startswith("for (;;);"):
        document = document[len("for (;;);"):].lstrip()
    if document.startswith(("{", "[")):
        fragments.append(document)
    for raw in fragments:
        if not raw:
            continue
        for candidate in dict.fromkeys((raw, html.unescape(raw))):
            try:
                decoded = _strict_json(candidate)
                if isinstance(decoded, (dict, list)):
                    payloads.append(_response_only(decoded))
                    break
            except (TypeError, ValueError, RecursionError):
                pass
    return payloads
