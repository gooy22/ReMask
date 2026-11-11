"""Read-only first-party session probes; imported cookies are never saved."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from .private_ads_document import _auth_gate
from .private_ads_http import _bootstrap_evidence, context_from_document
from .provisioning.models import ProvisioningError


def _reject(code):
    return ProvisioningError("PRIVATE_SESSION_PROBE_" + code,
        "Session comparison was not performed; saved profile data is unchanged.", retryable=False)


def _login_shape(body, final_url, *, include_entry=False):
    class Forms(HTMLParser):
        def __init__(self):
            super().__init__()
            self.forms, self.current, self.logout_link = [], None, False
            self.hidden_depth, self.visible_depth, self.text = 0, 0, ''
            self.anchor, self.anchor_text, self.entry_url = None, '', None

        def handle_starttag(self, tag, attrs):
            if tag in {'script','style','head','template'}:
                self.hidden_depth += 1
            elif tag in {'body','div','span','p','h1','h2','button','label','a','form'}:
                self.visible_depth += 1
            attrs = dict(attrs)
            if tag == 'form':
                self.current = None
                if len(self.forms) >= 8:
                    return
                try:
                    target = urlsplit(urljoin(final_url, attrs.get('action') or ''))
                except ValueError:
                    target = urlsplit('')
                host = target.hostname if target.hostname in {'www.facebook.com','facebook.com','m.facebook.com','business.facebook.com'} else 'unreviewed'
                path = target.path if host != 'unreviewed' and target.path in {
                    '/login/', '/login.php', '/login/device-based/regular/login/',
                    '/login/device-based/login/async/', '/checkpoint/', '/two_step_verification/'} else 'unreviewed'
                self.current = {'method':'POST' if str(attrs.get('method') or '').lower() == 'post' else 'GET',
                    'action_host':host, 'action_path':path, 'input_names':[]}
                self.forms.append(self.current)
            elif tag == 'input' and self.current is not None:
                name = attrs.get('name')
                if name in {'email','pass','encpass','lsd','jazoest','next','login','persistent','default_persistent',
                        'approvals_code','captcha_response','checkpoint_data','credential_type','try_num','unrecognized_tries'}:
                    if name not in self.current['input_names']:
                        self.current['input_names'].append(name)
            elif tag == 'a':
                self.anchor, self.anchor_text = attrs.get('href'), ''
                try:
                    target = urlsplit(urljoin(final_url, attrs.get('href') or ''))
                    if target.hostname in {'www.facebook.com','facebook.com','m.facebook.com'} and target.path in {'/logout.php','/logout/'}:
                        self.logout_link = True
                except ValueError:
                    pass

        def handle_endtag(self, tag):
            if tag in {'script','style','head','template'}:
                self.hidden_depth = max(0, self.hidden_depth - 1)
            elif tag in {'body','div','span','p','h1','h2','button','label','a','form'}:
                self.visible_depth = max(0, self.visible_depth - 1)
            if tag == 'form':
                self.current = None
            elif tag == 'a' and self.anchor:
                if ' '.join(self.anchor_text.split()).casefold() == 'use another profile':
                    try:
                        target = urlsplit(urljoin(final_url, self.anchor))
                        if (target.scheme == 'https' and target.hostname in {'www.facebook.com','facebook.com','m.facebook.com'}
                                and target.path in {'/login','/login/','/login.php'} and not target.fragment
                                and not target.username and not target.password and not target.port):
                            self.entry_url = target.geturl()
                    except ValueError:
                        pass
                self.anchor, self.anchor_text = None, ''

        def handle_data(self, data):
            if self.anchor and not self.hidden_depth and len(self.anchor_text) < 200:
                self.anchor_text += data[:200]
            if self.hidden_depth or not self.visible_depth or len(self.text) >= 800:
                return
            text = ' '.join(data.split())[:1200]
            text = re.sub(r'https?://\S+', '[url]', text, flags=re.I)
            text = re.sub(r'(?i)\b(access_token|fb_dtsg|lsd|password|cookie)\s*[:=]\s*\S+', r'\1=[redacted]', text)
            text = re.sub(r'[A-Za-z0-9_%:+/=-]{32,}', '[redacted]', text)
            self.text = (self.text + ' ' + text).strip()[:800]

    parser = Forms()
    parser.feed(body)
    entry = urlsplit(parser.entry_url) if parser.entry_url else None
    shape = {'forms':parser.forms, 'logout_link':parser.logout_link, 'server_text':parser.text,
        'other_profile_entry':{'ready':entry is not None,
            'host':entry.hostname if entry else '', 'path':entry.path if entry else '',
            'query_keys':sorted(set(parse_qs(entry.query)) & {'next','login_attempt','lwv','skip_api_login','ref',
                'initial_actor_id','login_source','display','locale','id','nux_source'}) if entry else []}}
    return (shape, parser.entry_url) if include_entry else shape


def _transport_kind(error):
    payload = getattr(error, 'meta_payload', None)
    failure = payload.get('transport_failure') if isinstance(payload, dict) else None
    kind = failure.get('kind') if isinstance(failure, dict) else None
    return kind if isinstance(kind, str) and kind in {
        'TimeoutError','ConnectTimeout','ReadTimeout','WriteTimeout','PoolTimeout','ConnectError','ReadError',
        'WriteError','ProxyError','RemoteProtocolError','LocalProtocolError'} else 'unavailable'


def _transport_reason(error):
    text, seen = '', set()
    try:
        for _ in range(8):
            if error is None or id(error) in seen:
                break
            seen.add(id(error))
            text += ' ' + type(error).__name__ + ' ' + str(error)[:2000]
            error = error.__cause__ or error.__context__
        text = text.casefold()
        for reason, markers in (
            ('certificate_verification',('certificate_verify','certificateverification','certificateverify')),
            ('dns_failure',('gaierror','name or service not known','name resolution')),
            ('tls_handshake',('sslerror','sslcert','wrong_version_number','unexpected_eof','eof occurred','tlsv1_alert')),
            ('connection_reset',('connection reset','connectionreseterror')),
            ('connection_refused',('connection refused','connectionrefusederror')),
            ('proxy_connect_rejected',('proxyerror',)),
            ('timeout',('timeout','timed out'))):
            if any(marker in text for marker in markers):
                return reason
    except Exception:
        pass
    return 'unavailable'


def profile_root_auth_cookie_deletion_names(proof):
    """Return only root-scoped c_user/xs deletions from a measured identity read."""
    if (not isinstance(proof, dict)
            or proof.get("document_kind") != "saved_identity"
            or proof.get("request_auth_matches") is not True
            or proof.get("saved_auth_snapshot_sent") is not True
            or proof.get("measurement_errors") != 0):
        return []
    wire = proof.get("wire")
    if (not isinstance(wire, dict) or wire.get("measurement_errors") != 0
            or not isinstance(wire.get("requests"), list) or not wire["requests"]
            or not isinstance(wire.get("responses"), list) or not wire["responses"]):
        return []
    requests, responses = wire["requests"], wire["responses"]
    for row in requests:
        names = row.get("header_auth_cookie_names") if isinstance(row, dict) else None
        if (not isinstance(row, dict)
                or row.get("host") not in {"www.facebook.com", "facebook.com"}
                or row.get("path") not in {"/me", "/profile.php"}
                or row.get("method") != "GET"
                or row.get("user_agent_matches") is not True
                or row.get("header_auth_state") != "matches"
                or not isinstance(names, list)
                or not {"c_user", "xs"} <= set(value for value in names if isinstance(value, str))):
            return []
    for row in responses:
        if (not isinstance(row, dict)
                or row.get("host") not in {"www.facebook.com", "facebook.com"}
                or row.get("path") not in {"/me", "/profile.php"}
                or row.get("status") not in {200, 302, 303, 307, 308}
                or row.get("protocol") not in {"HTTP/2", "HTTP/1.1"}):
            return []
    return sorted({
        name for row in responses
        for name in (row.get("profile_root_auth_cookie_deleted") or [])
        if name in {"c_user", "xs"}
    })


def _saved_identity_wire_summary(wire_result, *, request_auth_fingerprint):
    requests = wire_result.get("requests") or []
    responses = wire_result.get("responses") or []
    request_auth_matches = bool(requests) and requests[0].get("path") == "/me" and all(
        row.get("host") in {"www.facebook.com", "facebook.com"}
        and row.get("path") in {"/me", "/profile.php"}
        and row.get("method") == "GET"
        and row.get("user_agent_matches") is True
        and row.get("header_auth_state") == "matches"
        and {"c_user", "xs"} <= set(row.get("header_auth_cookie_names") or [])
        for row in requests
    )
    saved_auth_snapshot_sent = bool(requests) and (
        requests[0].get("header_auth_cookie_fingerprint") == request_auth_fingerprint
    )
    auth_cookie_directives_unchanged = bool(responses) and all(
        not row.get("auth_cookie_deleted")
        and not row.get("profile_root_auth_cookie_deleted")
        and not row.get("auth_cookie_set")
        and not row.get("profile_root_auth_cookie_set")
        and row.get("status") in {200, 302, 303, 307, 308}
        and row.get("protocol") in {"HTTP/2", "HTTP/1.1"}
        for row in responses
    )
    summary = {
        "request_auth_matches": request_auth_matches,
        "saved_auth_snapshot_sent": saved_auth_snapshot_sent,
        "auth_cookie_directives_unchanged": auth_cookie_directives_unchanged,
        "measurement_errors": wire_result.get("measurement_errors", 1),
        "profile_root_auth_cookie_deletion_names": [],
    }
    proof = {**summary, "document_kind": "saved_identity", "wire": wire_result}
    summary["profile_root_auth_cookie_deletion_names"] = profile_root_auth_cookie_deletion_names(proof)
    return summary


async def probe_document(context, payload, *, web_factory=None):
    required = {"cookies", "business_id", "ad_account_id"}
    if (not isinstance(payload, dict) or not required <= set(payload)
            or set(payload) - required - {"document_kind"}):
        raise _reject("INVALID_INPUT")
    kind = payload.get('document_kind', 'adsmanager')
    if not isinstance(kind, str) or kind not in {'adsmanager','identity','saved_identity','login_document','login_other_profile'}:
        raise _reject('INVALID_DOCUMENT')
    business, account = payload["business_id"], payload["ad_account_id"]
    if any(not isinstance(value, str) or not re.fullmatch(r"\d{5,30}", value)
           for value in (business, account)):
        raise _reject("INVALID_TARGET")
    cookies = payload["cookies"]
    if (not isinstance(cookies, dict) or not 2 <= len(cookies) <= 64
            or not cookies.get("xs") or sum(len(str(v)) for v in cookies.values()) > 65536):
        raise _reject("INVALID_COOKIES")
    for name, value in cookies.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}", name)
                or not isinstance(value, str) or not 1 <= len(value) <= 8192
                or any(ord(ch) < 33 or ord(ch) > 126 or ch in '\\";,' for ch in value)):
            raise _reject("INVALID_COOKIES")
    actor = str(context.cookies.get("c_user") or "")
    if not actor.isdigit() or cookies.get("c_user") != actor:
        raise _reject("ACTOR_MISMATCH")
    if not context.proxy or not context.user_agent:
        raise _reject("PROFILE_ROUTE_UNAVAILABLE")
    # Durable IDs are navigation targets only. This does not establish access.
    if not any(isinstance(row, dict)
            and str(row.get("ad_account_id") or row.get("account_id") or row.get("id") or "").removeprefix("act_") == account
            and str(row.get("business_id") or "") == business
            for row in (context.ad_accounts or [])):
        raise _reject("ACCOUNT_CONTEXT_UNAVAILABLE")
    from fb_worker import FacebookWebSession, WebProfile
    selected_cookies = dict(context.cookies) if kind == 'saved_identity' else dict(cookies)
    if kind in {'login_document','login_other_profile'}:
        # Read the public login form with the same device/proxy/UA, without
        # forwarding either saved or supplied authentication cookies.
        selected_cookies = {key:value for key,value in selected_cookies.items()
            if key in {'datr','sb','locale','wd','dpr','m_pixel_ratio','ps_l','ps_n'}}
    profile = WebProfile(str(context.profile_id), selected_cookies, context.proxy, context.user_agent)
    web = (web_factory or FacebookWebSession)(profile)
    web.private_only = True
    saved = WebProfile(str(context.profile_id), dict(context.cookies), context.proxy, context.user_agent)
    result = {"profile_id": str(context.profile_id), "document_kind":kind, "transport": "private_http_h2",
        "persisted": False, "objects_created": False, "browser_started": False,
        "saved_auth_cookie_fingerprint": _bootstrap_evidence("", "", saved, actor)["auth_cookie_fingerprint"]}
    url = ("https://adsmanager.facebook.com/adsmanager/manage/campaigns"
           f"?act={account}&business_id={business}")
    if kind in {'identity', 'saved_identity'}:
        url = 'https://www.facebook.com/me'
    elif kind in {'login_document','login_other_profile'}:
        url = 'https://www.facebook.com/login/'
    from .private_session_wire import SessionWireEvidence
    wire = SessionWireEvidence(actor, context.user_agent)
    followed = False
    try:
        async with asyncio.timeout(35):
            if callable(getattr(web, '_ensure_session', None)):
                try:
                    await wire.attach(web)
                except Exception:
                    wire.errors += 1
            status, body, final_url = await web.fetch_text(url, max_bytes=5_000_001, document_navigation=True)
            shape, entry = _login_shape(body, final_url, include_entry=True)
            if (kind == 'login_other_profile' and status == 200 and entry and entry != final_url
                    and _bootstrap_evidence(final_url, body, profile, actor)['document_actor'] in {'logged_out','absent'}):
                # Follow only the actual first-party, GET-only account-selector
                # link. No invented login URL, form submission or JS execution.
                status, body, final_url = await web.fetch_text(entry, max_bytes=5_000_001,
                    document_navigation=True, referer=final_url)
                followed = True
                shape = _login_shape(body, final_url)
        evidence = _bootstrap_evidence(final_url, body, profile, actor)
        final = urlsplit(final_url)
        scope = (kind == 'adsmanager' and status == 200 and len(body.encode()) <= 5_000_000 and final.scheme == "https"
            and final.hostname in {"adsmanager.facebook.com", "business.facebook.com"}
            and final.path == "/adsmanager/manage/campaigns"
            and parse_qs(final.query).get("act") == [account]
            and parse_qs(final.query).get("business_id") == [business])
        confirmed, context_code = False, "not_checked"
        if scope and not _auth_gate(final_url, body):
            try:
                context_from_document(body, account_id=account, business_id=business, actor_id=actor)
                confirmed, context_code = True, "confirmed"
            except ProvisioningError as error:
                context_code = error.code
        return {**result, "response_received": True, "http_status": status,
            "wire":wire.snapshot(),
            "legacy_gate": _auth_gate(final_url, body) or "none",
            "login_shape":shape, "other_profile_entry_followed":followed,
            "evidence": evidence, "document_scope_matches": scope,
            "bootstrap_context_confirmed": confirmed, "bootstrap_context_code": context_code}
    except Exception as error:
        # No exception text/response fragments and no automatic network retry.
        return {**result, "response_received": False,
            "wire":wire.snapshot(),
            "error_code": "PRIVATE_SESSION_PROBE_READ_UNAVAILABLE",
            "transport_failure_kind":_transport_kind(error),
            "transport_failure_reason":_transport_reason(error),
            "error_type": type(error).__name__ if re.fullmatch(r"[A-Za-z_]{1,60}", type(error).__name__) else "Exception"}
    finally:
        wire.detach()
        await web.close()


async def probe_saved_identity(context, *, web_factory=None):
    """Confirm only the saved actor with one GET /me; never renew or persist cookies."""
    cookies = getattr(context, "cookies", None)
    actor = str(cookies.get("c_user") or "") if isinstance(cookies, dict) else ""
    xs = str(cookies.get("xs") or "") if isinstance(cookies, dict) else ""
    user_agent = str(getattr(context, "user_agent", "") or "")
    proxy = str(getattr(context, "proxy", "") or "")
    if not re.fullmatch(r"\d{1,30}", actor) or not xs or not user_agent or not proxy:
        return {
            "profile_id": str(getattr(context, "profile_id", "")),
            "document_kind": "saved_identity",
            "response_received": False,
            "identity_session_confirmed": False,
            "persisted": False,
            "objects_created": False,
            "browser_started": False,
            "error_code": "PRIVATE_SESSION_PROBE_CONTEXT_UNAVAILABLE",
        }

    from fb_worker import FacebookWebSession, WebProfile
    profile = WebProfile(str(context.profile_id), dict(cookies), proxy, user_agent)
    web = (web_factory or FacebookWebSession)(profile)
    web.private_only = True
    from .private_session_wire import SessionWireEvidence
    wire = SessionWireEvidence(actor, user_agent)
    auth_fingerprint = hashlib.sha256(json.dumps(
        [actor, xs], ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    request_auth_fingerprint = hashlib.sha256(json.dumps(
        {"c_user": [actor], "xs": [xs]}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    result = {
        "profile_id": str(context.profile_id),
        "document_kind": "saved_identity",
        "response_received": False,
        "identity_session_confirmed": False,
        "persisted": False,
        "objects_created": False,
        "browser_started": False,
        "auth_cookie_fingerprint": auth_fingerprint,
    }
    try:
        async with asyncio.timeout(8):
            if callable(getattr(web, "_ensure_session", None)):
                try:
                    await wire.attach(web)
                except Exception:
                    wire.errors += 1
            else:
                wire.errors += 1
            status, body, final_url = await web.fetch_text(
                "https://www.facebook.com/me",
                max_bytes=1_000_001,
                document_navigation=True,
            )
        from .private_ads_http import _bootstrap_evidence
        evidence = _bootstrap_evidence(final_url, body, profile, actor)
        login_form = evidence.get("login_form") is True
        final = urlsplit(final_url)
        final_host = final.hostname if final.hostname in {"www.facebook.com", "facebook.com"} else "unreviewed"
        final_path = final.path if final_host != "unreviewed" and final.path in {"/me", "/profile.php"} else "unreviewed"
        wire_result = wire.snapshot()
        wire_summary = _saved_identity_wire_summary(
            wire_result, request_auth_fingerprint=request_auth_fingerprint,
        )
        request_auth_matches = wire_summary["request_auth_matches"]
        saved_auth_snapshot_sent = wire_summary["saved_auth_snapshot_sent"]
        auth_cookie_directives_unchanged = wire_summary["auth_cookie_directives_unchanged"]
        final_identity_target_matches = (
            final_path == "/me"
            or (final_path == "/profile.php" and parse_qs(final.query).get("id") == [actor])
        )
        confirmed = (
            status == 200
            and final_host == "www.facebook.com"
            and final_path in {"/me", "/profile.php"}
            and final_identity_target_matches
            and evidence.get("document_actor") == "matches"
            and evidence.get("profile_actor_matches") is True
            and evidence.get("path_gate") == "none"
            and not login_form
            and request_auth_matches
            and saved_auth_snapshot_sent
            and auth_cookie_directives_unchanged
            and wire_result.get("measurement_errors") == 0
        )
        return {
            **result,
            "response_received": True,
            "http_status": status,
            "final_host": final_host,
            "final_path": final_path,
            "final_identity_target_matches": final_identity_target_matches,
            "legacy_gate": evidence.get("path_gate", "unavailable"),
            "login_form": login_form,
            "document_actor": evidence.get("document_actor", "unavailable"),
            "profile_actor_matches": evidence.get("profile_actor_matches") is True,
            **wire_summary,
            "wire": wire_result,
            "identity_session_confirmed": confirmed,
        }
    except Exception as error:
        wire_result = wire.snapshot()
        wire_summary = _saved_identity_wire_summary(
            wire_result, request_auth_fingerprint=request_auth_fingerprint,
        )
        return {
            **result,
            **wire_summary,
            "wire": wire_result,
            "error_code": "PRIVATE_SESSION_PROBE_READ_UNAVAILABLE",
            "transport_failure_kind": _transport_kind(error),
            "transport_failure_reason": _transport_reason(error),
            "error_type": type(error).__name__ if re.fullmatch(r"[A-Za-z_]{1,60}", type(error).__name__) else "Exception",
        }
    finally:
        wire.detach()
        await web.close()
