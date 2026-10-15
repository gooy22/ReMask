"""The captured AdsGraphAPI / ApiClient protocol on the profile HTTP/2 pool.

Only fresh Ads Manager JSON configuration can initialize this client. It has
no OAuth, configured-token, Chromium, alternate-profile or implicit direct fallback.
An explicit matching profile-scoped direct-test lease can authorize its route.
Credentials are private memory; neither receipts nor diagnostics include them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import hashlib
import logging
import re
from urllib.parse import parse_qs, urlsplit

from .private_ads_draft import fail, flatten_params, identity
from .private_inventory import _auth_gate, _json_payloads, _JSON_SCRIPT_RE, _response_only
from .provisioning.models import ProvisioningError

log = logging.getLogger("remask.private_ads_http")

# ServerJS.handle receives literal JSON, including in legacy requireLazy
# script wrappers. Parse its data argument; never execute the wrapper.
_SERVERJS_HANDLE = re.compile(
    r'''requireLazy\(\s*\[\s*["']ServerJS["']\s*\]\s*,\s*function\s*\(\s*'''
    r'''(?P<receiver>[A-Za-z_$][A-Za-z0-9_$]*)\s*\)\s*\{\s*(?P=receiver)\.handle\(\s*''')


def _ads_document_payloads(body):
    payloads = _json_payloads(body)
    decoder = json.JSONDecoder()
    for script in _JSON_SCRIPT_RE.finditer(body):
        source = script.group(1)
        for call in _SERVERJS_HANDLE.finditer(source):
            try:
                value, end = decoder.raw_decode(source, call.end())
            except (ValueError, RecursionError):
                continue
            if isinstance(value, dict) and source[end:].lstrip().startswith(")"):
                payloads.append(_response_only(value))
    return payloads


class AdsManagerResponseError(ProvisioningError):
    """A received rejection, with only typed non-secret response metadata."""
    def __init__(self, diagnostic):
        safe = {}
        for key in ("http_status", "code", "error_subcode"):
            value = diagnostic.get(key)
            if type(value) is int and 0 <= value <= 2_147_483_647:
                safe[key] = value
        if type(diagnostic.get("is_transient")) is bool:
            safe["is_transient"] = diagnostic["is_transient"]
        if (isinstance(diagnostic.get("type"), str) and diagnostic["type"] in
                {"OAuthException", "GraphMethodException", "GraphAPIException", "ApplicationException"}):
            safe["type"] = diagnostic["type"]
        self.diagnostic = safe
        log.warning("ads_request_rejected diagnostic=%s", json.dumps(safe, sort_keys=True, separators=(",", ":")))
        details = [f"{key}={value}" for key, value in safe.items()]
        message = "Meta rejected the advertising request"
        if details:
            message += " (" + ", ".join(details) + ")"
        super().__init__("PRIVATE_ADS_DRAFT_REQUEST_REJECTED", message, retryable=False)


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


class _MissingSessionInitialization(ProvisioningError):
    def __init__(self, context):
        super().__init__("PRIVATE_ADS_DRAFT_SESSION_CONTEXT_UNAVAILABLE",
            "Ads Manager omitted its session initializer", retryable=True)
        self.context = context


def context_from_document(body, *, account_id, business_id, actor_id):
    """Read literal module data and source-supported initializers; never execute JS."""
    account_id, business_id, actor_id = map(identity, (account_id, business_id, actor_id))
    configurations = {name: [] for name in ("GraphAPIConfig", "UrlMapConfig", "CurrentUserInitialData")}
    setters = {name: [] for name in ("setAccessToken", "setClientID")}
    initializers = []
    for payload in _ads_document_payloads(body):
        for node in _walk(payload):
            if not isinstance(node, list) or len(node) < 3 or not isinstance(node[0], str):
                continue
            if node[0] in configurations and node[1] == [] and isinstance(node[2], dict):
                configurations[node[0]].append(node[2])
            # ServerJSDefine.getModuleNameAndHash strips the @hash suffix
            # before resolving a require/call. The hash is not a credential.
            module = node[0].split("@", 1)[0]
            if (module in {"ApiClient", "WebApiApplication"} and isinstance(node[1], str)
                    and node[1] in {"init", "setAccessToken", "setAccessTokenForClientID", "setClientID", "setWithCredentials"}):
                args = node[3] if len(node) > 3 else None
                initializers.append({"module":module, "method":node[1],
                    "arg_types":[type(value).__name__ for value in args] if isinstance(args, list) else []})
            if module == "ApiClient" and isinstance(node[1], str) and node[1] in setters:
                # ServerJS require rows carry arguments at index 3. Do not
                # search arbitrary response strings for credential patterns.
                args = node[3] if len(node) > 3 else None
                if isinstance(args, list) and len(args) == 1:
                    setters[node[1]].append(args[0])
            if (module == "WebApiApplication" and node[1] == "init" and len(node) >= 4
                    and isinstance(node[2], list) and isinstance(node[3], list) and len(node[3]) >= 2):
                # Public WebApiApplication.init forwards arguments 0/1 to
                # ApiClient.setAccessToken/setClientID. Literal ServerJS only.
                token, application = node[3][:2]
                if str(application) != "119211728144504":
                    raise fail("BOOTSTRAP_APPLICATION_UNREVIEWED", "Ads Manager application differs from reviewed initialization")
                setters["setAccessToken"].append(token)
                setters["setClientID"].append(str(application))
    graph = _unique(configurations["GraphAPIConfig"], "BOOTSTRAP_CONFIG_UNAVAILABLE")
    url_map = _unique(configurations["UrlMapConfig"], "BOOTSTRAP_CONFIG_UNAVAILABLE")
    user = _unique(configurations["CurrentUserInitialData"], "ACTOR_UNVERIFIED")
    if user.get("USER_ID") != actor_id:
        raise fail("ACTOR_MISMATCH", "Ads Manager returned another Facebook actor")
    # These are observed configuration values, not guesses about SDK routing.
    # A changed origin/version needs a new source review before dispatch.
    if graph.get("adsApiVersion") != "v22.0" or url_map.get("graph") != "adsmanager-graph.facebook.com":
        raise fail("BOOTSTRAP_ROUTE_UNREVIEWED", "Ads Manager routing differs from the captured sender")
    log.info("ads_session_initialization token_candidates=%d client_candidates=%d valid_token_candidates=%d initializers=%s",
        len(setters["setAccessToken"]), len(setters["setClientID"]),
        sum(isinstance(value, str) and 10 <= len(value) <= 4096 and not any(ch.isspace() or ord(ch) < 32 for ch in value)
            for value in setters["setAccessToken"]), json.dumps(initializers[:20], separators=(",", ":")))
    if not setters["setAccessToken"] and not setters["setClientID"]:
        # Reviewed WebApiApplication Ads Manager client. A cookie-authenticated
        # native renewal must succeed before this incomplete context is usable.
        raise _MissingSessionInitialization(AdsManagerContext(account_id, business_id, actor_id,
            "https://adsmanager-graph.facebook.com", "v22.0", "119211728144504", ""))
    token = _unique(setters["setAccessToken"], "SESSION_CONTEXT_UNAVAILABLE")
    application = _unique(setters["setClientID"], "SESSION_CONTEXT_UNAVAILABLE")
    if not isinstance(token, str) or not 10 <= len(token) <= 4096 or re.search(r"[\s\x00-\x1f]", token):
        raise fail("SESSION_CONTEXT_UNAVAILABLE", "Ads Manager session initialization is incomplete", True)
    return AdsManagerContext(account_id, business_id, actor_id,
        "https://adsmanager-graph.facebook.com", "v22.0", identity(str(application)), token)


class AdsManagerPrivateHttp:
    def __init__(self, web, context):
        self.web, self.context = web, context
        self.read_only = False
        self.document_origin = 'https://adsmanager.facebook.com'
        self._profile_proxy = web.profile.proxy
        self._cookie_digest = self._cookies()
        self._profile_name = getattr(web.profile, "name", "")
        self._profile_user_agent = getattr(web.profile, "user_agent", "")
        from .direct_test import direct_test_deadline
        self._direct_deadline = direct_test_deadline(self._profile_name) if not self._profile_proxy else 0

    def _cookies(self):
        # Digest remains private memory. A renewed/changed profile document
        # requires new initialization before another advertising request.
        return hashlib.sha256(json.dumps(dict(self.web.profile.cookies),
            sort_keys=True, separators=(",", ":")).encode()).digest()

    def _profile_matches(self):
        from .direct_test import direct_test_deadline
        route_valid = bool(self._profile_proxy) or (self._direct_deadline > 0 and
            direct_test_deadline(self._profile_name) == self._direct_deadline)
        return (route_valid and self.web.profile.proxy == self._profile_proxy
                and getattr(self.web.profile, "name", "") == self._profile_name
                and getattr(self.web.profile, "user_agent", "") == self._profile_user_agent
                and self._cookies() == self._cookie_digest
                and str(self.web.profile.cookies.get("c_user") or "") == self.context.actor_id)

    @classmethod
    async def open(cls, web, *, account_id, business_id, actor_id):
        account_id, business_id, actor_id = map(identity, (account_id, business_id, actor_id))
        web.private_only = True
        if str(web.profile.cookies.get("c_user") or "") != actor_id:
            raise fail("ACTOR_MISMATCH", "Advertising request belongs to another profile")
        url = ("https://adsmanager.facebook.com/adsmanager/manage/campaigns"
               f"?act={account_id}&business_id={business_id}")
        status, body, final_url = await web.fetch_text(url, max_bytes=5_000_001, document_navigation=True)
        final = urlsplit(final_url)
        account_matches = parse_qs(final.query).get("act") == [account_id]
        gate = _auth_gate(final_url, body)
        # Only typed status and comparison results may leave this boundary.
        # Raw URLs, HTML, cookies and initialization values can contain secrets.
        log.info("ads_bootstrap profile=%s status=%s bytes=%d host_reviewed=%s path_matches=%s account_matches=%s business_matches=%s gate=%s",
            getattr(web.profile, "name", ""), status, len(body.encode()),
            final.hostname in {"adsmanager.facebook.com", "business.facebook.com"},
            final.path == "/adsmanager/manage/campaigns",
            account_matches,
            parse_qs(final.query).get("business_id") == [business_id], gate or "none")
        if gate:
            raise fail(gate, "Ads Manager requires restoration of the profile session", True)
        if (status != 200 or len(body.encode()) > 5_000_000 or final.scheme != "https"
                or final.hostname not in {"adsmanager.facebook.com", "business.facebook.com"}
                or final.path != "/adsmanager/manage/campaigns"
                or parse_qs(final.query).get("business_id") != [business_id]):
            raise fail("BOOTSTRAP_SCOPE_UNVERIFIED", "Ads Manager did not confirm the requested account context", True)
        try:
            context = context_from_document(body, account_id=account_id,
                        business_id=business_id, actor_id=actor_id)
        except _MissingSessionInitialization as error:
            client = cls(web, error.context)
            client.document_origin = final.scheme + '://' + final.hostname
            client.read_only = True
            context = await client._renew_session(body)
            # Every renewed session must independently confirm the selected
            # account and BM, even if the document retained its act parameter.
            account_matches = False
        except ProvisioningError as error:
            log.warning("ads_bootstrap rejected profile=%s code=%s", getattr(web.profile, "name", ""), error.code)
            raise
        refresh = getattr(web, 'refresh_reviewed_modules', None)
        if callable(refresh):
            await refresh(body, final_url)
        client = cls(web, context)
        client.document_origin = final.scheme + '://' + final.hostname
        if not account_matches:
            # Meta may select a default account during document navigation.
            # Before returning a usable sender, prove the requested account
            # and BM through the reviewed account READ on this same session.
            client.read_only = True
            account = await client.get("/act_" + account_id, {"fields": ["id", "business"]})
            if (not isinstance(account.get("id"), str)
                    or account["id"].removeprefix("act_") != account_id
                    or not isinstance(account.get("business"), dict)
                    or account["business"].get("id") != business_id):
                log.warning("ads_bootstrap scope_rejected profile=%s account_matches=%s business_matches=%s",
                    getattr(web.profile, "name", ""),
                    isinstance(account.get("id"), str) and account['id'].removeprefix('act_') == account_id,
                    isinstance(account.get('business'), dict) and account['business'].get('id') == business_id)
                raise fail("BOOTSTRAP_SCOPE_UNVERIFIED", "Ads Manager did not confirm the requested account and Business", True)
            client.read_only = False
            log.info("ads_bootstrap scope_confirmed profile=%s source=exact_account_read", getattr(web.profile, "name", ""))
        return client

    async def _renew_session(self, document):
        """One cookie-authenticated native refresh, as AdsMgmtNetworkRefreshToken.

        No advertising write, OAuth grant, alternate actor or configured token.
        Source: XAdsCMRenewAccessTokenController + AsyncRequest.promisePayload.
        """
        try:
            bootstrap = await self.web.bootstrap()
            if (not self._profile_matches() or bootstrap.actor_id != self.context.actor_id
                    or not isinstance(bootstrap.fb_dtsg, str) or not bootstrap.fb_dtsg):
                raise fail("PROFILE_CONTEXT_UNAVAILABLE", "Session renewal requires the same confirmed actor")
            session = await self.web._ensure_session()
            if session.proxy != self._profile_proxy:
                raise fail("PROFILE_CONTEXT_UNAVAILABLE", "Session renewal route changed")
            form = {"client_id":self.context.application_id, "__a":"1", "__req":"1",
                    "__user":self.context.actor_id, "fb_dtsg":bootstrap.fb_dtsg}
            extract = getattr(self.web, '_extract_request_context', None)
            metadata = dict(getattr(bootstrap, 'request_context', {}) or {})
            if callable(extract):
                metadata.update(extract(document))
            for name in ('__hs','__rev','__s','__hsi','__dyn','__csr','__comet_req',
                         '__spin_r','__spin_b','__spin_t','dpr','__ccg'):
                value = metadata.get(name)
                if isinstance(value, (str, int)) and 0 < len(str(value)) <= 20000:
                    form[name] = str(value)
            counter = getattr(self.web, '_next_graphql_req', None)
            if callable(counter):
                form['__req'] = counter()
            for name in ("lsd", "jazoest"):
                value = getattr(bootstrap, name, "")
                if isinstance(value, str) and value:
                    form[name] = value
            configs = {name:[] for name in ('DTSGInitialData','DTSGInitData','LSD','SprinkleConfig')}
            for payload in _ads_document_payloads(document):
                for node in _walk(payload):
                    if (isinstance(node, list) and len(node) >= 3 and isinstance(node[0], str)
                            and node[0] in configs and node[1] == [] and isinstance(node[2], dict)):
                        configs[node[0]].append(node[2])
            for module, field in (('DTSGInitialData','fb_dtsg'),('DTSGInitData','fb_dtsg'),('LSD','lsd')):
                if configs[module]:
                    value = _unique(configs[module], 'SESSION_RENEWAL_CONFIG_UNAVAILABLE').get('token')
                    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
                        raise fail('SESSION_RENEWAL_CONFIG_UNAVAILABLE','Fresh AsyncRequest CSRF configuration is invalid')
                    form[field] = value
            if configs['SprinkleConfig']:
                sprinkle = _unique(configs['SprinkleConfig'],'SESSION_RENEWAL_CONFIG_UNAVAILABLE')
                version = sprinkle.get('version')
                if (sprinkle.get('param_name') != 'jazoest' or type(sprinkle.get('should_randomize')) is not bool
                        or not isinstance(version, (str,int)) or not str(version).isdigit()):
                    raise fail('SESSION_RENEWAL_CONFIG_UNAVAILABLE','Fresh AsyncRequest CSRF checksum is unreviewed')
                # DTSGUtils.getNumericValue sums UTF-16 code units, then adds
                # the fresh SprinkleConfig version when randomization is off.
                units = form['fb_dtsg'].encode('utf-16-le', errors='surrogatepass')
                total = sum(units[i] + 256 * units[i+1] for i in range(0,len(units),2))
                form['jazoest'] = ('' if sprinkle['should_randomize'] else str(version)) + str(total)
            headers = {"Accept":"application/json", "Origin":self.document_origin,
                       "Referer":self.document_origin + "/", "Sec-Fetch-Dest":"empty",
                       "Sec-Fetch-Mode":"cors", "Sec-Fetch-Site":"same-origin"}
            if form.get("lsd"):
                headers["X-FB-LSD"] = form["lsd"]
            if not self._profile_matches():
                raise fail("PROFILE_CONTEXT_UNAVAILABLE", "Session renewal profile changed before dispatch")
            log.info('ads_session_renewal envelope profile=%s fields=%s',self._profile_name,sorted(form))
            async with session.request("POST", self.document_origin + "/ads/manager/ajax/renew_access_token/",
                    data=form, headers=headers, allow_redirects=False, request_timeout_seconds=30) as response:
                if response.http_version != "HTTP/2":
                    raise fail("PROTOCOL_UNAVAILABLE", "Session renewal requires HTTP/2")
                raw = await response.content.read(2_000_001)
                if response.status != 200 or len(raw) > 2_000_000:
                    raise fail("SESSION_RENEWAL_UNAVAILABLE", "Meta did not return a usable session renewal")
            text = raw.decode("utf-8").strip()
            if text.startswith("for (;;);"):
                text = text[len("for (;;);"):].lstrip()
            result = json.loads(text)
            if not isinstance(result, dict) or result.get("error") or result.get("errors"):
                code = result.get("error") if isinstance(result, dict) else None
                log.warning("ads_session_renewal rejected profile=%s code=%s",
                    self._profile_name, code if type(code) is int else "unavailable")
                raise fail("SESSION_RENEWAL_REJECTED", "Meta rejected the cookie-authenticated advertising session renewal")
            payload = result.get("payload")
            if not isinstance(payload, dict):
                raise fail("SESSION_RENEWAL_UNAVAILABLE", "Meta session renewal payload is missing")
            token = payload.get("access_token")
            if (str(payload.get("client_id")) != self.context.application_id
                    or not isinstance(token, str) or not 10 <= len(token) <= 4096
                    or re.search(r"[\s\x00-\x1f]", token) or not self._profile_matches()):
                raise fail("SESSION_RENEWAL_UNAVAILABLE", "Meta session renewal did not confirm the reviewed client")
            log.info("ads_session_renewal confirmed profile=%s", self._profile_name)
            return replace(self.context, access_token=token)
        except ProvisioningError:
            raise
        except Exception:
            # Transport and decoder exceptions may contain credential-bearing
            # URLs or response content; never include them in diagnostics.
            raise fail("SESSION_RENEWAL_UNAVAILABLE", "Advertising session renewal did not return usable data", True) from None

    async def request(self, method, path, params, *, before_submit=None, files=None, multipart=False):
        if method not in {"GET", "POST"} or not re.fullmatch(r"/[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)?", path):
            raise fail("INVALID_ROUTE", "Advertising request route is malformed")
        if path.startswith("/act_") and path.split("/")[1] != "act_" + self.context.account_id:
            raise fail("READ_SCOPE_MISMATCH", "Advertising route belongs to another account")
        if method == "POST" and self.read_only:
            raise fail("RESUME_READ_ONLY", "Saved publication recovery cannot dispatch another write")
        if method == "POST" and not callable(before_submit):
            raise fail("SUBMIT_CLAIM_REQUIRED", "Advertising writes require the durable submission journal")
        if before_submit is not None and method != "POST":
            raise fail("INVALID_INPUT", "A read cannot claim a submission")
        self.web.private_only = True
        if not self._profile_matches():
            raise fail("PROFILE_CONTEXT_UNAVAILABLE", "Advertising profile route changed or is unavailable", True)
        session = await self.web._ensure_session()
        if session.proxy != self.web.profile.proxy:
            raise fail("PROFILE_CONTEXT_UNAVAILABLE", "Advertising proxy differs from the initialized profile", True)
        if not self.web.profile.proxy:
            from .direct_test import direct_test_deadline
            deadline = direct_test_deadline(getattr(self.web.profile, "name", ""))
            if not deadline or getattr(session, "direct_test_until", 0) != deadline:
                raise fail("PROFILE_CONTEXT_UNAVAILABLE", "Advertising direct test is absent, changed or expired", True)
        from .private_launch import PrivateLaunchContractStore
        PrivateLaunchContractStore._reject_auth_material(params)
        url = self.context.origin + "/" + self.context.version + path
        headers = {"Accept": "application/json", "Origin": "https://adsmanager.facebook.com",
                   "Referer": "https://adsmanager.facebook.com/", "X-Requested-With": "XMLHttpRequest"}
        if path.endswith("/advideos") and params.get("upload_phase") == "transfer":
            waterfall = params.get("waterfall_id")
            if not isinstance(waterfall, str) or not re.fullmatch(r"[a-f0-9]{32}", waterfall):
                raise fail("INVALID_INPUT", "Video upload waterfall identity is malformed")
            headers["X_FB_VIDEO_WATERFALL_ID"] = waterfall
        query = {"access_token": self.context.access_token, "suppress_http_code": "1"}
        fields = flatten_params(params)
        if files or multipart:
            query["suppress_http_code"] = "1"
            fields.setdefault("locale", "en_US")
            options = {"params": query, "data": fields if files else {},
                       "files": files or {key: (None, value) for key, value in fields.items()}}
        else:
            fields.setdefault("method", method.lower())
            fields.setdefault("pretty", "0")
            fields.setdefault("locale", "en_US")
            options = {"params": {**fields, **query}} if method == "GET" else {"params": query, "data": fields}
        if before_submit is not None:
            await before_submit()
        # Claim awaits the database; recheck the same profile afterwards.
        if not self._profile_matches() or session.proxy != self._profile_proxy:
            raise fail("PROFILE_CONTEXT_UNAVAILABLE", "Advertising profile changed before dispatch", True)
        try:
            async with session.request(method, url, headers=headers, allow_redirects=False,
                                       request_timeout_seconds=300 if files else 60, **options) as response:
                if response.http_version != "HTTP/2":
                    raise fail("PROTOCOL_UNAVAILABLE", "Advertising origin did not negotiate HTTP/2", True)
                raw = await response.content.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise fail("RESPONSE_TOO_LARGE", "Advertising response exceeds its body budget")
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    if response.status != 200:
                        raise AdsManagerResponseError({"http_status": response.status}) from None
                    raise fail("RESPONSE_INVALID", "Advertising response could not be decoded") from None
                if isinstance(result, dict) and (result.get("error") or result.get("errors")):
                    error = result.get("error")
                    raise AdsManagerResponseError({**(error if isinstance(error, dict) else {}),
                                                   "http_status": response.status})
                if response.status != 200:
                    raise AdsManagerResponseError({"http_status": response.status})
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

    async def post(self, path, params, *, before_submit, files=None, multipart=False):
        return await self.request("POST", path, params, before_submit=before_submit, files=files, multipart=multipart)
