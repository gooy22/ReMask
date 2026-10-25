"""One isolated Ads Manager document GET; imported cookies are never saved."""
from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from .private_ads_document import _auth_gate
from .private_ads_http import _bootstrap_evidence, context_from_document
from .provisioning.models import ProvisioningError


def _reject(code):
    return ProvisioningError("PRIVATE_SESSION_PROBE_" + code,
        "Session comparison was not performed; saved profile data is unchanged.", retryable=False)


def _login_shape(body, final_url):
    class Forms(HTMLParser):
        def __init__(self):
            super().__init__()
            self.forms, self.current, self.logout_link = [], None, False
            self.hidden_depth, self.visible_depth, self.text = 0, 0, ''

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

        def handle_data(self, data):
            if self.hidden_depth or not self.visible_depth or len(self.text) >= 800:
                return
            text = ' '.join(data.split())[:1200]
            text = re.sub(r'https?://\S+', '[url]', text, flags=re.I)
            text = re.sub(r'(?i)\b(access_token|fb_dtsg|lsd|password|cookie)\s*[:=]\s*\S+', r'\1=[redacted]', text)
            text = re.sub(r'[A-Za-z0-9_%:+/=-]{32,}', '[redacted]', text)
            self.text = (self.text + ' ' + text).strip()[:800]

    parser = Forms()
    parser.feed(body)
    return {'forms':parser.forms, 'logout_link':parser.logout_link, 'server_text':parser.text}


def _transport_kind(error):
    payload = getattr(error, 'meta_payload', None)
    failure = payload.get('transport_failure') if isinstance(payload, dict) else None
    kind = failure.get('kind') if isinstance(failure, dict) else None
    return kind if isinstance(kind, str) and kind in {
        'TimeoutError','ConnectTimeout','ReadTimeout','WriteTimeout','PoolTimeout','ConnectError','ReadError',
        'WriteError','ProxyError','RemoteProtocolError','LocalProtocolError'} else 'unavailable'


async def probe_document(context, payload, *, web_factory=None):
    required = {"cookies", "business_id", "ad_account_id"}
    if (not isinstance(payload, dict) or not required <= set(payload)
            or set(payload) - required - {"document_kind"}):
        raise _reject("INVALID_INPUT")
    kind = payload.get('document_kind', 'adsmanager')
    if not isinstance(kind, str) or kind not in {'adsmanager','identity','saved_identity','login_document'}:
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
    if kind == 'login_document':
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
    elif kind == 'login_document':
        url = 'https://www.facebook.com/login/'
    try:
        async with asyncio.timeout(35):
            status, body, final_url = await web.fetch_text(url, max_bytes=5_000_001, document_navigation=True)
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
            "legacy_gate": _auth_gate(final_url, body) or "none",
            "login_shape":_login_shape(body, final_url),
            "evidence": evidence, "document_scope_matches": scope,
            "bootstrap_context_confirmed": confirmed, "bootstrap_context_code": context_code}
    except Exception as error:
        # No exception text/response fragments and no automatic network retry.
        return {**result, "response_received": False,
            "error_code": "PRIVATE_SESSION_PROBE_READ_UNAVAILABLE",
            "transport_failure_kind":_transport_kind(error),
            "error_type": type(error).__name__ if re.fullmatch(r"[A-Za-z_]{1,60}", type(error).__name__) else "Exception"}
    finally:
        await web.close()
