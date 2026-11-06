"""Safe HTTP/2 continuation for Meta's one-click Business login handoff.

Only a uniquely identifiable continuation control on the exact Business login
handoff is eligible. Credential, checkpoint, ambiguous, and cross-origin flows
are left for supported reauthentication.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from .meta_auth_document import auth_gate as _auth_gate

log = logging.getLogger("remask.business_login_gate")


_FACEBOOK_HOST_SUFFIX = ".facebook.com"
_CREDENTIAL_NAME = re.compile(
    r"(^|[_-])(pass(?:word)?|email(?:_address)?|username|user_name|identifier|otp|"
    r"security_code|verification_code)([_-]|$)|^login$",
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

_LOGIN_ID_HINT_ATTRS = frozenset({
    "data-user-id", "data-userid", "data-account-id", "data-accountid",
    "data-uid", "data-profile-id", "data-profileid",
})
_LOGIN_ID_FIELDS = frozenset({
    "uid", "user_id", "userid", "account_id", "accountid", "c_user",
    "__user", "saved_account_id", "login_user_id",
})

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


def _exact_shared_root_auth_cookie_deletion(directive) -> bool:
    if not isinstance(directive, dict):
        return False
    sets = {str(name).casefold() for name in directive.get("sets", [])}
    deletions = {str(name).casefold() for name in directive.get("deletions", [])}
    scopes = directive.get("deletion_scopes")
    return (
        not sets
        and deletions == {"c_user", "xs"}
        and isinstance(scopes, dict)
        and all(
            scopes.get(name) == {"domain": "shared", "path": "root"}
            for name in ("c_user", "xs")
        )
    )


def _navigation_fetch_site(referer: str, destination: str) -> str:
    if not referer:
        return "none"
    try:
        source = urlsplit(referer)
        target = urlsplit(destination)
        source_host = str(source.hostname or "").casefold()
        target_host = str(target.hostname or "").casefold()
        source_port = source.port or (443 if source.scheme == "https" else 80)
        target_port = target.port or (443 if target.scheme == "https" else 80)
    except ValueError:
        return "cross-site"
    if (source.scheme, source_host, source_port) == (target.scheme, target_host, target_port):
        return "same-origin"
    source_facebook = source_host == "facebook.com" or source_host.endswith(_FACEBOOK_HOST_SUFFIX)
    target_facebook = target_host == "facebook.com" or target_host.endswith(_FACEBOOK_HOST_SUFFIX)
    if source_facebook and target_facebook:
        return "same-site"
    return "cross-site"




_REFERRER_POLICIES = frozenset({
    "no-referrer", "no-referrer-when-downgrade", "same-origin", "origin",
    "strict-origin", "origin-when-cross-origin",
    "strict-origin-when-cross-origin", "unsafe-url",
})


def _last_referrer_policy(values) -> str:
    """Return the last valid Referrer-Policy token from one or more headers."""
    if isinstance(values, str):
        values = (values,)
    policy = ""
    for value in values or ():
        for token in str(value or "").split(","):
            candidate = token.strip().casefold()
            if candidate in _REFERRER_POLICIES:
                policy = candidate
    return policy


def _navigation_referrer(source: str, destination: str, policy: str) -> str:
    """Serialize the initiating page's Referer for a navigation hop."""
    if not source:
        return ""
    try:
        src = urlsplit(str(source))
        dst = urlsplit(str(destination))
        if (src.scheme.casefold() not in {"http", "https"}
                or dst.scheme.casefold() not in {"http", "https"}
                or not src.hostname or not dst.hostname
                or src.username is not None or src.password is not None):
            return ""
        source_host = src.hostname.casefold()
        target_host = dst.hostname.casefold()
        source_port = src.port or (443 if src.scheme.casefold() == "https" else 80)
        target_port = dst.port or (443 if dst.scheme.casefold() == "https" else 80)
    except (TypeError, ValueError):
        return ""

    same_origin = (
        src.scheme.casefold(), source_host, source_port
    ) == (
        dst.scheme.casefold(), target_host, target_port
    )
    downgrade = src.scheme.casefold() == "https" and dst.scheme.casefold() != "https"
    selected = str(policy or "strict-origin-when-cross-origin").strip().casefold()
    if selected not in _REFERRER_POLICIES:
        selected = "strict-origin-when-cross-origin"

    full_url = urlunsplit((src.scheme, src.netloc, src.path or "/", src.query, ""))
    host = f"[{source_host}]" if ":" in source_host else source_host
    default_port = 443 if src.scheme.casefold() == "https" else 80
    origin = f"{src.scheme}://{host}"
    if source_port != default_port:
        origin += f":{source_port}"
    origin += "/"

    if selected == "no-referrer":
        return ""
    if selected == "same-origin":
        return full_url if same_origin else ""
    if selected == "origin":
        return origin
    if selected == "strict-origin":
        return "" if downgrade else origin
    if selected == "origin-when-cross-origin":
        return full_url if same_origin else origin
    if selected == "no-referrer-when-downgrade":
        return "" if downgrade else full_url
    if selected == "unsafe-url":
        return full_url
    if downgrade:
        return ""
    return full_url if same_origin else origin


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
        r"Продолжить с аккаунтом Facebook|Продолжить через Facebook|"
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
        self.identity_stack: list[tuple[str, list[str]]] = []
        self.credential_forms: set[str] = set()

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
        identity_values = [
            value for key, value in attrs.items()
            if key in _LOGIN_ID_HINT_ATTRS and re.fullmatch(r"[0-9]{5,30}", value)
        ]
        inherited_identity_values = [
            value for _, values in self.identity_stack for value in values
        ]
        candidate_identity_values = inherited_identity_values + identity_values
        hidden = self._current_hidden() or _hidden(attrs)

        if tag == "form":
            self.form_counter += 1
            form_id = str(attrs.get("id") or f"__business_gate_form_{self.form_counter}")
            self.forms[form_id] = {
                "name": str(attrs.get("name") or ""),
                "action": attrs.get("action") or "",
                "method": str(attrs.get("method") or "get").casefold(),
                "fields": [],
                "hidden": hidden,
                "interactive": False,
                "credential_input": False,
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
                    if form_id in self.forms:
                        self.credential_forms.add(form_id)
                        self.forms[form_id]["credential_input"] = True
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
                        "identity_values": candidate_identity_values,
                    })
            elif tag in {"select", "textarea"} and form_id and not hidden:
                form = self.forms.get(form_id)
                if form is not None:
                    form["interactive"] = True

            role = str(attrs.get("role") or "").casefold()
            aria_button = role == "button" and tag not in {"button", "input"}
            if tag in {"button", "a"} or aria_button:
                control_type = str(attrs.get("type") or "").casefold()
                candidate = {
                    "tag": tag,
                    "attrs": attrs,
                    "form_id": form_id,
                    "hidden": hidden,
                    "text": "",
                    "identity_values": candidate_identity_values,
                }
                if not hidden and (tag == "a" or control_type in {"", "submit"}):
                    self.candidates.append(candidate)
                    self.active_controls.append(candidate)

        if tag not in self._VOID:
            self.element_stack.append((tag, hidden))
            self.identity_stack.append((tag, identity_values))

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
        for index in range(len(self.identity_stack) - 1, -1, -1):
            if self.identity_stack[index][0] == tag:
                del self.identity_stack[index:]
                break


