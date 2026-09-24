from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, unquote, unquote_plus, urlencode, urlsplit


CheckpointCallback = Callable[[dict[str, Any]], Awaitable[None]]


class BrowserBusinessError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        diagnostic: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "BROWSER_BUSINESS_ERROR").strip()
        self.retryable = bool(retryable)
        self.diagnostic = diagnostic or {}


@dataclass(slots=True)
class BrowserPreflightResult:
    ready: bool
    current_url: str = ""
    create_surface_ready: bool = False
    account_id: str = ""
    diagnostics: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BrowserCreateResult:
    business_id: str
    before_ids: list[str]
    after_ids: list[str]
    response_business_id: str = ""
    response_friendly_name: str = ""
    response_path: str = ""
    recovered: bool = False


@dataclass(slots=True)
class BrowserAdAccountResult:
    business_id: str
    ad_account_id: str
    response_friendly_name: str = ""
    response_doc_id: str = ""
    response_path: str = ""


@dataclass(slots=True)
class BrowserPageResult:
    business_id: str
    page_id: str
    already_attached: bool = False


_BROWSER_LIMIT = max(1, int(os.getenv("REMASK_BM_BROWSER_CONCURRENCY") or "2"))
_BROWSER_SEMAPHORE = asyncio.Semaphore(_BROWSER_LIMIT)
_PROFILE_LOCKS: dict[str, asyncio.Lock] = {}
_PROFILE_LOCKS_GUARD = asyncio.Lock()


async def _get_profile_lock(profile_id: str) -> asyncio.Lock:
    key = _clean(profile_id) or "unknown"
    async with _PROFILE_LOCKS_GUARD:
        current = _PROFILE_LOCKS.get(key)
        if current is None:
            current = asyncio.Lock()
            _PROFILE_LOCKS[key] = current
        return current


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _digits(value: Any) -> str:
    text = _clean(value)
    return text if re.fullmatch(r"\d{5,30}", text) else ""


def _request_graphql_meta(request: Any) -> dict[str, Any]:
    """Parse Meta GraphQL request metadata without exposing auth fields."""
    method = ""
    url = ""
    raw = ""
    body_decodable = True

    try:
        method = _clean(getattr(request, "method", "")).upper()
        url = _clean(getattr(request, "url", ""))
        raw_buffer = getattr(request, "post_data_buffer", None)
        if raw_buffer:
            if isinstance(raw_buffer, bytes):
                raw = raw_buffer.decode("utf-8")
            else:
                raw = str(raw_buffer)
        else:
            raw = str(getattr(request, "post_data", "") or "")
    except (UnicodeDecodeError, UnicodeError):
        body_decodable = False
        try:
            raw = str(getattr(request, "post_data", "") or "")
        except Exception:
            raw = ""
    except Exception:
        body_decodable = False
        try:
            raw = str(getattr(request, "post_data", "") or "")
        except Exception:
            raw = ""

    parsed = parse_qs(raw, keep_blank_values=True) if raw else {}
    friendly = _clean(
        (parsed.get("fb_api_req_friendly_name") or [""])[0]
    )
    if not friendly:
        try:
            headers = getattr(request, "headers", {}) or {}
            friendly = _clean(
                headers.get("x-fb-friendly-name")
                or headers.get("X-FB-Friendly-Name")
            )
        except Exception:
            friendly = ""
    doc_id = _clean((parsed.get("doc_id") or [""])[0])

    variables: dict[str, Any] = {}
    variables_raw = _clean((parsed.get("variables") or [""])[0])
    if variables_raw:
        try:
            decoded_variables = json.loads(variables_raw)
            if isinstance(decoded_variables, dict):
                variables = decoded_variables
        except (ValueError, json.JSONDecodeError):
            variables = {}

    raw_input = variables.get("input")
    input_data = raw_input if isinstance(raw_input, dict) else {}

    return {
        "method": method,
        "url": url,
        "friendly_name": friendly,
        "doc_id": doc_id,
        "variables": variables,
        "input": input_data,
        "decoded_raw": unquote_plus(raw) if raw else "",
        "body_decodable": body_decodable,
    }


def _ad_account_required_attribution_post_data(
    request: Any,
    *,
    end_advertiser: str = "NONE",
    media_agency: str = "NONE",
    partner: str = "NONE",
) -> tuple[str, dict[str, str]]:
    """Fill Meta-required attribution fields on its own Add-RK GraphQL request.

    Meta's Business /adaccount contract requires end_advertiser,
    media_agency and partner even when no external entity is involved.  The
    Business Settings frontend can currently emit the private CREATE mutation
    without those optional-looking UI choices.  Preserve Meta's live request
    verbatim and only add missing required values to variables.input.
    """
    try:
        raw_buffer = getattr(request, "post_data_buffer", None)
        if raw_buffer:
            raw = (
                raw_buffer.decode("utf-8")
                if isinstance(raw_buffer, bytes)
                else str(raw_buffer)
            )
        else:
            raw = str(getattr(request, "post_data", "") or "")
    except Exception:
        return "", {}

    if not raw:
        return "", {}

    parsed = parse_qs(raw, keep_blank_values=True)
    variables_raw = _clean((parsed.get("variables") or [""])[0])
    if not variables_raw:
        return raw, {}

    try:
        variables = json.loads(variables_raw)
    except (ValueError, json.JSONDecodeError):
        return raw, {}
    if not isinstance(variables, dict):
        return raw, {}

    input_data = variables.get("input")
    if not isinstance(input_data, dict):
        return raw, {}

    defaults = {
        "end_advertiser": _clean(end_advertiser) or "NONE",
        "media_agency": _clean(media_agency) or "NONE",
        "partner": _clean(partner) or "NONE",
    }
    applied: dict[str, str] = {}
    for key, value in defaults.items():
        current = input_data.get(key)
        if current is None or (isinstance(current, str) and not current.strip()):
            input_data[key] = value
            applied[key] = value

    if not applied:
        return raw, {}

    parsed["variables"] = [
        json.dumps(variables, ensure_ascii=False, separators=(",", ":"))
    ]
    return urlencode(parsed, doseq=True), applied


def _proxy_config(raw_proxy: str | None) -> dict[str, str] | None:
    raw = _clean(raw_proxy)
    if not raw:
        return None

    proxy_url = raw if "://" in raw else f"http://{raw}"
    parts = urlsplit(proxy_url)
    if not parts.hostname or not parts.port:
        raise BrowserBusinessError(
            "PROXY_INVALID",
            "Facebook profile proxy is invalid.",
            retryable=False,
        )

    result = {
        "server": f"{parts.scheme or 'http'}://{parts.hostname}:{parts.port}"
    }
    if parts.username:
        result["username"] = unquote(parts.username)
    if parts.password:
        result["password"] = unquote(parts.password)
    return result


def _decode_graphql_text(raw: str) -> Any:
    body = _clean(raw)
    if body.startswith("for (;;);"):
        body = body[len("for (;;);"):].lstrip()
    if not body:
        return None

    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        pass

    # Facebook/Relay may stream several JSON payloads as newline-delimited
    # chunks. Keep all successfully decoded chunks: downstream ID/error
    # walkers already recurse through lists.
    chunks: list[Any] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("for (;;);"):
            line = line[len("for (;;);"):].lstrip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue

    if len(chunks) == 1:
        return chunks[0]
    if chunks:
        return chunks
    return None


def _graphql_error_details(payload: Any) -> list[dict[str, Any]]:
    """Extract compact, non-secret GraphQL error details from Meta responses."""
    output: list[dict[str, Any]] = []

    def add_error(value: Any) -> None:
        if not isinstance(value, dict):
            return

        message = _clean(
            value.get("message")
            or value.get("summary")
            or value.get("errorSummary")
            or value.get("error_summary")
            or value.get("errorDescription")
            or value.get("error_description")
            or value.get("description")
            or value.get("description_raw")
            or value.get("error_user_msg")
            or value.get("error_user_title")
        )

        extensions = value.get("extensions")
        if not isinstance(extensions, dict):
            extensions = {}

        code = _clean(
            value.get("code")
            or value.get("error")
            or extensions.get("code")
            or extensions.get("error_code")
        )
        subcode = _clean(
            value.get("error_subcode")
            or value.get("subcode")
            or extensions.get("error_subcode")
        )
        error_type = _clean(
            value.get("type")
            or extensions.get("type")
            or extensions.get("classification")
        )

        if not message and not code and not subcode:
            return

        row = {
            "message": message[:1000],
            "code": code[:120],
            "subcode": subcode[:120],
            "type": error_type[:120],
        }
        if row not in output:
            output.append(row)

    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list):
            for item in errors[:20]:
                add_error(item)

        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            add_error(raw_error)
        elif raw_error is not None:
            add_error(
                {
                    "error": raw_error,
                    "message": (
                        payload.get("errorDescription")
                        or payload.get("error_summary")
                        or payload.get("errorSummary")
                    ),
                }
            )

        if any(
            key in payload
            for key in (
                "errorDescription",
                "error_description",
                "errorSummary",
                "error_summary",
                "error_user_msg",
            )
        ):
            add_error(payload)

    elif isinstance(payload, list):
        for item in payload[:20]:
            for row in _graphql_error_details(item):
                if row not in output:
                    output.append(row)

    return output[:10]


def _meta_error_retryable(errors: list[dict[str, Any]]) -> bool:
    text = " ".join(
        " ".join(
            _clean(row.get(key))
            for key in ("message", "code", "subcode", "type")
        )
        for row in errors
        if isinstance(row, dict)
    ).casefold()

    non_retryable = (
        "permission",
        "not allowed",
        "not eligible",
        "restricted",
        "restriction",
        "checkpoint",
        "confirm your",
        "verify your",
        "business limit",
        "maximum",
        "too many business",
        "temporarily blocked",
        "misusing this feature",
    )
    if any(marker in text for marker in non_retryable):
        return False

    retryable = (
        "rate limit",
        "try again",
        "temporarily unavailable",
        "server error",
        "timeout",
        "timed out",
        "please retry",
    )
    return any(marker in text for marker in retryable)


def _extract_created_business_id(payload: Any) -> tuple[str, str]:
    """Extract only IDs from known Meta Business CREATE response shapes."""
    known_paths = (
        ("data", "business_create", "business", "id"),
        ("data", "business_create", "id"),
        ("data", "bizkit_create_business", "business", "id"),
        ("data", "bizkit_create_business", "id"),
        ("data", "business_manager_create", "business", "id"),
        ("data", "business_manager_create", "id"),
    )

    chunks = payload if isinstance(payload, list) else [payload]
    found: list[tuple[str, str]] = []

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        for path in known_paths:
            current: Any = chunk
            valid = True
            for key in path:
                if not isinstance(current, dict) or key not in current:
                    valid = False
                    break
                current = current[key]
            if not valid:
                continue
            candidate = _digits(current)
            if candidate:
                row = (candidate, ".".join(path))
                if row not in found:
                    found.append(row)

    unique_ids = sorted({business_id for business_id, _ in found})
    if len(unique_ids) != 1:
        return "", ""

    business_id = unique_ids[0]
    response_path = next(
        path
        for candidate, path in found
        if candidate == business_id
    )
    return business_id, response_path


def _normalize_ad_account_id(value: Any) -> str:
    raw = _clean(value)
    if raw.lower().startswith("act_"):
        raw = raw[4:]
    if not raw.isdigit() or not (5 <= len(raw) <= 30):
        return ""
    return "act_" + raw


def _extract_created_ad_account_id(payload: Any) -> tuple[str, str]:
    known_nodes = (
        "ad_account_create",
        "business_ad_account_create",
        "bizkit_create_ad_account",
        "create_ad_account",
        "adaccount_create",
    )
    chunks = payload if isinstance(payload, list) else [payload]
    matches: list[tuple[str, str]] = []

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        data = chunk.get("data")
        if not isinstance(data, dict):
            continue

        for node_name in known_nodes:
            node = data.get(node_name)
            if not isinstance(node, dict):
                continue

            direct = _normalize_ad_account_id(
                node.get("id") or node.get("account_id")
            )
            if direct:
                matches.append((direct, f"data.{node_name}.id"))

            for child_name in ("ad_account", "account"):
                child = node.get(child_name)
                if not isinstance(child, dict):
                    continue
                nested = _normalize_ad_account_id(
                    child.get("id") or child.get("account_id")
                )
                if nested:
                    matches.append(
                        (
                            nested,
                            f"data.{node_name}.{child_name}.id",
                        )
                    )

    unique = sorted({account_id for account_id, _ in matches})
    if len(unique) != 1:
        return "", ""

    account_id = unique[0]
    path = next(
        response_path
        for candidate, response_path in matches
        if candidate == account_id
    )
    return account_id, path


def _walk_business_ids(value: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    if isinstance(value, dict):
        for key, child in value.items():
            clean_key = str(key or "").lower()
            child_path = f"{path}.{clean_key}" if path else clean_key

            if clean_key in {"business_id", "businessid"}:
                candidate = _digits(child)
                if candidate:
                    found.append((candidate, child_path))

            if clean_key == "id":
                candidate = _digits(child)
                if candidate and any(
                    marker in path.lower()
                    for marker in (
                        "business",
                        "bizkit_create",
                        "business_creation",
                        "create_business",
                    )
                ):
                    found.append((candidate, child_path))

            found.extend(_walk_business_ids(child, child_path))

    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_business_ids(child, f"{path}[{index}]"))

    return found


def _business_ids_from_text(text: str) -> set[str]:
    raw = str(text or "")
    output: set[str] = set()

    patterns = (
        r"[?&]business_id=(\d{5,30})",
        r"[?&]businessId=(\d{5,30})",
        r"\/businesses\/(\d{5,30})(?:[\/?#\"']|$)",
        r"[\"']business_id[\"']\s*[:=]\s*[\"']?(\d{5,30})",
        r"[\"']businessId[\"']\s*[:=]\s*[\"']?(\d{5,30})",
        r"[\"']selectedBusinessID[\"']\s*[:=]\s*[\"']?(\d{5,30})",
    )
    for pattern in patterns:
        output.update(re.findall(pattern, raw, flags=re.IGNORECASE))

    return {value for value in output if _digits(value)}


