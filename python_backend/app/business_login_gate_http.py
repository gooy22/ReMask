"""Safe HTTP/2 continuation for Meta's one-click Business login handoff.

Only a uniquely identifiable continuation control on the exact Business login
handoff is eligible. Credential, checkpoint, ambiguous, and cross-origin flows
are left for supported reauthentication.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit


_FACEBOOK_HOST_SUFFIX = ".facebook.com"
_CREDENTIAL_NAME = re.compile(
    r"(^|[_-])(pass(?:word)?|email|login|username|user_name|identifier|otp|"
    r"security_code|verification_code)([_-]|$)",
    re.IGNORECASE,
)
_CHALLENGE_MARKERS = (
    "two-factor authentication",
    "authentication code",
    "enter security code",
    "checkpoint",
    "confirm your identity",
    "captcha",
    "verify you are human",
    "security check",
    "by continuing, you agree",
    "by clicking continue, you agree",
    "accept the terms",
)


def _facebook_url(value: str) -> bool:
    try:
        parts = urlsplit(str(value or "").strip())
        port = parts.port
    except ValueError:
        return False
    host = str(parts.hostname or "").casefold()
    return (
        parts.scheme == "https"
        and (host == "facebook.com" or host.endswith(_FACEBOOK_HOST_SUFFIX))
        and not parts.username
        and not parts.password
        and port in (None, 443)
    )


def is_business_login_gate_url(value: str) -> bool:
    try:
        parts = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    return (
        _facebook_url(value)
        and str(parts.hostname or "").casefold() == "business.facebook.com"
        and str(parts.path or "").casefold().rstrip("/") == "/business/loginpage"
    )


def _hidden(attrs: dict[str, str]) -> bool:
    style = re.sub(r"\s+", "", str(attrs.get("style") or "").casefold())
    return (
        "hidden" in attrs
        or str(attrs.get("aria-hidden") or "").casefold() == "true"
        or "display:none" in style
        or "visibility:hidden" in style
        or "opacity:0" in style
    )


def _credential_input(attrs: dict[str, str]) -> bool:
    kind = str(attrs.get("type") or "").casefold()
    name = str(attrs.get("name") or "").casefold()
    autocomplete = str(attrs.get("autocomplete") or "").casefold()
    aria = str(attrs.get("aria-label") or "").casefold()
    return (
        kind in {"password", "email"}
        or autocomplete == "current-password"
        or bool(_CREDENTIAL_NAME.search(name))
        or any(term in aria for term in ("password", "email address", "login"))
    )


def _score(label: str) -> tuple[int, str]:
    text = re.sub(r"\s+", " ", str(label or "")).strip()
    if re.fullmatch(
        r"Continue with Facebook|Log in with Facebook|Войти через Facebook|"
        r"Увійти через Facebook|Mit Facebook anmelden|Se connecter avec Facebook|"
        r"Đăng nhập bằng Facebook",
        text,
        re.IGNORECASE,
    ):
        return 300, "facebook_continue"
    if re.fullmatch(
        r"Continue as .+|Продолжить как .+|Продовжити як .+|Weiter als .+|"
        r"Continuer en tant que .+|Tiếp tục với tư cách .+",
        text,
        re.IGNORECASE,
    ):
        return 250, "continue_as"
    if re.fullmatch(r"Continue to (?:Meta )?Business(?: Suite)?", text, re.IGNORECASE):
        return 200, "business_continue"
    if re.fullmatch(
        r"Continue|Продолжить|Продовжити|Weiter|Continuer|Tiếp tục",
        text,
        re.IGNORECASE,
    ):
        return 100, "generic_continue"
    return 0, ""


class _BusinessGateParser(HTMLParser):
    _VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: dict[str, dict] = {}
        self.form_stack: list[str] = []
        self.element_stack: list[tuple[str, bool]] = []
        self.active_controls: list[dict] = []
        self.candidates: list[dict] = []
        self.has_credentials = False
        self.form_counter = 0
        self.external_fields: dict[str, list[tuple[str, str]]] = {}
        self.visible_text: list[str] = []

    def _current_hidden(self) -> bool:
        return any(hidden for _, hidden in self.element_stack)

    def _current_text_suppressed(self) -> bool:
        return any(
            tag in {"head", "script", "style", "template", "noscript"}
            for tag, _ in self.element_stack
        )

    def _current_form(self) -> str:
        return self.form_stack[-1] if self.form_stack else ""

    def handle_starttag(self, tag: str, raw_attrs) -> None:
        tag = tag.casefold()
        attrs = {str(k).casefold(): str(v or "") for k, v in raw_attrs}
        hidden = self._current_hidden() or _hidden(attrs)

        if tag == "form":
            self.form_counter += 1
            form_id = str(attrs.get("id") or f"__business_gate_form_{self.form_counter}")
            self.forms[form_id] = {
                "action": attrs.get("action") or "",
                "method": str(attrs.get("method") or "get").casefold(),
                "fields": [],
                "hidden": hidden,
                "interactive": False,
            }
            self.form_stack.append(form_id)
        else:
            form_id = str(attrs.get("form") or self._current_form())
            if tag == "input":
                kind = str(attrs.get("type") or "text").casefold()
                name = str(attrs.get("name") or "").strip()
                value = str(attrs.get("value") or "")
                if _credential_input(attrs):
                    self.has_credentials = True
                if form_id:
                    form = self.forms.get(form_id)
                    if form is not None and not hidden:
                        if kind not in {"hidden", "submit", "image", "button", "reset"}:
                            form["interactive"] = True
                    if name and kind == "hidden":
                        field = (name, value)
                        if form is not None:
                            form["fields"].append(field)
                        else:
                            self.external_fields.setdefault(form_id, []).append(field)
                if kind == "submit" and not hidden:
                    self.candidates.append({
                        "tag": "input",
                        "attrs": attrs,
                        "form_id": form_id,
                        "hidden": hidden,
                        "text": attrs.get("value") or "",
                    })
            elif tag in {"select", "textarea"} and form_id and not hidden:
                form = self.forms.get(form_id)
                if form is not None:
                    form["interactive"] = True

            if tag in {"button", "a"}:
                control_type = str(attrs.get("type") or "").casefold()
                candidate = {
                    "tag": tag,
                    "attrs": attrs,
                    "form_id": form_id,
                    "hidden": hidden,
                    "text": "",
                }
                if not hidden and (tag == "a" or control_type in {"", "submit"}):
                    self.candidates.append(candidate)
                    self.active_controls.append(candidate)

        if tag not in self._VOID:
            self.element_stack.append((tag, hidden))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._current_hidden() or self._current_text_suppressed():
            return
        self.visible_text.append(data)
        for candidate in self.active_controls:
            candidate["text"] += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self.active_controls) - 1, -1, -1):
            if self.active_controls[index]["tag"] == tag:
                del self.active_controls[index:]
                break
        if tag == "form" and self.form_stack:
            self.form_stack.pop()
        for index in range(len(self.element_stack) - 1, -1, -1):
            if self.element_stack[index][0] == tag:
                del self.element_stack[index:]
                break


def _extract_business_login_continuation(
    body: str,
    gate_url: str,
) -> tuple[dict | None, str]:
    """Return a safe continuation plus a non-sensitive outcome code."""
    if not is_business_login_gate_url(gate_url):
        return None, "not_business_gate"
    if not isinstance(body, str):
        return None, "invalid_response_body"
    if len(body) > 2_000_000:
        return None, "response_body_too_large"
    parser = _BusinessGateParser()
    try:
        parser.feed(body)
        parser.close()
    except Exception:
        return None, "malformed_gate_markup"
    visible_text = re.sub(r"\s+", " ", " ".join(parser.visible_text)).casefold()
    if (any(marker in visible_text for marker in _CHALLENGE_MARKERS)
            or re.search(r"\bby (?:continuing|clicking continue|logging in),?\s+you agree\b", visible_text)):
        return None, "authentication_challenge"
    if parser.has_credentials:
        return None, "credential_input_present"

    for form_id, fields in parser.external_fields.items():
        if form_id in parser.forms:
            parser.forms[form_id]["fields"].extend(fields)

    ranked = []
    for candidate in parser.candidates:
        attrs = candidate["attrs"]
        label = re.sub(
            r"\s+", " ", str(candidate["text"] or attrs.get("aria-label") or "")
        ).strip()
        score, label_kind = _score(label)
        if score:
            ranked.append((score, label_kind, candidate))
    if not ranked:
        return None, "no_supported_continuation_control"
    best_score = max(row[0] for row in ranked)
    best = [row for row in ranked if row[0] == best_score]
    if len(best) != 1:
        return None, "ambiguous_continuation_controls"

    _, label_kind, candidate = best[0]
    attrs = candidate["attrs"]
    if candidate["tag"] == "a":
        method = "GET"
        action = urljoin(gate_url, str(attrs.get("href") or ""))
        fields = []
    else:
        form_id = str(candidate.get("form_id") or "")
        form = parser.forms.get(form_id)
        if form is None or form.get("hidden") or form.get("interactive"):
            return None, "interactive_or_hidden_form"
        method = str(attrs.get("formmethod") or form.get("method") or "get").upper()
        if method not in {"GET", "POST"}:
            return None, "unsupported_form_method"
        action_value = attrs.get("formaction") or form.get("action") or gate_url
        action = urljoin(gate_url, str(action_value))
        fields = list(form.get("fields") or [])
        name = str(attrs.get("name") or "").strip()
        value = str(attrs.get("value") or "")
        if name:
            fields.append((name, value))
        if candidate["tag"] == "input" and name and not fields:
            fields.append((name, value))

    if not _facebook_url(action):
        return None, "non_facebook_continuation_target"
    if len(fields) > 80 or any(
        not name
        or len(name) > 256
        or len(value) > 8192
        or _CREDENTIAL_NAME.search(name)
        for name, value in fields
    ):
        return None, "unsafe_continuation_fields"
    if sum(len(name) + len(value) for name, value in fields) > 16000:
        return None, "continuation_fields_too_large"

    return {
        "method": method,
        "url": action,
        "fields": fields,
        "label_kind": label_kind,
    }, "continuation_ready"


def extract_business_login_continuation(
    body: str,
    gate_url: str,
) -> dict | None:
    """Return a single safe same-profile continuation request, if unambiguous."""
    return _extract_business_login_continuation(body, gate_url)[0]


async def _bounded_response(response, max_bytes: int = 4_000_000) -> str:
    raw = await response.content.read(max_bytes + 1)
    raw = raw[:max_bytes]
    charset = str(getattr(response, "charset", "") or "utf-8")
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _auth_navigation(url: str) -> bool:
    if not _facebook_url(url):
        return True
    parts = urlsplit(url)
    path = str(parts.path or "").casefold().rstrip("/")
    return (
        is_business_login_gate_url(url)
        or path in {"/login", "/login.php", "/checkpoint"}
        or path.startswith("/login/")
        or path.startswith("/checkpoint/")
    )


async def _request_facebook(
    session,
    profile,
    method: str,
    url: str,
    *,
    fields: list[tuple[str, str]] | None = None,
    referer: str = "",
):
    current_method = method.upper()
    current_url = url
    current_fields = list(fields or [])
    current_referer = referer
    last_status = 0
    last_url = url
    last_body = ""
    last_headers: dict[str, str] = {}
    for _ in range(4):
        headers = {
            "User-Agent": str(profile.user_agent or ""),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        if current_referer:
            headers["Referer"] = current_referer
        kwargs = {
            "proxy": profile.proxy,
            "headers": headers,
            "allow_redirects": False,
            "request_timeout_seconds": 15,
        }
        if current_method == "POST":
            headers["Origin"] = f"https://{urlsplit(current_url).hostname}"
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            kwargs["content"] = urlencode(current_fields)
        elif current_fields:
            kwargs["params"] = current_fields
        async with session.request(current_method, current_url, **kwargs) as response:
            last_status = int(response.status)
            last_url = str(response.url)
            location = str(response.headers.get("Location") or "")
            last_headers = {str(key):str(value) for key,value in response.headers.items()}
            last_body = await _bounded_response(response)
        if last_status not in {301, 302, 303, 307, 308} or not location:
            return last_status, last_url, last_body, last_headers
        next_url = urljoin(last_url, location)
        if not _facebook_url(next_url) or _auth_navigation(next_url):
            return last_status, next_url, last_body, last_headers
        current_referer = last_url
        current_url = next_url
        if last_status in {301, 302, 303} and current_method == "POST":
            current_method = "GET"
            current_fields = []
    return last_status, last_url, last_body, last_headers

async def continue_business_login_gate(
    web,
    *,
    gate_url: str,
    body: str,
    return_url: str,
) -> dict | None:
    """Submit one safe Meta continuation, then verify the original read-only URL."""
    def set_outcome(value: str) -> None:
        # Codes only: never store or log response text, form values, cookies, or URLs.
        try:
            web._business_login_gate_outcome = value
        except Exception:
            pass

    continuation, outcome = _extract_business_login_continuation(body, gate_url)
    set_outcome(outcome)
    if not continuation:
        return None

    profile = getattr(web, "profile", None)
    actor_id = str(getattr(profile, "cookies", {}).get("c_user") or "")
    if not actor_id:
        set_outcome("missing_actor")
        return None
    if not _facebook_url(return_url):
        set_outcome("invalid_return_url")
        return None

    try:
        session = await web._ensure_session()
        before = session.facebook_cookie_snapshot()
        if (
            str(before.get("c_user") or "") != actor_id
            or not str(before.get("xs") or "").strip()
        ):
            set_outcome("profile_session_mismatch")
            return None

        action_status, action_url, _, _ = await _request_facebook(
            session, profile, continuation["method"], continuation["url"],
            fields=continuation["fields"], referer=gate_url,
        )
        if action_status in {401, 403}:
            set_outcome("meta_rejected_continuation")
            return None
        if _auth_navigation(action_url) and not is_business_login_gate_url(action_url):
            set_outcome("continuation_redirected_to_auth")
            return None
        after = session.facebook_cookie_snapshot()
        if (
            str(after.get("c_user") or "") != actor_id
            or not str(after.get("xs") or "").strip()
        ):
            set_outcome("profile_session_changed")
            await web.close()
            return None

        status, final_url, response_body, response_headers = await _request_facebook(
            session, profile, "GET", return_url, referer=gate_url,
        )
        after = session.facebook_cookie_snapshot()
        if (
            200 <= status < 400
            and str(after.get("c_user") or "") == actor_id
            and bool(str(after.get("xs") or "").strip())
            and not _auth_navigation(final_url)
        ):
            set_outcome("recovered")
            return {
                "status":status,
                "body":response_body,
                "url":final_url,
                "headers":response_headers,
            }
        set_outcome("return_url_not_verified")
        return None
    except Exception as exc:
        # Exception type is safe to report; exception text can contain request data.
        set_outcome("transport_error_" + type(exc).__name__)
        return None
