from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, unquote, unquote_plus, urlsplit


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
    recovered: bool = False


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
            or value.get("errorDescription")
            or value.get("error_description")
            or value.get("description")
            or value.get("error_user_msg")
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
    SETTINGS_PAGES_URL = (
        "https://business.facebook.com/settings/pages/?business_id={business_id}"
    )

    CREATE_NAMES = (
        "Create a business portfolio",
        "Create business portfolio",
        "Create a business",
        "Create business",
        "Create account",
        "Создать бизнес-портфолио",
        "Создать бизнес",
        "Создать аккаунт",
        "Створити бізнес-портфоліо",
        "Створити бізнес",
        "Створити обліковий запис",
        "Business-Portfolio erstellen",
        "Unternehmensportfolio erstellen",
        "Business erstellen",
        "Portfolio erstellen",
    )

    ADD_NAMES = (
        "Add",
        "Добавить",
        "Додати",
        "Hinzufügen",
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
                    "--disable-background-networking",
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
                service_workers="block",
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
            return str(await self.page.locator("body").inner_text(timeout=5000) or "")
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
                retryable=True,
                diagnostic={"url": url},
            )

    async def _diagnostic(self, stage: str) -> dict[str, Any]:
        if self.page is None:
            return {}

        result = {
            "stage": _clean(stage),
            "url": _clean(self.page.url),
        }

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

        try:
            email_count = await self.page.locator('input[type="email"]').count()
        except Exception:
            email_count = 0

        try:
            text_count = await self.page.locator(
                'input:not([type]), input[type="text"]'
            ).count()
        except Exception:
            text_count = 0

        if email_count >= 1 and text_count >= 1:
            return True

        body = (await self._body_text()).lower()
        has_email = any(
            marker in body
            for marker in (
                "business email",
                "business email address",
                "рабочий электронный адрес",
                "электронный адрес компании",
                "робоча електронна адреса",
                "електронна адреса компанії",
                "geschäftliche e-mail-adresse",
                "geschäftliche email-adresse",
                "geschäftliche e-mail",
            )
        )
        has_name = any(
            marker in body
            for marker in (
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
            )
        )
        return has_email and has_name

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

    async def _try_open_top_left_portfolio_menu(self) -> bool:
        if self.page is None:
            return False

        if await self._has_create_surface():
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

                    const xs = [20, 52, 88, 124, 160, 192, 224];
                    const ys = [68, 82, 96, 110, 124, 138, 152, 166, 180, 194, 208, 224, 240, 256];
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
                                const tag = el.tagName || '';

                                if (r.x > 300 || r.y < 54 || r.y > 276) continue;
                                if (r.width < 70 || r.width > 280) continue;
                                if (r.height < 24 || r.height > 90) continue;
                                if (!text || /^Meta Business Suite$/i.test(text)) continue;
                                if (/^(Home|Startseite|Start|Главная|Головна)$/i.test(text)) continue;

                                rows.push({el, r, text, role, tabindex, tag});
                            }
                        }
                    }

                    rows.sort((a,b) => {
                        const ai = a.r.width * a.r.height;
                        const bi = b.r.width * b.r.height;
                        const aInteractive = (
                            a.role === 'button' ||
                            a.tag === 'BUTTON' ||
                            a.tabindex === '0'
                        ) ? 1 : 0;
                        const bInteractive = (
                            b.role === 'button' ||
                            b.tag === 'BUTTON' ||
                            b.tabindex === '0'
                        ) ? 1 : 0;
                        if (aInteractive !== bInteractive) {
                            return bInteractive - aInteractive;
                        }
                        // When Meta implements the selector as nested DIVs,
                        // prefer the largest container in the selector band so
                        // the synthetic click bubbles through the whole row.
                        return bi - ai || a.r.y - b.r.y;
                    });

                    const compact = rows.slice(0,12).map(row => ({
                        text:row.text,
                        role:row.role,
                        tag:row.tag,
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
                    "sidebar_probe": probe,
                }

                if probe.get("clicked"):
                    if await self._wait_for_create_surface():
                        return True

                    diagnostic = await self._diagnostic(
                        "portfolio_sidebar_selector_open_without_create"
                    )
                    self._last_selector_diagnostic = {
                        "sidebar_probe": probe,
                        **diagnostic,
                    }
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(120)
        except Exception as exc:
            self._last_selector_diagnostic = {
                "sidebar_probe_error": f"{exc.__class__.__name__}: {exc}"
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
                    if x > 260 or y < 105 or y > 210:
                        continue

                    text_value = _clean(await item.inner_text(timeout=1000))
                    aria = _clean(await item.get_attribute("aria-label"))
                    title = _clean(await item.get_attribute("title"))
                    key = " ".join((text_value, aria, title)).lower()

                    # Explicitly reject the logo control discovered by canary.
                    if text_value.casefold() == "meta business suite":
                        continue

                    score = 0
                    if any(token in key for token in ("business", "portfolio", "asset")):
                        score -= 100
                    if any(token in key for token in ("switch", "select", "account")):
                        score -= 50
                    score += int(y)
                    candidates.append((score, y, x, item))
                except Exception:
                    continue

        candidates.sort(key=lambda row: (row[0], row[1], row[2]))

        seen: set[tuple[int, int]] = set()
        for _, y, x, item in candidates[:10]:
            marker = (round(x), round(y))
            if marker in seen:
                continue
            seen.add(marker)
            try:
                await item.click(timeout=2500)
                if await self._wait_for_create_surface():
                    return True
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(120)
            except Exception:
                try:
                    await self.page.keyboard.press("Escape")
                except Exception:
                    pass

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

        menu_open = await self._try_open_top_left_portfolio_menu()
        if menu_open:
            if not open_form:
                return True

            if await self._click_named(self.CREATE_NAMES):
                await self._assert_authenticated()
                if await self._wait_for_form_ready():
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
                retryable=False,
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
            clicked = await self._click_named(
                (
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
                )
            )
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
        if not await self._open_create_entry(open_form=True):
            diag = await self._diagnostic("create_form_unavailable")
            raise BrowserBusinessError(
                "BUSINESS_CREATE_UI_UNAVAILABLE",
                "Meta Business portfolio creation form could not be opened.",
                retryable=False,
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
                ),
                value=display_name,
            )

        if user_first_name:
            await self._fill_first(
                labels=("First name", "Имя", "Ім'я", "Vorname"),
                value=user_first_name,
            )
        if user_last_name:
            await self._fill_first(
                labels=("Last name", "Фамилия", "Прізвище", "Nachname"),
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
            if visible_count >= 4:
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
    ) -> tuple[str, str]:
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
                    clicked = await self._click_named(
                        (
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
                        )
                    )

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
            if payload is not None:
                ids = _walk_business_ids(payload)
                if ids:
                    business_id = ids[0][0]

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

            return business_id, friendly

        except BrowserBusinessError:
            raise
        except Exception:
            # If CREATE was actually sent, the network gate has already
            # persisted CREATE_SUBMITTED. A missing response is reconciled from
            # the Business portfolio inventory and is never blindly retried.
            return "", ""
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

        response_business_id, friendly = await self._submit_create_and_observe(
            name,
            before_submit=create_checkpoint,
        )

        await create_checkpoint(
            {
                "activity": (
                    "CREATE_RESPONSE_OBSERVED"
                    if response_business_id
                    else "CREATE_RESPONSE_UNCONFIRMED"
                ),
                "activity_at": int(time.time()),
                "response_business_id": response_business_id,
                "response_friendly_name": friendly,
            }
        )

        await self.page.wait_for_timeout(1800)

        # Always verify through current UI state, even if GraphQL response
        # exposed an ID.
        await create_checkpoint(
            {
                "activity": "VERIFY_CREATE_INVENTORY",
                "activity_at": int(time.time()),
            }
        )
        after_map = await self.snapshot_businesses()
        after_ids = set(after_map)
        before_ids = set(before_map)

        if response_business_id and response_business_id in after_ids:
            return BrowserCreateResult(
                business_id=response_business_id,
                before_ids=sorted(before_ids),
                after_ids=sorted(after_ids),
                response_business_id=response_business_id,
                response_friendly_name=friendly,
            )

        created = sorted(after_ids - before_ids)
        if len(created) == 1:
            return BrowserCreateResult(
                business_id=created[0],
                before_ids=sorted(before_ids),
                after_ids=sorted(after_ids),
                response_business_id=response_business_id,
                response_friendly_name=friendly,
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
    "BrowserBusinessError",
    "BrowserCreateResult",
    "BrowserPageResult",
    "BrowserPreflightResult",
    "FacebookBusinessBrowser",
]