def _extract_business_login_continuation(
    body: str,
    gate_url: str,
    *,
    diagnostic: dict | None = None,
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

    if diagnostic is not None:
        def form_kind(form_id: str, name: str) -> str:
            names = {str(form_id or "").casefold(), str(name or "").casefold()}
            if "g_form" in names:
                return "g_form"
            if "login_form" in names:
                return "login_form"
            return "named" if any(names) else "unnamed"

        def action_kind(action_value: str) -> str:
            action = urljoin(gate_url, str(action_value or gate_url))
            if not _facebook_url(action):
                return "other_origin"
            path = str(urlsplit(action).path or "/").casefold().rstrip("/") or "/"
            return {
                "/login/device-based/validate": "device_validate",
                "/business/loginpage/continue": "business_continue",
                "/business/loginrequest/fb": "business_fb_handoff",
                "/business/loginpage": "business_gate",
            }.get(path, "facebook_other")

        form_rows = []
        field_names = set()
        for form_id, form in parser.forms.items():
            fields = form.get("fields") or []
            names = [name for name, _ in fields if isinstance(name, str)]
            field_names.update(
                name for name in names
                if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", name)
            )
            form_rows.append({
                "kind": form_kind(form_id, form.get("name", "")),
                "method": str(form.get("method") or "get").upper(),
                "action": action_kind(form.get("action", "")),
                "field_count": len(fields),
                "interactive": bool(form.get("interactive")),
                "hidden": bool(form.get("hidden")),
            })
        label_kinds = set()
        control_kinds = set()
        for candidate in parser.candidates:
            attrs = candidate["attrs"]
            label = re.sub(
                r"\s+", " ", str(candidate["text"] or attrs.get("aria-label") or "")
            ).strip()
            score, label_kind = _score(label)
            if score:
                label_kinds.add(label_kind)
            control_kinds.add(
                str(candidate["tag"]).casefold()
                + ":"
                + str(attrs.get("type") or "").casefold()
            )
        diagnostic.update({
            "body_chars": len(body),
            "form_count": len(form_rows),
            "forms": form_rows[:20],
            "hidden_field_names": sorted(field_names)[:40],
            "control_kinds": sorted(control_kinds)[:20],
            "continue_label_kinds": sorted(label_kinds),
            "credential_input_present": parser.has_credentials,
            "business_gate_root_marker_present": "BusinessToolsF3UnifiedLoginPageView.react" in body,
        })

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
        # Some Business login gates ship an otherwise inert POST form with a
        # generated submit id (for example, u_0_b) and no button text/value.
        # Accept it only when Meta names the form g_form/login_form and there
        # is exactly one visible native submit control in that form.
        unnamed_submits = []
        for candidate in parser.candidates:
            if candidate["tag"] not in {"input", "button"} or candidate["hidden"]:
                continue
            attrs = candidate["attrs"]
            label = re.sub(
                r"\s+", " ", str(candidate["text"] or attrs.get("aria-label") or "")
            ).strip()
            if label:
                continue
            form = parser.forms.get(str(candidate.get("form_id") or ""))
            if not form or form.get("hidden") or form.get("interactive"):
                continue
            form_names = {
                str(candidate.get("form_id") or "").casefold(),
                str(form.get("name") or "").casefold(),
            }
            if not form_names.intersection({"g_form", "login_form"}):
                continue
            method = str(attrs.get("formmethod") or form.get("method") or "get").upper()
            if method == "POST":
                unnamed_submits.append(candidate)
        if len(unnamed_submits) != 1:
            return None, (
                "ambiguous_continuation_controls"
                if unnamed_submits else "no_supported_continuation_control"
            )
        ranked.append((50, "named_login_form_submit", unnamed_submits[0]))
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


def _login_route(url: str) -> bool:
    if not _facebook_url(url):
        return False
    path = str(urlsplit(url).path or "").casefold().rstrip("/")
    return path in {"/login", "/login.php"} or path.startswith("/login/")




def _saved_account_login_continuation(
    body: str,
    login_url: str,
    expected_actor_id: str,
    *,
    diagnostic: dict | None = None,
) -> tuple[dict | None, str]:
    """Continue only one inert, explicit control bound to this actor."""
    report = diagnostic if diagnostic is not None else {}
    try:
        parts = urlsplit(str(login_url or ""))
        host = str(parts.hostname or "").casefold()
    except ValueError:
        return None, "invalid_login_url"
    if host != "www.facebook.com" or not _login_route(login_url):
        return None, "not_facebook_login_route"
    actor_id = str(expected_actor_id or "").strip()
    if not re.fullmatch(r"[0-9]{5,30}", actor_id):
        return None, "invalid_expected_actor"
    if not isinstance(body, str) or len(body) > 2_000_000:
        return None, "login_body_invalid"

    parser = _BusinessGateParser()
    try:
        parser.feed(body)
        parser.close()
    except Exception:
        return None, "malformed_login_markup"
    for form_id, fields in parser.external_fields.items():
        if form_id in parser.forms:
            parser.forms[form_id]["fields"].extend(fields)
    visible_text = re.sub(r"\s+", " ", " ".join(parser.visible_text)).casefold()
    challenge_present = bool(
        any(marker in visible_text for marker in _CHALLENGE_MARKERS)
        or re.search(
            r"\bby (?:continuing|clicking continue|logging in),?\s+you agree\b",
            visible_text,
        )
    )

    def form_actor_bound(form):
        return bool(
            isinstance(form, dict)
            and any(
                str(name).casefold() in _LOGIN_ID_FIELDS and str(value) == actor_id
                for name, value in form.get("fields") or []
            )
        )

    def candidate_actor_bound(candidate):
        identities = {
            str(value) for value in candidate.get("identity_values", [])
            if isinstance(value, str)
        }
        form = parser.forms.get(str(candidate.get("form_id") or ""))
        return actor_id in identities or form_actor_bound(form)

    def action_kind(value):
        try:
            action_parts = urlsplit(urljoin(login_url, str(value or login_url)))
            host = str(action_parts.hostname or "").casefold()
            path = str(action_parts.path or "/").casefold()
        except ValueError:
            return "invalid"
        if host != "www.facebook.com":
            return "outside_facebook"
        if path.startswith("/login/device-based/validate"):
            return "device_validate"
        if path == "/login" or path.startswith("/login/"):
            return "login"
        return "other"

    form_rows = []
    actor_form_marker = False
    for form_id, form in parser.forms.items():
        bound = form_actor_bound(form)
        actor_form_marker = actor_form_marker or bound
        form_rows.append({
            "kind": form_id if form_id in {"g_form", "login_form"} else "other",
            "method": str(form.get("method") or "get").upper(),
            "action_kind": action_kind(form.get("action")),
            "field_count": len(form.get("fields") or []),
            "interactive": bool(form.get("interactive")),
            "credential_input": bool(form.get("credential_input")),
            "actor_field_bound": bound,
        })

    control_rows = []
    for candidate in parser.candidates:
        attrs = candidate["attrs"]
        label = re.sub(
            r"\s+", " ", str(candidate["text"] or attrs.get("aria-label") or "")
        ).strip()
        _, kind = _score(label)
        control_rows.append({
            "tag": candidate["tag"],
            "kind": kind or "other",
            "in_form": bool(candidate.get("form_id")),
            "actor_bound": candidate_actor_bound(candidate),
        })

    report.update({
        "body_chars": len(body),
        "form_count": len(parser.forms),
        "forms": form_rows[:16],
        "control_count": len(parser.candidates),
        "controls": control_rows[:24],
        "challenge_present": challenge_present,
        "actor_field_bound": actor_form_marker,
        "actor_id_marker_in_body": actor_id in body,
        "script_count": len(re.findall(r"<script\b", body, flags=re.IGNORECASE)),
        "json_script_count": len(re.findall(
            r"<script\b[^>]*type\s*=\s*['\"]application/json['\"]",
            body,
            flags=re.IGNORECASE,
        )),
        "script_continue_marker": bool(re.search(
            r"continue\s+as|continue\s+with\s+facebook|saved.?account",
            body,
            flags=re.IGNORECASE,
        )),
    })
    if challenge_present:
        return None, "authentication_challenge"

    ranked = []
    for candidate in parser.candidates:
        attrs = candidate["attrs"]
        label = re.sub(
            r"\s+", " ", str(candidate["text"] or attrs.get("aria-label") or "")
        ).strip()
        _, kind = _score(label)
        if kind == "continue_as":
            ranked.append((candidate, kind))
        elif (
            kind in {"facebook_continue", "generic_continue"}
            and candidate["tag"] in {"button", "input"}
            and candidate.get("form_id")
        ):
            # Meta may render the same-actor action as a plain Continue button.
            # Only form controls are considered; exact actor binding and the
            # inert-form checks below remain mandatory.
            ranked.append((candidate, kind))
    if not ranked:
        return None, "no_saved_account_control"
    if len(ranked) != 1:
        return None, "ambiguous_saved_account_controls"

    candidate, control_kind = ranked[0]
    attrs = candidate["attrs"]
    form_id = str(candidate.get("form_id") or "")
    form = parser.forms.get(form_id) if form_id else None
    if candidate["tag"] == "a":
        method = "GET"
        action = urljoin(login_url, str(attrs.get("href") or ""))
        fields = []
    else:
        if form is None or form.get("hidden") or form.get("interactive"):
            return None, "saved_account_form_not_inert"
        if form.get("credential_input") or form_id in parser.credential_forms:
            return None, "saved_account_form_has_credentials"
        method = str(attrs.get("formmethod") or form.get("method") or "get").upper()
        if method not in {"GET", "POST"}:
            return None, "unsupported_saved_account_method"
        action = urljoin(login_url, str(attrs.get("formaction") or form.get("action") or login_url))
        fields = list(form.get("fields") or [])
        name = str(attrs.get("name") or "").strip()
        value = str(attrs.get("value") or "")
        if name:
            fields.append((name, value))

    identities = {
        str(value) for value in candidate.get("identity_values", [])
        if isinstance(value, str)
    }
    if form is not None:
        for name, value in form.get("fields") or []:
            if str(name).casefold() in _LOGIN_ID_FIELDS and str(value) == actor_id:
                identities.add(actor_id)
    if actor_id not in identities:
        return None, "saved_account_actor_unverified"

    try:
        action_parts = urlsplit(action)
    except ValueError:
        return None, "invalid_saved_account_target"
    if (not _facebook_url(action) or str(action_parts.hostname or "").casefold() != "www.facebook.com"
            or not _login_route(action)):
        return None, "saved_account_target_out_of_scope"
    if len(fields) > 80 or any(
        not name or len(name) > 256 or len(value) > 8192 or _CREDENTIAL_NAME.search(name)
        for name, value in fields
    ):
        return None, "unsafe_saved_account_fields"
    if sum(len(name) + len(value) for name, value in fields) > 16000:
        return None, "saved_account_fields_too_large"
    label_kind = (
        "continue_as_same_actor"
        if control_kind == "continue_as"
        else control_kind + "_same_actor"
    )
    return {
        "method": method,
        "url": action,
        "fields": fields,
        "label_kind": label_kind,
    }, "saved_account_actor_verified"

async def _request_facebook(
    session,
    profile,
    method: str,
    url: str,
    *,
    fields: list[tuple[str, str]] | None = None,
    referer: str = "",
    allow_login_redirect: bool = False,
    referrer_policy: str = "",
    restore_same_profile_cookies_for_business_login_redirect: bool = False,
):
    current_method = method.upper()
    current_url = url
    current_fields = list(fields or [])
    current_referer = referer
    current_referrer_policy = (
        _last_referrer_policy(referrer_policy) or "strict-origin-when-cross-origin"
    )
    last_status = 0
    last_url = url
    last_body = ""
    last_headers: dict[str, str] = {}
    restore_profile_cookies = getattr(session, "restore_profile_cookies", None)
    profile_cookie_snapshot = getattr(session, "_profile_cookies", None)
    profile_cookies = getattr(profile, "cookies", {})
    source_snapshot = (
        profile_cookie_snapshot
        if isinstance(profile_cookie_snapshot, dict) and profile_cookie_snapshot
        else profile_cookies if isinstance(profile_cookies, dict) else {}
    )
    expected_cookie_snapshot = {
        str(name): str(value) for name, value in source_snapshot.items()
    }
    expected_actor_id = str(expected_cookie_snapshot.get("c_user") or "")
    expected_xs = str(expected_cookie_snapshot.get("xs") or "").strip()

    def snapshot_matches_expected(current) -> bool:
        return (
            isinstance(current, dict)
            and bool(expected_cookie_snapshot)
            and bool(expected_actor_id)
            and bool(expected_xs)
            and all(
                str(current.get(name) or "") == value
                for name, value in expected_cookie_snapshot.items()
            )
            and str(current.get("c_user") or "") == expected_actor_id
            and bool(str(current.get("xs") or "").strip())
        )

    def meta_auth_directive_pending() -> bool:
        return bool(getattr(session, "_meta_redirect_auth_cookie_directive_pending", False))

    def restore_cookie_snapshot_if_lost() -> None:
        if not callable(restore_profile_cookies) or meta_auth_directive_pending():
            return
        current = session.facebook_cookie_snapshot()
        if snapshot_matches_expected(current):
            accept_refresh = getattr(session, "accept_same_actor_cookie_refresh", None)
            if callable(accept_refresh):
                accept_refresh()
            return
        restore_profile_cookies()

    for hop in range(4):
        # The caller checked the selected actor and xs before this first request.
        # A same-profile Facebook response can change the cookie jar; before a
        # subsequent redirect hop, restore only if that jar no longer matches
        # the selected profile snapshot.
        if hop > 0:
            current_cookies = session.facebook_cookie_snapshot()
            if not snapshot_matches_expected(current_cookies):
                if meta_auth_directive_pending():
                    current_actor = str(current_cookies.get("c_user") or "")
                    if current_actor and current_actor != expected_actor_id:
                        return 0, current_url, "", {}
                else:
                    if not callable(restore_profile_cookies):
                        return 0, current_url, "", {}
                    restore_profile_cookies()
                    current_cookies = session.facebook_cookie_snapshot()
                    if not snapshot_matches_expected(current_cookies):
                        return 0, current_url, "", {}
            accept_refresh = getattr(session, "accept_same_actor_cookie_refresh", None)
            if callable(accept_refresh):
                accept_refresh()
        headers = {
            "User-Agent": str(profile.user_agent or ""),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": _navigation_fetch_site(current_referer, current_url),
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        request_referrer = _navigation_referrer(
            current_referer, current_url, current_referrer_policy,
        )
        if request_referrer:
            headers["Referer"] = request_referrer
        kwargs = {
            "proxy": profile.proxy,
            "headers": headers,
            "allow_redirects": False,
            "request_timeout_seconds": 15,
            "cookie_jar_guard": restore_cookie_snapshot_if_lost,
        }
        if current_method == "POST":
            headers["Origin"] = f"https://{urlsplit(current_url).hostname}"
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            kwargs["content"] = urlencode(current_fields)
        elif current_fields:
            kwargs["params"] = current_fields
        response_referrer_policies = []
        response_auth_cookie_directive = None
        async with session.request(current_method, current_url, **kwargs) as response:
            last_status = int(response.status)
            last_url = str(response.url)
            location = str(response.headers.get("Location") or "")
            response_extensions = getattr(response, "extensions", None)
            if isinstance(response_extensions, dict):
                response_auth_cookie_directive = response_extensions.get(
                    "remask_meta_auth_cookie_directive"
                )
            get_list = getattr(response.headers, "get_list", None)
            response_referrer_policies = (
                get_list("referrer-policy") if callable(get_list) else []
            )
            last_headers = {str(key):str(value) for key,value in response.headers.items()}
            last_body = await _bounded_response(response)
        if last_status not in {301, 302, 303, 307, 308} or not location:
            return last_status, last_url, last_body, last_headers
        next_url = urljoin(last_url, location)
        source_parts = urlsplit(last_url)
        target_parts = urlsplit(next_url)
        same_profile_business_login_handoff = (
            restore_same_profile_cookies_for_business_login_redirect
            and last_status == 302
            and current_method == "GET"
            and _facebook_url(last_url)
            and str(source_parts.hostname or "").casefold() == "business.facebook.com"
            and str(source_parts.path or "").casefold().rstrip("/")
                == "/business/loginrequest/fb"
            and _facebook_url(next_url)
            and str(target_parts.hostname or "").casefold() == "www.facebook.com"
            and _login_route(next_url)
            and _exact_shared_root_auth_cookie_deletion(response_auth_cookie_directive)
        )
        if same_profile_business_login_handoff:
            current_cookies = session.facebook_cookie_snapshot()
            current_actor = str(current_cookies.get("c_user") or "")
            current_xs = str(current_cookies.get("xs") or "").strip()
            if (
                current_actor not in {"", expected_actor_id}
                or current_xs not in {"", expected_xs}
                or not callable(restore_profile_cookies)
            ):
                return last_status, next_url, last_body, last_headers
            restore_profile_cookies()
            restored_cookies = session.facebook_cookie_snapshot()
            if not snapshot_matches_expected(restored_cookies):
                return last_status, next_url, last_body, last_headers
            log.info(
                "business_login_gate_auth_cookie_snapshot_restore profile=%s "
                "stage=business_loginrequest_fb_to_www_login actor_match=true cookie_count=%d",
                getattr(profile, "name", "unknown"), len(restored_cookies),
            )
        if not _facebook_url(next_url):
            return last_status, next_url, last_body, last_headers
        if _auth_navigation(next_url):
            # A native Facebook handoff can redirect through /login even when
            # the existing profile session is sufficient. Follow only that
            # same-site GET navigation; a login form or checkpoint is still a
            # hard stop, and no credentials or forms are submitted here.
            can_follow_existing_login = (
                allow_login_redirect
                and current_method == "GET"
                and _login_route(next_url)
            )
            if not can_follow_existing_login:
                return last_status, next_url, last_body, last_headers
        updated_referrer_policy = _last_referrer_policy(response_referrer_policies)
        if updated_referrer_policy:
            current_referrer_policy = updated_referrer_policy
        current_referer = last_url
        current_url = next_url
        log.info(
            "business_login_gate_redirect profile=%s hop=%d method=%s status=%d source_host=%s target_host=%s sec_fetch_site=%s",
            getattr(profile, "name", "unknown"), hop + 1, current_method, last_status,
            str(urlsplit(last_url).hostname or "").casefold(),
            str(urlsplit(next_url).hostname or "").casefold(),
            _navigation_fetch_site(current_referer, current_url),
        )
        if hop == 0 and last_status == 302:
            log.info(
                "business_login_gate_redirect_settle profile=%s seconds=3",
                getattr(profile, "name", "unknown"),
            )
            await asyncio.sleep(3.0)
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
    referrer_policy: str = "",
) -> dict | None:
    """Submit one safe Meta continuation, then verify the original read-only URL."""
    def set_outcome(value: str) -> None:
        # Codes only: never store or log response text, form values, cookies, or URLs.
        try:
            web._business_login_gate_outcome = value
        except Exception:
            pass

    gate_diagnostic = {}
    continuation, outcome = _extract_business_login_continuation(
        body, gate_url, diagnostic=gate_diagnostic,
    )
    if not continuation and outcome == "no_supported_continuation_control":
        # Meta serves this same-profile handoff as a JS-rendered FB button on
        # some Business login pages. Parse only the current document's reviewed
        # immutable bundle and literal props; never execute that JavaScript.
        diagnostic = {}
        try:
            from .private_business_login import native_business_login_continuation
            native_url = native_business_login_continuation(
                body, return_url, diagnostic=diagnostic,
            )
        except Exception as exc:
            native_url = None
            outcome = "native_parser_error_" + type(exc).__name__
        if native_url:
            continuation = {
                "method": "GET",
                "url": native_url,
                "fields": [],
                "label_kind": "native_fb",
            }
            outcome = "native_fb_continuation_ready"
        elif outcome == "no_supported_continuation_control":
            reason = str(diagnostic.get("reason") or "unavailable")
            gate_diagnostic["native_reason"] = reason
            if reason == "request_scope":
                for key in (
                    "scope_url_parseable",
                    "scope_scheme_https",
                    "scope_host_allowed",
                    "scope_authority_clean",
                    "scope_path_allowed",
                    "scope_query_keys_allowed",
                    "scope_act_count_valid",
                    "scope_act_format_valid",
                    "scope_business_count_valid",
                    "scope_business_format_valid",
                ):
                    value = diagnostic.get(key)
                    if type(value) is bool:
                        gate_diagnostic[key] = value
            for key in (
                "native_prop_keys",
                "native_request_id_node_type",
                "native_referrer_uri_node_type",
            ):
                value = diagnostic.get(key)
                if key == "native_prop_keys" and isinstance(value, list):
                    safe_names = [
                        name for name in value[:80]
                        if isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name)
                    ]
                    gate_diagnostic[key] = safe_names
                elif isinstance(value, str) and re.fullmatch(r"[A-Za-z_]{1,32}", value):
                    gate_diagnostic[key] = value
            outcome = (
                "native_" + reason
                if re.fullmatch(r"[a-z_]{1,64}", reason)
                else "native_continuation_unavailable"
            )
        if native_url:
            gate_diagnostic["native_reason"] = str(diagnostic.get("reason") or "native_fb_continuation")
    profile_name = str(getattr(getattr(web, "profile", None), "name", "") or "unknown")
    log.info("business_login_gate_parse profile=%s outcome=%s summary=%s",
        profile_name, outcome,
        json.dumps(gate_diagnostic, sort_keys=True, separators=(",", ":")))
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
        restore_profile_cookies = getattr(session, "restore_profile_cookies", None)
        before = session.facebook_cookie_snapshot()
        profile_snapshot = getattr(session, "_profile_cookies", None)
        profile_xs = (
            str(profile_snapshot.get("xs") or "").strip()
            if isinstance(profile_snapshot, dict)
            else ""
        )
        profile_snapshot_mismatch = (
            isinstance(profile_snapshot, dict)
            and (
                str(profile_snapshot.get("c_user") or "") != actor_id
                or not profile_xs
                or str(before.get("c_user") or "") != actor_id
                or not str(before.get("xs") or "").strip()
                or str(before.get("xs") or "").strip() != profile_xs
                or any(name not in before for name in profile_snapshot)
            )
        )
        meta_auth_directive_pending = bool(
            getattr(session, "_meta_redirect_auth_cookie_directive_pending", False)
        )
        if (
            profile_snapshot_mismatch
            or str(before.get("c_user") or "") != actor_id
            or not str(before.get("xs") or "").strip()
        ) and not meta_auth_directive_pending:
            # Recover only from an unannounced cookie-jar loss. Once Meta has
            # issued an auth-cookie directive, never resurrect the old snapshot.
            restore_profile_cookies = getattr(session, "restore_profile_cookies", None)
            if callable(restore_profile_cookies):
                restore_profile_cookies()
                before = session.facebook_cookie_snapshot()
        current_actor = str(before.get("c_user") or "")
        if meta_auth_directive_pending:
            if current_actor and current_actor != actor_id:
                set_outcome("profile_session_mismatch")
                return None
        elif (
            current_actor != actor_id
            or not str(before.get("xs") or "").strip()
            or (
                isinstance(profile_snapshot, dict)
                and any(name not in before for name in profile_snapshot)
            )
        ):
            set_outcome("profile_session_mismatch")
            return None
        accept_refresh = getattr(session, "accept_same_actor_cookie_refresh", None)
        if callable(accept_refresh):
            accept_refresh()

        action_status, action_url, action_body, action_headers = await _request_facebook(
            session, profile, continuation["method"], continuation["url"],
            fields=continuation["fields"], referer=gate_url,
            allow_login_redirect=True,
            referrer_policy=referrer_policy,
            restore_same_profile_cookies_for_business_login_redirect=True,
        )
        if action_status in {401, 403}:
            set_outcome("meta_rejected_continuation")
            return None
        saved_login_outcome = ""
        saved_login = None
        saved_login_diagnostic = {}
        if _login_route(action_url):
            saved_login, saved_login_outcome = _saved_account_login_continuation(
                action_body, action_url, actor_id,
                diagnostic=saved_login_diagnostic,
            )
            log.info(
                "business_login_gate_saved_login profile=%s outcome=%s summary=%s",
                profile_name, saved_login_outcome,
                json.dumps(saved_login_diagnostic, sort_keys=True, separators=(",", ":")),
            )
            if saved_login:
                action_status, action_url, action_body, _ = await _request_facebook(
                    session, profile, saved_login["method"], saved_login["url"],
                    fields=saved_login["fields"], referer=action_url,
                    allow_login_redirect=True,
                    referrer_policy=(
                        _last_referrer_policy([
                            value for key, value in action_headers.items()
                            if str(key).casefold() == "referrer-policy"
                        ])
                        or referrer_policy
                    ),
                )
                if action_status in {401, 403}:
                    set_outcome("meta_rejected_saved_account")
                    return None
        login_forms = saved_login_diagnostic.get("forms")
        inert_login_page = (
            isinstance(login_forms, list)
            and len(login_forms) == 1
            and isinstance(login_forms[0], dict)
            and login_forms[0].get("credential_input") is False
            and login_forms[0].get("interactive") is False
            and login_forms[0].get("action_kind") == "login"
        )
        inert_login_read_probe = (
            _login_route(action_url)
            and action_status == 200
            and saved_login is None
            and saved_login_outcome == "no_saved_account_control"
            and inert_login_page
            and saved_login_diagnostic.get("challenge_present") is False
        )
        if inert_login_read_probe:
            log.info(
                "business_login_gate_login_page_read_probe profile=%s status=200 credentials=false challenge=false",
                profile_name,
            )
        if (_auth_navigation(action_url)
                and not is_business_login_gate_url(action_url)
                and not inert_login_read_probe):
            parts = urlsplit(action_url)
            host = str(parts.hostname or "").casefold()
            path = str(parts.path or "/").casefold().rstrip("/") or "/"
            route = (
                "checkpoint" if path.startswith("/checkpoint")
                else "login" if path == "/login" or path.startswith("/login/")
                else "login_php" if path == "/login.php"
                else "business_gate" if is_business_login_gate_url(action_url)
                else "other"
            )
            log.info(
                "business_login_gate_handoff_rejected profile=%s status=%d host=%s route=%s",
                profile_name, action_status, host, route,
            )
            set_outcome(
                ("saved_login_" + saved_login_outcome)
                if _login_route(action_url) and saved_login_outcome
                else ("continuation_login_required"
                    if _login_route(action_url) else "continuation_redirected_to_auth")
            )
            return None
        after = session.facebook_cookie_snapshot()
        meta_auth_directive_pending = bool(
            getattr(session, "_meta_redirect_auth_cookie_directive_pending", False)
        )
        if str(after.get("c_user") or "") and str(after.get("c_user") or "") != actor_id:
            # A different nonempty actor came back from Meta. Never overwrite
            # that response with the selected profile's old cookie snapshot.
            close_web = getattr(web, "close", None)
            if callable(close_web):
                try:
                    await close_web()
                except Exception:
                    pass
            set_outcome("profile_session_changed")
            return None
        if (
            str(after.get("c_user") or "") != actor_id
            or not str(after.get("xs") or "").strip()
        ) and callable(restore_profile_cookies) and not meta_auth_directive_pending:
            restore_profile_cookies()
            after = session.facebook_cookie_snapshot()
        if (
            str(after.get("c_user") or "") != actor_id
            or not str(after.get("xs") or "").strip()
        ):
            set_outcome("profile_session_changed")
            # The owning bootstrap controls session cleanup after this read-only handoff.
            return None

        status, final_url, response_body, response_headers = await _request_facebook(
            session, profile, "GET", return_url, referer=gate_url,
            referrer_policy=referrer_policy,
        )
        after = session.facebook_cookie_snapshot()
        if (
            200 <= status < 400
            and str(after.get("c_user") or "") == actor_id
            and bool(str(after.get("xs") or "").strip())
            and not _auth_navigation(final_url)
            and not _auth_gate(final_url, response_body)
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