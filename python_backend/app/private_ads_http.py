"""The captured AdsGraphAPI / ApiClient protocol on the profile HTTP/2 pool.

Only fresh Ads Manager JSON configuration can initialize this client. It has
no OAuth, configured-token, Chromium, alternate-profile or direct-route path.
Credentials are private memory; neither receipts nor diagnostics include them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from urllib.parse import parse_qs, urlsplit

from .private_ads_draft import fail, flatten_params, identity
from .private_inventory import _auth_gate, _json_payloads
from .provisioning.models import ProvisioningError


def _walk(value, depth=0):
    if depth > 32:
        raise fail("BOOTSTRAP_CONFIG_INVALID", "Ads Manager configuration exceeds its nesting limit")
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, depth + 1)


def _unique(values, code):
    encoded = {json.dumps(value, sort_keys=True, separators=(",", ":")) for value in values}
    if len(encoded) != 1:
        raise fail(code, "Fresh Ads Manager configuration is missing or conflicting", True)
    return json.loads(next(iter(encoded)))


@dataclass(frozen=True, slots=True)
class AdsManagerContext:
    account_id: str
    business_id: str
    actor_id: str
    origin: str
    version: str
    application_id: str
    access_token: str = field(repr=False)


def context_from_document(body, *, account_id, business_id, actor_id):
    """Read literal module data and ApiClient setter calls, never execute JS."""
    account_id, business_id, actor_id = map(identity, (account_id, business_id, actor_id))
    configurations = {name: [] for name in ("GraphAPIConfig", "UrlMapConfig", "CurrentUserInitialData")}
    setters = {name: [] for name in ("setAccessToken", "setClientID")}
    for payload in _json_payloads(body):
        for node in _walk(payload):
            if not isinstance(node, list) or len(node) < 3 or not isinstance(node[0], str):
                continue
            if node[0] in configurations and isinstance(node[1], list) and isinstance(node[2], dict):
                configurations[node[0]].append(node[2])
            if node[0] == "ApiClient" and isinstance(node[1], str) and node[1] in setters:
                # ServerJS require rows carry arguments at index 3. Do not
                # search arbitrary response strings for credential patterns.
                args = node[3] if len(node) > 3 else None
                if isinstance(args, list) and len(args) == 1:
                    setters[node[1]].append(args[0])
    graph = _unique(configurations["GraphAPIConfig"], "BOOTSTRAP_CONFIG_UNAVAILABLE")
    url_map = _unique(configurations["UrlMapConfig"], "BOOTSTRAP_CONFIG_UNAVAILABLE")
    user = _unique(configurations["CurrentUserInitialData"], "ACTOR_UNVERIFIED")
    if user.get("USER_ID") != actor_id:
        raise fail("ACTOR_MISMATCH", "Ads Manager returned another Facebook actor")
    # These are observed configuration values, not guesses about SDK routing.
    # A changed origin/version needs a new source review before dispatch.
    if graph.get("adsApiVersion") != "v22.0" or url_map.get("graph") != "adsmanager-graph.facebook.com":
        raise fail("BOOTSTRAP_ROUTE_UNREVIEWED", "Ads Manager routing differs from the captured sender")
    token = _unique(setters["setAccessToken"], "SESSION_CONTEXT_UNAVAILABLE")
    application = _unique(setters["setClientID"], "SESSION_CONTEXT_UNAVAILABLE")
    if not isinstance(token, str) or not 10 <= len(token) <= 4096 or re.search(r"[\s\x00-\x1f]", token):
        raise fail("SESSION_CONTEXT_UNAVAILABLE", "Ads Manager session initialization is incomplete", True)
    return AdsManagerContext(account_id, business_id, actor_id,
        "https://adsmanager-graph.facebook.com", "v22.0", identity(str(application)), token)


class AdsManagerPrivateHttp:
    def __init__(self, web, context):
        self.web, self.context = web, context

    @classmethod
    async def open(cls, web, *, account_id, business_id, actor_id):
        account_id, business_id, actor_id = map(identity, (account_id, business_id, actor_id))
        if str(web.profile.cookies.get("c_user") or "") != actor_id:
            raise fail("ACTOR_MISMATCH", "Advertising request belongs to another profile")
        url = ("https://adsmanager.facebook.com/adsmanager/manage/campaigns"
               f"?act={account_id}&business_id={business_id}")
        status, body, final_url = await web.fetch_text(url, max_bytes=5_000_001, document_navigation=True)
        final = urlsplit(final_url)
        gate = _auth_gate(final_url, body)
        if gate:
            raise fail("SESSION_UNAVAILABLE", "Ads Manager requires restoration of the profile session", True)
        if (status != 200 or len(body.encode()) > 5_000_000 or final.scheme != "https"
                or final.hostname not in {"adsmanager.facebook.com", "business.facebook.com"}
                or final.path != "/adsmanager/manage/campaigns"
                or parse_qs(final.query).get("act") != [account_id]
                or parse_qs(final.query).get("business_id") != [business_id]):
            raise fail("BOOTSTRAP_SCOPE_UNVERIFIED", "Ads Manager did not confirm the requested account context", True)
        return cls(web, context_from_document(body, account_id=account_id,
                    business_id=business_id, actor_id=actor_id))

    async def request(self, method, path, params, *, before_submit=None, files=None):
        if method not in {"GET", "POST"} or not re.fullmatch(r"/[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)?", path):
            raise fail("INVALID_ROUTE", "Advertising request route is malformed")
        if path.startswith("/act_") and path.split("/")[1] != "act_" + self.context.account_id:
            raise fail("READ_SCOPE_MISMATCH", "Advertising route belongs to another account")
        if before_submit is not None and method != "POST":
            raise fail("INVALID_INPUT", "A read cannot claim a submission")
        self.web.private_only = True
        if (str(self.web.profile.cookies.get("c_user") or "") != self.context.actor_id
                or not self.web.profile.proxy):
            raise fail("PROFILE_CONTEXT_UNAVAILABLE", "Advertising profile route changed or is unavailable", True)
        session = await self.web._ensure_session()
        if session.proxy != self.web.profile.proxy:
            raise fail("PROFILE_CONTEXT_UNAVAILABLE", "Advertising proxy differs from the initialized profile", True)
        from .private_launch import PrivateLaunchContractStore
        PrivateLaunchContractStore._reject_auth_material(params)
        url = self.context.origin + "/" + self.context.version + path
        headers = {"Accept": "application/json", "Origin": "https://adsmanager.facebook.com",
                   "Referer": "https://adsmanager.facebook.com/", "X-Requested-With": "XMLHttpRequest"}
        query = {"access_token": self.context.access_token}
        fields = flatten_params(params)
        if files:
            query["suppress_http_code"] = "1"
            fields.setdefault("locale", "en_US")
            options = {"params": query, "data": fields, "files": files}
        else:
            fields.setdefault("method", method.lower())
            fields.setdefault("pretty", "0")
            fields.setdefault("locale", "en_US")
            options = {"params": {**fields, **query}} if method == "GET" else {"params": query, "data": fields}
        if before_submit is not None:
            await before_submit()
        try:
            async with session.request(method, url, headers=headers, allow_redirects=False, **options) as response:
                if response.http_version != "HTTP/2":
                    raise fail("PROTOCOL_UNAVAILABLE", "Advertising origin did not negotiate HTTP/2", True)
                raw = await response.content.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise fail("RESPONSE_TOO_LARGE", "Advertising response exceeds its body budget")
                if response.status != 200:
                    raise fail("HTTP_REJECTED", "Ads Manager rejected the advertising request", True)
                result = json.loads(raw)
        except Exception as exc:
            if isinstance(exc, ProvisioningError):
                raise
            # Exception strings and URLs can include the session credential.
            raise fail("HTTP_UNAVAILABLE", "Advertising HTTP request did not return a usable response", True) from None
        if not isinstance(result, dict) or result.get("error") or result.get("errors"):
            raise fail("REQUEST_REJECTED", "Meta returned an advertising request error")
        return result

    async def get(self, path, params):
        return await self.request("GET", path, params)

    async def post(self, path, params, *, before_submit, files=None):
        return await self.request("POST", path, params, before_submit=before_submit, files=files)
