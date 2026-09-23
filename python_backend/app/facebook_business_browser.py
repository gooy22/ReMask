from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import unquote, unquote_plus, urlsplit


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
        return None


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
                ],
            }
            proxy = _proxy_config(getattr(self.context, "proxy", None))
            if proxy:
                launch_kwargs["proxy"] = proxy

            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            self._browser_context = await self._browser.new_context(
                user_agent=_clean(getattr(self.context, "user_agent", "")),
                locale="en-US",
                viewport={"width": 1440, "height": 1000},
                service_workers="block",
            )

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
        page = self.page
        self.page = None

        if page is not None:
            try:
                await page.close()
            except Exception:
                pass

        if self._browser_context is not None:
            try:
                await self._browser_context.close()
            except Exception:
                pass
            self._browser_context = None

        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        if self._profile_lock_acquired and self._profile_lock is not None:
            self._profile_lock.release()
            self._profile_lock_acquired = False
        self._profile_lock = None

        self._release_semaphore()

    async def _goto(self, url: str) -> str:
        if self.page is None:
            await self.open()

        try:
            await self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
            await self.page.wait_for_timeout(1200)
        except Exception as exc:
            await self._diagnostic("navigation_error")
            raise BrowserBusinessError(
                "FACEBOOK_NAVIGATION_FAILED",
                f"Facebook navigation failed: {exc.__class__.__name__}: {exc}",
                retryable=True,
            ) from exc

        await self._assert_authenticated()
        return _clean(self.page.url)

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

    async def _try_open_top_left_portfolio_menu(self) -> bool:
        if self.page is None:
            return False

        if await self._has_create_surface():
            return True

        # Current Meta Business Suite places the business/page selector BELOW
        # the Meta Business Suite logo and ABOVE Home/Startseite. The selector
        # is not consistently exposed as a button/aria control, so first locate
        # it geometrically in that narrow left-sidebar band and click the
        # deepest visible element there. Do not click the Meta Business Suite
        # logo itself: live canary proved that is a different control.
        try:
            probe = await self.page.evaluate(
                """() => {
                    const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const label = (el) => [
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('title') || '',
                        el.innerText || el.textContent || ''
                    ].join(' ').replace(/\\s+/g, ' ').trim();

                    const all = Array.from(document.querySelectorAll('*'));
                    const home = all
                        .filter(visible)
                        .map(el => ({el, r: el.getBoundingClientRect(), text: label(el)}))
                        .filter(row =>
                            row.r.x < 230 &&
                            row.r.y > 120 &&
                            row.r.y < 260 &&
                            /^(Home|Startseite|Start|Главная|Головна)$/i.test(row.text)
                        )
                        .sort((a,b) => a.r.y - b.r.y)[0];

                    const homeY = home ? home.r.y : 205;
                    const rows = all
                        .filter(visible)
                        .map((el, index) => ({
                            el,
                            index,
                            r: el.getBoundingClientRect(),
                            text: label(el),
                            role: el.getAttribute('role') || '',
                            tabindex: el.getAttribute('tabindex') || '',
                            tag: el.tagName
                        }))
                        .filter(row => {
                            const r = row.r;
                            if (r.x > 220 || r.y < 118 || r.y >= homeY - 2) return false;
                            if (r.width < 90 || r.width > 225 || r.height < 28 || r.height > 85) return false;
                            if (!row.text || /^Meta Business Suite$/i.test(row.text)) return false;
                            if (/^(Home|Startseite|Start|Главная|Головна)$/i.test(row.text)) return false;
                            return true;
                        });

                    // Prefer the smallest/deepest candidate: this is normally
                    // the current Page/business selector itself rather than a
                    // large sidebar wrapper.
                    rows.sort((a,b) => {
                        const ai = a.r.width * a.r.height;
                        const bi = b.r.width * b.r.height;
                        const ar = a.role === 'button' || a.tag === 'BUTTON' || a.tabindex === '0' ? -100000 : 0;
                        const br = b.role === 'button' || b.tag === 'BUTTON' || b.tabindex === '0' ? -100000 : 0;
                        return (ar + ai) - (br + bi) || b.r.y - a.r.y;
                    });

                    const best = rows[0];
                    if (!best) {
                        return {
                            clicked:false,
                            homeY,
                            candidates: rows.slice(0,12).map(row => ({
                                text:row.text, role:row.role, tag:row.tag,
                                x:Math.round(row.r.x), y:Math.round(row.r.y),
                                w:Math.round(row.r.width), h:Math.round(row.r.height)
                            }))
                        };
                    }

                    best.el.click();
                    return {
                        clicked:true,
                        homeY,
                        clickedCandidate:{
                            text:best.text, role:best.role, tag:best.tag,
                            x:Math.round(best.r.x), y:Math.round(best.r.y),
                            w:Math.round(best.r.width), h:Math.round(best.r.height)
                        },
                        candidates: rows.slice(0,12).map(row => ({
                            text:row.text, role:row.role, tag:row.tag,
                            x:Math.round(row.r.x), y:Math.round(row.r.y),
                            w:Math.round(row.r.width), h:Math.round(row.r.height)
                        }))
                    };
                }"""
            )
            if isinstance(probe, dict) and probe.get("clicked"):
                await self.page.wait_for_timeout(650)
                if await self._has_create_surface():
                    return True

                # Preserve a compact probe before the larger diagnostic so the
                # exact clicked selector survives Railway log truncation.
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
                await self.page.wait_for_timeout(500)
                if await self._has_create_surface():
                    return True
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(120)
            except Exception:
                try:
                    await self.page.keyboard.press("Escape")
                except Exception:
                    pass

        return False

    async def _open_create_entry(self, *, open_form: bool) -> bool:
        entry_urls = (self.HOME_URL, self.OVERVIEW_URL)

        for entry_url in entry_urls:
            await self._goto(entry_url)

            if await self._form_ready():
                return True

            menu_open = await self._try_open_top_left_portfolio_menu()
            if menu_open:
                if not open_form:
                    return True

                if await self._click_named(self.CREATE_NAMES):
                    await self.page.wait_for_timeout(650)
                    await self._assert_authenticated()
                    if await self._form_ready():
                        return True

        # Legacy/no-portfolio fallback. Existing-portfolio accounts may redirect
        # this URL back to Home, so it is intentionally last.
        await self._goto(self.CREATE_URL)
        if await self._form_ready():
            return True

        if await self._try_open_top_left_portfolio_menu():
            if not open_form:
                return True
            if await self._click_named(self.CREATE_NAMES):
                await self.page.wait_for_timeout(650)
                await self._assert_authenticated()
                return await self._form_ready()

        return False

    async def preflight(self) -> BrowserPreflightResult:
        diagnostics: list[str] = []

        await self._goto(self.HOME_URL)
        diagnostics.append("home_authenticated")

        ready = await self._open_create_entry(open_form=False)
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

    async def snapshot_businesses(self) -> dict[str, str]:
        await self._goto(self.HOME_URL)

        # The home document often contains only the currently selected
        # portfolio. Open the real top-left portfolio selector first so the
        # rendered DOM also contains the other portfolios available to this
        # Facebook profile. This makes CREATE reconciliation useful even when
        # Meta does not switch the current portfolio after creation.
        selector_opened = False
        try:
            selector_opened = bool(
                await self.page.evaluate(
                    """() => {
                        const visible = (el) => {
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            return r.width > 0 && r.height > 0
                                && s.display !== 'none'
                                && s.visibility !== 'hidden'
                                && s.pointerEvents !== 'none';
                        };
                        const label = (el) => [
                            el.getAttribute('aria-label') || '',
                            el.getAttribute('title') || '',
                            el.innerText || el.textContent || ''
                        ].join(' ').replace(/\\s+/g, ' ').trim();

                        const all = Array.from(document.querySelectorAll('*'));
                        const home = all
                            .filter(visible)
                            .map(el => ({el, r:el.getBoundingClientRect(), text:label(el)}))
                            .filter(row =>
                                row.r.x < 230 &&
                                row.r.y > 120 &&
                                row.r.y < 260 &&
                                /^(Home|Startseite|Start|Главная|Головна)$/i.test(row.text)
                            )
                            .sort((a,b) => a.r.y - b.r.y)[0];
                        const homeY = home ? home.r.y : 205;

                        const rows = all
                            .filter(visible)
                            .map(el => ({
                                el,
                                r:el.getBoundingClientRect(),
                                text:label(el),
                                role:el.getAttribute('role') || '',
                                tabindex:el.getAttribute('tabindex') || '',
                                tag:el.tagName
                            }))
                            .filter(row => {
                                const r=row.r;
                                return r.x <= 220 && r.y >= 118 && r.y < homeY - 2
                                    && r.width >= 90 && r.width <= 225
                                    && r.height >= 28 && r.height <= 85
                                    && row.text
                                    && !/^Meta Business Suite$/i.test(row.text)
                                    && !/^(Home|Startseite|Start|Главная|Головна)$/i.test(row.text);
                            });

                        rows.sort((a,b) => {
                            const aa=a.r.width*a.r.height;
                            const ba=b.r.width*b.r.height;
                            const ap=a.role==='button'||a.tag==='BUTTON'||a.tabindex==='0' ? -100000 : 0;
                            const bp=b.role==='button'||b.tag==='BUTTON'||b.tabindex==='0' ? -100000 : 0;
                            return (ap+aa)-(bp+ba) || b.r.y-a.r.y;
                        });
                        if (!rows[0]) return false;
                        rows[0].el.click();
                        return true;
                    }"""
                )
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
        try:
            if request.method.upper() != "POST":
                return False
            if "graphql" not in str(request.url or "").lower():
                return False
            post_data = str(request.post_data or "")
        except Exception:
            return False

        decoded = unquote_plus(post_data)
        lower = decoded.lower()
        expected = business_name.lower()
        return (
            expected in lower
            and any(
                marker in lower
                for marker in (
                    "businesscreation",
                    "createbusiness",
                    "create_business",
                    "business_creation",
                    "portfolio",
                )
            )
        )

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
                friendly = _clean(
                    response.request.headers.get("x-fb-friendly-name")
                    or response.request.headers.get("X-FB-Friendly-Name")
                )
            except Exception:
                friendly = ""

            return business_id, friendly

        except BrowserBusinessError:
            raise
        except Exception:
            # If CREATE was actually sent, the network gate has already
            # persisted CREATE_SUBMITTED. A missing response is reconciled from
            # the Business portfolio inventory and is never blindly retried.
            return "", ""
        finally:
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
                "could be confirmed. ReMask will not submit CREATE again."
            ),
            retryable=False,
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

        await self._prepare_create_form(
            business_name=name,
            user_email=email,
            user_first_name=_clean(user_first_name),
            user_last_name=_clean(user_last_name),
            profile_display_name=_clean(profile_display_name),
        )

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

        response_business_id, friendly = await self._submit_create_and_observe(
            name,
            before_submit=create_checkpoint,
        )
        await self.page.wait_for_timeout(1800)

        # Always verify through current UI state, even if GraphQL response
        # exposed an ID.
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
    def _response_matches_page_add(
        response: Any,
        *,
        business_id: str,
        page_id: str,
    ) -> bool:
        try:
            if response.request.method.upper() != "POST":
                return False
            if "graphql" not in str(response.url or "").lower():
                return False
            decoded = unquote_plus(str(response.request.post_data or ""))
        except Exception:
            return False

        return business_id in decoded and page_id in decoded

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

        try:
            async with self.page.expect_response(
                lambda response: self._response_matches_page_add(
                    response,
                    business_id=business,
                    page_id=page,
                ),
                timeout=self.timeout_ms,
            ):
                if before_submit is not None:
                    await before_submit(
                        {
                            "phase": "PAGE_ADD_CLICK_INTENT",
                            "business_id": business,
                            "primary_page_id": page,
                            "page_click_intent_at": int(time.time()),
                        }
                    )

                clicked = await self._click_named(
                    ("Add Page", "Add", "Continue", "Добавить Страницу", "Добавить", "Продолжить", "Додати сторінку", "Додати", "Продовжити", "Seite hinzufügen", "Hinzufügen", "Weiter")
                )
                if not clicked:
                    if before_submit is not None:
                        await before_submit(
                            {
                                "phase": "PAGE_ADD_NOT_SUBMITTED",
                                "business_id": business,
                                "primary_page_id": page,
                                "page_not_submitted_at": int(time.time()),
                            }
                        )
                    diag = await self._diagnostic("page_add_submit_missing")
                    raise BrowserBusinessError(
                        "PAGE_ADD_UI_CHANGED",
                        "Meta Page-add submit action was not found.",
                        retryable=False,
                        diagnostic=diag,
                    )

                if before_submit is not None:
                    try:
                        await before_submit(
                            {
                                "phase": "PAGE_ADD_SUBMITTED",
                                "business_id": business,
                                "primary_page_id": page,
                                "page_submitted_at": int(time.time()),
                            }
                        )
                    except Exception as exc:
                        raise BrowserBusinessError(
                            "PAGE_CHECKPOINT_FAILED_AFTER_CLICK",
                            (
                                "Meta Page-add was clicked, but ReMask could not "
                                "persist the submitted checkpoint. Page state "
                                "must be verified before any retry."
                            ),
                            retryable=False,
                        ) from exc
        except BrowserBusinessError:
            raise
        except Exception:
            # Response interception is diagnostic only. Verification below is
            # authoritative.
            pass

        await self.page.wait_for_timeout(1500)

        if not await self.verify_page_attached(business_id=business, page_id=page):
            diag = await self._diagnostic("page_attach_unconfirmed")
            raise BrowserBusinessError(
                "PAGE_ATTACH_RESULT_UNKNOWN",
                (
                    "Meta Page add was submitted but the selected Page could "
                    "not be confirmed in Business Settings. ReMask will not "
                    "blindly submit the Page-add action again."
                ),
                retryable=False,
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