class FacebookBusinessBrowser:
    """
    Browser-first Meta Business workflow.

    This class deliberately does NOT reconstruct or submit private GraphQL
    mutations. It drives Meta's own UI and observes the requests/responses
    generated by Meta's frontend.
    """

    ROOT_URL = "https://business.facebook.com/"
    HOME_URL = "https://business.facebook.com/latest/home"
    OVERVIEW_URL = "https://business.facebook.com/overview"
    CREATE_URL = "https://business.facebook.com/reg/"
    DIRECT_CREATE_URL = "https://business.facebook.com/create"
    ADS_MANAGER_URL = "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
    SETTINGS_PAGES_URL = (
        "https://business.facebook.com/settings/pages/?business_id={business_id}"
    )
    SETTINGS_AD_ACCOUNTS_URLS = (
        "https://business.facebook.com/settings/ad-accounts/?business_id={business_id}",
        "https://business.facebook.com/latest/settings/ad_accounts?business_id={business_id}",
        "https://business.facebook.com/latest/settings/ad_accounts/?business_id={business_id}",
    )
    AD_ACCOUNT_SECTION_NAMES = (
        "Ad accounts",
        "Advertising accounts",
        "Рекламные аккаунты",
        "Рекламні акаунти",
        "Werbekonten",
        "Comptes publicitaires",
        "বিজ্ঞাপন অ্যাকাউন্ট",
        "বিজ্ঞাপন অ্যাকাউন্টসমূহ",
        "Tài khoản quảng cáo",
        "विज्ञापन खाते",
        "विज्ञापन खाता",
    )
    AD_ACCOUNT_CREATE_ENTRY_NAMES = (
        "Create a new ad account",
        "Create new ad account",
        "Create ad account",
        "Add a new ad account",
        "Создать новый рекламный аккаунт",
        "Создать рекламный аккаунт",
        "Створити новий рекламний акаунт",
        "Створити рекламний акаунт",
        "Neues Werbekonto erstellen",
        "Werbekonto erstellen",
        "Créer un nouveau compte publicitaire",
        "Créer un compte publicitaire",
        "Nouveau compte publicitaire",
        "নতুন বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন",
        "বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন",
        "Tạo tài khoản quảng cáo mới",
        "Tạo tài khoản quảng cáo",
        "नया विज्ञापन खाता बनाएँ",
        "नया विज्ञापन खाता बनाएं",
        "विज्ञापन खाता बनाएँ",
        "विज्ञापन खाता बनाएं",
    )
    AD_ACCOUNT_SUBMIT_NAMES = (
        "Create ad account",
        "Create",
        "Next",
        "Continue",
        "Создать рекламный аккаунт",
        "Создать",
        "Далее",
        "Продолжить",
        "Створити рекламний акаунт",
        "Створити",
        "Далі",
        "Продовжити",
        "Werbekonto erstellen",
        "Erstellen",
        "Weiter",
        "Créer un compte publicitaire",
        "Créer",
        "Suivant",
        "Continuer",
        "বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন",
        "তৈরি করুন",
        "পরবর্তী",
        "চালিয়ে যান",
        "Tạo tài khoản quảng cáo",
        "Tạo",
        "Tiếp",
        "Tiếp tục",
        "विज्ञापन खाता बनाएँ",
        "विज्ञापन खाता बनाएं",
        "बनाएँ",
        "बनाएं",
        "अगला",
        "आगे",
        "जारी रखें",
    )

    CREATE_NAMES = (
        "Create a business portfolio",
        "Create business portfolio",
        "Create a business",
        "Create business",
        "Create a portfolio",
        "Create portfolio",
        "Create account",
        "Создать бизнес-портфолио",
        "Создать портфолио",
        "Создать бизнес",
        "Создать аккаунт",
        "Створити бізнес-портфоліо",
        "Створити портфоліо",
        "Створити бізнес",
        "Створити обліковий запис",
        "Business-Portfolio erstellen",
        "Unternehmensportfolio erstellen",
        "Business erstellen",
        "Portfolio erstellen",
        "Créer un portefeuille business",
        "Créer un portefeuille professionnel",
        "Créer un portefeuille",
        "Créer une entreprise",
        "Créer un compte",
    )

    SUBMIT_NAMES = (
        "Create",
        "Submit",
        "Continue",
        "Создать",
        "Продолжить",
        "Створити",
        "Продовжити",
        "Erstellen",
        "Senden",
        "Weiter",
        "Créer",
        "Continuer",
        "Envoyer",
        "Valider",
    )

    ADD_NAMES = (
        "Add",
        "Add ad account",
        "Add an ad account",
        "Добавить",
        "Додати",
        "Hinzufügen",
        "Ajouter",
        "Ajouter un compte publicitaire",
        "Ajouter des comptes publicitaires",
        "যোগ করুন",
        "Thêm",
        "जोड़ें",
    )

    ADD_EXISTING_PAGE_NAMES = (
        "Add an existing Facebook Page",
        "Add a Page",
        "Add existing Page",
        "Добавить существующую Страницу Facebook",
        "Добавить Страницу",
        "Додати наявну сторінку Facebook",
        "Додати сторінку",
        "Bestehende Facebook-Seite hinzufügen",
        "Vorhandene Facebook-Seite hinzufügen",
        "Facebook-Seite hinzufügen",
        "Seite hinzufügen",
    )

    def __init__(self, context: Any, *, timeout_seconds: int = 45) -> None:
        self.context = context
        self.timeout_seconds = max(15, int(timeout_seconds))
        self.timeout_ms = self.timeout_seconds * 1000

        self._playwright = None
        self._browser = None
        self._browser_context = None
        self.page = None
        self._semaphore_acquired = False
        self._profile_lock: asyncio.Lock | None = None
        self._profile_lock_acquired = False
        self._last_selector_diagnostic: dict[str, Any] = {}
        self._last_ad_account_section_diagnostic: dict[str, Any] = {}
        self._browser_events: list[dict[str, Any]] = []

    async def __aenter__(self) -> "FacebookBusinessBrowser":
        await self.open()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    @property
    def profile_id(self) -> str:
        return _clean(getattr(self.context, "profile_id", ""))

    async def open(self) -> None:
        if self.page is not None:
            return

        await _BROWSER_SEMAPHORE.acquire()
        self._semaphore_acquired = True

        self._profile_lock = await _get_profile_lock(self.profile_id)
        await self._profile_lock.acquire()
        self._profile_lock_acquired = True

        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            self._release_semaphore()
            raise BrowserBusinessError(
                "BROWSER_UNAVAILABLE",
                "Playwright is unavailable in the worker image.",
                retryable=False,
            ) from exc

        try:
            self._playwright = await async_playwright().start()
            executable_path = _clean(
                os.getenv("REMASK_CHROMIUM_EXECUTABLE") or "/usr/bin/chromium"
            )

            launch_kwargs: dict[str, Any] = {
                "headless": True,
                "executable_path": executable_path,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--disable-sync",
                    "--disable-translate",
                    "--disable-default-apps",
                    "--disable-component-update",
                    "--no-first-run",
                    "--renderer-process-limit=2",
                    "--blink-settings=imagesEnabled=false",
                ],
            }
            proxy = _proxy_config(getattr(self.context, "proxy", None))
            if proxy:
                launch_kwargs["proxy"] = proxy

            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            self._browser_context = await self._browser.new_context(
                user_agent=_clean(getattr(self.context, "user_agent", "")),
                locale="en-US",
                viewport={"width": 1280, "height": 800},
                service_workers="allow",
                reduced_motion="reduce",
            )

            async def block_heavy_resources(route: Any, request: Any) -> None:
                if _clean(getattr(request, "resource_type", "")).lower() in {
                    "image",
                    "media",
                    "font",
                }:
                    await route.abort()
                    return
                await route.continue_()

            await self._browser_context.route("**/*", block_heavy_resources)

            raw_cookies = getattr(self.context, "cookies", {}) or {}
            cookies: list[dict[str, Any]] = []

            if isinstance(raw_cookies, dict):
                for name, value in raw_cookies.items():
                    clean_name = _clean(name)
                    clean_value = str(value or "")
                    if not clean_name or not clean_value:
                        continue
                    cookies.append(
                        {
                            "name": clean_name,
                            "value": clean_value,
                            "domain": ".facebook.com",
                            "path": "/",
                            "secure": True,
                            "sameSite": "Lax",
                        }
                    )
            elif isinstance(raw_cookies, list):
                for row in raw_cookies:
                    if not isinstance(row, dict):
                        continue
                    clean_name = _clean(row.get("name"))
                    clean_value = str(row.get("value") or "")
                    if not clean_name or not clean_value:
                        continue
                    item = {
                        "name": clean_name,
                        "value": clean_value,
                        "domain": _clean(row.get("domain")) or ".facebook.com",
                        "path": _clean(row.get("path")) or "/",
                    }
                    for key in ("expires", "httpOnly", "secure", "sameSite"):
                        if key in row and row.get(key) is not None:
                            item[key] = row.get(key)
                    cookies.append(item)

            if not cookies:
                raise BrowserBusinessError(
                    "SESSION_COOKIES_MISSING",
                    "Facebook browser session has no cookies.",
                    retryable=False,
                )

            await self._browser_context.add_cookies(cookies)
            self.page = await self._browser_context.new_page()
            self.page.set_default_timeout(self.timeout_ms)

            def record_browser_event(kind: str, *, url: str = "", detail: str = "") -> None:
                clean_url = ""
                if url:
                    try:
                        parsed = urlsplit(url)
                        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:500]
                    except Exception:
                        clean_url = _clean(url).split("?", 1)[0][:500]
                self._browser_events.append(
                    {
                        "kind": _clean(kind)[:80],
                        "url": clean_url,
                        "detail": _clean(detail)[:500],
                    }
                )
                if len(self._browser_events) > 40:
                    del self._browser_events[:-40]

            def on_request_failed(request: Any) -> None:
                try:
                    resource_type = _clean(getattr(request, "resource_type", ""))
                    if resource_type in {"image", "media", "font"}:
                        return
                    record_browser_event(
                        "request_failed",
                        url=_clean(getattr(request, "url", "")),
                        detail=f"{resource_type}: {_clean(getattr(request, 'failure', ''))}",
                    )
                except Exception:
                    pass

            def on_page_error(error: Any) -> None:
                try:
                    record_browser_event(
                        "page_error",
                        detail=f"{error.__class__.__name__}: {error}",
                    )
                except Exception:
                    pass

            self.page.on("requestfailed", on_request_failed)
            self.page.on("pageerror", on_page_error)

        except BrowserBusinessError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise BrowserBusinessError(
                "BROWSER_START_FAILED",
                f"Profile-bound Chromium failed to start: {exc.__class__.__name__}: {exc}",
                retryable=True,
            ) from exc

    def _release_semaphore(self) -> None:
        if self._semaphore_acquired:
            self._semaphore_acquired = False
            _BROWSER_SEMAPHORE.release()

    async def close(self) -> None:
        async def bounded_cleanup(awaitable: Any, *, timeout: float) -> None:
            try:
                await asyncio.wait_for(awaitable, timeout=timeout)
            except BaseException:
                # Cleanup must never wedge the worker. A crashed Meta renderer
                # can make Playwright close calls stall; the next, broader
                # cleanup level still gets a chance to terminate Chromium.
                pass

        page = self.page
        self.page = None

        browser_context = self._browser_context
        self._browser_context = None

        browser = self._browser
        self._browser = None

        playwright = self._playwright
        self._playwright = None

        if page is not None:
            await bounded_cleanup(page.close(), timeout=1.5)

        if browser_context is not None:
            await bounded_cleanup(browser_context.close(), timeout=2.0)

        if browser is not None:
            await bounded_cleanup(browser.close(), timeout=4.0)

        if playwright is not None:
            await bounded_cleanup(playwright.stop(), timeout=2.0)

        if self._profile_lock_acquired and self._profile_lock is not None:
            self._profile_lock.release()
            self._profile_lock_acquired = False
        self._profile_lock = None

        self._release_semaphore()

    async def _goto(self, url: str) -> str:
        if self.page is None:
            await self.open()

        for attempt in range(2):
            try:
                await self.page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                await self.page.wait_for_timeout(900)
                await self._assert_authenticated()
                return _clean(self.page.url)

            except BrowserBusinessError:
                # Preserve meaningful account/session errors such as
                # SESSION_EXPIRED and CHECKPOINT_REQUIRED.
                raise

            except Exception as exc:
                text = f"{exc.__class__.__name__}: {exc}"
                lower = text.lower()

                interrupted_navigation = (
                    "net::err_aborted" in lower
                    or "frame was detached" in lower
                    or "navigation interrupted" in lower
                )
                if interrupted_navigation:
                    # Meta Business Suite is an SPA and may replace/detach the
                    # document during navigation. Playwright can surface that
                    # as ERR_ABORTED even though Meta completed the transition.
                    # No irreversible action happens in _goto(), so a single
                    # navigation retry is safe if the replacement DOM is not
                    # ready yet.
                    try:
                        await asyncio.sleep(0.45)
                        await self._assert_authenticated()
                        current_url = _clean(self.page.url)
                        current_body = await self._body_text()
                        form_ready = await self._form_ready()
                        create_surface = await self._has_create_surface()
                        facebook_surface = (
                            current_url
                            and current_url != "about:blank"
                            and "facebook.com" in current_url.lower()
                        )
                        # For read-only navigation, an authenticated Meta/Facebook
                        # URL is sufficient evidence that ERR_ABORTED came from
                        # an SPA redirect/frame replacement rather than a failed
                        # login. The caller will still verify the expected form
                        # or create surface separately.
                        if facebook_surface:
                            return current_url
                    except BrowserBusinessError:
                        raise
                    except Exception:
                        pass

                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue

                page_crashed = (
                    "page crashed" in lower
                    or "targetclosederror" in lower
                    or "target page, context or browser has been closed" in lower
                )

                if page_crashed and attempt == 0:
                    # Safe at navigation boundaries: release the crashed
                    # Chromium process and open a fresh low-memory context.
                    await self.close()
                    await asyncio.sleep(0.25)
                    await self.open()
                    continue

                try:
                    await self._diagnostic("navigation_error")
                except Exception:
                    pass
                raise BrowserBusinessError(
                    "FACEBOOK_NAVIGATION_FAILED",
                    f"Facebook navigation failed: {text}",
                    retryable=True,
                ) from exc

        raise BrowserBusinessError(
            "FACEBOOK_NAVIGATION_FAILED",
            "Facebook navigation failed after Chromium relaunch.",
            retryable=True,
        )

    async def _body_text(self) -> str:
        if self.page is None:
            return ""
        try:
            return str(await self.page.locator("body").inner_text(timeout=1500) or "")
        except Exception:
            return ""

    async def _assert_authenticated(self) -> None:
        if self.page is None:
            raise BrowserBusinessError(
                "BROWSER_NOT_OPEN",
                "Facebook browser page is not open.",
                retryable=False,
            )

        url = _clean(self.page.url)
        lower_url = url.lower()
        body = (await self._body_text()).lower()

        if "/login" in lower_url or "login.php" in lower_url:
            await self._diagnostic("login")
            raise BrowserBusinessError(
                "SESSION_EXPIRED",
                "Facebook redirected the profile to login.",
                retryable=False,
                diagnostic={"url": url},
            )

        if "/checkpoint" in lower_url or "checkpoint" in body[:4000]:
            await self._diagnostic("checkpoint")
            raise BrowserBusinessError(
                "CHECKPOINT_REQUIRED",
                "Facebook requires a checkpoint for this profile.",
                retryable=False,
                diagnostic={"url": url},
            )

        if (
            "two-factor authentication" in body
            or "enter security code" in body
            or "authentication code" in body
        ):
            await self._diagnostic("two_factor")
            raise BrowserBusinessError(
                "TWO_FACTOR_REQUIRED",
                "Facebook requires two-factor authentication.",
                retryable=False,
                diagnostic={"url": url},
            )

        if (
            "temporarily blocked" in body
            or "temporarily restricted" in body
            or "you’re temporarily blocked" in body
            or "you're temporarily blocked" in body
        ):
            await self._diagnostic("temporarily_blocked")
            raise BrowserBusinessError(
                "FACEBOOK_TEMPORARILY_BLOCKED",
                "Facebook temporarily blocked this profile action.",
                retryable=False,
                diagnostic={"url": url},
            )

    async def _diagnostic(self, stage: str) -> dict[str, Any]:
        if self.page is None:
            return {}

        result = {
            "stage": _clean(stage),
            "url": _clean(self.page.url),
        }

        # Always capture a small, non-file diagnostic surface. Production
        # normally keeps REMASK_BM_DIAGNOSTICS disabled, so returning only
        # stage+URL made live Meta UI failures impossible to distinguish.
        try:
            result["title"] = _clean(await self.page.title())[:300]
        except Exception:
            pass

        try:
            viewport = await self.page.evaluate(
                """() => ({
                    width: window.innerWidth,
                    height: window.innerHeight,
                    dpr: window.devicePixelRatio || 1
                })"""
            )
            if isinstance(viewport, dict):
                result["viewport"] = viewport
        except Exception:
            pass

        try:
            dom_state = await asyncio.wait_for(
                self.page.evaluate(
                    """() => ({
                        readyState: document.readyState,
                        bodyChildren: document.body ? document.body.children.length : -1,
                        bodyTextLength: document.body ? (document.body.innerText || '').length : -1,
                        scripts: document.scripts ? document.scripts.length : -1,
                        inputs: document.querySelectorAll('input').length,
                        interactive: document.querySelectorAll(
                            'button, [role="button"], [role="menuitem"], [aria-haspopup], a[href]'
                        ).length
                    })"""
                ),
                timeout=1.5,
            )
            if isinstance(dom_state, dict):
                result["dom_state"] = dom_state
        except Exception:
            pass

        if self._browser_events:
            result["browser_events"] = self._browser_events[-20:]

        try:
            body = " ".join((await self._body_text()).split())
            if body:
                result["body_excerpt"] = body[:3500]
        except Exception:
            pass

        try:
            lightweight = await self.page.locator(
                'button, [role="button"], [role="menuitem"], '
                '[aria-haspopup], [aria-expanded], a[href]'
            ).evaluate_all(
                """els => els.slice(0, 260).map(el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    if (!(r.width > 0 && r.height > 0)
                        || s.display === 'none'
                        || s.visibility === 'hidden') {
                        return null;
                    }
                    return {
                        tag: el.tagName,
                        role: el.getAttribute('role') || '',
                        text: (el.innerText || el.textContent || '')
                            .replace(/\\s+/g, ' ').trim().slice(0, 180),
                        aria: (el.getAttribute('aria-label') || '').slice(0, 180),
                        title: (el.getAttribute('title') || '').slice(0, 180),
                        haspopup: el.getAttribute('aria-haspopup') || '',
                        expanded: el.getAttribute('aria-expanded') || '',
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        w: Math.round(r.width),
                        h: Math.round(r.height)
                    };
                }).filter(Boolean)"""
            )
            if isinstance(lightweight, list):
                result["visible_controls"] = lightweight[:60]
                result["top_left_controls"] = [
                    row
                    for row in lightweight
                    if int(row.get("x") or 0) < 620
                    and int(row.get("y") or 0) < 420
                ][:60]
        except Exception:
            pass

        if _clean(os.getenv("REMASK_BM_DIAGNOSTICS")) not in {"1", "true", "yes"}:
            return result

        base = _clean(os.getenv("REMASK_BM_DIAG_DIR"))
        if not base:
            volume = _clean(os.getenv("RAILWAY_VOLUME_MOUNT_PATH"))
            base = f"{volume}/bm-diagnostics" if volume else "/tmp/remask-bm-diagnostics"

        safe_profile = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.profile_id or "profile")
        safe_stage = re.sub(r"[^A-Za-z0-9_.-]+", "_", stage or "stage")
        stamp = str(int(time.time() * 1000))
        root = Path(base) / safe_profile
        root.mkdir(parents=True, exist_ok=True)

        try:
            screenshot = root / f"{stamp}-{safe_stage}.png"
            await self.page.screenshot(path=str(screenshot), full_page=True)
            result["screenshot"] = str(screenshot)
        except Exception:
            pass

        try:
            text_path = root / f"{stamp}-{safe_stage}.txt"
            body = await self._body_text()
            text_path.write_text(
                f"URL: {self.page.url}\n\n{body[:50000]}",
                encoding="utf-8",
            )
            result["text"] = str(text_path)
        except Exception:
            pass

        try:
            controls = await self.page.locator(
                'button, [role="button"], [role="menuitem"], a[href]'
            ).evaluate_all(
                """els => els.slice(0, 220).map(el => {
                    const r = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return {
                        tag: el.tagName,
                        role: el.getAttribute("role") || "",
                        text: (el.innerText || el.textContent || "").trim().slice(0, 180),
                        aria: (el.getAttribute("aria-label") || "").slice(0, 180),
                        title: (el.getAttribute("title") || "").slice(0, 180),
                        haspopup: el.getAttribute("aria-haspopup") || "",
                        expanded: el.getAttribute("aria-expanded") || "",
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        w: Math.round(r.width),
                        h: Math.round(r.height),
                        visible: r.width > 0 && r.height > 0 && style.visibility !== "hidden" && style.display !== "none"
                    };
                }).filter(row => row.visible)"""
            )
            result["controls"] = controls[:80]
            result["top_left_controls"] = sorted(
                [
                    row for row in controls
                    if int(row.get("x") or 0) < 560
                    and int(row.get("y") or 0) < 360
                ],
                key=lambda row: (
                    int(row.get("y") or 0),
                    int(row.get("x") or 0),
                ),
            )[:80]
        except Exception:
            pass

        try:
            top_left = await self.page.locator(
                '[role], [aria-label], [tabindex], button, a[href]'
            ).evaluate_all(
                """els => els.map(el => {
                    const r = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    if (!(r.width > 0 && r.height > 0) || style.visibility === "hidden" || style.display === "none") return null;
                    if (r.x >= 560 || r.y >= 360) return null;
                    return {
                        tag: el.tagName,
                        role: el.getAttribute("role") || "",
                        text: (el.innerText || el.textContent || "").trim().slice(0, 220),
                        aria: (el.getAttribute("aria-label") || "").slice(0, 220),
                        title: (el.getAttribute("title") || "").slice(0, 220),
                        tabindex: el.getAttribute("tabindex") || "",
                        haspopup: el.getAttribute("aria-haspopup") || "",
                        expanded: el.getAttribute("aria-expanded") || "",
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        w: Math.round(r.width),
                        h: Math.round(r.height)
                    };
                }).filter(Boolean).sort((a,b) => (a.y-b.y) || (a.x-b.x)).slice(0, 140)"""
            )
            result["top_left_surface"] = top_left
        except Exception:
            pass

        return result

    async def _quick_surface_state(self) -> dict[str, Any]:
        if self.page is None:
            return {"blank": True}

        try:
            state = await asyncio.wait_for(
                self.page.evaluate(
                    """() => {
                        const bodyText = document.body ? (document.body.innerText || '') : '';
                        const interactive = document.querySelectorAll(
                            'button, [role="button"], [role="menuitem"], [aria-haspopup], a[href], input'
                        ).length;
                        return {
                            ready_state: document.readyState,
                            body_text_length: bodyText.trim().length,
                            interactive_count: interactive,
                            body_children: document.body ? document.body.children.length : 0,
                            scripts: document.scripts ? document.scripts.length : 0
                        };
                    }"""
                ),
                timeout=1.5,
            )
        except Exception:
            return {"blank": False, "probe_failed": True}

        if not isinstance(state, dict):
            return {"blank": False, "probe_failed": True}

        body_len = int(state.get("body_text_length") or 0)
        interactive = int(state.get("interactive_count") or 0)
        children = int(state.get("body_children") or 0)
        state["blank"] = bool(body_len < 20 and interactive == 0 and children <= 3)
        return state


    async def _has_create_surface(self) -> bool:
        if self.page is None:
            return False

        for name in self.CREATE_NAMES:
            pattern = re.compile(re.escape(name), re.IGNORECASE)
            for role in ("button", "link", "menuitem"):
                try:
                    locator = self.page.get_by_role(role, name=pattern)
                    if await locator.count() and await locator.first.is_visible():
                        return True
                except Exception:
                    continue

        # Meta Business Suite keeps the portfolio menu mounted in the SPA
        # and, on the live profile canary, the CREATE entry was discoverable
        # from rendered body text before the role locator became stable. Keep
        # this mounted-surface fallback; the actual click path still requires
        # a visible/enabled control in _click_named().
        body = (await self._body_text()).lower()
        return any(name.lower() in body for name in self.CREATE_NAMES)

    async def _form_ready(self) -> bool:
        if self.page is None:
            return False

        # Never infer the CREATE form from a raw input count. The normal Meta
        # Business Suite home page can expose four unrelated inputs (search,
        # onboarding, connection widgets, etc.), which caused profile 4 to be
        # misclassified as CREATE_FORM_READY and then fail at submit.
        body = (await self._body_text()).casefold()

        email_markers = (
            "business email",
            "business email address",
            "рабочий электронный адрес",
            "электронный адрес компании",
            "робоча електронна адреса",
            "електронна адреса компанії",
            "geschäftliche e-mail-adresse",
            "geschäftliche email-adresse",
            "geschäftliche e-mail",
            "adresse e-mail professionnelle",
            "adresse e-mail de l’entreprise",
            "adresse e-mail de l'entreprise",
            "e-mail professionnel",
        )
        name_markers = (
            "business portfolio name",
            "business name",
            "business and account name",
            "название бизнес-портфолио",
            "название компании",
            "назва бізнес-портфоліо",
            "назва компанії",
            "business-portfolio-name",
            "name des business-portfolios",
            "unternehmensname",
            "nom du portefeuille business",
            "nom du portefeuille professionnel",
            "nom de l’entreprise",
            "nom de l'entreprise",
        )

        has_email = any(marker in body for marker in email_markers)
        has_name = any(marker in body for marker in name_markers)
        if has_email and has_name:
            return True

        # Secondary semantic fallback for A/B variants where labels are
        # attached only to input attributes and are absent from body.innerText.
        semantic_email = False
        semantic_name = False
        try:
            inputs = self.page.locator("input:visible")
            count = min(await inputs.count(), 16)
            for index in range(count):
                item = inputs.nth(index)
                input_type = _clean(await item.get_attribute("type")).casefold()
                key = " ".join(
                    _clean(await item.get_attribute(attr))
                    for attr in (
                        "name",
                        "id",
                        "placeholder",
                        "aria-label",
                        "autocomplete",
                    )
                ).casefold()

                if (
                    input_type == "email"
                    and any(
                        token in key
                        for token in (
                            "business",
                            "profession",
                            "entreprise",
                            "company",
                            "geschäft",
                        )
                    )
                ):
                    semantic_email = True

                if any(
                    token in key
                    for token in (
                        "business_name",
                        "business name",
                        "portfolio",
                        "portefeuille",
                        "entreprise",
                        "unternehmensname",
                    )
                ):
                    semantic_name = True
        except Exception:
            pass

        return semantic_email and semantic_name

    async def _wait_for_create_surface(
        self,
        *,
        timeout_ms: int = 3000,
        interval_ms: int = 250,
    ) -> bool:
        deadline = time.monotonic() + max(0.25, timeout_ms / 1000)
        while time.monotonic() < deadline:
            if await self._has_create_surface():
                return True
            await self.page.wait_for_timeout(interval_ms)
        return await self._has_create_surface()

    async def _wait_for_form_ready(
        self,
        *,
        timeout_ms: int = 3500,
        interval_ms: int = 250,
    ) -> bool:
        deadline = time.monotonic() + max(0.25, timeout_ms / 1000)
        while time.monotonic() < deadline:
            if await self._form_ready():
                return True
            await self.page.wait_for_timeout(interval_ms)
        return await self._form_ready()

    async def _try_open_known_asset_selector(self) -> bool:
        """
        Open Meta's top-left account/business selector by anchoring on a Page
        that ReMask already knows belongs to this FB profile.

        This specifically handles Business Suite sessions that are pinned to a
        Page via ?asset_id=... and therefore redirect /reg/ back to Home.
        """
        if self.page is None:
            return False

        raw_pages = getattr(self.context, "pages", None) or []
        known_pages: list[tuple[str, str]] = []
        for row in raw_pages:
            if not isinstance(row, dict):
                continue
            page_id = _digits(row.get("id"))
            page_name = _clean(row.get("name") or row.get("title"))
            if page_id and page_name:
                known_pages.append((page_id, page_name))

        if not known_pages:
            return False

        current_asset = ""
        try:
            query = parse_qs(urlsplit(_clean(self.page.url)).query)
            current_asset = _digits(
                (query.get("asset_id") or query.get("assetId") or [""])[0]
            )
        except Exception:
            current_asset = ""

        # This targeted path is deliberately limited to the live failure
        # mode we observed: Meta redirected Business Suite into a known Page
        # context via ?asset_id=<PageID>. Other profiles keep the generic,
        # already-canary-tested selector path below.
        if not current_asset:
            return False

        pinned_pages = [
            row for row in known_pages
            if row[0] == current_asset
        ]
        if not pinned_pages:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "known_asset_selector": {
                    "current_asset_id": current_asset,
                    "matched_known_page": False,
                    "known_pages": [
                        {"id": page_id, "name": page_name}
                        for page_id, page_name in known_pages[:12]
                    ],
                },
            }
            return False

        known_pages = pinned_pages

        diagnostic_rows: list[dict[str, Any]] = []

        async def try_click_candidate(candidate: Any, *, label: str) -> bool:
            try:
                if not await candidate.is_visible():
                    return False

                target = candidate
                try:
                    role = _clean(await candidate.get_attribute("role")).lower()
                    tag = _clean(
                        await candidate.evaluate("(e) => e.tagName || ''")
                    ).upper()
                    has_popup = _clean(
                        await candidate.get_attribute("aria-haspopup")
                    )
                    if (
                        role not in {"button", "menuitem", "link"}
                        and tag not in {"BUTTON", "A"}
                        and not has_popup
                    ):
                        ancestor = candidate.locator(
                            'xpath=ancestor-or-self::*['
                            '@role="button" or @role="menuitem" or '
                            '@aria-haspopup or self::button or self::a'
                            '][1]'
                        )
                        if await ancestor.count():
                            target = ancestor.first
                except Exception:
                    target = candidate

                box = await target.bounding_box()
                if not box:
                    return False

                x = float(box.get("x") or 0)
                y = float(box.get("y") or 0)
                w = float(box.get("width") or 0)
                h = float(box.get("height") or 0)

                diagnostic_rows.append(
                    {
                        "label": label[:180],
                        "x": round(x),
                        "y": round(y),
                        "w": round(w),
                        "h": round(h),
                    }
                )

                # Keep the click constrained to the Business Suite top-left
                # selector zone so a same-named Page in the main content is
                # never treated as the account selector.
                if x > 330 or y > 340 or w <= 0 or h <= 0:
                    return False

                await target.click(timeout=3000)
                if await self._wait_for_create_surface(
                    timeout_ms=6500,
                    interval_ms=250,
                ):
                    self._last_selector_diagnostic = {
                        "known_asset_selector": {
                            "current_asset_id": current_asset,
                            "matched_label": label,
                            "candidates": diagnostic_rows[-12:],
                        }
                    }
                    return True

                try:
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(120)
                except Exception:
                    pass
            except Exception as exc:
                diagnostic_rows.append(
                    {
                        "label": label[:180],
                        "error": f"{exc.__class__.__name__}: {exc}"[:500],
                    }
                )
            return False

        for page_id, page_name in known_pages:
            # Role-based search first: most Meta selector variants expose the
            # current Page as an accessible button/menu item.
            name_pattern = re.compile(
                re.escape(page_name),
                re.IGNORECASE,
            )
            for role in ("button", "menuitem", "link"):
                try:
                    locator = self.page.get_by_role(role, name=name_pattern)
                    count = min(await locator.count(), 8)
                except Exception:
                    count = 0

                for index in range(count):
                    if await try_click_candidate(
                        locator.nth(index),
                        label=f"{page_name} [{page_id}]/{role}",
                    ):
                        return True

            # Some A/B variants render the Page name in a nested DIV/span and
            # put the click handler on an ancestor.
            try:
                text_nodes = self.page.get_by_text(
                    name_pattern,
                    exact=False,
                )
                count = min(await text_nodes.count(), 12)
            except Exception:
                count = 0

            for index in range(count):
                if await try_click_candidate(
                    text_nodes.nth(index),
                    label=f"{page_name} [{page_id}]/text",
                ):
                    return True

        # Last-resort bounded probe for Meta A/B variants where the current
        # Page selector has no accessible name at all. Live profile-4 evidence
        # showed an authenticated Page-pinned shell with zero name candidates,
        # so probe only interactive controls in the upper-left region and rank
        # them by asset-id/page-name/popup evidence before clicking.
        geometry_rows: list[dict[str, Any]] = []
        try:
            geometry_rows = await self.page.evaluate(
                """(args) => {
                    const assetId = String(args.assetId || '');
                    const pageNames = (args.pageNames || [])
                        .map(v => String(v || '').trim().toLowerCase())
                        .filter(Boolean);
                    const els = Array.from(document.querySelectorAll(
                        'button,[role="button"],[role="menuitem"],[aria-haspopup],'
                        + '[aria-expanded],a[href],[data-asset-id],[data-asset_id]'
                    )).slice(0, 700);
                    const rows = [];
                    for (const el of els) {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        if (!(r.width > 0 && r.height > 0)
                            || s.display === 'none'
                            || s.visibility === 'hidden'
                            || s.pointerEvents === 'none') continue;
                        if (r.x > 520 || r.y > 520 || r.right < 0 || r.bottom < 0) continue;

                        const aria = (el.getAttribute('aria-label') || '').trim();
                        const title = (el.getAttribute('title') || '').trim();
                        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                        const href = (el.getAttribute('href') || '').trim();
                        const role = (el.getAttribute('role') || '').trim();
                        const haspopup = (el.getAttribute('aria-haspopup') || '').trim();
                        const expanded = (el.getAttribute('aria-expanded') || '').trim();
                        const dataAsset = (
                            el.getAttribute('data-asset-id')
                            || el.getAttribute('data-asset_id')
                            || ''
                        ).trim();

                        const label = [aria, title, text].filter(Boolean).join(' | ').slice(0, 320);
                        const key = label.toLowerCase();
                        const assetMatch = !!assetId && (
                            href.includes(assetId)
                            || dataAsset === assetId
                            || key.includes(assetId)
                        );
                        const nameMatch = pageNames.some(name => key.includes(name));

                        let score = 0;
                        if (assetMatch) score += 500;
                        if (nameMatch) score += 320;
                        if (haspopup) score += 180;
                        if (role === 'button' || role === 'menuitem') score += 90;
                        if (expanded) score += 40;
                        if (r.x <= 360) score += 60;
                        if (r.y <= 260) score += 40;

                        if (!assetMatch && !nameMatch && /^(home|inbox|ads|content|planner|insights|notifications|settings)$/i.test(text)) {
                            score -= 300;
                        }
                        if (score < 180) continue;

                        rows.push({
                            score,
                            x: Math.round(r.x + r.width / 2),
                            y: Math.round(r.y + r.height / 2),
                            left: Math.round(r.x),
                            top: Math.round(r.y),
                            w: Math.round(r.width),
                            h: Math.round(r.height),
                            label,
                            href: href.slice(0, 320),
                            role,
                            haspopup,
                            expanded,
                            assetMatch,
                            nameMatch
                        });
                    }
                    rows.sort((a, b) => b.score - a.score || a.top - b.top || a.left - b.left);
                    return rows.slice(0, 24);
                }""",
                {
                    "assetId": current_asset,
                    "pageNames": [name for _, name in known_pages],
                },
            )
        except Exception as exc:
            geometry_rows = [{
                "error": f"{exc.__class__.__name__}: {exc}"[:500],
            }]

        if isinstance(geometry_rows, list):
            for row in geometry_rows[:6]:
                if not isinstance(row, dict):
                    continue
                x = row.get("x")
                y = row.get("y")
                if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                    continue
                try:
                    await self.page.mouse.click(float(x), float(y))
                    if await self._wait_for_form_ready(
                        timeout_ms=1200,
                        interval_ms=200,
                    ):
                        self._last_selector_diagnostic = {
                            "known_asset_selector": {
                                "current_asset_id": current_asset,
                                "geometry_match": row,
                                "geometry_candidates": geometry_rows[:12],
                            }
                        }
                        return True
                    if await self._wait_for_create_surface(
                        timeout_ms=4200,
                        interval_ms=250,
                    ):
                        self._last_selector_diagnostic = {
                            "known_asset_selector": {
                                "current_asset_id": current_asset,
                                "geometry_match": row,
                                "geometry_candidates": geometry_rows[:12],
                            }
                        }
                        return True
                    try:
                        await self.page.keyboard.press("Escape")
                        await self.page.wait_for_timeout(120)
                    except Exception:
                        pass
                except Exception as exc:
                    row["click_error"] = f"{exc.__class__.__name__}: {exc}"[:500]
                    try:
                        await self.page.keyboard.press("Escape")
                    except Exception:
                        pass

        self._last_selector_diagnostic = {
            "known_asset_selector": {
                "current_asset_id": current_asset,
                "known_pages": [
                    {"id": page_id, "name": page_name}
                    for page_id, page_name in known_pages[:12]
                ],
                "candidates": diagnostic_rows[-20:],
                "geometry_candidates": geometry_rows[:20]
                if isinstance(geometry_rows, list)
                else [],
            }
        }
        return False

    async def _try_open_top_left_portfolio_menu(
        self,
        *,
        skip_known_asset: bool = False,
    ) -> bool:
        if self.page is None:
            return False

        if await self._has_create_surface():
            return True

        # Meta can pin Business Suite to a Page via ?asset_id=... and then
        # redirect direct /reg/ navigation back to Home. In that state, use
        # the Page already known in ProfileContext as the selector anchor.
        if not skip_known_asset and await self._try_open_known_asset_selector():
            return True

        # Meta serves at least two Business Suite sidebar variants.
        # In one, "Meta Business Suite" is a narrow collapse/expand control;
        # in another, the same visible label is itself the portfolio selector.
        # Treat both as read-only candidates and trust only the observed result:
        # if clicking it exposes a Create action, keep it; otherwise Escape and
        # continue to the geometry/structured fallbacks below.
        try:
            role_buttons = self.page.locator('[role="button"]')
            count = min(await role_buttons.count(), 180)
            for index in range(count):
                item = role_buttons.nth(index)
                if not await item.is_visible():
                    continue
                text_value = _clean(await item.inner_text(timeout=1000))
                if text_value.casefold() != "meta business suite":
                    continue
                box = await item.bounding_box()
                if not box:
                    continue

                width = float(box.get("width") or 0)
                x = float(box.get("x") or 0)
                y = float(box.get("y") or 0)
                top_left_business_control = (
                    x <= 40
                    and y <= 140
                    and 0 < width <= 240
                )
                if not top_left_business_control:
                    continue

                try:
                    await item.click(timeout=3000)
                    if await self._wait_for_create_surface(
                        timeout_ms=2200,
                        interval_ms=200,
                    ):
                        return True
                finally:
                    # Clicking the wrong A/B variant is harmless navigation/UI
                    # state only. Close any menu/popover before trying another
                    # candidate path.
                    try:
                        await self.page.keyboard.press("Escape")
                        await self.page.wait_for_timeout(120)
                    except Exception:
                        pass
                break
        except Exception:
            # Read-only candidate probing must never make Add BM fail by itself.
            pass

        # Current Meta Business Suite places the business/page selector BELOW
        # the Meta Business Suite logo and ABOVE Home/Startseite. Do not walk
        # the complete SPA DOM here: the live Railway canary showed that a
        # querySelectorAll("*") geometry scan can destabilize Chromium. Probe
        # only a small grid inside that narrow sidebar band.
        try:
            probe = await self.page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el || el === document.body || el === document.documentElement) {
                            return false;
                        }
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const label = (el) => [
                        (el.getAttribute && el.getAttribute('aria-label')) || '',
                        (el.getAttribute && el.getAttribute('title')) || '',
                        el.innerText || el.textContent || ''
                    ].join(' ').replace(/\\s+/g, ' ').trim();

                    const xs = [16, 36, 60, 88, 120, 156, 192, 228, 264, 300, 328];
                    const ys = [58, 72, 86, 100, 114, 128, 142, 156, 170, 184, 198, 214, 230, 246, 264, 282, 300, 318];
                    const seen = new Set();
                    const rows = [];

                    for (const y of ys) {
                        for (const x of xs) {
                            const stack = document.elementsFromPoint(x, y) || [];
                            for (const el of stack.slice(0, 12)) {
                                if (seen.has(el) || !visible(el)) continue;
                                seen.add(el);

                                const r = el.getBoundingClientRect();
                                const text = label(el);
                                const role = (el.getAttribute && el.getAttribute('role')) || '';
                                const tabindex = (el.getAttribute && el.getAttribute('tabindex')) || '';
                                const haspopup = (el.getAttribute && el.getAttribute('aria-haspopup')) || '';
                                const tag = el.tagName || '';
                                const interactive = (
                                    role === 'button' ||
                                    role === 'menuitem' ||
                                    tag === 'BUTTON' ||
                                    tag === 'A' ||
                                    tabindex === '0' ||
                                    !!haspopup
                                );

                                if (r.x > 360 || r.y < 48 || r.y > 340) continue;
                                if (r.width < 18 || r.width > 340) continue;
                                if (r.height < 18 || r.height > 110) continue;
                                if (/^Meta Business Suite$/i.test(text)) continue;
                                if (/^(Home|Accueil|Startseite|Start|Главная|Головна)$/i.test(text)) continue;
                                if (!text && !interactive) continue;

                                rows.push({el, r, text, role, tabindex, haspopup, tag, interactive});
                            }
                        }
                    }

                    const score = (row) => {
                        const key = (row.text || '').toLowerCase();
                        let value = Math.round(row.r.y);
                        if (row.interactive) value -= 160;
                        if (row.haspopup) value -= 180;
                        if (/business|portfolio|account|asset|switch|select|page|profile|entreprise|portefeuille|compte|actif|changer|sélection|selection|profil/.test(key)) value -= 140;
                        if (!row.text) value += 35;
                        return value;
                    };

                    rows.sort((a,b) => {
                        const as = score(a);
                        const bs = score(b);
                        if (as !== bs) return as - bs;
                        const ai = a.r.width * a.r.height;
                        const bi = b.r.width * b.r.height;
                        return bi - ai || a.r.y - b.r.y;
                    });

                    const compact = rows.slice(0,16).map(row => ({
                        text:row.text,
                        role:row.role,
                        tag:row.tag,
                        haspopup:row.haspopup,
                        x:Math.round(row.r.x),
                        y:Math.round(row.r.y),
                        w:Math.round(row.r.width),
                        h:Math.round(row.r.height)
                    }));
                    const best = rows[0];

                    if (!best) {
                        return {clicked:false, candidates:compact};
                    }

                    best.el.click();
                    return {
                        clicked:true,
                        clickedCandidate:compact[0],
                        candidates:compact
                    };
                }"""
            )
            if isinstance(probe, dict):
                # Always preserve the compact band probe. If no candidate was
                # clickable this is the only useful evidence of what Meta
                # actually rendered between the logo and Home.
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "sidebar_probe": probe,
                }

                if probe.get("clicked"):
                    if await self._wait_for_create_surface():
                        return True

                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "sidebar_probe": probe,
                        "sidebar_open_without_create": {
                            "url": _clean(self.page.url),
                            "surface": await self._quick_surface_state(),
                        },
                    }
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(120)
        except Exception as exc:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "sidebar_probe_error": f"{exc.__class__.__name__}: {exc}",
            }
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                pass

        # Structured-menu fallbacks for other Business Suite variants.
        selectors = (
            'button[aria-haspopup="menu"]',
            '[role="button"][aria-haspopup="menu"]',
            'button[aria-expanded]',
            '[role="button"][aria-expanded]',
            '[role="button"]',
            'button',
            '[tabindex="0"]',
        )
        candidates: list[tuple[int, float, float, Any]] = []

        for selector in selectors:
            try:
                locator = self.page.locator(selector)
                count = min(await locator.count(), 60)
            except Exception:
                continue

            for index in range(count):
                item = locator.nth(index)
                try:
                    if not await item.is_visible():
                        continue
                    box = await item.bounding_box()
                    if not box:
                        continue

                    x = float(box.get("x") or 0)
                    y = float(box.get("y") or 0)
                    if x > 340 or y < 55 or y > 325:
                        continue

                    text_value = _clean(await item.inner_text(timeout=1000))
                    aria = _clean(await item.get_attribute("aria-label"))
                    title = _clean(await item.get_attribute("title"))
                    key = " ".join((text_value, aria, title)).lower()

                    # Explicitly reject the logo control discovered by canary.
                    if text_value.casefold() == "meta business suite":
                        continue

                    score = 0
                    if any(
                        token in key
                        for token in (
                            "business",
                            "portfolio",
                            "asset",
                            "entreprise",
                            "portefeuille",
                            "actif",
                        )
                    ):
                        score -= 100
                    if any(
                        token in key
                        for token in (
                            "switch",
                            "select",
                            "account",
                            "changer",
                            "sélection",
                            "selection",
                            "compte",
                        )
                    ):
                        score -= 50
                    score += int(y)
                    candidates.append((score, y, x, item))
                except Exception:
                    continue

        candidates.sort(key=lambda row: (row[0], row[1], row[2]))

        seen: set[tuple[int, int]] = set()
        for _, y, x, item in candidates[:4]:
            marker = (round(x), round(y))
            if marker in seen:
                continue
            seen.add(marker)
            try:
                await item.click(timeout=1200)
                if await self._wait_for_create_surface(
                    timeout_ms=1400,
                    interval_ms=200,
                ):
                    return True
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(120)
            except Exception:
                try:
                    await self.page.keyboard.press("Escape")
                except Exception:
                    pass

        return False

    async def _try_open_direct_create_url(self) -> bool:
        """
        Try Meta's current direct Business Portfolio creation route.

        This is navigation-only until Meta's own form is visible. It is useful
        for Page-pinned Business Suite sessions where /reg/ redirects back to
        latest/home?asset_id=<PageID> and no portfolio selector is rendered.
        """
        if self.page is None:
            return False

        requested_url = self.DIRECT_CREATE_URL
        final_url = ""
        try:
            final_url = await asyncio.wait_for(
                self._goto(requested_url),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "direct_create_route": {
                    "requested_url": requested_url,
                    "timeout": 10,
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False
        except BrowserBusinessError as exc:
            if exc.code != "FACEBOOK_NAVIGATION_FAILED":
                raise
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "direct_create_route": {
                    "requested_url": requested_url,
                    "navigation_error": exc.code,
                    "message": str(exc)[:500],
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False

        await self._assert_authenticated()

        if await self._wait_for_form_ready(timeout_ms=2500, interval_ms=200):
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "direct_create_route": {
                    "requested_url": requested_url,
                    "final_url": _clean(final_url or self.page.url),
                    "form_ready": True,
                },
            }
            return True

        create_surface = await self._wait_for_create_surface(
            timeout_ms=2500,
            interval_ms=200,
        )
        if create_surface and await self._click_named(self.CREATE_NAMES):
            await self._assert_authenticated()
            if await self._wait_for_form_ready(
                timeout_ms=6500,
                interval_ms=250,
            ):
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "direct_create_route": {
                        "requested_url": requested_url,
                        "final_url": _clean(self.page.url),
                        "create_surface": True,
                        "create_clicked": True,
                        "form_ready": True,
                    },
                }
                return True

        redirected_asset_id = ""
        try:
            query = parse_qs(urlsplit(_clean(self.page.url)).query)
            redirected_asset_id = _digits(
                (query.get("asset_id") or query.get("assetId") or [""])[0]
            )
        except Exception:
            redirected_asset_id = ""

        self._last_selector_diagnostic = {
            **self._last_selector_diagnostic,
            "direct_create_route": {
                "requested_url": requested_url,
                "final_url": _clean(final_url or self.page.url),
                "create_surface": bool(create_surface),
                "form_ready": False,
                "redirected_asset_id": redirected_asset_id,
            },
        }
        return False


    async def _try_open_overview_create_entry(self) -> bool:
        if self.page is None:
            return False

        requested_url = self.OVERVIEW_URL
        try:
            final_url = await asyncio.wait_for(
                self._goto(requested_url),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "overview_route": {
                    "requested_url": requested_url,
                    "timeout": 10,
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False
        except BrowserBusinessError as exc:
            if exc.code != "FACEBOOK_NAVIGATION_FAILED":
                raise
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "overview_route": {
                    "requested_url": requested_url,
                    "navigation_error": exc.code,
                    "message": str(exc)[:500],
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False

        await self._assert_authenticated()

        if await self._wait_for_form_ready(timeout_ms=2200, interval_ms=200):
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "overview_route": {
                    "requested_url": requested_url,
                    "final_url": _clean(final_url or self.page.url),
                    "form_ready": True,
                },
            }
            return True

        if await self._wait_for_create_surface(timeout_ms=2200, interval_ms=200):
            if await self._click_named(self.CREATE_NAMES):
                await self._assert_authenticated()
                if await self._wait_for_form_ready(timeout_ms=3500, interval_ms=200):
                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "overview_route": {
                            "requested_url": requested_url,
                            "final_url": _clean(self.page.url),
                            "create_clicked": True,
                            "form_ready": True,
                        },
                    }
                    return True

        menu_open = False
        try:
            menu_open = await asyncio.wait_for(
                self._try_open_top_left_portfolio_menu(),
                timeout=4.0,
            )
        except asyncio.TimeoutError:
            pass

        if menu_open and await self._click_named(self.CREATE_NAMES):
            await self._assert_authenticated()
            if await self._wait_for_form_ready(timeout_ms=3500, interval_ms=200):
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "overview_route": {
                        "requested_url": requested_url,
                        "final_url": _clean(self.page.url),
                        "menu_open": True,
                        "form_ready": True,
                    },
                }
                return True

        self._last_selector_diagnostic = {
            **self._last_selector_diagnostic,
            "overview_route": {
                "requested_url": requested_url,
                "final_url": _clean(final_url or self.page.url),
                "form_ready": False,
                "surface": await self._quick_surface_state(),
            },
        }
        return False


    async def _try_open_ads_manager_create_entry(self) -> bool:
        """
        Bounded fallback for Meta's 2026 Ads Manager Business Portfolio
        selector. This path is used only when Business Suite renders no usable
        account/business selector in a known Page context.
        """
        if self.page is None:
            return False

        probe_rows: list[dict[str, Any]] = []

        try:
            await asyncio.wait_for(
                self._goto(self.ADS_MANAGER_URL),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "ads_manager_probe": {
                    "navigation_timeout": 30,
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False
        except BrowserBusinessError as exc:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "ads_manager_probe": {
                    "navigation_error": exc.code,
                    "message": str(exc)[:500],
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False

        await self._assert_authenticated()

        if await self._form_ready():
            return True

        if await self._has_create_surface():
            if await self._click_named(self.CREATE_NAMES):
                await self._assert_authenticated()
                if await self._wait_for_form_ready(
                    timeout_ms=6500,
                    interval_ms=250,
                ):
                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "ads_manager_probe": {
                            "create_surface_direct": True,
                            "url": _clean(self.page.url),
                        },
                    }
                    return True

        # Current Ads Manager exposes its account/portfolio selector in the
        # upper-left account area. Probe only that bounded region and favor
        # controls that look like account selectors.
        selectors = (
            'button',
            '[role="button"]',
            '[role="menuitem"]',
            '[aria-haspopup]',
            '[aria-expanded]',
        )
        candidates: list[tuple[int, float, float, Any, str]] = []

        for selector in selectors:
            try:
                locator = self.page.locator(selector)
                count = min(await locator.count(), 100)
            except Exception:
                continue

            for index in range(count):
                item = locator.nth(index)
                try:
                    if not await item.is_visible() or not await item.is_enabled():
                        continue
                    box = await item.bounding_box()
                    if not box:
                        continue

                    x = float(box.get("x") or 0)
                    y = float(box.get("y") or 0)
                    w = float(box.get("width") or 0)
                    h = float(box.get("height") or 0)
                    if x > 620 or y > 260 or w <= 0 or h <= 0:
                        continue

                    text_value = _clean(await item.inner_text(timeout=700))
                    aria = _clean(await item.get_attribute("aria-label"))
                    title = _clean(await item.get_attribute("title"))
                    haspopup = _clean(await item.get_attribute("aria-haspopup"))
                    expanded = _clean(await item.get_attribute("aria-expanded"))
                    key = " ".join((text_value, aria, title)).casefold()

                    score = int(y)
                    if haspopup:
                        score -= 120
                    if expanded:
                        score -= 40
                    if any(
                        token in key
                        for token in (
                            "account",
                            "ad account",
                            "business",
                            "portfolio",
                            "switch",
                            "select",
                        )
                    ):
                        score -= 100
                    if re.search(r"\b\d{5,30}\b", key):
                        score -= 60

                    label = " | ".join(
                        value
                        for value in (text_value, aria, title)
                        if value
                    )[:300]
                    probe_rows.append(
                        {
                            "selector": selector,
                            "label": label,
                            "x": round(x),
                            "y": round(y),
                            "w": round(w),
                            "h": round(h),
                            "haspopup": haspopup,
                            "expanded": expanded,
                            "score": score,
                        }
                    )
                    candidates.append((score, y, x, item, label))
                except Exception:
                    continue

        candidates.sort(key=lambda row: (row[0], row[1], row[2]))
        seen: set[tuple[int, int]] = set()

        for _, y, x, item, label in candidates[:8]:
            marker = (round(x), round(y))
            if marker in seen:
                continue
            seen.add(marker)

            try:
                await item.click(timeout=2200)
                if await self._wait_for_create_surface(
                    timeout_ms=3500,
                    interval_ms=250,
                ):
                    if await self._click_named(self.CREATE_NAMES):
                        await self._assert_authenticated()
                        if await self._wait_for_form_ready(
                            timeout_ms=6500,
                            interval_ms=250,
                        ):
                            self._last_selector_diagnostic = {
                                **self._last_selector_diagnostic,
                                "ads_manager_probe": {
                                    "selector_opened": True,
                                    "matched_label": label,
                                    "url": _clean(self.page.url),
                                    "candidates": probe_rows[:30],
                                },
                            }
                            return True
                try:
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(120)
                except Exception:
                    pass
            except Exception:
                try:
                    await self.page.keyboard.press("Escape")
                except Exception:
                    pass

        self._last_selector_diagnostic = {
            **self._last_selector_diagnostic,
            "ads_manager_probe": {
                "selector_opened": False,
                "url": _clean(self.page.url if self.page else ""),
                "candidates": probe_rows[:30],
            },
        }
        return False

    async def _open_create_entry(
        self,
        *,
        open_form: bool,
        already_on_home: bool = False,
    ) -> bool:
        # Keep production on the lightweight Business Suite HOME SPA. The
        # heavier /overview and /reg surfaces have crashed Chromium on Railway
        # and are not required when the portfolio selector is available.
        # Preflight may already have authenticated HOME; do not navigate to the
        # same SPA twice because each Meta navigation may take tens of seconds.
        if not already_on_home:
            await self._goto(self.HOME_URL)

        if await self._form_ready():
            return True

        # Live profile-4 evidence showed Meta redirecting /reg/ back to
        # latest/home?asset_id=<PageID>. Once we are already pinned to a Page
        # that ReMask knows belongs to this profile, repeatedly navigating
        # ROOT -> /reg/ -> HOME only burns the whole BUSINESS timeout and lands
        # back in the same state. Handle that state once, with bounded selector
        # probes, and fail fast with diagnostics if the Create surface is not
        # reachable.
        current_asset = ""

        def read_current_asset() -> str:
            try:
                current_query = parse_qs(urlsplit(_clean(self.page.url)).query)
                return _digits(
                    (
                        current_query.get("asset_id")
                        or current_query.get("assetId")
                        or [""]
                    )[0]
                )
            except Exception:
                return ""

        current_asset = read_current_asset()

        # Meta Business Suite may add ?asset_id=<PageID> a moment after the
        # initial DOM becomes usable. If we classify the route too early we
        # fall into the generic menu scanner, which used to consume the whole
        # outer CREATE timeout. Give the SPA a short bounded chance to expose
        # its final Page context before choosing the routing branch.
        if open_form and already_on_home and not current_asset:
            for _ in range(8):
                await self.page.wait_for_timeout(200)
                current_asset = read_current_asset()
                if current_asset:
                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "late_asset_context": {
                            "asset_id": current_asset,
                            "url": _clean(self.page.url),
                        },
                    }
                    break

        known_page_ids = {
            _digits(row.get("id"))
            for row in (getattr(self.context, "pages", None) or [])
            if isinstance(row, dict) and _digits(row.get("id"))
        }
        pinned_known_asset = bool(
            open_form
            and current_asset
            and current_asset in known_page_ids
        )

        if pinned_known_asset:
            surface_state = await self._quick_surface_state()
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "asset_context_fast_path": {
                    "asset_id": current_asset,
                    "url": _clean(self.page.url),
                    "surface": surface_state,
                },
            }
            skip_home_selectors = bool(surface_state.get("blank"))

            targeted_open = False
            if not skip_home_selectors:
                try:
                    targeted_open = await asyncio.wait_for(
                        self._try_open_known_asset_selector(),
                        timeout=4.0,
                    )
                except asyncio.TimeoutError:
                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "asset_selector_timeout": 4,
                    }

            if targeted_open:
                if await self._click_named(self.CREATE_NAMES):
                    await self._assert_authenticated()
                    if await self._wait_for_form_ready(
                        timeout_ms=6500,
                        interval_ms=250,
                    ):
                        return True

            generic_open = False
            if not skip_home_selectors:
                try:
                    generic_open = await asyncio.wait_for(
                        self._try_open_top_left_portfolio_menu(
                            skip_known_asset=True,
                        ),
                        timeout=4.0,
                    )
                except asyncio.TimeoutError:
                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "asset_generic_selector_timeout": 4,
                    }

            if generic_open:
                if await self._click_named(self.CREATE_NAMES):
                    await self._assert_authenticated()
                    if await self._wait_for_form_ready(
                        timeout_ms=6500,
                        interval_ms=250,
                    ):
                        return True

            # Current Meta also exposes a direct /create route. It is distinct
            # from the legacy /reg/ route that was observed redirecting this
            # Page-pinned profile back to Home.
            direct_create_open = False
            try:
                direct_create_open = await asyncio.wait_for(
                    self._try_open_direct_create_url(),
                    timeout=12.0,
                )
            except asyncio.TimeoutError:
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "direct_create_outer_timeout": 12,
                }
            if direct_create_open:
                return True

            overview_open = False
            try:
                overview_open = await asyncio.wait_for(
                    self._try_open_overview_create_entry(),
                    timeout=14.0,
                )
            except asyncio.TimeoutError:
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "overview_outer_timeout": 14,
                }
            if overview_open:
                return True

            # Business Suite rendered no usable selector and the direct route
            # did not expose the form. Try Ads Manager as an independent UI
            # surface before declaring this profile unable to reach CREATE.
            ads_manager_open = False
            try:
                ads_manager_open = await asyncio.wait_for(
                    self._try_open_ads_manager_create_entry(),
                    timeout=40.0,
                )
            except asyncio.TimeoutError:
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "ads_manager_fallback_timeout": 40,
                }

            if ads_manager_open:
                return True

            # Do not navigate to ROOT or /reg/ from the same known Page
            # context: Meta already proved that /reg/ redirects back here.
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "asset_context_fast_fail": True,
            }
            return False

        menu_open = False
        try:
            menu_open = await asyncio.wait_for(
                self._try_open_top_left_portfolio_menu(),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "generic_portfolio_menu_timeout": 5,
                "url": _clean(self.page.url if self.page else ""),
            }

        if menu_open:
            if not open_form:
                return True

            if await self._click_named(self.CREATE_NAMES):
                await self._assert_authenticated()
                if await self._wait_for_form_ready():
                    return True

        # The SPA can finish its Page redirect while the bounded generic probe
        # is running. Re-enter once through the known-asset branch instead of
        # continuing through the long generic fallback chain.
        late_asset = read_current_asset()
        if (
            open_form
            and late_asset
            and late_asset in known_page_ids
            and not pinned_known_asset
        ):
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "late_asset_reclassify": {
                    "asset_id": late_asset,
                    "url": _clean(self.page.url),
                },
            }
            return await self._open_create_entry(
                open_form=True,
                already_on_home=True,
            )

        # Try Meta's current direct Business Portfolio creation route before
        # falling back to older Business Suite navigation surfaces.
        if await self._try_open_direct_create_url():
            return True

        # Accounts without an existing Business Portfolio can land on a
        # Page/personal-profile Business Suite shell whose selector sits higher
        # than the normal business selector. On an actual CREATE request, probe
        # the root Business Suite entry as a second UI surface before /reg/.
        # This is navigation-only and does not submit a mutation.
        if open_form:
            try:
                await self._goto(self.ROOT_URL)
            except BrowserBusinessError as exc:
                if exc.code != "FACEBOOK_NAVIGATION_FAILED":
                    raise
            else:
                if await self._form_ready():
                    return True
                if await self._try_open_top_left_portfolio_menu():
                    if await self._click_named(self.CREATE_NAMES):
                        await self._assert_authenticated()
                        if await self._wait_for_form_ready():
                            return True

        # Direct registration remains a normal fallback. The older live
        # canary reached the real Meta create form through /reg/ even when the
        # HOME selector was mounted/ambiguous. _goto() now tolerates Meta SPA
        # ERR_ABORTED redirects, so this path is safe to retry as navigation.
        try:
            await self._goto(self.CREATE_URL)
        except BrowserBusinessError as exc:
            if exc.code != "FACEBOOK_NAVIGATION_FAILED":
                raise

        if await self._form_ready():
            return True

        if await self._try_open_top_left_portfolio_menu():
            if not open_form:
                return True
            if await self._click_named(self.CREATE_NAMES):
                await self._assert_authenticated()
                if await self._wait_for_form_ready():
                    return True

        # /overview is much heavier and has crashed Railway Chromium. Keep it
        # diagnostic-only rather than part of normal Add BM execution.
        legacy_fallback = _clean(
            os.getenv("REMASK_BM_LEGACY_NAV_FALLBACK")
        ).lower() in {"1", "true", "yes", "on"}
        if legacy_fallback:
            try:
                await self._goto(self.OVERVIEW_URL)
            except BrowserBusinessError as exc:
                if exc.code != "FACEBOOK_NAVIGATION_FAILED":
                    raise
            else:
                if await self._form_ready():
                    return True
                if await self._try_open_top_left_portfolio_menu():
                    if not open_form:
                        return True
                    if await self._click_named(self.CREATE_NAMES):
                        await self._assert_authenticated()
                        if await self._wait_for_form_ready():
                            return True

        return False

    async def discover_managed_pages(
        self,
        *,
        fast: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Discover Fan Pages from the authenticated browser session.

        This is read-only and is used when the PHP/profile cache has no Pages.
        It deliberately inspects Meta's rendered Pages surfaces instead of
        issuing a reconstructed LIST_PAGES mutation.
        """
        from .facebook_page_discovery import _extract_pages_from_browser_document

        surfaces = (
            "https://www.facebook.com/pages/?category=your_pages",
        ) if fast else (
            "https://www.facebook.com/pages/?category=your_pages",
            "https://www.facebook.com/pages/?category=your_pages&ref=bookmarks",
            "https://www.facebook.com/pages/",
        )
        merged: dict[str, dict[str, Any]] = {}
        diagnostics: list[str] = []

        for url in surfaces:
            try:
                await self._goto(url)
                await self.page.wait_for_timeout(1200)
                document = await self.page.content()
            except BrowserBusinessError:
                raise
            except Exception as exc:
                diagnostics.append(
                    f"{url}: {exc.__class__.__name__}: {exc}"
                )
                continue

            pages = _extract_pages_from_browser_document(document)

            # Current Facebook "Your Pages" surfaces may render Page cards as
            # normal anchors without a parseable Page JSON object. Collect
            # those visible links too; this is read-only DOM inspection.
            link_rows: list[dict[str, str]] = []
            try:
                link_rows = await self.page.locator(
                    'main a[href], [role="main"] a[href], a[href]'
                ).evaluate_all(
                    """els => els.slice(0, 2500).map(el => ({
                        href: el.href || '',
                        text: (
                            el.innerText
                            || el.getAttribute('aria-label')
                            || el.getAttribute('title')
                            || ''
                        ).replace(/\\s+/g, ' ').trim()
                    })).filter(row => row.href && row.text)"""
                )
            except Exception as exc:
                diagnostics.append(
                    f"{url}: anchor scan {exc.__class__.__name__}"
                )

            link_pages: list[dict[str, Any]] = []
            seen_link_ids: set[str] = set()
            link_patterns = (
                re.compile(r"[?&](?:page_id|id)=(\d{5,25})(?:&|$)", re.IGNORECASE),
                re.compile(r"/pages/(?:[^/?#]+/)?(\d{5,25})(?:[/?#]|$)", re.IGNORECASE),
            )
            for link_row in link_rows:
                if not isinstance(link_row, dict):
                    continue
                href = _clean(link_row.get("href"))
                name = _clean(link_row.get("text"))
                if not href or not name:
                    continue
                page_id = ""
                for pattern in link_patterns:
                    match = pattern.search(href)
                    if match:
                        page_id = _digits(match.group(1))
                        if page_id:
                            break
                if not page_id or page_id in seen_link_ids:
                    continue

                # Do not treat generic Facebook navigation/profile anchors as
                # Pages unless this is a Pages surface or the URL itself says
                # /pages/. All scanned URLs here are explicit Your Pages/Page
                # surfaces, so this condition remains intentionally narrow.
                if "/pages/" not in href.lower() and "category=your_pages" not in url.lower():
                    continue

                seen_link_ids.add(page_id)
                link_pages.append({
                    "id": page_id,
                    "name": name[:240],
                    "category": "",
                    "source": "browser_dom_link",
                })

            if link_pages:
                pages.extend(link_pages)

            diagnostics.append(
                f"{url}: bytes={len(document)} "
                f"json_pages={len(_extract_pages_from_browser_document(document))} "
                f"link_pages={len(link_pages)} merged_candidates={len(pages)}"
            )
            for row in pages:
                if not isinstance(row, dict):
                    continue
                page_id = _digits(row.get("id"))
                if not page_id:
                    continue
                current = merged.get(page_id)
                if current is None:
                    merged[page_id] = dict(row)
                    continue
                for key, value in row.items():
                    if key not in current or current.get(key) in ("", None, [], {}):
                        current[key] = value

            if merged:
                break

        if not merged:
            diag = await self._diagnostic("browser_pages_empty")
            diag["page_discovery"] = diagnostics[-8:]
            raise BrowserBusinessError(
                "FAN_PAGES_NOT_DISCOVERED",
                "Authenticated Facebook browser surfaces returned no parseable Fan Pages.",
                retryable=False,
                diagnostic=diag,
            )

        return sorted(
            merged.values(),
            key=lambda row: _clean(row.get("name")).casefold(),
        )

    async def preflight(self) -> BrowserPreflightResult:
        diagnostics: list[str] = []

        await self._goto(self.HOME_URL)
        diagnostics.append("home_authenticated")

        ready = await self._open_create_entry(
            open_form=False,
            already_on_home=True,
        )
        if ready:
            diagnostics.append("portfolio_create_action_visible")

        if not ready:
            diag = await self._diagnostic("create_surface_missing")
            if self._last_selector_diagnostic:
                diag = {
                    "selector_attempt": self._last_selector_diagnostic,
                    **diag,
                }
            raise BrowserBusinessError(
                "BUSINESS_CREATE_UI_UNAVAILABLE",
                "Meta Business portfolio create action is not available for this profile.",
                retryable=True,
                diagnostic=diag,
            )

        account_id = _digits(
            getattr(self.context, "cookies", {}).get("c_user")
            if isinstance(getattr(self.context, "cookies", {}), dict)
            else ""
        )

        return BrowserPreflightResult(
            ready=True,
            current_url=_clean(self.page.url),
            create_surface_ready=True,
            account_id=account_id,
            diagnostics=diagnostics,
        )

    async def preflight_create_form(self) -> dict[str, Any]:
        """
        Open Meta's real Create business portfolio form without filling or
        submitting it. This safe canary performs no irreversible mutation.
        """
        if not await self._open_create_entry(open_form=True):
            diag = await self._diagnostic("create_form_preflight_failed")
            raise BrowserBusinessError(
                "BUSINESS_CREATE_FORM_UNAVAILABLE",
                "Create business portfolio was visible, but Meta's creation form did not open.",
                retryable=False,
                diagnostic=diag,
            )

        await self._assert_authenticated()
        if not await self._form_ready():
            diag = await self._diagnostic("create_form_fields_missing")
            raise BrowserBusinessError(
                "CREATE_UI_CHANGED",
                "Meta opened Business creation but expected name/email fields were not found.",
                retryable=False,
                diagnostic=diag,
            )

        fields: list[dict[str, Any]] = []
        try:
            inputs = self.page.locator("input:visible")
            count = min(await inputs.count(), 20)
            for index in range(count):
                item = inputs.nth(index)
                fields.append({
                    "type": _clean(await item.get_attribute("type")) or "text",
                    "name": _clean(await item.get_attribute("name"))[:120],
                    "aria": _clean(await item.get_attribute("aria-label"))[:240],
                    "placeholder": _clean(await item.get_attribute("placeholder"))[:240],
                })
        except Exception:
            fields = []

        return {
            "ready": True,
            "current_url": _clean(self.page.url),
            "field_count": len(fields),
            "fields": fields,
        }

    async def preflight_fill_create_form(self) -> dict[str, Any]:
        """
        Fill the real Meta form with disposable canary values, then stop.
        Nothing is submitted and no Business is created.
        """
        canary_name = "ReMask Canary Business"
        canary_email = "remask-canary@example.com"
        await self._prepare_create_form(
            business_name=canary_name,
            user_email=canary_email,
            user_first_name="ReMask",
            user_last_name="Canary",
            profile_display_name="ReMask Canary",
        )

        values: list[str] = []
        try:
            inputs = self.page.locator("input:visible")
            count = min(await inputs.count(), 20)
            for index in range(count):
                values.append(_clean(await inputs.nth(index).input_value()))
        except Exception:
            values = []

        name_present = canary_name in values
        email_present = canary_email in values
        if not name_present or not email_present:
            raw_diag = await self._diagnostic("create_form_fill_mismatch")
            diag = {
                "name_present": name_present,
                "email_present": email_present,
                "filled_input_count": sum(1 for value in values if value),
                "visible_input_count": len(values),
                **raw_diag,
            }
            raise BrowserBusinessError(
                "CREATE_FORM_FILL_FAILED",
                "Meta form opened, but ReMask could not prove Business name and email were filled correctly.",
                retryable=False,
                diagnostic=diag,
            )

        return {
            "ready": True,
            "name_present": True,
            "email_present": True,
            "filled_input_count": sum(1 for value in values if value),
            "visible_input_count": len(values),
        }

    async def preflight_capture_create_request(self) -> dict[str, Any]:
        """
        Exercise the real final Create click while blocking every Meta POST
        before it leaves Chromium.

        This is a non-mutating canary: it proves what request Meta's frontend
        would send without allowing CREATE to reach Facebook. The route stays
        installed until the browser context is closed, preventing frontend
        retries after the first aborted request.
        """
        if self.page is None:
            await self.open()

        canary_name = "ReMask Canary Business"
        canary_email = "remask-canary@example.com"

        await self._prepare_create_form(
            business_name=canary_name,
            user_email=canary_email,
            user_first_name="ReMask",
            user_last_name="Canary",
            profile_display_name="ReMask Canary",
        )

        loop = asyncio.get_running_loop()
        captured: asyncio.Future[dict[str, Any]] = loop.create_future()
        blocked_posts: list[dict[str, Any]] = []

        def request_summary(request: Any) -> dict[str, Any]:
            raw = ""
            body_decodable = True
            try:
                raw_buffer = getattr(request, "post_data_buffer", None)
                if raw_buffer:
                    if isinstance(raw_buffer, bytes):
                        raw = raw_buffer.decode("utf-8")
                    else:
                        raw = str(raw_buffer)
                else:
                    raw = _clean(getattr(request, "post_data", ""))
            except (UnicodeDecodeError, UnicodeError):
                body_decodable = False
                raw = ""
            except Exception:
                body_decodable = False
                raw = ""

            parsed = parse_qs(raw, keep_blank_values=True) if raw else {}

            friendly = _clean(
                (parsed.get("fb_api_req_friendly_name") or [""])[0]
            )
            doc_id = _clean((parsed.get("doc_id") or [""])[0])

            variables_raw = _clean((parsed.get("variables") or [""])[0])
            variable_keys: list[str] = []
            input_keys: list[str] = []
            if variables_raw:
                try:
                    variables = json.loads(variables_raw)
                    if isinstance(variables, dict):
                        variable_keys = sorted(str(key) for key in variables)
                        raw_input = variables.get("input")
                        if isinstance(raw_input, dict):
                            input_keys = sorted(str(key) for key in raw_input)
                except (ValueError, json.JSONDecodeError):
                    pass

            return {
                "url": _clean(getattr(request, "url", "")),
                "method": _clean(getattr(request, "method", "")),
                "friendly_name": friendly,
                "doc_id": doc_id,
                "form_keys": sorted(
                    str(key)
                    for key in parsed
                    if str(key) not in {
                        "fb_dtsg",
                        "lsd",
                        "jazoest",
                    }
                ),
                "variable_keys": variable_keys,
                "input_keys": input_keys,
                "contains_canary_name": canary_name.casefold()
                in unquote_plus(raw).casefold(),
                "contains_canary_email": canary_email.casefold()
                in unquote_plus(raw).casefold(),
                "body_decodable": body_decodable,
            }

        async def block_meta_posts(route: Any, request: Any) -> None:
            try:
                method = _clean(request.method).upper()
                host = _clean(urlsplit(_clean(request.url)).hostname).lower()
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass
                return

            if method != "POST" or not (
                host == "facebook.com"
                or host.endswith(".facebook.com")
            ):
                try:
                    await route.continue_()
                except Exception:
                    pass
                return

            summary = request_summary(request)
            blocked_posts.append(summary)
            if len(blocked_posts) > 40:
                del blocked_posts[:-40]

            # During this canary no Meta POST is allowed to leave Chromium.
            # This guarantees the final Create click cannot mutate the account.
            try:
                await route.abort()
            except Exception:
                return

            if not captured.done() and (
                summary["contains_canary_name"]
                or summary["contains_canary_email"]
            ):
                captured.set_result(summary)

        await self.page.route("**/*", block_meta_posts)

        clicked = await self._click_named(self.CREATE_NAMES)
        if not clicked:
            clicked = await self._click_named(self.SUBMIT_NAMES)
        if not clicked:
            diag = await self._diagnostic("blocked_create_submit_missing")
            raise BrowserBusinessError(
                "CREATE_UI_CHANGED",
                "Safe canary could not find Meta's final Create action.",
                retryable=False,
                diagnostic=diag,
            )

        try:
            summary = await asyncio.wait_for(
                asyncio.shield(captured),
                timeout=min(12.0, float(self.timeout_seconds)),
            )
        except asyncio.TimeoutError as exc:
            diag = await self._diagnostic("blocked_create_request_missing")
            diag["blocked_post_count"] = len(blocked_posts)
            diag["blocked_posts"] = blocked_posts[-12:]
            raise BrowserBusinessError(
                "CREATE_REQUEST_NOT_OBSERVED",
                (
                    "Meta's final Create action was clicked, all Meta POSTs were "
                    "blocked, but no identifiable create request was observed."
                ),
                retryable=False,
                diagnostic=diag,
            ) from exc

        return {
            "ready": True,
            "blocked": True,
            "request": summary,
            "blocked_post_count": len(blocked_posts),
        }

    async def snapshot_businesses(self) -> dict[str, str]:
        await self._goto(self.HOME_URL)

        # The home document often contains only the currently selected
        # portfolio. Open the real top-left portfolio selector first so the
        # rendered DOM also contains the other portfolios available to this
        # Facebook profile. This makes CREATE reconciliation useful even when
        # Meta does not switch the current portfolio after creation.
        selector_opened = False
        try:
            selector_probe = await self.page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el || el === document.body || el === document.documentElement) {
                            return false;
                        }
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const label = (el) => [
                        (el.getAttribute && el.getAttribute('aria-label')) || '',
                        (el.getAttribute && el.getAttribute('title')) || '',
                        el.innerText || el.textContent || ''
                    ].join(' ').replace(/\\s+/g, ' ').trim();

                    const xs = [20, 52, 88, 124, 160, 196, 228];
                    const ys = [
                        58, 72, 86, 100, 114, 128, 142, 156,
                        170, 184, 198, 212, 226, 240, 254, 268
                    ];
                    const seen = new Set();
                    const rows = [];

                    for (const y of ys) {
                        for (const x of xs) {
                            const stack = document.elementsFromPoint(x, y) || [];
                            for (const el of stack.slice(0, 10)) {
                                if (seen.has(el) || !visible(el)) continue;
                                seen.add(el);
                                const r = el.getBoundingClientRect();
                                const text = label(el);
                                const role = (el.getAttribute && el.getAttribute('role')) || '';
                                const tabindex = (el.getAttribute && el.getAttribute('tabindex')) || '';
                                const tag = el.tagName || '';

                                if (r.x > 300 || r.y < 48 || r.y > 285) continue;
                                if (r.width < 70 || r.width > 300) continue;
                                if (r.height < 22 || r.height > 100) continue;
                                if (!text) continue;

                                rows.push({el, r, text, role, tabindex, tag});
                            }
                        }
                    }

                    const homeRows = rows.filter(row =>
                        /^(Home|Startseite|Start|Главная|Головна)$/i.test(row.text)
                    );
                    const homeY = homeRows.length
                        ? Math.min(...homeRows.map(row => row.r.y))
                        : 285;

                    const candidates = rows.filter(row =>
                        row.r.y < homeY - 2
                        && !/^Meta Business Suite$/i.test(row.text)
                        && !/^(Home|Startseite|Start|Главная|Головна)$/i.test(row.text)
                        && !/^(Create|Создать|Створити|Erstellen)$/i.test(row.text)
                    );

                    candidates.sort((a,b) => {
                        const ai = (
                            a.role === 'button' ||
                            a.tag === 'BUTTON' ||
                            a.tabindex === '0'
                        ) ? 1 : 0;
                        const bi = (
                            b.role === 'button' ||
                            b.tag === 'BUTTON' ||
                            b.tabindex === '0'
                        ) ? 1 : 0;
                        if (ai !== bi) return bi - ai;
                        // Portfolio selector normally sits directly above Home.
                        if (a.r.y !== b.r.y) return b.r.y - a.r.y;
                        return (b.r.width * b.r.height) - (a.r.width * a.r.height);
                    });

                    const best = candidates[0];
                    if (!best) {
                        return {
                            clicked:false,
                            candidates:candidates.slice(0,12).map(row => ({
                                text:row.text,
                                x:Math.round(row.r.x),
                                y:Math.round(row.r.y),
                                w:Math.round(row.r.width),
                                h:Math.round(row.r.height)
                            }))
                        };
                    }

                    best.el.click();
                    return {
                        clicked:true,
                        clickedCandidate:{
                            text:best.text,
                            x:Math.round(best.r.x),
                            y:Math.round(best.r.y),
                            w:Math.round(best.r.width),
                            h:Math.round(best.r.height)
                        }
                    };
                }"""
            )
            selector_opened = bool(
                isinstance(selector_probe, dict)
                and selector_probe.get("clicked")
            )
            if selector_opened:
                await self.page.wait_for_timeout(550)
        except Exception:
            selector_opened = False

        href_rows: list[dict[str, str]] = []
        try:
            href_rows = await self.page.locator("a[href]").evaluate_all(
                """els => els.slice(0, 3000).map(el => ({
                    href: el.href || "",
                    text: (el.innerText || el.textContent || "").trim()
                }))"""
            )
        except Exception:
            href_rows = []

        output: dict[str, str] = {}
        for row in href_rows:
            if not isinstance(row, dict):
                continue
            href = str(row.get("href") or "")
            text = _clean(row.get("text"))
            for business_id in _business_ids_from_text(href):
                if business_id not in output or (text and not output[business_id]):
                    output[business_id] = text

        try:
            content = await self.page.content()
        except Exception:
            content = ""

        for business_id in _business_ids_from_text(content):
            output.setdefault(business_id, "")

        if selector_opened:
            try:
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(120)
            except Exception:
                pass

        return output

    @staticmethod
    def _request_matches_ad_account_create(
        request: Any,
        *,
        business_id: str,
        account_name: str,
    ) -> bool:
        meta = _request_graphql_meta(request)
        if _clean(meta.get("method")).upper() != "POST":
            return False
        if "graphql" not in _clean(meta.get("url")).lower():
            return False

        doc_id = _clean(meta.get("doc_id"))
        if not doc_id.isdigit():
            return False

        decoded = _clean(meta.get("decoded_raw")).casefold()
        friendly = _clean(meta.get("friendly_name")).casefold()
        expected_name = _clean(account_name).casefold()
        business = _digits(business_id)

        if not expected_name or expected_name not in decoded:
            return False

        operation_markers = (
            "adaccountcreate",
            "ad_account_create",
            "createadaccount",
            "create_ad_account",
            "businessadaccountcreate",
            "business_ad_account_create",
            "ad account create",
        )
        operation_match = any(
            marker in friendly or marker in decoded
            for marker in operation_markers
        )
        business_match = bool(business and business in decoded)

        return operation_match and business_match

    @staticmethod
    def _response_matches_ad_account_create(
        response: Any,
        *,
        business_id: str,
        account_name: str,
    ) -> bool:
        try:
            return FacebookBusinessBrowser._request_matches_ad_account_create(
                response.request,
                business_id=business_id,
                account_name=account_name,
            )
        except Exception:
            return False

    async def _wait_for_ad_account_settings_ready(
        self,
        *,
        business_id: str,
        timeout_seconds: float = 12.0,
    ) -> bool:
        """Wait for Meta Business Settings to finish client-side hydration."""
        business = _digits(business_id)
        if self.page is None:
            return False

        deadline = time.monotonic() + max(2.0, float(timeout_seconds))
        saw_nonempty_body = False

        while time.monotonic() < deadline:
            try:
                await self._assert_authenticated()
            except BrowserBusinessError:
                raise
            except Exception:
                pass

            body = (await self._body_text()).casefold()
            if body.strip():
                saw_nonempty_body = True

            markers = (
                "ad account",
                "advertising account",
                "реклам",
                "werbekonto",
                "compte publicitaire",
                "comptes publicitaires",
                "বিজ্ঞাপন অ্যাকাউন্ট",
                "tài khoản quảng cáo",
                "विज्ञापन खाता",
                "विज्ञापन खाते",
            )
            if any(marker in body for marker in markers):
                return True

            # Meta can render icon/button chrome before useful body text.
            # A visible Add/Create control on the expected business URL is
            # enough to treat the surface as hydrated.
            try:
                current = _clean(self.page.url)
                has_business = bool(business and business in current)
                visible_action = bool(
                    await self.page.evaluate(
                        """() => {
                            const visible = el => {
                                const r = el.getBoundingClientRect();
                                const s = getComputedStyle(el);
                                return r.width > 0 && r.height > 0
                                    && s.display !== 'none'
                                    && s.visibility !== 'hidden'
                                    && s.pointerEvents !== 'none';
                            };
                            const nodes = [...document.querySelectorAll(
                                'button,a,[role="button"],[role="menuitem"]'
                            )];
                            return nodes.some(el => {
                                if (!visible(el)) return false;
                                const t = (
                                    (el.getAttribute('aria-label') || '') + ' ' +
                                    (el.innerText || el.textContent || '')
                                ).replace(/\\s+/g, ' ').trim().toLowerCase();
                                return [
                                    'add','create','ajouter','créer',
                                    'добавить','создать','додати','створити',
                                    'hinzufügen','erstellen',
                                    'যোগ করুন','তৈরি করুন',
                                    'thêm','tạo',
                                    'जोड़ें','बनाएँ','बनाएं'
                                ].some(x => t.includes(x));
                            });
                        }"""
                    )
                )
                if has_business and visible_action:
                    return True
            except Exception:
                pass

            await self.page.wait_for_timeout(500)

        # A completely empty body after navigation is a hydration/load
        # failure, not proof that Meta changed the UI.
        return saw_nonempty_body and False

    async def _activate_ad_account_settings_section(
        self,
        *,
        business_id: str = "",
    ) -> bool:
        """Open the real Ad Accounts pane, preferring Meta's own section href."""
        if self.page is None:
            return False

        business = _digits(business_id)
        self._last_ad_account_section_diagnostic = {}

        # Meta often renders the visible localized label inside a nested span
        # while the actual SPA navigation lives on an ancestor <a> or role
        # control. Discover that real interactive node first. This avoids the
        # false-positive "click succeeded" state where clicking only the text
        # node leaves the generic Settings shell mounted.
        probe: dict[str, Any] | None = None
        try:
            raw_probe = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                    const names = [
                        'ad accounts',
                        'advertising accounts',
                        'рекламные аккаунты',
                        'рекламні акаунти',
                        'werbekonten',
                        'comptes publicitaires',
                        'বিজ্ঞাপন অ্যাকাউন্ট',
                        'বিজ্ঞাপন অ্যাকাউন্টসমূহ',
                        'tài khoản quảng cáo',
                        'विज्ञापन खाते',
                        'विज्ञापन खाता'
                    ];
                    const interactive = [
                        ...document.querySelectorAll(
                            'a[href],button,[role="link"],[role="menuitem"],'
                            + '[role="button"],[tabindex]'
                        )
                    ];
                    const rows = [];
                    for (const el of interactive) {
                        if (!visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        // The settings navigation is in Meta's left column.
                        // Keeping this bounded prevents a similarly named
                        // control in the content pane from winning.
                        if (r.x > 620 || r.y < 35 || r.y > 760) continue;

                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        const href = (
                            el.tagName === 'A'
                                ? (el.href || '')
                                : ((el.closest && el.closest('a[href]'))?.href || '')
                        );
                        const hrefKey = clean(href);
                        const hrefMatch = (
                            hrefKey.includes('/settings/ad_accounts')
                            || hrefKey.includes('/settings/ad-accounts')
                        );
                        const exactText = names.some(name => text === name);
                        const shortText = names.some(
                            name => text.includes(name)
                                && text.length <= Math.max(150, name.length + 90)
                        );
                        if (!hrefMatch && !exactText && !shortText) continue;

                        let score = Math.round(r.y);
                        if (hrefMatch) score -= 1000;
                        if (exactText) score -= 500;
                        if (el.tagName === 'A') score -= 180;
                        if ((el.getAttribute('role') || '') === 'link') score -= 120;
                        if (r.x < 360) score -= 100;

                        rows.push({
                            el,
                            href,
                            text,
                            score,
                            x: Math.round(r.x),
                            y: Math.round(r.y),
                            w: Math.round(r.width),
                            h: Math.round(r.height),
                            tag: el.tagName || '',
                            role: el.getAttribute('role') || ''
                        });
                    }
                    rows.sort((a,b) => a.score - b.score || a.y - b.y || a.x - b.x);
                    const best = rows[0];
                    if (!best) return {mode:'none', candidates:[]};

                    const compact = rows.slice(0,12).map(row => ({
                        href: row.href,
                        text: row.text,
                        x: row.x,
                        y: row.y,
                        w: row.w,
                        h: row.h,
                        tag: row.tag,
                        role: row.role,
                        score: row.score
                    }));

                    if (best.href && (
                        best.href.includes('/settings/ad_accounts')
                        || best.href.includes('/settings/ad-accounts')
                    )) {
                        return {
                            mode:'href',
                            href:best.href,
                            text:best.text,
                            x:best.x,
                            y:best.y,
                            tag:best.tag,
                            role:best.role,
                            candidates:compact
                        };
                    }

                    best.el.scrollIntoView({block:'center'});
                    best.el.click();
                    return {
                        mode:'click',
                        href:'',
                        text:best.text,
                        x:best.x,
                        y:best.y,
                        tag:best.tag,
                        role:best.role,
                        candidates:compact
                    };
                }"""
            )
            if isinstance(raw_probe, dict):
                probe = raw_probe
        except Exception as exc:
            self._last_ad_account_section_diagnostic = {
                "mode": "probe_error",
                "error": f"{exc.__class__.__name__}: {exc}"[:500],
            }

        if isinstance(probe, dict):
            mode = _clean(probe.get("mode")).lower()
            href = _clean(probe.get("href"))
            self._last_ad_account_section_diagnostic = {
                "mode": mode or "none",
                "href": href[:900],
                "text": _clean(probe.get("text"))[:240],
                "x": probe.get("x"),
                "y": probe.get("y"),
                "tag": _clean(probe.get("tag"))[:40],
                "role": _clean(probe.get("role"))[:80],
                "candidates": (
                    probe.get("candidates")[:8]
                    if isinstance(probe.get("candidates"), list)
                    else []
                ),
            }

            if mode == "href" and href:
                # Never follow a link that explicitly targets a different BM.
                href_business = ""
                try:
                    href_query = parse_qs(urlsplit(href).query)
                    href_business = _digits(
                        (href_query.get("business_id") or [""])[0]
                    )
                except Exception:
                    href_business = ""

                if business and href_business and href_business != business:
                    self._last_ad_account_section_diagnostic["href_rejected"] = (
                        "different_business"
                    )
                else:
                    try:
                        final_url = await self._goto(href)
                        await self._assert_authenticated()
                        await self.page.wait_for_timeout(700)
                        self._last_ad_account_section_diagnostic["final_url"] = (
                            _clean(final_url or self.page.url)[:900]
                        )
                        return True
                    except BrowserBusinessError as exc:
                        if exc.code in {
                            "SESSION_EXPIRED",
                            "CHECKPOINT_REQUIRED",
                            "TWO_FACTOR_REQUIRED",
                            "FACEBOOK_TEMPORARILY_BLOCKED",
                        }:
                            raise
                        self._last_ad_account_section_diagnostic[
                            "href_navigation_error"
                        ] = f"{exc.code}: {exc}"[:700]
                    except Exception as exc:
                        self._last_ad_account_section_diagnostic[
                            "href_navigation_error"
                        ] = f"{exc.__class__.__name__}: {exc}"[:700]

            if mode == "click":
                await self.page.wait_for_timeout(900)
                self._last_ad_account_section_diagnostic["final_url"] = (
                    _clean(getattr(self.page, "url", ""))[:900]
                )
                return True

        # Last fallback for variants where Playwright exposes the semantic
        # role cleanly but the bounded DOM probe above cannot see the item.
        clicked = await self._click_named(
            self.AD_ACCOUNT_SECTION_NAMES,
            roles=("link", "menuitem", "button"),
        )
        if clicked:
            await self.page.wait_for_timeout(900)
            self._last_ad_account_section_diagnostic = {
                **self._last_ad_account_section_diagnostic,
                "mode": "role_click",
                "final_url": _clean(getattr(self.page, "url", ""))[:900],
            }
            return True

        return False

    async def _click_ad_account_action_dom(
        self,
        *,
        allow_generic_add: bool = False,
    ) -> str:
        """Click Meta's current Ad Account Create/Add control by visible DOM text."""
        if self.page is None:
            return ""

        try:
            result = await self.page.evaluate(
                """(allowGenericAdd) => {
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                    const accountWords = [
                        'ad account','advertising account','реклам',
                        'werbekonto','compte publicitaire',
                        'বিজ্ঞাপন অ্যাকাউন্ট','tài khoản quảng cáo',
                        'विज्ञापन खाता','विज्ञापन खाते'
                    ];
                    const createWords = [
                        'create','new ad account',
                        'создать','створити','erstellen',
                        'créer','nouveau compte publicitaire',
                        'তৈরি করুন','নতুন বিজ্ঞাপন অ্যাকাউন্ট',
                        'tạo','tài khoản quảng cáo mới',
                        'बनाएँ','बनाएं','नया विज्ञापन खाता'
                    ];
                    const addWords = [
                        'add','ajouter','добавить','додати',
                        'hinzufügen','যোগ করুন','thêm','जोड़ें'
                    ];
                    const nodes = [...document.querySelectorAll(
                        'button,a,[role="button"],[role="menuitem"],'
                        + '[role="menuitemradio"],[role="option"]'
                    )];
                    const rows = nodes
                        .filter(visible)
                        .map(el => ({
                            el,
                            text: clean(
                                (el.getAttribute('aria-label') || '') + ' ' +
                                (el.getAttribute('title') || '') + ' ' +
                                (el.innerText || el.textContent || '')
                            )
                        }))
                        .filter(row => row.text);

                    const direct = rows
                        .filter(row =>
                            accountWords.some(x => row.text.includes(x))
                            && createWords.some(x => row.text.includes(x))
                        )
                        .sort((a,b) => a.text.length - b.text.length);
                    if (direct.length) {
                        direct[0].el.scrollIntoView({block: 'center'});
                        direct[0].el.click();
                        return 'create';
                    }

                    if (!allowGenericAdd) return '';

                    const generic = rows
                        .filter(row => {
                            const r = row.el.getBoundingClientRect();
                            const isInteractive = row.el.matches(
                                'button,a,[role="button"],[role="link"]'
                            );
                            // The Add-RK action is in Meta's right content pane.
                            // Accept anchors/role=link as well as buttons, but
                            // keep the x-bound so sidebar navigation cannot win.
                            if (
                                !isInteractive
                                || r.x < 300
                                || row.text.length > 96
                            ) {
                                return false;
                            }
                            if (accountWords.some(x => row.text.includes(x))) {
                                return false;
                            }
                            return addWords.some(
                                word => row.text === word
                                    || row.text.startsWith(word + ' ')
                            );
                        })
                        .sort((a,b) => {
                            const at = a.text.length - b.text.length;
                            if (at) return at;
                            return a.el.getBoundingClientRect().x
                                - b.el.getBoundingClientRect().x;
                        });
                    if (!generic.length) return '';
                    generic[0].el.scrollIntoView({block: 'center'});
                    generic[0].el.click();
                    return 'add';
                }""",
                bool(allow_generic_add),
            )
        except Exception:
            return ""

        value = _clean(result).lower()
        return value if value in {"create", "add"} else ""

    async def _wait_for_ad_account_create_entry(
        self,
        *,
        timeout_seconds: float = 6.0,
    ) -> bool:
        """Wait specifically for the Create-new-Ad-Account menu entry.

        This must not treat the persistent generic Add button as readiness,
        otherwise a second Add click can toggle Meta's popup closed.
        """
        if self.page is None:
            return False

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            if await self._click_named(
                self.AD_ACCOUNT_CREATE_ENTRY_NAMES,
                roles=(
                    "button",
                    "link",
                    "menuitem",
                    "menuitemradio",
                    "option",
                ),
            ):
                return True

            try:
                action = await self._click_ad_account_action_dom(
                    allow_generic_add=False
                )
                if action == "create":
                    return True
            except Exception:
                pass

            await self.page.wait_for_timeout(250)

        return False

    async def _ad_account_popup_candidates(self) -> list[str]:
        """Return compact visible popup/menu text after clicking Add."""
        if self.page is None:
            return []
        try:
            rows = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const out = [];
                    const seen = new Set();
                    const selectors = [
                        '[role="menu"] [role="menuitem"]',
                        '[role="menu"] [role="menuitemradio"]',
                        '[role="listbox"] [role="option"]',
                        '[role="dialog"] button',
                        '[role="dialog"] a',
                        '[data-visualcompletion="ignore-dynamic"] [role="button"]',
                        '[data-visualcompletion="ignore-dynamic"] a'
                    ];
                    for (const selector of selectors) {
                        for (const el of document.querySelectorAll(selector)) {
                            if (!visible(el)) continue;
                            const text = clean(
                                (el.getAttribute('aria-label') || '') + ' ' +
                                (el.getAttribute('title') || '') + ' ' +
                                (el.innerText || el.textContent || '')
                            );
                            if (!text || text.length > 260) continue;
                            const r = el.getBoundingClientRect();
                            const row = text
                                + ' [tag=' + (el.tagName || '')
                                + ' role=' + (el.getAttribute('role') || '')
                                + ' x=' + Math.round(r.x)
                                + ' y=' + Math.round(r.y)
                                + ']';
                            if (seen.has(row)) continue;
                            seen.add(row);
                            out.push(row);
                            if (out.length >= 30) return out;
                        }
                    }
                    return out;
                }"""
            )
            if isinstance(rows, list):
                return [_clean(x)[:300] for x in rows if _clean(x)][:30]
        except Exception:
            pass
        return []

    async def _ad_account_action_candidates(self) -> list[str]:
        if self.page is None:
            return []
        try:
            rows = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const markers = [
                        'add','create','ad account','advertising account',
                        'ajouter','créer','compte publicitaire',
                        'добавить','создать','реклам',
                        'додати','створити',
                        'hinzufügen','erstellen','werbekonto',
                        'যোগ করুন','তৈরি করুন','বিজ্ঞাপন অ্যাকাউন্ট',
                        'thêm','tạo','tài khoản quảng cáo',
                        'जोड़ें','बनाएँ','बनाएं','विज्ञापन खाता'
                    ];
                    const out = [];
                    const seen = new Set();
                    for (const el of document.querySelectorAll(
                        'button,a,[role="button"],[role="link"],[role="menuitem"],'
                        + '[role="menuitemradio"],[role="option"]'
                    )) {
                        if (!visible(el)) continue;
                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        const lower = text.toLowerCase();
                        if (!text || text.length > 180) continue;
                        if (!markers.some(x => lower.includes(x))) continue;
                        const r = el.getBoundingClientRect();
                        const key = [
                            text,
                            el.tagName || '',
                            el.getAttribute('role') || '',
                            Math.round(r.x),
                            Math.round(r.y)
                        ].join('|');
                        if (seen.has(key)) continue;
                        seen.add(key);
                        out.push(
                            text
                            + ' [tag=' + (el.tagName || '')
                            + ' role=' + (el.getAttribute('role') || '')
                            + ' x=' + Math.round(r.x)
                            + ' y=' + Math.round(r.y)
                            + ']'
                        );
                        if (out.length >= 30) break;
                    }
                    return out;
                }"""
            )
            if isinstance(rows, list):
                return [_clean(value)[:180] for value in rows if _clean(value)][:30]
        except Exception:
            pass
        return []

    async def _wait_for_ad_account_create_action(
        self,
        *,
        timeout_seconds: float = 10.0,
    ) -> bool:
        """Wait for a real Add/Create control in the Ad Accounts content pane.

        The left settings navigation itself contains localized "Ad accounts"
        text, so body-text hydration markers are not enough. This probe waits
        for an actionable control in the main pane and deliberately ignores
        sidebar-only labels.
        """
        if self.page is None:
            return False

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            try:
                ready = bool(
                    await self.page.evaluate(
                        """() => {
                            const visible = el => {
                                const r = el.getBoundingClientRect();
                                const s = getComputedStyle(el);
                                return r.width > 0 && r.height > 0
                                    && s.display !== 'none'
                                    && s.visibility !== 'hidden'
                                    && s.pointerEvents !== 'none';
                            };
                            const clean = text => (text || '')
                                .replace(/\s+/g, ' ').trim().toLowerCase();
                            const accountWords = [
                                'ad account','advertising account','реклам',
                                'werbekonto','compte publicitaire',
                                'বিজ্ঞাপন অ্যাকাউন্ট','tài khoản quảng cáo',
                                'विज्ञापन खाता','विज्ञापन खाते'
                            ];
                            const actionWords = [
                                'add','create','ajouter','créer',
                                'добавить','создать','додати','створити',
                                'hinzufügen','erstellen',
                                'যোগ করুন','তৈরি করুন',
                                'thêm','tạo','जोड़ें','बनाएँ','बनाएं'
                            ];
                            const genericActions = new Set([
                                'add','create','ajouter','créer',
                                'добавить','создать','додати','створити',
                                'hinzufügen','erstellen',
                                'যোগ করুন','তৈরি করুন',
                                'thêm','tạo','जोड़ें','बनाएँ','बनाएं'
                            ]);
                            const nodes = [...document.querySelectorAll(
                                'button,a,[role="button"],[role="menuitem"],[aria-haspopup]'
                            )];
                            return nodes.some(el => {
                                if (!visible(el)) return false;
                                const r = el.getBoundingClientRect();
                                const text = clean(
                                    (el.getAttribute('aria-label') || '') + ' ' +
                                    (el.getAttribute('title') || '') + ' ' +
                                    (el.innerText || el.textContent || '')
                                );
                                if (!text) return false;
                                const hasAccount = accountWords.some(x => text.includes(x));
                                const hasAction = actionWords.some(x => text.includes(x));
                                if (hasAccount && hasAction) return true;
                                return r.x >= 300 && genericActions.has(text);
                            });
                        }"""
                    )
                )
                if ready:
                    return True
            except Exception:
                pass
            await self.page.wait_for_timeout(250)

        return False

    async def _open_ad_account_create_form(
        self,
        *,
        business_id: str,
        account_name: str,
    ) -> None:
        business = _digits(business_id)
        if not business:
            raise BrowserBusinessError(
                "INVALID_BUSINESS_ID",
                "Ad Account create requires a numeric Business ID.",
                retryable=False,
            )

        opened = False
        navigation_errors: list[str] = []
        hydration_attempts = 0
        for template in self.SETTINGS_AD_ACCOUNTS_URLS:
            try:
                target_url = template.format(business_id=business)
                await self._goto(target_url)
                hydration_attempts += 1
                if await self._wait_for_ad_account_settings_ready(
                    business_id=business,
                    timeout_seconds=12.0,
                ):
                    opened = True
                    break

                # Meta occasionally returns the Business Settings shell first
                # (interactive DOM, 0 body text) and hydrates only after a
                # reload. Retry the same URL once before trying another route.
                try:
                    await self.page.reload(
                        wait_until="commit",
                        timeout=self.timeout_ms,
                    )
                    await self.page.wait_for_timeout(500)
                    hydration_attempts += 1
                    if await self._wait_for_ad_account_settings_ready(
                        business_id=business,
                        timeout_seconds=10.0,
                    ):
                        opened = True
                        break
                except Exception as reload_exc:
                    navigation_errors.append(
                        f"reload: {type(reload_exc).__name__}: {reload_exc}"
                    )
            except BrowserBusinessError as exc:
                navigation_errors.append(f"{exc.code}: {exc}")
                if exc.code in {
                    "SESSION_EXPIRED",
                    "CHECKPOINT_REQUIRED",
                    "TWO_FACTOR_REQUIRED",
                    "FACEBOOK_TEMPORARILY_BLOCKED",
                }:
                    raise

        if not opened:
            diag = await self._diagnostic("ad_account_settings_unavailable")
            diag["business_id"] = business
            diag["navigation_errors"] = navigation_errors[-6:]
            diag["hydration_attempts"] = hydration_attempts
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_UI_UNAVAILABLE",
                "Meta Business Settings Ad Accounts surface could not be opened.",
                retryable=True,
                diagnostic=diag,
            )

        # Meta's migrated settings URL can land on the generic settings shell
        # even though the URL already contains /ad_accounts. Resolve and open
        # Meta's own sidebar href first, then require a real right-pane action.
        section_clicked = await self._activate_ad_account_settings_section(
            business_id=business,
        )
        action_surface_ready = await self._wait_for_ad_account_create_action(
            timeout_seconds=8.0 if section_clicked else 4.0,
        )
        section_reload_attempted = False
        section_route_attempts: list[str] = []

        # If Meta mounted only the generic Settings shell, reload the route that
        # the actual sidebar item resolved to. This is intentionally different
        # from re-clicking the same text node.
        if section_clicked and not action_surface_ready:
            current_url = _clean(self.page.url)
            if (
                "/settings/ad_accounts" in current_url
                or "/settings/ad-accounts" in current_url
            ):
                try:
                    await self.page.reload(
                        wait_until="commit",
                        timeout=self.timeout_ms,
                    )
                    section_reload_attempted = True
                    await self.page.wait_for_timeout(700)
                    await self._assert_authenticated()
                    await self._activate_ad_account_settings_section(
                        business_id=business,
                    )
                    action_surface_ready = (
                        await self._wait_for_ad_account_create_action(
                            timeout_seconds=8.0,
                        )
                    )
                except BrowserBusinessError as exc:
                    section_route_attempts.append(
                        f"reload:{exc.code}:{exc}"
                    )
                    if exc.code in {
                        "SESSION_EXPIRED",
                        "CHECKPOINT_REQUIRED",
                        "TWO_FACTOR_REQUIRED",
                        "FACEBOOK_TEMPORARILY_BLOCKED",
                    }:
                        raise
                except Exception as exc:
                    section_route_attempts.append(
                        f"reload:{exc.__class__.__name__}:{exc}"
                    )

        # Final bounded routing fallback: try every known Meta settings route,
        # but this time judge success only by the real right-pane Add/Create
        # control instead of sidebar text.
        if not action_surface_ready:
            for template in self.SETTINGS_AD_ACCOUNTS_URLS:
                target_url = template.format(business_id=business)
                try:
                    await self._goto(target_url)
                    await self.page.wait_for_timeout(450)
                    await self._assert_authenticated()
                    activated = await self._activate_ad_account_settings_section(
                        business_id=business,
                    )
                    ready = await self._wait_for_ad_account_create_action(
                        timeout_seconds=4.5 if activated else 2.5,
                    )
                    section_route_attempts.append(
                        f"{target_url}:activated={activated}:ready={ready}"
                    )
                    if ready:
                        action_surface_ready = True
                        section_clicked = section_clicked or activated
                        break
                except BrowserBusinessError as exc:
                    section_route_attempts.append(
                        f"{target_url}:{exc.code}:{exc}"
                    )
                    if exc.code in {
                        "SESSION_EXPIRED",
                        "CHECKPOINT_REQUIRED",
                        "TWO_FACTOR_REQUIRED",
                        "FACEBOOK_TEMPORARILY_BLOCKED",
                    }:
                        raise
                except Exception as exc:
                    section_route_attempts.append(
                        f"{target_url}:{exc.__class__.__name__}:{exc}"
                    )

        entry_clicked = await self._click_named(
            self.AD_ACCOUNT_CREATE_ENTRY_NAMES
        )
        if not entry_clicked:
            entry_clicked = (
                await self._click_ad_account_action_dom(
                    allow_generic_add=False
                )
                == "create"
            )

        add_clicked = False
        post_add_candidates: list[str] = []
        if not entry_clicked:
            add_clicked = await self._click_named(
                self.ADD_NAMES,
                roles=("button", "link", "menuitem"),
            )
            dom_action = ""
            if not add_clicked:
                dom_action = await self._click_ad_account_action_dom(
                    allow_generic_add=True
                )
                if dom_action == "create":
                    entry_clicked = True
                add_clicked = dom_action == "add"

            if add_clicked and not entry_clicked:
                # Click Add exactly once. Re-clicking can toggle Meta's popup
                # closed. Wait only for the CREATE menu item from here.
                entry_clicked = await self._wait_for_ad_account_create_entry(
                    timeout_seconds=6.0,
                )
                if not entry_clicked:
                    post_add_candidates = (
                        await self._ad_account_popup_candidates()
                    )

        if not entry_clicked and not add_clicked:
            # No Add action was found at all. Give the page one late hydration
            # window, then retry the direct Create entry only (still no second
            # Add toggle).
            await self.page.wait_for_timeout(1200)
            entry_clicked = await self._wait_for_ad_account_create_entry(
                timeout_seconds=4.0,
            )

        if not entry_clicked:
            raw_diag = await self._diagnostic(
                "ad_account_create_entry_missing"
            )
            diag: dict[str, Any] = {
                "stage": "ad_account_create_entry_missing",
                "business_id": business,
                "section_clicked": section_clicked,
                "section_reload_attempted": section_reload_attempted,
                "section_activation": self._last_ad_account_section_diagnostic,
                "action_surface_ready": action_surface_ready,
                "action_candidates": (
                    await self._ad_account_action_candidates()
                ),
                "add_clicked": add_clicked,
                "post_add_candidates": post_add_candidates,
                "section_route_attempts": section_route_attempts[-8:],
                "hydration_attempts": hydration_attempts,
            }
            for key, value in raw_diag.items():
                if key not in diag:
                    diag[key] = value
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_UI_CHANGED",
                "Meta Ad Account create entry was not found.",
                retryable=True,
                diagnostic=diag,
            )

        await self.page.wait_for_timeout(500)

        name_filled = await self._fill_first(
            labels=(
                "Ad account name",
                "Advertising account name",
                "Account name",
                "Название рекламного аккаунта",
                "Название аккаунта",
                "Назва рекламного акаунта",
                "Назва облікового запису",
                "Name des Werbekontos",
                "Nom du compte publicitaire",
                "Nom du compte",
                "বিজ্ঞাপন অ্যাকাউন্টের নাম",
                "Tên tài khoản quảng cáo",
                "विज्ञापन खाते का नाम",
                "विज्ञापन खाता नाम",
            ),
            value=account_name,
        )

        if not name_filled:
            try:
                candidates = self.page.locator(
                    'div[role="dialog"] input:visible, form input:visible'
                )
                count = min(await candidates.count(), 20)
            except Exception:
                count = 0

            for index in range(count):
                candidate = candidates.nth(index)
                try:
                    kind = _clean(
                        await candidate.get_attribute("type")
                    ).lower()
                    if kind in {
                        "hidden","checkbox","radio","submit","button"
                    }:
                        continue
                    placeholder = _clean(
                        await candidate.get_attribute("placeholder")
                    ).casefold()
                    aria = _clean(
                        await candidate.get_attribute("aria-label")
                    ).casefold()
                    if "search" in placeholder or "search" in aria:
                        continue
                    current = _clean(await candidate.input_value())
                    if current and len(current) > 2:
                        continue
                    await candidate.fill(account_name)
                    name_filled = True
                    break
                except Exception:
                    continue

        if not name_filled:
            diag = await self._diagnostic("ad_account_name_input_missing")
            diag["business_id"] = business
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_UI_CHANGED",
                "Meta Ad Account form opened but the account-name field was not found.",
                retryable=True,
                diagnostic=diag,
            )

    async def create_ad_account(
        self,
        *,
        business_id: str,
        account_name: str,
        currency: str = "USD",
        timezone_id: int = 1,
        before_submit: CheckpointCallback | None = None,
    ) -> BrowserAdAccountResult:
        business = _digits(business_id)
        name = _clean(account_name)
        if not business:
            raise BrowserBusinessError(
                "INVALID_BUSINESS_ID",
                "Ad Account create requires a numeric Business ID.",
                retryable=False,
            )
        if not name:
            raise BrowserBusinessError(
                "INVALID_INPUT",
                "Ad Account name is required.",
                retryable=False,
            )

        await self._open_ad_account_create_form(
            business_id=business,
            account_name=name,
        )

        async def checkpoint(patch: dict[str, Any]) -> None:
            if before_submit is None:
                return
            await before_submit(
                {
                    **patch,
                    "business_id": business,
                    "account_name": name,
                    "currency": _clean(currency).upper(),
                    "timezone_id": int(timezone_id),
                }
            )

        await checkpoint(
            {
                "phase": "CREATE_PREPARED",
                "activity": "AD_ACCOUNT_FORM_READY",
                "activity_at": int(time.time()),
            }
        )

        loop = asyncio.get_running_loop()
        gate_future: asyncio.Future[bool] = loop.create_future()
        response_future: asyncio.Future[Any] = loop.create_future()

        async def gate(route: Any, request: Any) -> None:
            if not self._request_matches_ad_account_create(
                request,
                business_id=business,
                account_name=name,
            ):
                await route.continue_()
                return

            if gate_future.done():
                await route.continue_()
                return

            try:
                request_meta = _request_graphql_meta(request)
                await checkpoint(
                    {
                        "phase": "CREATE_SUBMITTED",
                        "activity": "AD_ACCOUNT_CREATE_SUBMITTED",
                        "activity_at": int(time.time()),
                        "network_gate": "before_meta_send",
                        "create_doc_id": _clean(
                            request_meta.get("doc_id")
                        ),
                        "create_friendly_name": _clean(
                            request_meta.get("friendly_name")
                        ),
                    }
                )
            except Exception as exc:
                try:
                    await route.abort()
                finally:
                    if not gate_future.done():
                        gate_future.set_exception(
                            BrowserBusinessError(
                                "CREATE_CHECKPOINT_FAILED_BEFORE_SEND",
                                (
                                    "ReMask intercepted Meta Add-RK CREATE but "
                                    "could not persist the submitted checkpoint, "
                                    "so the request was blocked before Meta."
                                ),
                                retryable=True,
                            )
                        )
                return

            patched_post_data, attribution_defaults = (
                _ad_account_required_attribution_post_data(request)
            )
            if attribution_defaults:
                await checkpoint(
                    {
                        "phase": "CREATE_SUBMITTED",
                        "activity": "AD_ACCOUNT_REQUIRED_ATTRIBUTION_APPLIED",
                        "activity_at": int(time.time()),
                        "required_attribution_defaults": sorted(
                            attribution_defaults.keys()
                        ),
                    }
                )
                await route.continue_(post_data=patched_post_data)
            else:
                await route.continue_()
            if not gate_future.done():
                gate_future.set_result(True)

        def observe_response(response: Any) -> None:
            if response_future.done():
                return
            if self._response_matches_ad_account_create(
                response,
                business_id=business,
                account_name=name,
            ):
                response_future.set_result(response)

        await self.page.route("**/api/graphql/**", gate)
        self.page.on("response", observe_response)

        next_names = (
            "Next",
            "Continue",
            "Suivant",
            "Continuer",
            "Weiter",
            "Fortfahren",
            "Далее",
            "Продолжить",
            "Далі",
            "Продовжити",
            "পরবর্তী",
            "চালিয়ে যান",
            "Tiếp",
            "Tiếp tục",
            "अगला",
            "आगे",
            "जारी रखें",
        )
        final_names = (
            "Create ad account",
            "Create account",
            "Create",
            "Создать рекламный аккаунт",
            "Создать аккаунт",
            "Создать",
            "Створити рекламний акаунт",
            "Створити обліковий запис",
            "Створити",
            "Werbekonto erstellen",
            "Konto erstellen",
            "Erstellen",
            "Créer un compte publicitaire",
            "Créer le compte publicitaire",
            "Créer le compte",
            "Créer",
            "বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন",
            "তৈরি করুন",
            "Tạo tài khoản quảng cáo",
            "Tạo",
            "विज्ञापन खाता बनाएँ",
            "विज्ञापन खाता बनाएं",
            "बनाएँ",
            "बनाएं",
        )

        try:
            clicked_any = False
            for _ in range(8):
                if gate_future.done():
                    break

                next_clicked = await self._click_named(next_names)
                if next_clicked:
                    clicked_any = True
                    await self.page.wait_for_timeout(650)
                    if gate_future.done():
                        break
                    continue

                final_clicked = await self._click_named(
                    final_names,
                    before_click=lambda: checkpoint(
                        {
                            "phase": "CREATE_CLICK_INTENT",
                            "activity": "AD_ACCOUNT_CREATE_CLICK_INTENT",
                            "activity_at": int(time.time()),
                        }
                    ),
                )
                if final_clicked:
                    clicked_any = True
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(gate_future),
                            timeout=3.0,
                        )
                    except asyncio.TimeoutError:
                        await self.page.wait_for_timeout(400)
                    if gate_future.done():
                        break
                    continue

                break

            if gate_future.done() and gate_future.exception() is not None:
                raise gate_future.exception()

            if not gate_future.done():
                await checkpoint(
                    {
                        "phase": "CREATE_NOT_SUBMITTED",
                        "activity": "AD_ACCOUNT_CREATE_NOT_SUBMITTED",
                        "activity_at": int(time.time()),
                    }
                )
                diag = await self._diagnostic(
                    "ad_account_create_submit_missing"
                )
                diag["clicked_any"] = clicked_any
                raise BrowserBusinessError(
                    "AD_ACCOUNT_CREATE_UI_CHANGED",
                    (
                        "Meta Ad Account form was opened, but ReMask could not "
                        "reach an identifiable CREATE request. No CREATE was sent."
                    ),
                    retryable=True,
                    diagnostic=diag,
                )

            try:
                response = await asyncio.wait_for(
                    asyncio.shield(response_future),
                    timeout=min(15.0, float(self.timeout_seconds)),
                )
            except asyncio.TimeoutError as exc:
                await checkpoint(
                    {
                        "phase": "CREATE_RESULT_UNKNOWN",
                        "activity": "AD_ACCOUNT_RESPONSE_UNCONFIRMED",
                        "activity_at": int(time.time()),
                    }
                )
                raise BrowserBusinessError(
                    "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                    (
                        "Meta Add-RK CREATE passed the network gate but the "
                        "response was not observed. Reconcile inventory before retry."
                    ),
                    retryable=True,
                    diagnostic=await self._diagnostic(
                        "ad_account_create_response_missing"
                    ),
                ) from exc

            try:
                raw = await response.text()
                payload = _decode_graphql_text(raw)
            except Exception:
                payload = None

            request_meta = _request_graphql_meta(response.request)
            friendly = _clean(request_meta.get("friendly_name"))

            ad_account_id = ""
            response_path = ""
            if payload is not None:
                ad_account_id, response_path = (
                    _extract_created_ad_account_id(payload)
                )

            meta_errors = _graphql_error_details(payload)
            if not ad_account_id and meta_errors:
                await checkpoint(
                    {
                        "phase": "CREATE_REJECTED",
                        "activity": "AD_ACCOUNT_CREATE_REJECTED",
                        "activity_at": int(time.time()),
                        "meta_errors": meta_errors,
                        "create_friendly_name": friendly,
                    }
                )
                parts: list[str] = []
                for row in meta_errors[:3]:
                    code = _clean(row.get("code"))
                    subcode = _clean(row.get("subcode"))
                    message = _clean(row.get("message"))
                    prefix = "/".join(
                        value for value in (code, subcode) if value
                    )
                    if prefix and message:
                        parts.append(f"{prefix}: {message}")
                    elif message:
                        parts.append(message)
                    elif prefix:
                        parts.append(prefix)

                raise BrowserBusinessError(
                    "META_AD_ACCOUNT_CREATE_REJECTED",
                    (
                        " · ".join(parts)
                        or "Meta rejected Ad Account creation."
                    )[:2500],
                    retryable=_meta_error_retryable(meta_errors),
                    diagnostic={
                        "meta_errors": meta_errors,
                        "request": self._safe_graphql_request_summary(
                            response.request
                        ),
                    },
                )

            if not ad_account_id:
                await checkpoint(
                    {
                        "phase": "CREATE_RESULT_UNKNOWN",
                        "activity": "AD_ACCOUNT_ID_UNCONFIRMED",
                        "activity_at": int(time.time()),
                        "create_friendly_name": friendly,
                    }
                )
                raise BrowserBusinessError(
                    "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                    (
                        "Meta returned an Add-RK response but ReMask could not "
                        "prove a numeric Ad Account ID. Reconcile inventory before retry."
                    ),
                    retryable=True,
                    diagnostic={
                        "request": self._safe_graphql_request_summary(
                            response.request
                        ),
                    },
                )

            await checkpoint(
                {
                    "phase": "CREATE_CONFIRMED",
                    "activity": "AD_ACCOUNT_CREATE_CONFIRMED",
                    "activity_at": int(time.time()),
                    "ad_account_id": ad_account_id,
                    "create_friendly_name": friendly,
                    "create_response_path": response_path,
                }
            )
            return BrowserAdAccountResult(
                business_id=business,
                ad_account_id=ad_account_id,
                response_friendly_name=friendly,
                response_doc_id=_clean(request_meta.get("doc_id")),
                response_path=response_path,
            )

        finally:
            if not gate_future.done():
                gate_future.cancel()
            if not response_future.done():
                response_future.cancel()
            try:
                self.page.remove_listener("response", observe_response)
            except Exception:
                pass
            try:
                await self.page.unroute("**/api/graphql/**", gate)
            except Exception:
                pass

    async def capture_ad_account_create_request(
        self,
        *,
        business_id: str,
    ) -> dict[str, Any]:
        """Capture Meta's current Add-RK private GraphQL request without sending CREATE."""
        business = _digits(business_id)
        if not business:
            raise BrowserBusinessError(
                "INVALID_BUSINESS_ID",
                "Ad Account capture requires a numeric Business ID.",
                retryable=False,
            )
        if self.page is None:
            await self.open()

        canary_name = f"ReMask RK Canary {int(time.time())}"
        opened = False
        for template in self.SETTINGS_AD_ACCOUNTS_URLS:
            try:
                await self._goto(template.format(business_id=business))
                body = (await self._body_text()).casefold()
                if (
                    business in _clean(self.page.url)
                    or "ad account" in body
                    or "реклам" in body
                    or "werbekonto" in body
                    or "compte publicitaire" in body
                ):
                    opened = True
                    break
            except BrowserBusinessError as exc:
                if exc.code in {
                    "SESSION_EXPIRED",
                    "CHECKPOINT_REQUIRED",
                    "TWO_FACTOR_REQUIRED",
                    "FACEBOOK_TEMPORARILY_BLOCKED",
                }:
                    raise

        if not opened:
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_UI_UNAVAILABLE",
                "Meta Business Settings Ad Accounts surface could not be opened.",
                retryable=True,
                diagnostic=await self._diagnostic("ad_account_settings_unavailable"),
            )

        entry_clicked = await self._click_named(self.AD_ACCOUNT_CREATE_ENTRY_NAMES)
        if not entry_clicked:
            if await self._click_named(self.ADD_NAMES):
                await self.page.wait_for_timeout(350)
                entry_clicked = await self._click_named(
                    self.AD_ACCOUNT_CREATE_ENTRY_NAMES
                )
        if not entry_clicked:
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_UI_CHANGED",
                "Meta Ad Account create entry was not found.",
                retryable=True,
                diagnostic=await self._diagnostic("ad_account_create_entry_missing"),
            )

        await self.page.wait_for_timeout(500)
        name_filled = await self._fill_first(
            labels=(
                "Ad account name",
                "Advertising account name",
                "Account name",
                "Название рекламного аккаунта",
                "Название аккаунта",
                "Назва рекламного акаунта",
                "Name des Werbekontos",
                "Nom du compte publicitaire",
            ),
            value=canary_name,
        )
        if not name_filled:
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_UI_CHANGED",
                "Meta Ad Account form opened but the account-name field was not found.",
                retryable=True,
                diagnostic=await self._diagnostic("ad_account_name_input_missing"),
            )

        loop = asyncio.get_running_loop()
        captured: asyncio.Future[dict[str, Any]] = loop.create_future()

        async def intercept(route: Any, request: Any) -> None:
            try:
                method = _clean(request.method).upper()
                host = _clean(urlsplit(_clean(request.url)).hostname).lower()
            except Exception:
                await route.continue_()
                return
            if method != "POST" or not (
                host == "facebook.com" or host.endswith(".facebook.com")
            ):
                await route.continue_()
                return

            meta = _request_graphql_meta(request)
            raw = _clean(getattr(request, "post_data", ""))
            decoded = unquote_plus(raw).casefold()
            friendly = _clean(meta.get("friendly_name"))
            looks_like_create = (
                "adaccount" in friendly.casefold()
                and "create" in friendly.casefold()
            )
            contains_canary = canary_name.casefold() in decoded
            if not (contains_canary or looks_like_create):
                await route.continue_()
                return

            parsed = parse_qs(raw, keep_blank_values=True) if raw else {}
            allowed = {
                "__aaid","__bid","__hs","__hblp","__hsdp","__rev","__s",
                "__hsi","__dyn","__csr","__comet_req","__spin_r","__spin_b",
                "__spin_t","__jssesw","__crn","__req","__ccg","dpr",
                "server_timestamps","fb_api_caller_class",
            }
            envelope = {
                key: _clean(values[0])
                for key, values in parsed.items()
                if key in allowed and isinstance(values, list) and values and _clean(values[0])
            }
            row = {
                "doc_id": _clean(meta.get("doc_id")),
                "friendly_name": friendly,
                "endpoint_url": _clean(getattr(request, "url", "")),
                "variables": (
                    meta.get("variables")
                    if isinstance(meta.get("variables"), dict)
                    else {}
                ),
                "request_envelope": envelope,
                "canary_name": canary_name,
                "business_id": business,
                "source": "live_business_settings_capture",
            }
            await route.abort()
            if not captured.done():
                captured.set_result(row)

        await self.page.route("**/*", intercept)
        try:
            for _ in range(6):
                if captured.done():
                    break
                if not await self._click_named(self.AD_ACCOUNT_SUBMIT_NAMES):
                    break
                try:
                    await asyncio.wait_for(asyncio.shield(captured), timeout=1.8)
                    break
                except asyncio.TimeoutError:
                    await self.page.wait_for_timeout(250)

            if not captured.done():
                try:
                    row = await asyncio.wait_for(
                        asyncio.shield(captured),
                        timeout=6.0,
                    )
                except asyncio.TimeoutError as exc:
                    raise BrowserBusinessError(
                        "AD_ACCOUNT_CREATE_REQUEST_NOT_OBSERVED",
                        "Meta Ad Account form was filled, but no private CREATE request was observed. No CREATE reached Meta.",
                        retryable=True,
                        diagnostic=await self._diagnostic("ad_account_create_request_missing"),
                    ) from exc
            else:
                row = captured.result()
        finally:
            try:
                await self.page.unroute("**/*", intercept)
            except Exception:
                pass

        if not _digits(row.get("doc_id")) or not isinstance(row.get("variables"), dict):
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_MUTATION_NOT_CAPTURED",
                "Blocked Add-RK action did not expose a usable GraphQL doc_id and variables. No CREATE reached Meta.",
                retryable=True,
                diagnostic=await self._diagnostic("ad_account_create_request_not_graphql"),
            )
        return row

    async def _fill_first(
        self,
        *,
        labels: tuple[str, ...],
        value: str,
        input_type: str | None = None,
    ) -> bool:
        if self.page is None or not value:
            return False

        for label in labels:
            pattern = re.compile(re.escape(label), re.IGNORECASE)
            try:
                locator = self.page.get_by_label(pattern)
                if await locator.count() and await locator.first.is_visible():
                    await locator.first.fill(value)
                    return True
            except Exception:
                pass

            try:
                locator = self.page.get_by_placeholder(pattern)
                if await locator.count() and await locator.first.is_visible():
                    await locator.first.fill(value)
                    return True
            except Exception:
                pass

        # Meta's 2026 creation dialog renders several inputs without
        # name/aria/placeholder and does not consistently wire <label for=>.
        # Find a visible label-like text node and fill the nearest ancestor's
        # visible input through Playwright so React still receives input events.
        for label in labels:
            pattern = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
            try:
                text_nodes = self.page.get_by_text(pattern)
                count = min(await text_nodes.count(), 8)
            except Exception:
                count = 0

            for index in range(count):
                try:
                    text_node = text_nodes.nth(index)
                    if not await text_node.is_visible():
                        continue
                    nearby = text_node.locator(
                        'xpath=ancestor::*[.//input and not(self::body)][1]//input'
                    )
                    input_count = min(await nearby.count(), 6)
                    for input_index in range(input_count):
                        candidate = nearby.nth(input_index)
                        if not await candidate.is_visible() or not await candidate.is_editable():
                            continue
                        candidate_type = _clean(
                            await candidate.get_attribute("type")
                        ).lower()
                        if candidate_type in {"hidden", "checkbox", "radio", "submit", "button"}:
                            continue
                        await candidate.fill(value)
                        return True
                except Exception:
                    continue

        selector = "input"
        if input_type:
            selector += f'[type="{input_type}"]'

        try:
            candidates = self.page.locator(selector)
            count = min(await candidates.count(), 20)
        except Exception:
            return False

        label_tokens = tuple(token.lower() for token in labels)
        for index in range(count):
            locator = candidates.nth(index)
            try:
                if not await locator.is_visible():
                    continue
                key = " ".join(
                    _clean(await locator.get_attribute(attr))
                    for attr in ("name", "id", "placeholder", "aria-label")
                ).lower()
                if key and any(token.lower() in key for token in label_tokens):
                    await locator.fill(value)
                    return True
            except Exception:
                continue

        return False

    async def _click_named(
        self,
        names: tuple[str, ...],
        *,
        roles: tuple[str, ...] = ("button", "link", "menuitem"),
        before_click: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        if self.page is None:
            return False

        for name in names:
            pattern = re.compile(rf"^\s*{re.escape(name)}\s*$", re.IGNORECASE)
            for role in roles:
                try:
                    locator = self.page.get_by_role(role, name=pattern)
                    count = await locator.count()
                    for index in range(min(count, 6)):
                        item = locator.nth(index)
                        if await item.is_visible() and await item.is_enabled():
                            if before_click is not None:
                                await before_click()
                            await item.click()
                            return True
                except Exception:
                    continue

        return False

    async def _prepare_create_form(
        self,
        *,
        business_name: str,
        user_email: str,
        user_first_name: str,
        user_last_name: str,
        profile_display_name: str,
    ) -> None:
        already_on_home = False
        if self.page is not None:
            try:
                current = urlsplit(_clean(self.page.url))
                path = (current.path or "").rstrip("/")
                already_on_home = (
                    current.netloc.lower().endswith("business.facebook.com")
                    and path.startswith("/latest/home")
                )
            except Exception:
                already_on_home = False

        try:
            create_entry_ready = await asyncio.wait_for(
                self._open_create_entry(
                    open_form=True,
                    already_on_home=already_on_home,
                ),
                timeout=60.0,
            )
        except asyncio.TimeoutError as exc:
            diag = await self._diagnostic("create_form_timeout")
            if self._last_selector_diagnostic:
                diag = {
                    "selector_attempt": self._last_selector_diagnostic,
                    **diag,
                }
            raise BrowserBusinessError(
                "BUSINESS_CREATE_FORM_TIMEOUT",
                "Meta Business portfolio creation form did not become reachable within 60s.",
                retryable=True,
                diagnostic=diag,
            ) from exc

        if not create_entry_ready:
            diag = await self._diagnostic("create_form_unavailable")
            if self._last_selector_diagnostic:
                diag = {
                    "selector_attempt": self._last_selector_diagnostic,
                    **diag,
                }

            current_url = _clean(self.page.url if self.page else "")
            try:
                current_query = parse_qs(urlsplit(current_url).query)
            except Exception:
                current_query = {}

            redirected_asset_id = _digits(
                (
                    current_query.get("asset_id")
                    or current_query.get("assetId")
                    or [""]
                )[0]
            )
            if redirected_asset_id:
                diag["asset_context_redirect"] = True
                diag["redirected_asset_id"] = redirected_asset_id

            raise BrowserBusinessError(
                "BUSINESS_CREATE_UI_UNAVAILABLE",
                "Meta Business portfolio creation form could not be opened.",
                retryable=True,
                diagnostic=diag,
            )

        if not await self._form_ready():
            diag = await self._diagnostic("create_form_false_positive")
            if self._last_selector_diagnostic:
                diag = {
                    "selector_attempt": self._last_selector_diagnostic,
                    **diag,
                }
            raise BrowserBusinessError(
                "BUSINESS_CREATE_FORM_LOST",
                "Meta returned to Business Suite before the Business creation form became usable.",
                retryable=True,
                diagnostic=diag,
            )

        business_filled = await self._fill_first(
            labels=(
                "Business portfolio name",
                "Business name",
                "Business and account name",
                "Название бизнес-портфолио",
                "Название компании",
                "Назва бізнес-портфоліо",
                "Назва компанії",
                "Business-Portfolio-Name",
                "Name des Business-Portfolios",
                "Unternehmensname",
                "Nom du portefeuille business",
                "Nom du portefeuille professionnel",
                "Nom de l’entreprise",
                "Nom de l'entreprise",
            ),
            value=business_name,
        )

        display_name = _clean(profile_display_name)
        if not display_name:
            display_name = " ".join(
                value
                for value in (_clean(user_first_name), _clean(user_last_name))
                if value
            ).strip()

        if display_name:
            await self._fill_first(
                labels=(
                    "Your name",
                    "Name",
                    "Ваше имя",
                    "Ваше ім'я",
                    "Dein Name",
                    "Ihr Name",
                    "Votre nom",
                    "Nom complet",
                ),
                value=display_name,
            )

        if user_first_name:
            await self._fill_first(
                labels=("First name", "Имя", "Ім'я", "Vorname", "Prénom"),
                value=user_first_name,
            )
        if user_last_name:
            await self._fill_first(
                labels=("Last name", "Фамилия", "Прізвище", "Nachname", "Nom"),
                value=user_last_name,
            )

        email_filled = await self._fill_first(
            labels=(
                "Business email",
                "Business email address",
                "Email",
                "Рабочий электронный адрес",
                "Электронный адрес компании",
                "Робоча електронна адреса",
                "Електронна адреса компанії",
                "Geschäftliche E-Mail-Adresse",
                "Geschäftliche Email-Adresse",
                "Geschäftliche E-Mail",
                "Adresse e-mail professionnelle",
                "Adresse e-mail de l’entreprise",
                "Adresse e-mail de l'entreprise",
                "E-mail professionnel",
            ),
            value=user_email,
            input_type="email",
        )

        # Fallback for current registration form variants with unlabeled inputs.
        if not business_filled:
            try:
                text_inputs = self.page.locator(
                    'input:not([type]), input[type="text"]'
                )
                count = await text_inputs.count()
                for index in range(count):
                    candidate = text_inputs.nth(index)
                    if await candidate.is_visible():
                        current = _clean(await candidate.input_value())
                        if not current:
                            await candidate.fill(business_name)
                            business_filled = True
                            break
            except Exception:
                pass

        if not email_filled:
            try:
                email_inputs = self.page.locator('input[type="email"]')
                if await email_inputs.count() and await email_inputs.first.is_visible():
                    await email_inputs.first.fill(user_email)
                    email_filled = True
            except Exception:
                pass

        # Confirmed live 2026 Meta form fallback. The current dialog exposes
        # four visible text inputs with almost no DOM metadata:
        #   0 portfolio name, 1 first name, 2 last name, 3 business email.
        # Only use this positional mapping when the full four-field surface is
        # present; do not apply it to unrelated/shorter form variants.
        try:
            visible_inputs = self.page.locator(
                'input:visible:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])'
            )
            visible_count = await visible_inputs.count()
            if await self._form_ready() and visible_count >= 4:
                first_value = _clean(user_first_name)
                last_value = _clean(user_last_name)
                if not first_value or not last_value:
                    name_parts = [
                        part
                        for part in re.split(r"\s+", _clean(profile_display_name))
                        if part
                    ]
                    if not first_value and name_parts:
                        first_value = name_parts[0]
                    if not last_value and len(name_parts) > 1:
                        last_value = " ".join(name_parts[1:])

                current_business = _clean(
                    await visible_inputs.nth(0).input_value()
                )
                if current_business != business_name:
                    await visible_inputs.nth(0).fill(business_name)
                business_filled = True

                if first_value:
                    current = _clean(await visible_inputs.nth(1).input_value())
                    if current != first_value:
                        await visible_inputs.nth(1).fill(first_value)

                if last_value:
                    current = _clean(await visible_inputs.nth(2).input_value())
                    if current != last_value:
                        await visible_inputs.nth(2).fill(last_value)

                current_email = _clean(
                    await visible_inputs.nth(3).input_value()
                )
                if current_email != user_email:
                    await visible_inputs.nth(3).fill(user_email)
                email_filled = True
        except Exception:
            pass

        if not business_filled:
            diag = await self._diagnostic("business_name_field_missing")
            raise BrowserBusinessError(
                "CREATE_UI_CHANGED",
                "Meta Business creation form did not expose a Business name field.",
                retryable=False,
                diagnostic=diag,
            )

        if not email_filled:
            diag = await self._diagnostic("business_email_field_missing")
            raise BrowserBusinessError(
                "CREATE_UI_CHANGED",
                "Meta Business creation form did not expose a Business email field.",
                retryable=False,
                diagnostic=diag,
            )

    @staticmethod
    def _request_matches_create(request: Any, business_name: str) -> bool:
        meta = _request_graphql_meta(request)
        if meta["method"] != "POST":
            return False
        if "graphql" not in str(meta["url"]).lower():
            return False

        expected = _clean(business_name).casefold()
        if not expected:
            return False

        friendly = _clean(meta["friendly_name"]).casefold()
        decoded = _clean(meta["decoded_raw"]).casefold()
        input_data = meta["input"] if isinstance(meta["input"], dict) else {}

        operation_markers = (
            "businesscreation",
            "businesscreate",
            "createbusiness",
            "create_business",
            "business_creation",
        )
        operation_match = any(
            marker in friendly or marker in decoded
            for marker in operation_markers
        )

        candidate_names = []
        for key in (
            "business_name",
            "businessName",
            "name",
            "portfolio_name",
            "portfolioName",
        ):
            value = input_data.get(key)
            if isinstance(value, str) and value.strip():
                candidate_names.append(value.strip().casefold())

        name_match = (
            expected in candidate_names
            or expected in decoded
        )

        # The live canary has repeatedly observed
        # useBusinessCreationMutationMutation with input.business_name.
        # Prefer that structured evidence over fragile raw substring scanning.
        return operation_match and name_match

    @staticmethod
    def _response_matches_create(response: Any, business_name: str) -> bool:
        try:
            return FacebookBusinessBrowser._request_matches_create(
                response.request,
                business_name,
            )
        except Exception:
            return False

    async def _submit_create_and_observe(
        self,
        business_name: str,
        *,
        before_submit: CheckpointCallback | None = None,
    ) -> tuple[str, str, str]:
        if self.page is None:
            raise BrowserBusinessError(
                "BROWSER_NOT_OPEN",
                "Facebook browser page is not open.",
                retryable=False,
            )

        loop = asyncio.get_running_loop()
        gate_future: asyncio.Future[bool] = loop.create_future()

        async def gate(route: Any, request: Any) -> None:
            if not self._request_matches_create(request, business_name):
                await route.continue_()
                return

            if gate_future.done():
                await route.continue_()
                return

            try:
                if before_submit is not None:
                    await before_submit(
                        {
                            "phase": "CREATE_SUBMITTED",
                            "activity": "CREATE_SUBMITTED",
                            "activity_at": int(time.time()),
                            "submitted_at": int(time.time()),
                            "network_gate": "before_meta_send",
                        }
                    )
            except Exception as exc:
                try:
                    await route.abort()
                finally:
                    if not gate_future.done():
                        gate_future.set_exception(
                            BrowserBusinessError(
                                "CREATE_CHECKPOINT_FAILED_BEFORE_SEND",
                                (
                                    "ReMask intercepted Meta CREATE but could not "
                                    "persist the submitted checkpoint, so the "
                                    "request was blocked before reaching Meta."
                                ),
                                retryable=True,
                            )
                        )
                return

            await route.continue_()
            if not gate_future.done():
                gate_future.set_result(True)

        await self.page.route("**/api/graphql/**", gate)

        try:
            if not await self._form_ready():
                if before_submit is not None:
                    await before_submit(
                        {
                            "phase": "CREATE_NOT_SUBMITTED",
                            "activity": "CREATE_NOT_SUBMITTED",
                            "activity_at": int(time.time()),
                            "not_submitted_at": int(time.time()),
                        }
                    )
                diag = await self._diagnostic("create_form_missing_before_submit")
                raise BrowserBusinessError(
                    "BUSINESS_CREATE_FORM_LOST",
                    "Meta Business creation form disappeared before submit.",
                    retryable=True,
                    diagnostic=diag,
                )

            if before_submit is not None:
                await before_submit(
                    {
                        "phase": "CREATE_CLICK_INTENT",
                        "activity": "CREATE_CLICK_INTENT",
                        "activity_at": int(time.time()),
                        "click_intent_at": int(time.time()),
                    }
                )

            async with self.page.expect_response(
                lambda response: self._response_matches_create(
                    response,
                    business_name,
                ),
                timeout=self.timeout_ms,
            ) as response_info:
                clicked = await self._click_named(self.CREATE_NAMES)
                if not clicked:
                    clicked = await self._click_named(self.SUBMIT_NAMES)

                if not clicked:
                    if before_submit is not None:
                        await before_submit(
                            {
                                "phase": "CREATE_NOT_SUBMITTED",
                                "activity": "CREATE_NOT_SUBMITTED",
                                "activity_at": int(time.time()),
                                "not_submitted_at": int(time.time()),
                            }
                        )
                    diag = await self._diagnostic("create_submit_missing")
                    raise BrowserBusinessError(
                        "CREATE_UI_CHANGED",
                        "Meta Business creation submit button was not found.",
                        retryable=False,
                        diagnostic=diag,
                    )

            response_task = asyncio.ensure_future(response_info.value)
            gate_task = asyncio.ensure_future(gate_future)

            done, _ = await asyncio.wait(
                {response_task, gate_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )

            if gate_task in done and gate_task.exception() is not None:
                response_task.cancel()
                await asyncio.gather(response_task, return_exceptions=True)
                raise gate_task.exception()

            response = await response_task
            if not gate_future.done():
                await asyncio.wait_for(gate_future, timeout=2.0)

            raw = await response.text()
            payload = _decode_graphql_text(raw)

            business_id = ""
            response_path = ""
            if payload is not None:
                business_id, response_path = _extract_created_business_id(payload)

            friendly = ""
            try:
                request_meta = _request_graphql_meta(response.request)
                friendly = _clean(request_meta.get("friendly_name"))
            except Exception:
                friendly = ""

            meta_errors = _graphql_error_details(payload)
            if not business_id and meta_errors:
                retryable = _meta_error_retryable(meta_errors)
                if before_submit is not None:
                    await before_submit(
                        {
                            "phase": "CREATE_REJECTED",
                            "activity": "CREATE_REJECTED",
                            "activity_at": int(time.time()),
                            "meta_errors": meta_errors,
                            "response_friendly_name": friendly,
                        }
                    )

                parts = []
                for row in meta_errors[:3]:
                    code = _clean(row.get("code"))
                    subcode = _clean(row.get("subcode"))
                    message = _clean(row.get("message"))
                    prefix = "/".join(value for value in (code, subcode) if value)
                    if prefix and message:
                        parts.append(f"{prefix}: {message}")
                    elif message:
                        parts.append(message)
                    elif prefix:
                        parts.append(prefix)

                error_message = " · ".join(parts) or "Meta rejected Business creation."
                raise BrowserBusinessError(
                    "META_CREATE_REJECTED",
                    error_message[:2500],
                    retryable=retryable,
                    diagnostic={
                        "meta_errors": meta_errors,
                        "request": self._safe_graphql_request_summary(
                            response.request
                        ),
                    },
                )

            return business_id, friendly, response_path

        except BrowserBusinessError:
            raise
        except Exception:
            # If CREATE was actually sent, the network gate has already
            # persisted CREATE_SUBMITTED. A missing response is reconciled from
            # the Business portfolio inventory and is never blindly retried.
            return "", "", ""
        finally:
            if not gate_future.done():
                gate_future.cancel()
            try:
                await self.page.unroute("**/api/graphql/**", gate)
            except Exception:
                pass

    async def reconcile_created_business(
        self,
        *,
        before_ids: list[str] | set[str],
        business_name: str,
    ) -> BrowserCreateResult:
        before = {_digits(value) for value in before_ids}
        before.discard("")

        after_map = await self.snapshot_businesses()
        after = set(after_map)
        created = sorted(after - before)

        if len(created) == 1:
            return BrowserCreateResult(
                business_id=created[0],
                before_ids=sorted(before),
                after_ids=sorted(after),
                recovered=True,
            )

        if len(created) > 1:
            # Prefer a unique link label matching the requested Business name.
            expected = business_name.casefold()
            named = [
                business_id
                for business_id in created
                if _clean(after_map.get(business_id)).casefold() == expected
            ]
            if len(named) == 1:
                return BrowserCreateResult(
                    business_id=named[0],
                    before_ids=sorted(before),
                    after_ids=sorted(after),
                    recovered=True,
                )

            raise BrowserBusinessError(
                "CREATE_RECONCILIATION_AMBIGUOUS",
                (
                    "Multiple new Business portfolios appeared after CREATE; "
                    "ReMask will not guess which one belongs to this job."
                ),
                retryable=False,
                diagnostic={
                    "before_ids": sorted(before),
                    "after_ids": sorted(after),
                    "new_ids": created,
                },
            )

        raise BrowserBusinessError(
            "CREATE_RESULT_UNKNOWN",
            (
                "CREATE was submitted but no uniquely new Business portfolio "
                "could be confirmed. Retry is verification-only; ReMask will "
                "not submit CREATE again."
            ),
            retryable=True,
            diagnostic={
                "before_ids": sorted(before),
                "after_ids": sorted(after),
            },
        )

    async def create_business(
        self,
        *,
        business_name: str,
        user_email: str,
        user_first_name: str = "",
        user_last_name: str = "",
        profile_display_name: str = "",
        before_snapshot: dict[str, str] | None = None,
        before_submit: CheckpointCallback | None = None,
    ) -> BrowserCreateResult:
        name = _clean(business_name)
        email = _clean(user_email)

        if not name:
            raise BrowserBusinessError(
                "INVALID_INPUT",
                "Business name is required.",
                retryable=False,
            )
        if not email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise BrowserBusinessError(
                "BUSINESS_EMAIL_REQUIRED",
                "A valid Business email is required by the Meta creation form.",
                retryable=False,
            )

        before_map = before_snapshot
        if before_map is None:
            before_map = await self.snapshot_businesses()

        async def create_checkpoint(patch: dict[str, Any]) -> None:
            if before_submit is None:
                return
            await before_submit(
                {
                    **patch,
                    "business_name": name,
                    "business_ids_before": sorted(before_map),
                }
            )

        await create_checkpoint(
            {
                "activity": "CREATE_FORM_OPENING",
                "activity_at": int(time.time()),
            }
        )

        await self._prepare_create_form(
            business_name=name,
            user_email=email,
            user_first_name=_clean(user_first_name),
            user_last_name=_clean(user_last_name),
            profile_display_name=_clean(profile_display_name),
        )

        await create_checkpoint(
            {
                "activity": "CREATE_FORM_READY",
                "activity_at": int(time.time()),
            }
        )

        response_business_id, friendly, response_path = (
            await self._submit_create_and_observe(
                name,
                before_submit=create_checkpoint,
            )
        )

        await create_checkpoint(
            {
                "activity": (
                    "CREATE_RESPONSE_OBSERVED"
                    if response_business_id
                    else "CREATE_RESPONSE_UNCONFIRMED"
                ),
                "activity_at": int(time.time()),
                "create_response_business_id": response_business_id,
                "create_response_friendly_name": friendly,
                "create_response_path": response_path,
            }
        )

        before_ids = set(before_map)

        # The response belongs to the exact CREATE mutation that passed the
        # network gate. A numeric ID from a known CREATE response path is
        # authoritative Meta evidence; Business Suite inventory can lag behind
        # the mutation response and must not turn a success into a timeout.
        if response_business_id and response_path:
            return BrowserCreateResult(
                business_id=response_business_id,
                before_ids=sorted(before_ids),
                after_ids=sorted(before_ids | {response_business_id}),
                response_business_id=response_business_id,
                response_friendly_name=friendly,
                response_path=response_path,
            )

        await self.page.wait_for_timeout(1800)

        # If the response was missing or unparseable, fall back to UI
        # reconciliation before declaring the result unknown.
        await create_checkpoint(
            {
                "activity": "VERIFY_CREATE_INVENTORY",
                "activity_at": int(time.time()),
            }
        )
        after_map = await self.snapshot_businesses()
        after_ids = set(after_map)

        created = sorted(after_ids - before_ids)
        if len(created) == 1:
            return BrowserCreateResult(
                business_id=created[0],
                before_ids=sorted(before_ids),
                after_ids=sorted(after_ids),
                response_business_id=response_business_id,
                response_friendly_name=friendly,
                response_path=response_path,
            )

        await create_checkpoint(
            {
                "activity": "RECONCILE_CREATE",
                "activity_at": int(time.time()),
            }
        )
        return await self.reconcile_created_business(
            before_ids=sorted(before_ids),
            business_name=name,
        )

    async def verify_page_attached(self, *, business_id: str, page_id: str) -> bool:
        business = _digits(business_id)
        page = _digits(page_id)
        if not business or not page:
            return False

        await self._goto(self.SETTINGS_PAGES_URL.format(business_id=business))
        await self.page.wait_for_timeout(1200)

        try:
            content = await self.page.content()
        except Exception:
            content = ""

        # The selected business ID is in the URL, while a Page ID appearing in
        # the rendered settings document indicates that asset is present.
        if page in content:
            return True

        body = await self._body_text()
        return page in body

    @staticmethod
    def _safe_graphql_request_summary(request: Any) -> dict[str, Any]:
        """Return non-secret request metadata for diagnostics/canaries."""
        meta = _request_graphql_meta(request)
        variables = (
            meta["variables"]
            if isinstance(meta.get("variables"), dict)
            else {}
        )
        raw_input = variables.get("input")
        input_keys = (
            sorted(str(key) for key in raw_input)
            if isinstance(raw_input, dict)
            else []
        )
        return {
            "url": _clean(meta.get("url")),
            "method": _clean(meta.get("method")),
            "friendly_name": _clean(meta.get("friendly_name")),
            "doc_id": _clean(meta.get("doc_id")),
            "variable_keys": sorted(str(key) for key in variables),
            "input_keys": input_keys,
            "body_decodable": bool(meta.get("body_decodable")),
        }

    @staticmethod
    def _request_matches_page_add(
        request: Any,
        *,
        business_id: str,
        page_id: str,
    ) -> bool:
        meta = _request_graphql_meta(request)
        if meta["method"] != "POST":
            return False
        if "graphql" not in str(meta["url"]).lower():
            return False

        business = _digits(business_id)
        page = _digits(page_id)
        if not business or not page:
            return False

        friendly = _clean(meta["friendly_name"]).casefold()
        decoded = _clean(meta["decoded_raw"])
        variables = (
            meta["variables"]
            if isinstance(meta.get("variables"), dict)
            else {}
        )

        try:
            variables_text = json.dumps(
                variables,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        except Exception:
            variables_text = ""

        evidence = decoded + "\n" + variables_text
        ids_match = business in evidence and page in evidence

        operation_match = (
            "mutation" in friendly
            and any(
                marker in friendly
                for marker in (
                    "addpage",
                    "pageadd",
                    "claimpage",
                    "pageclaim",
                    "businesspage",
                    "asset",
                )
            )
        ) or (
            "mutation" in decoded.casefold()
            and any(
                marker in decoded.casefold()
                for marker in (
                    "addpage",
                    "pageadd",
                    "claimpage",
                    "pageclaim",
                )
            )
        )

        return ids_match and operation_match

    @staticmethod
    def _response_matches_page_add(
        response: Any,
        *,
        business_id: str,
        page_id: str,
    ) -> bool:
        try:
            return FacebookBusinessBrowser._request_matches_page_add(
                response.request,
                business_id=business_id,
                page_id=page_id,
            )
        except Exception:
            return False

    async def preflight_page_add_form(
        self,
        *,
        business_id: str,
        page_id: str,
    ) -> dict[str, Any]:
        """
        Open and fill Meta's real Add existing Page dialog, then stop before
        any final Add/Confirm/Request approval action. Read-only/search
        requests are allowed; no Page mutation is submitted.
        """
        business = _digits(business_id)
        page = _digits(page_id)
        if not business or not page:
            raise BrowserBusinessError(
                "INVALID_PRIMARY_PAGE",
                "Business ID and Page ID must be numeric.",
                retryable=False,
            )

        if await self.verify_page_attached(
            business_id=business,
            page_id=page,
        ):
            return {
                "ready": True,
                "already_attached": True,
                "business_id": business,
                "page_id": page,
                "current_url": _clean(self.page.url if self.page else ""),
            }

        await self._goto(self.SETTINGS_PAGES_URL.format(business_id=business))

        if not await self._click_named(self.ADD_NAMES):
            diag = await self._diagnostic("page_preflight_add_button_missing")
            raise BrowserBusinessError(
                "PAGE_ADD_UI_CHANGED",
                "Meta Business Settings did not expose the Add Page action.",
                retryable=False,
                diagnostic=diag,
            )

        await self.page.wait_for_timeout(500)

        if not await self._click_named(self.ADD_EXISTING_PAGE_NAMES):
            body = (await self._body_text()).lower()
            if (
                "page id" not in body
                and "page url" not in body
                and "facebook page" not in body
            ):
                diag = await self._diagnostic(
                    "page_preflight_existing_action_missing"
                )
                raise BrowserBusinessError(
                    "PAGE_ADD_UI_CHANGED",
                    "Meta Business Settings did not expose Add existing Page.",
                    retryable=False,
                    diagnostic=diag,
                )

        page_filled = await self._fill_first(
            labels=(
                "Facebook Page URL or ID",
                "Page URL or ID",
                "Page ID",
                "Facebook Page",
                "URL или ID Страницы Facebook",
                "ID Страницы",
                "URL або ID сторінки Facebook",
                "ID сторінки",
                "Facebook-Seiten-URL oder -ID",
                "Seiten-URL oder -ID",
                "Seiten-ID",
                "Facebook-Seite",
            ),
            value=page,
        )
        if not page_filled:
            try:
                inputs = self.page.locator(
                    'input:not([type]), input[type="text"], input[type="search"]'
                )
                for index in range(min(await inputs.count(), 20)):
                    candidate = inputs.nth(index)
                    if await candidate.is_visible() and await candidate.is_editable():
                        await candidate.fill(page)
                        page_filled = True
                        break
            except Exception:
                pass

        if not page_filled:
            diag = await self._diagnostic("page_preflight_id_field_missing")
            raise BrowserBusinessError(
                "PAGE_ADD_UI_CHANGED",
                "Meta Page-add dialog did not expose a Page URL/ID field.",
                retryable=False,
                diagnostic=diag,
            )

        await self.page.wait_for_timeout(1200)

        # Selecting a search result is non-mutating. Stop before any
        # Add/Confirm/Request approval button is clicked.
        selected = False
        try:
            exact = self.page.get_by_text(
                re.compile(rf"^\s*{re.escape(page)}\s*$")
            )
            for index in range(min(await exact.count(), 5)):
                candidate = exact.nth(index)
                if not await candidate.is_visible():
                    continue
                target = candidate.locator(
                    'xpath=ancestor-or-self::*[@role="option" or @role="button" or self::button][1]'
                )
                if await target.count() and await target.first.is_visible():
                    await target.first.click()
                    selected = True
                    await self.page.wait_for_timeout(450)
                    break
        except Exception:
            pass

        if not selected:
            try:
                radios = self.page.get_by_role("radio")
                visible = []
                for index in range(min(await radios.count(), 8)):
                    item = radios.nth(index)
                    if await item.is_visible() and await item.is_enabled():
                        visible.append(item)
                if len(visible) == 1:
                    if not await visible[0].is_checked():
                        await visible[0].check()
                    selected = True
                    await self.page.wait_for_timeout(250)
            except Exception:
                pass

        body = await self._body_text()
        final_actions = []
        for label in (
            "Add Page",
            "Add Facebook Page",
            "Confirm",
            "Request approval",
            "Добавить Страницу",
            "Подтвердить",
            "Додати сторінку",
            "Підтвердити",
            "Seite hinzufügen",
            "Bestätigen",
        ):
            try:
                locator = self.page.get_by_role(
                    "button",
                    name=re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE),
                )
                if await locator.count() and await locator.first.is_visible():
                    final_actions.append(label)
            except Exception:
                continue

        return {
            "ready": True,
            "already_attached": False,
            "business_id": business,
            "page_id": page,
            "page_filled": True,
            "result_selected": selected,
            "final_actions": final_actions,
            "page_id_visible": page in body,
            "current_url": _clean(self.page.url),
        }

    async def add_existing_page(
        self,
        *,
        business_id: str,
        page_id: str,
        before_submit: CheckpointCallback | None = None,
    ) -> BrowserPageResult:
        business = _digits(business_id)
        page = _digits(page_id)
        if not business or not page:
            raise BrowserBusinessError(
                "INVALID_PRIMARY_PAGE",
                "Business ID and Page ID must be numeric.",
                retryable=False,
            )

        if await self.verify_page_attached(business_id=business, page_id=page):
            return BrowserPageResult(
                business_id=business,
                page_id=page,
                already_attached=True,
            )

        await self._goto(self.SETTINGS_PAGES_URL.format(business_id=business))

        if not await self._click_named(self.ADD_NAMES):
            diag = await self._diagnostic("page_add_button_missing")
            raise BrowserBusinessError(
                "PAGE_ADD_UI_CHANGED",
                "Meta Business Settings did not expose the Add Page action.",
                retryable=False,
                diagnostic=diag,
            )

        await self.page.wait_for_timeout(500)

        if not await self._click_named(self.ADD_EXISTING_PAGE_NAMES):
            # Some UI variants open directly into the existing-page dialog.
            body = (await self._body_text()).lower()
            if "page id" not in body and "page url" not in body and "facebook page" not in body:
                diag = await self._diagnostic("page_existing_action_missing")
                raise BrowserBusinessError(
                    "PAGE_ADD_UI_CHANGED",
                    "Meta Business Settings did not expose Add existing Page.",
                    retryable=False,
                    diagnostic=diag,
                )

        page_filled = await self._fill_first(
            labels=(
                "Facebook Page URL or ID",
                "Page URL or ID",
                "Page ID",
                "Facebook Page",
                "URL или ID Страницы Facebook",
                "ID Страницы",
                "URL або ID сторінки Facebook",
                "ID сторінки",
                "Facebook-Seiten-URL oder -ID",
                "Seiten-URL oder -ID",
                "Seiten-ID",
                "Facebook-Seite",
            ),
            value=page,
        )
        if not page_filled:
            try:
                inputs = self.page.locator(
                    'input:not([type]), input[type="text"], input[type="search"]'
                )
                count = await inputs.count()
                for index in range(count):
                    candidate = inputs.nth(index)
                    if await candidate.is_visible():
                        await candidate.fill(page)
                        page_filled = True
                        break
            except Exception:
                pass

        if not page_filled:
            diag = await self._diagnostic("page_id_field_missing")
            raise BrowserBusinessError(
                "PAGE_ADD_UI_CHANGED",
                "Meta Page-add dialog did not expose a Page URL/ID field.",
                retryable=False,
                diagnostic=diag,
            )

        loop = asyncio.get_running_loop()
        gate_future: asyncio.Future[bool] = loop.create_future()
        response_future: asyncio.Future[Any] = loop.create_future()

        def observe_response(response: Any) -> None:
            if response_future.done():
                return
            try:
                if self._response_matches_page_add(
                    response,
                    business_id=business,
                    page_id=page,
                ):
                    response_future.set_result(response)
            except Exception:
                return

        self.page.on("response", observe_response)

        async def gate(route: Any, request: Any) -> None:
            if not self._request_matches_page_add(
                request,
                business_id=business,
                page_id=page,
            ):
                await route.continue_()
                return

            if gate_future.done():
                await route.continue_()
                return

            try:
                if before_submit is not None:
                    await before_submit(
                        {
                            "phase": "PAGE_ADD_SUBMITTED",
                            "activity": "PAGE_ADD_SUBMITTED",
                            "activity_at": int(time.time()),
                            "business_id": business,
                            "primary_page_id": page,
                            "page_submitted_at": int(time.time()),
                            "network_gate": "before_meta_send",
                        }
                    )
            except Exception as exc:
                try:
                    await route.abort()
                finally:
                    if not gate_future.done():
                        gate_future.set_exception(
                            BrowserBusinessError(
                                "PAGE_CHECKPOINT_FAILED_BEFORE_SEND",
                                (
                                    "ReMask intercepted Meta Page-add but could "
                                    "not persist the submitted checkpoint, so "
                                    "the request was blocked before reaching Meta."
                                ),
                                retryable=True,
                                diagnostic=self._safe_graphql_request_summary(
                                    request
                                ),
                            )
                        )
                return

            await route.continue_()
            if not gate_future.done():
                gate_future.set_result(True)

        await self.page.route("**/api/graphql/**", gate)

        try:
            # Search results are often a selectable list before the review
            # step. Prefer an exact Page-ID result; otherwise select the only
            # visible option/radio if Meta rendered one.
            try:
                exact = self.page.get_by_text(
                    re.compile(rf"^\\s*{re.escape(page)}\\s*$")
                )
                count = min(await exact.count(), 5)
                for index in range(count):
                    candidate = exact.nth(index)
                    if not await candidate.is_visible():
                        continue
                    target = candidate.locator(
                        'xpath=ancestor-or-self::*[@role="option" or @role="button" or self::button][1]'
                    )
                    if await target.count() and await target.first.is_visible():
                        await target.first.click()
                        await self.page.wait_for_timeout(450)
                        break
            except Exception:
                pass

            try:
                radios = self.page.get_by_role("radio")
                visible_radios = []
                for index in range(min(await radios.count(), 8)):
                    item = radios.nth(index)
                    if await item.is_visible() and await item.is_enabled():
                        visible_radios.append(item)
                if len(visible_radios) == 1 and not await visible_radios[0].is_checked():
                    await visible_radios[0].check()
                    await self.page.wait_for_timeout(250)
            except Exception:
                pass

            sent = False
            clicked_any = False
            page_click_intent_written = False

            async def checkpoint_page_click_intent() -> None:
                nonlocal page_click_intent_written
                if page_click_intent_written or before_submit is None:
                    return
                await before_submit(
                    {
                        "phase": "PAGE_ADD_CLICK_INTENT",
                        "activity": "PAGE_ADD_CLICK_INTENT",
                        "activity_at": int(time.time()),
                        "business_id": business,
                        "primary_page_id": page,
                        "page_click_intent_at": int(time.time()),
                    }
                )
                page_click_intent_written = True

            for _ in range(7):
                if gate_future.done():
                    sent = gate_future.exception() is None
                    break

                # Meta can show a consent checkbox on the final review step.
                try:
                    checkboxes = self.page.get_by_role("checkbox")
                    for index in range(min(await checkboxes.count(), 12)):
                        checkbox = checkboxes.nth(index)
                        if not await checkbox.is_visible() or not await checkbox.is_enabled():
                            continue
                        if await checkbox.is_checked():
                            continue
                        label = " ".join(
                            [
                                _clean(await checkbox.get_attribute("aria-label")),
                                _clean(
                                    await checkbox.evaluate(
                                        "(e) => (e.parentElement && e.parentElement.innerText) || ''"
                                    )
                                )[:500],
                            ]
                        ).lower()
                        if any(
                            marker in label
                            for marker in (
                                "agree",
                                "terms",
                                "confirm",
                                "understand",
                                "соглас",
                                "подтверж",
                                "погодж",
                                "підтвер",
                                "zustimm",
                                "bestät",
                            )
                        ):
                            await checkbox.check()
                            await self.page.wait_for_timeout(200)
                except Exception:
                    pass

                final_clicked = await self._click_named(
                    (
                        "Add Page",
                        "Add Facebook Page",
                        "Add Page and Instagram",
                        "Confirm",
                        "Request approval",
                        "Добавить Страницу",
                        "Добавить страницу",
                        "Подтвердить",
                        "Додати сторінку",
                        "Підтвердити",
                        "Seite hinzufügen",
                        "Bestätigen",
                    ),
                    before_click=checkpoint_page_click_intent,
                )
                if final_clicked:
                    clicked_any = True
                    await self.page.wait_for_timeout(700)
                    if gate_future.done():
                        sent = gate_future.exception() is None
                        break
                    continue

                next_clicked = await self._click_named(
                    (
                        "Next",
                        "Continue",
                        "Review",
                        "Select",
                        "Далее",
                        "Продолжить",
                        "Проверить",
                        "Выбрать",
                        "Далі",
                        "Продовжити",
                        "Перевірити",
                        "Вибрати",
                        "Weiter",
                        "Fortfahren",
                        "Überprüfen",
                        "Auswählen",
                    )
                )
                if next_clicked:
                    clicked_any = True
                    await self.page.wait_for_timeout(700)
                    continue

                break

            if gate_future.done() and gate_future.exception() is not None:
                raise gate_future.exception()

            if gate_future.done() and gate_future.exception() is None:
                response = None
                try:
                    response = await asyncio.wait_for(
                        asyncio.shield(response_future),
                        timeout=min(8.0, float(self.timeout_seconds)),
                    )
                except asyncio.TimeoutError:
                    if before_submit is not None:
                        await before_submit(
                            {
                                "activity": "PAGE_ADD_RESPONSE_UNCONFIRMED",
                                "activity_at": int(time.time()),
                                "business_id": business,
                                "primary_page_id": page,
                            }
                        )
                except Exception:
                    response = None

                if response is not None:
                    try:
                        raw = await response.text()
                        payload = _decode_graphql_text(raw)
                    except Exception:
                        payload = None

                    meta_errors = _graphql_error_details(payload)
                    if meta_errors:
                        retryable = _meta_error_retryable(meta_errors)
                        if before_submit is not None:
                            await before_submit(
                                {
                                    "phase": "PAGE_ADD_REJECTED",
                                    "activity": "PAGE_ADD_REJECTED",
                                    "activity_at": int(time.time()),
                                    "business_id": business,
                                    "primary_page_id": page,
                                    "meta_errors": meta_errors,
                                }
                            )

                        parts = []
                        for row in meta_errors[:3]:
                            code = _clean(row.get("code"))
                            subcode = _clean(row.get("subcode"))
                            message = _clean(row.get("message"))
                            prefix = "/".join(
                                value for value in (code, subcode) if value
                            )
                            if prefix and message:
                                parts.append(f"{prefix}: {message}")
                            elif message:
                                parts.append(message)
                            elif prefix:
                                parts.append(prefix)

                        error_message = (
                            " · ".join(parts)
                            or "Meta rejected Page attachment."
                        )
                        raise BrowserBusinessError(
                            "META_PAGE_ADD_REJECTED",
                            error_message[:2500],
                            retryable=retryable,
                            diagnostic={
                                "meta_errors": meta_errors,
                                "request": self._safe_graphql_request_summary(
                                    response.request
                                ),
                            },
                        )

                    if before_submit is not None:
                        await before_submit(
                            {
                                "activity": "PAGE_ADD_RESPONSE_OBSERVED",
                                "activity_at": int(time.time()),
                                "business_id": business,
                                "primary_page_id": page,
                                "response_friendly_name": _clean(
                                    _request_graphql_meta(
                                        response.request
                                    ).get("friendly_name")
                                ),
                            }
                        )

            if not gate_future.done():
                # A non-GraphQL Meta variant may still have completed the
                # operation. If a FINAL action was clicked, preserve
                # PAGE_ADD_CLICK_INTENT until verification so retry cannot
                # blindly submit a second ownership request. Only mark
                # NOT_SUBMITTED when no final mutation-capable click happened.
                if before_submit is not None and not page_click_intent_written:
                    await before_submit(
                        {
                            "phase": "PAGE_ADD_NOT_SUBMITTED",
                            "business_id": business,
                            "primary_page_id": page,
                            "page_not_submitted_at": int(time.time()),
                        }
                    )

                if not clicked_any:
                    diag = await self._diagnostic("page_add_submit_missing")
                    raise BrowserBusinessError(
                        "PAGE_ADD_UI_CHANGED",
                        "Meta Page-add review/submit action was not found.",
                        retryable=False,
                        diagnostic=diag,
                    )

        except BrowserBusinessError:
            raise
        finally:
            if not gate_future.done():
                gate_future.cancel()
            if not response_future.done():
                response_future.cancel()
            try:
                self.page.remove_listener("response", observe_response)
            except Exception:
                pass
            try:
                await self.page.unroute("**/api/graphql/**", gate)
            except Exception:
                pass

        await self.page.wait_for_timeout(1500)

        if not await self.verify_page_attached(business_id=business, page_id=page):
            diag = await self._diagnostic("page_attach_unconfirmed")
            raise BrowserBusinessError(
                "PAGE_ATTACH_RESULT_UNKNOWN",
                (
                    "Meta Page add was submitted but the selected Page could "
                    "not be confirmed in Business Settings. Retry is "
                    "verification-only; ReMask will not blindly submit the "
                    "Page-add action again."
                ),
                retryable=True,
                diagnostic=diag,
            )

        return BrowserPageResult(
            business_id=business,
            page_id=page,
            already_attached=False,
        )


__all__ = [
    "BrowserAdAccountResult",
    "BrowserBusinessError",
    "BrowserCreateResult",
    "BrowserPageResult",
    "BrowserPreflightResult",
    "FacebookBusinessBrowser",
]
