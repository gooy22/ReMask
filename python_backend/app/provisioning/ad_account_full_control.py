"""Full control for the current operator on the exact created portfolio RK."""
from __future__ import annotations

import asyncio
import re

from ..facebook_business_browser import BrowserBusinessError
from .page_full_control import _full_control


async def _proof(page, uid):
    rows = await page.locator('[role="row"],[role="listitem"]').evaluate_all(r"""(rows, uid) => {
        const norm = value => String(value || '').replace(/\s+/g,' ').trim();
        const visible = el => {
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const exact = text => /(^|\W)You(\W|$)/i.test(text)
            || new RegExp('(^|\\D)' + uid + '(\\D|$)').test(text);
        return rows.filter(visible).filter(el => !el.closest('[role="dialog"],[aria-modal="true"]'))
            .filter(el => exact(norm(el.innerText)) && ![...el.querySelectorAll(
                '[role="row"],[role="listitem"]')].some(child => visible(child) && exact(norm(child.innerText))))
            .map(el => norm(el.innerText).slice(0,1000));
    }""", uid)
    if len(rows) != 1 or not re.search(r"full control|full access|полный контроль|повний контроль", rows[0], re.I):
        return None
    if re.search(r"partial|task access|частичн", rows[0], re.I):
        return None
    return {"source": "exact_ad_account_people_full_control", "operator_uid": uid, "row": rows[0]}


async def ensure_ad_account_full_control(browser, business, account, name, checkpoint, prior):
    uid = str((getattr(browser.context, "cookies", {}) or {}).get("c_user") or "")
    if not uid.isdigit() or not str(account).isdigit() or not name:
        raise BrowserBusinessError("RK_OPERATOR_IDENTITY_UNAVAILABLE",
            "The exact RK name and current profile identity are required", retryable=True)
    await browser._goto(
        "https://business.facebook.com/latest/settings/ad_accounts?business_id=" + business,
        timeout_ms=12000, wait_until="commit", settle_ms=1000, attempts=1,
    )
    await browser._assert_authenticated()
    identity = await browser._read_selected_ad_account_identity(
        business_id=business, account_name=name,
    )
    if (not identity.get("confirmed") or identity.get("business_id") != business
            or str(identity.get("ad_account_id") or "").removeprefix("act_") != account):
        raise BrowserBusinessError("RK_OPERATOR_SCOPE_UNVERIFIED",
            "Meta did not prove the exact RK in this portfolio before assigning rights", retryable=True)
    proof = await _proof(browser.page, uid)
    if proof is None:
        pending = (prior.get("rk_operator_assignment_phase") in {"CLICK_INTENT", "SUBMITTED"}
            and str(prior.get("rk_operator_account_id") or account) == account
            and str(prior.get("rk_operator_business_id") or business) == business)
        if pending:
            raise BrowserBusinessError("RK_OPERATOR_ASSIGN_RESULT_UNKNOWN",
                "The saved full-control assignment is verification-only until the exact profile row is confirmed",
                retryable=True)
        assign = browser.page.get_by_role("button", name=re.compile(r"^(Assign people|Add people)$", re.I)).filter(visible=True)
        if await assign.count() != 1:
            raise BrowserBusinessError("RK_OPERATOR_ASSIGNMENT_REQUIRED",
                "The exact RK people-assignment action is unavailable", retryable=True)
        await assign.click(timeout=3000)
        dialog = browser.page.get_by_role("dialog").filter(visible=True)
        if await dialog.count() != 1:
            raise BrowserBusinessError("RK_OPERATOR_ASSIGNMENT_REQUIRED",
                "The RK people-assignment dialog is not unique", retryable=True)
        person = dialog.get_by_role("checkbox", name=re.compile(r"\bYou\b", re.I)).filter(visible=True)
        if await person.count() != 1:
            person = dialog.get_by_role("checkbox", name=re.compile(r"(?<!\d)" + re.escape(uid) + r"(?!\d)")).filter(visible=True)
        if await person.count() != 1:
            raise BrowserBusinessError("RK_OPERATOR_IDENTITY_UNAVAILABLE",
                "The current profile is not uniquely identified in the RK assignment dialog", retryable=True)
        await person.check(timeout=3000)
        try:
            await _full_control(dialog)
        except BrowserBusinessError as exc:
            raise BrowserBusinessError("RK_FULL_CONTROL_UI_UNAVAILABLE", str(exc), retryable=exc.retryable) from exc
        submit = dialog.get_by_role("button", name=re.compile(r"^(Assign|Save)$", re.I)).filter(visible=True)
        if await submit.count() != 1 or not await submit.is_enabled():
            raise BrowserBusinessError("RK_OPERATOR_ASSIGNMENT_REQUIRED",
                "The RK full-control assignment action is unavailable", retryable=True)
        await checkpoint({"rk_operator_assignment_phase": "CLICK_INTENT",
            "rk_operator_account_id": account, "rk_operator_business_id": business})
        try:
            await submit.click(timeout=5000)
        except Exception:
            pass
        await checkpoint({"rk_operator_assignment_phase": "SUBMITTED",
            "rk_operator_account_id": account, "rk_operator_business_id": business})
        deadline = asyncio.get_running_loop().time() + 7
        while asyncio.get_running_loop().time() < deadline:
            proof = await _proof(browser.page, uid)
            if proof is not None:
                break
            await asyncio.sleep(0.35)
        if proof is None:
            raise BrowserBusinessError("RK_OPERATOR_FULL_CONTROL_UNVERIFIED",
                "The exact profile full-control row is not yet confirmed after assignment", retryable=True)
    identity = await browser._reconcile_created_ad_account_from_ui(
        business_id=business, account_name=name,
    )
    if (not identity.get("confirmed") or identity.get("business_id") != business
            or str(identity.get("ad_account_id") or "").removeprefix("act_") != account):
        raise BrowserBusinessError("RK_OPERATOR_SCOPE_UNVERIFIED",
            "The exact RK details changed before full-control confirmation", retryable=True)
    await checkpoint({"rk_operator_assignment_phase": "CONFIRMED",
        "rk_operator_account_id": account, "rk_operator_business_id": business,
        "rk_operator_full_control_verified": True, "rk_operator_full_control_proof": proof})
    return {"rk_operator_full_control_verified": True, "rk_operator_full_control_proof": proof}
