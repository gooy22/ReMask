"""Pure response parsing shared by the Ads Manager HTTP transport.

This module has no profile, browser, or network dependency. Request-shaped
fields are stripped before Ads Manager bootstrap data is inspected.
"""
from __future__ import annotations

import html
import json
import math
import re
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
    return _strict_json_decoder().decode(raw)


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
