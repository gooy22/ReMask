"""Confirm the existing Page task in Ads Manager's account overview.

This onboarding confirmation is distinct from Business portfolio ownership and
does not grant an ad account Page permissions. Only Meta's own UI sends it.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .facebook_business_browser import BrowserBusinessError, _request_graphql_meta, _ads_manager_scope_account_from_request, _decode_graphql_text

PAGE_TASK = re.compile(
    r"(?:Create|Confirm) Facebook Page|Созда(?:ть|ние) Страниц|Подтвердить Страниц|"
    r"Створити сторінку|Підтвердити сторінку|Facebook.Seite|Créer une Page", re.I
)
CONFIRM = re.compile(r"^(Confirm|Подтвердить|Підтвердити|Bestätigen|Confirmer|নিশ্চিত করুন|Xác nhận|पुष्टि करें)$", re.I)


def exact_main_scope(url: str, business: str, observed: set[str]) -> bool:
    parsed = urlsplit(url)
    if parsed.hostname not in {"adsmanager.facebook.com", "business.facebook.com"}:
        return False
    scoped = set(parse_qs(parsed.query).get("business_id", []))
    return observed == {business} and (not scoped or scoped == {business})


def page_task_statuses(payload: Any, path: str = "") -> list[dict[str, Any]]:
    """Extract only explicit Page onboarding completion, never generic success."""
    out = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            child = path + "." + str(key)
            compact = re.sub(r"[^a-z]", "", child.lower())
            leaf = re.sub(r"[^a-z]", "", str(key).lower())
            if "page" in compact and ("onboarding" in compact or "creation" in compact or "confirmation" in compact):
                if (leaf.endswith("completed") or leaf in {"iscomplete", "iscompleted", "isconfirmed"}) and isinstance(value, bool):
                    out.append({"path": child, "completed": value})
                elif leaf in {"status", "state", "completionstatus"} and isinstance(value, str):
                    out.append({"path": child, "completed": value.upper() in {"COMPLETED", "CONFIRMED", "COMPLETE", "DONE"}})
            out.extend(page_task_statuses(value, child))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            out.extend(page_task_statuses(value, path + f"[{index}]"))
    return out


async def confirm_main_page(browser: Any, *, business_id: str,
                            page_id: str, page_name: str, before_submit: Any,
                            verification_only: bool = False) -> dict[str, Any]:
    """Require the exact personal business scope and the exact Page card.

    An interrupted final click can only be reconciled; it is never repeated.
    Missing task/card/completion evidence is an error, not a success.
    """
    page = browser.page
    observed: set[str] = set()
    queries: list[str] = []
    status_evidence: list[dict[str, Any]] = []
    response_tasks: set[asyncio.Task[Any]] = set()

    def on_request(request: Any) -> None:
        meta = _request_graphql_meta(request)
        variables = meta.get("variables") or {}
        if not isinstance(variables, dict):
            return
        # A route parameter alone is not proof: Meta can ignore it and choose
        # another portfolio. The live selector must name our personal scope.
        if "scopingselector" in str(meta.get("friendly_name") or "").lower():
            value = str(variables.get("firstLevelScopeId") or "")
            if value.isdigit():
                observed.add(value)
        if _ads_manager_scope_account_from_request(meta, business_id=business_id):
            observed.add(business_id)
        name = str(meta.get("friendly_name") or "")
        if name and name not in queries and len(queries) < 20:
            queries.append(name[:180])

    async def inspect_response(response: Any) -> None:
        try:
            if 'graphql' not in str(response.url).lower():
                return
            meta = _request_graphql_meta(response.request)
            friendly = str(meta.get('friendly_name') or '').lower()
            if not any(word in friendly for word in ('onboarding', 'accountoverview', 'pageconfirm')):
                return
            act = (parse_qs(urlsplit(str(page.url)).query).get('act') or [''])[0]
            def has_account(value: Any) -> bool:
                if isinstance(value, dict):
                    return any(('account' in str(k).lower() and str(v).removeprefix('act_') == act and bool(act))
                               or has_account(v) for k,v in value.items())
                if isinstance(value, list):
                    return any(has_account(v) for v in value)
                return False
            payload = _decode_graphql_text(await response.text())
            statuses = page_task_statuses(payload)
            scoped = exact_main_scope(str(page.url), business_id, observed) and has_account(meta.get('variables') or {})
            status_evidence.extend({**status, 'scoped':scoped, 'operation':str(meta.get('friendly_name') or '')[:160]}
                                   for status in statuses)
            del status_evidence[:-20]
        except Exception:
            return

    def on_response(response: Any) -> None:
        task = asyncio.create_task(inspect_response(response)); response_tasks.add(task)
        task.add_done_callback(response_tasks.discard)

    page.on("request", on_request)
    page.on("response", on_response)
    try:
        await browser._goto(
            "https://adsmanager.facebook.com/adsmanager/manage/accounts?business_id=" + business_id,
            timeout_ms=15000, wait_until="commit", settle_ms=1000, attempts=1,
        )
        deadline = asyncio.get_running_loop().time() + 45
        card = None
        while asyncio.get_running_loop().time() < deadline:
            await browser._assert_authenticated()
            headings = page.get_by_text(PAGE_TASK)
            if await headings.count():
                # The smallest ancestor containing this task heading and its
                # Confirm control keeps payment/terms actions out of scope.
                candidate = headings.first.locator("xpath=ancestor::*[.//*[self::button or @role='button'][normalize-space(.)='Confirm' or normalize-space(.)='Подтвердить' or normalize-space(.)='Підтвердити'] or .//*[normalize-space(.)='Completed' or normalize-space(.)='Confirmed']][1]")
                if await candidate.count() and await candidate.is_visible():
                    card = candidate
                    if exact_main_scope(str(page.url), business_id, observed):
                        break
            if exact_main_scope(str(page.url), business_id, observed) and any(s['scoped'] and s['completed'] for s in status_evidence):
                return {"confirmed":True,"already_confirmed":True,"main_business_id":business_id,"page_id":page_id,
                        "completion_evidence":status_evidence}
            await page.wait_for_timeout(250)

        async def fail(code: str, detail: str) -> None:
            diagnostic = await browser._diagnostic("main_page_confirmation")
            diagnostic.update({"main_business_id": business_id, "page_id": page_id,
                               "observed_scopes": sorted(observed), "queries": queries,
                               "page_task_statuses":status_evidence})
            raise BrowserBusinessError(code, detail, retryable=True, diagnostic=diagnostic)

        body = await browser._body_text()
        if not observed and re.search(r"Loading your ad account|Загрузка рекламного", body, re.I):
            await fail("PAGE_CONFIRM_LOADING", "Ads Manager is still loading the ad account. No Confirm was clicked.")
        if not exact_main_scope(str(page.url), business_id, observed):
            await fail("MAIN_BUSINESS_SCOPE_UNCONFIRMED", "Meta did not confirm the requested main Business scope. No Confirm was clicked.")
        if card is None:
            await fail("PAGE_CONFIRM_UI_UNAVAILABLE", "The Create Facebook Page / Confirm task was not found. No Confirm was clicked.")

        text = await card.inner_text(timeout=2500)
        links = await asyncio.wait_for(card.locator("a[href]").evaluate_all("els => els.map(e=>e.getAttribute('href')||'')"), timeout=2.5)
        exact_id = any(re.search(r"(?<!\d)" + re.escape(page_id) + r"(?!\d)", str(link)) for link in links)
        # This card previews an existing managed Page rather than transferring
        # ownership. The handler resolves the exact name from durable creation
        # state and rejects duplicate names before entering this UI.
        if not exact_id and not any(line.strip() == page_name for line in text.splitlines()):
            await fail("PAGE_CONFIRM_PAGE_MISMATCH", "The Page task does not identify the saved Page. No Confirm was clicked.")

        button = card.get_by_role("button", name=CONFIRM)
        async def completed() -> bool:
            if any(s['scoped'] and s['completed'] for s in status_evidence):
                return True
            return bool(await card.get_by_text(re.compile(r"^(Completed|Confirmed|Done|Подтверждено|Выполнено|Підтверджено|Завершено)$", re.I)).count())

        if await completed():
            return {"confirmed": True, "already_confirmed": True, "main_business_id": business_id, "page_id": page_id}
        if verification_only:
            await fail("PAGE_CONFIRM_RESULT_UNKNOWN", "Previous Confirm may have reached Meta. Retry only verifies completion; Confirm was not repeated.")
        if await button.count() != 1 or not await button.is_visible() or not await button.is_enabled():
            await fail("PAGE_CONFIRM_UI_UNAVAILABLE", "The Page task has no unique enabled Confirm control. No Confirm was clicked.")

        await before_submit({"phase": "PAGE_CONFIRM_CLICK_INTENT", "activity": "CONFIRM_MAIN_BUSINESS_PAGE",
                             "main_business_id": business_id, "confirm_page_id": page_id,
                             "browser_diagnostic": {"stage": "confirm_card_verified", "body_excerpt": text,
                                                    "observed_scopes": sorted(observed)}})
        await button.click(timeout=5000)
        # Keep the post-click evidence even if Chromium or the driver dies
        # while reconciling the task. No credential/network payload is saved.
        try:
            after = await asyncio.wait_for(browser._body_text(), timeout=2)
            await before_submit({"phase": "PAGE_CONFIRM_CLICK_INTENT", "activity": "VERIFY_MAIN_BUSINESS_PAGE",
                "browser_diagnostic": {"stage": "after_confirm", "body_excerpt": after[:3500],
                                       "observed_scopes": sorted(observed)}})
        except asyncio.TimeoutError:
            pass
        deadline = asyncio.get_running_loop().time() + 12
        while asyncio.get_running_loop().time() < deadline:
            await browser._assert_authenticated()
            if exact_main_scope(str(page.url), business_id, observed) and await completed():
                return {"confirmed": True, "already_confirmed": False, "main_business_id": business_id, "page_id": page_id}
            await page.wait_for_timeout(250)
        await fail("PAGE_CONFIRM_RESULT_UNKNOWN", "Confirm was clicked, but Meta did not show completion of this Page task. Retry is verification-only.")
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)
        for task in response_tasks:
            task.cancel()
