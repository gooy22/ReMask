"""Add the exact existing Page and prove full operator control in Business Settings."""
from __future__ import annotations
import asyncio
import re
from urllib.parse import parse_qs, urlsplit

from ..facebook_business_browser import BrowserBusinessError

MODE = "existing_page_full_control"


async def _full_control(scope):
    pattern = re.compile(r"full control|manage everything|everything|полный контроль|повний контроль", re.I)
    candidates = []
    for role in ("checkbox", "switch", "radio"):
        controls = scope.get_by_role(role, name=pattern).filter(visible=True)
        for index in range(await controls.count()):
            candidates.append(controls.nth(index))
    if len(candidates) != 1:
        raise BrowserBusinessError("PAGE_FULL_CONTROL_UI_UNAVAILABLE",
            "The exact full-control permission is not uniquely selectable", retryable=True)
    control = candidates[0]
    async def selected():
        try:
            return await control.is_checked()
        except Exception:
            return await control.get_attribute("aria-checked") == "true"
    if not await selected():
        if not await control.is_enabled():
            raise BrowserBusinessError("PAGE_FULL_CONTROL_NOT_AVAILABLE",
                "Meta does not permit full control of this Page", retryable=True)
        if await control.get_attribute("role") == "switch":
            await control.click(timeout=3000)
        else:
            await control.check(timeout=3000)
    if not await selected():
        raise BrowserBusinessError("PAGE_FULL_CONTROL_UNVERIFIED",
            "The full-control selection did not become active", retryable=True)


async def _operator_full_proof(page, uid):
    rows = await page.locator('[role="row"],[role="listitem"]').evaluate_all(r"""(rows, uid) => {
        const norm = value => String(value || '').replace(/\s+/g,' ').trim();
        const visible = el => {
            const r=el.getBoundingClientRect(), s=getComputedStyle(el);
            return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none';
        };
        const identity = text => /(^|\W)You(\W|$)/i.test(text) ||
            new RegExp('(^|\\D)'+uid+'(\\D|$)').test(text);
        return rows.filter(visible).filter(el => !el.closest('[role="dialog"],[aria-modal="true"]')).filter(el => {
            const text=norm(el.innerText || '');
            return text.length<=1000 && identity(text) &&
                ![...el.querySelectorAll('[role="row"],[role="listitem"]')].some(child =>
                    visible(child) && identity(norm(child.innerText)));
        }).map(el => ({text:norm(el.innerText).slice(0,1000)}));
    }""", uid)
    if len(rows) != 1:
        return None
    text = rows[0]["text"]
    if (not re.search(r"\bfull control\b|manage everything|полный контроль|повний контроль", text, re.I)
            or re.search(r"partial access|partial control|task access|частичн", text, re.I)):
        return None
    return {"source":"exact_page_people_full_control", "operator_uid":uid, "row":text}


async def _approve_existing_request(browser, config, business, checkpoint):
    # Use the Page owner's normal approval surface, but never accept the old
    # Ads partner row as ownership evidence.
    from .page_access_handler import (_resolve_target_business_name, _resolve_owner_page_actor,
        _saved_i_user_cookie, _set_i_user, _wait_owner_access_surface,
        _pick_owner_review_request, _visible_dialog_or_page, _facebook_password, _one)
    name, _ = await _resolve_target_business_name(browser, business, config.get("target_business_identity"))
    actor, _ = await _resolve_owner_page_actor(browser, config)
    saved = _saved_i_user_cookie(browser)
    try:
        await _set_i_user(browser, actor)
        await browser._goto("https://www.facebook.com/settings/?tab=profile_access",
            timeout_ms=12000, wait_until="commit", settle_ms=1100, attempts=1)
        await browser._assert_authenticated()
        await _wait_owner_access_surface(browser.page, name)
        review, _ = await _pick_owner_review_request(browser.page, business, name)
        if review is None:
            return False
        await review.click(timeout=4000)
        await browser.page.wait_for_timeout(650)
        scope = await _visible_dialog_or_page(browser.page)
        # Ownership approval must identify adding the Page to the portfolio.
        # An old partial-access request must never satisfy this operation.
        body = await scope.inner_text(timeout=2000)
        if not re.search(r"add.{0,80}page|page.{0,80}(business portfolio|ownership)|"
                r"добав.{0,80}страниц|додат.{0,80}сторін", body, re.I|re.S):
            raise BrowserBusinessError("PAGE_OWNERSHIP_APPROVAL_UNVERIFIED",
                "The pending review does not prove the existing-Page ownership request", retryable=True)
        next_button = scope.get_by_role("button", name=re.compile(r"^Next$", re.I)).filter(visible=True)
        if await _one(next_button) and await next_button.is_enabled():
            await next_button.click(timeout=3000)
            await browser.page.wait_for_timeout(500)
            scope = await _visible_dialog_or_page(browser.page)
        approve = scope.get_by_role("button",
            name=re.compile(r"^(Accept|Approve|Confirm|Accept request|Approve request)$", re.I)).filter(visible=True)
        if not await _one(approve) or not await approve.is_enabled():
            return False
        await checkpoint({"phase":"TARGET_PAGE_ACCESS_FULL_OWNER_APPROVE_CLICK_INTENT","access_mode":MODE})
        await approve.click(timeout=5000)
        await browser.page.wait_for_timeout(700)
        await checkpoint({"phase":"TARGET_PAGE_ACCESS_FULL_OWNER_APPROVE_SUBMITTED","access_mode":MODE})
        passwords = browser.page.locator('input[type="password"]:visible')
        if await passwords.count():
            password = _facebook_password(browser)
            if await passwords.count()!=1 or not password:
                raise BrowserBusinessError("PAGE_OWNER_PASSWORD_CONFIRM_REQUIRED",
                    "Meta requires the profile's password to approve Page ownership", retryable=True)
            await passwords.fill(password, timeout=3000)
            scope = await _visible_dialog_or_page(browser.page)
            confirm = scope.get_by_role("button", name=re.compile(r"^(Confirm|Continue|Submit)$",re.I)).filter(visible=True)
            if not await _one(confirm) or not await confirm.is_enabled():
                raise BrowserBusinessError("PAGE_OWNER_PASSWORD_CONFIRM_REQUIRED",
                    "The ownership password confirmation is unavailable",retryable=True)
            await confirm.click(timeout=5000)
            await browser.page.wait_for_timeout(700)
    finally:
        await _set_i_user(browser, None, restore=saved)
    return await browser.verify_page_attached(
        business_id=business, page_id=config["page_id"], require_owned=True)


async def ensure_existing_page_full_control(browser, config, business, checkpoint, prior):
    from .page_access_handler import _select_page, _one
    page_id = str(config["page_id"])
    uid = str((getattr(browser.context,"cookies",{}) or {}).get("c_user") or "")
    if not uid.isdigit():
        raise BrowserBusinessError("PAGE_OPERATOR_IDENTITY_UNAVAILABLE",
            "The authenticated operator's exact identity is unavailable",retryable=True)
    full_prior = prior.get("access_mode")==MODE
    owned = await browser.verify_page_attached(
        business_id=business,page_id=page_id,require_owned=True)
    if not owned:
        recorded_owner = str(config.get("owner_business_id") or "")
        if config.get("owner_business_confirmed") is True and recorded_owner and recorded_owner != business:
            raise BrowserBusinessError("PAGE_OWNED_BY_ANOTHER_BUSINESS",
                "This exact Page is recorded as owned by another portfolio ("+recorded_owner+
                "); it cannot be added as owned by two portfolios",retryable=False)
        phase = str(prior.get("phase") or "")
        pending = full_prior and phase in {
            "TARGET_PAGE_ACCESS_FULL_ADD_CLICK_INTENT", "TARGET_PAGE_ACCESS_FULL_ADD_SUBMITTED",
            "TARGET_PAGE_ACCESS_FULL_OWNER_APPROVE_CLICK_INTENT", "TARGET_PAGE_ACCESS_FULL_OWNER_APPROVE_SUBMITTED"}
        if pending:
            # A submitted approval is verification-only. Never re-click it.
            if "OWNER_APPROVE" not in phase:
                owned = await _approve_existing_request(browser,config,business,checkpoint)
        else:
            async def before_submit(patch):
                mapped = {"PAGE_ADD_CLICK_INTENT":"TARGET_PAGE_ACCESS_FULL_ADD_CLICK_INTENT",
                    "PAGE_ADD_SUBMITTED":"TARGET_PAGE_ACCESS_FULL_ADD_SUBMITTED",
                    "PAGE_ADD_REJECTED":"TARGET_PAGE_ACCESS_FULL_ADD_REJECTED",
                    "PAGE_ADD_NOT_SUBMITTED":"TARGET_PAGE_ACCESS_FULL_ADD_NOT_SUBMITTED"}
                patch=dict(patch)
                if patch.get("phase") in mapped:
                    patch["phase"]=mapped[patch["phase"]]
                await checkpoint({**patch,"access_mode":MODE,"requested_tasks":["MANAGE"]})
            try:
                await browser.add_existing_page(business_id=business,page_id=page_id,
                    page_name=config["name"],before_submit=before_submit,require_owned=True)
                owned = True  # add_existing_page ends with live ownership verification.
            except BrowserBusinessError as exc:
                if exc.code != "PAGE_ATTACH_RESULT_UNKNOWN":
                    raise
                owned = await _approve_existing_request(browser,config,business,checkpoint)
        if not owned:
            raise BrowserBusinessError("PAGE_OWNERSHIP_RESULT_UNKNOWN",
                "The existing-Page add/approval is pending; retry checks ownership without another submission",
                retryable=True)
    operator_pending = full_prior and prior.get("phase") in {
        "TARGET_PAGE_OPERATOR_FULL_ASSIGN_CLICK_INTENT","TARGET_PAGE_OPERATOR_FULL_ASSIGN_SUBMITTED"}
    await checkpoint({**({} if operator_pending else {"phase":"TARGET_PAGE_ACCESS_FULL_OWNERSHIP_CONFIRMED"}),
        "access_mode":MODE,"page_owned_by_business":True,"owner_business_id":business})

    # Ownership verification navigates this same browser to the exact BM Pages
    # surface. Never start operator selection in a fresh about:blank session.
    selection = await _select_page(browser,config["name"],page_id,business)
    current = urlsplit(str(browser.page.url))
    query = parse_qs(current.query)
    if "/settings/pages" not in current.path or business not in query.get("business_id",[]):
        raise BrowserBusinessError("PAGE_OPERATOR_BUSINESS_SCOPE_UNVERIFIED",
            "The operator form is outside the verified portfolio's Pages surface",retryable=True)
    proof = await _operator_full_proof(browser.page,uid)
    if proof is None:
        if full_prior and prior.get("phase") in {
                "TARGET_PAGE_OPERATOR_FULL_ASSIGN_CLICK_INTENT","TARGET_PAGE_OPERATOR_FULL_ASSIGN_SUBMITTED"}:
            raise BrowserBusinessError("PAGE_OPERATOR_ASSIGN_RESULT_UNKNOWN",
                "The saved full-control assignment is verification-only until the exact operator row is confirmed",
                retryable=True)
        assign = browser.page.get_by_role("button",name=re.compile(r"^(Assign people|Add people)$",re.I)).filter(visible=True)
        if not await _one(assign):
            raise BrowserBusinessError("PAGE_OPERATOR_ASSIGNMENT_REQUIRED",
                "The exact Page's people-assignment action is unavailable",retryable=True)
        await assign.click(timeout=3000)
        dialog = browser.page.get_by_role("dialog").filter(visible=True)
        if not await _one(dialog):
            raise BrowserBusinessError("PAGE_OPERATOR_ASSIGNMENT_REQUIRED",
                "The people-assignment dialog is not unique",retryable=True)
        person = dialog.get_by_role("checkbox",name=re.compile(r"\bYou\b",re.I)).filter(visible=True)
        if not await _one(person):
            person = dialog.get_by_role("checkbox",name=re.compile(r"(?<!\d)"+re.escape(uid)+r"(?!\d)")).filter(visible=True)
        if not await _one(person):
            raise BrowserBusinessError("PAGE_OPERATOR_IDENTITY_UNAVAILABLE",
                "The current profile is not uniquely identified in the people-assignment dialog",retryable=True)
        await person.check(timeout=3000)
        await _full_control(dialog)
        submit = dialog.get_by_role("button",name=re.compile(r"^(Assign|Save)$",re.I)).filter(visible=True)
        if not await _one(submit) or not await submit.is_enabled():
            raise BrowserBusinessError("PAGE_OPERATOR_ASSIGNMENT_REQUIRED",
                "The full-control assignment action is unavailable",retryable=True)
        await checkpoint({"phase":"TARGET_PAGE_OPERATOR_FULL_ASSIGN_CLICK_INTENT",
            "access_mode":MODE,"requested_tasks":["MANAGE"],"operator_selection":selection})
        try:
            await submit.click(timeout=5000)
        except Exception:
            # The click may have reached Meta. Observe the same attempt only.
            pass
        await checkpoint({"phase":"TARGET_PAGE_OPERATOR_FULL_ASSIGN_SUBMITTED",
            "access_mode":MODE,"requested_tasks":["MANAGE"]})
        deadline = asyncio.get_running_loop().time()+7
        while asyncio.get_running_loop().time()<deadline:
            proof = await _operator_full_proof(browser.page,uid)
            if proof is not None:
                break
            await asyncio.sleep(0.35)
        if proof is None:
            raise BrowserBusinessError("PAGE_OPERATOR_FULL_CONTROL_UNVERIFIED",
                "Assignment was sent, but the exact profile's full-control row is not yet confirmed",retryable=True)
    await checkpoint({"phase":"TARGET_PAGE_OPERATOR_FULL_ASSIGN_CONFIRMED","access_mode":MODE,
        "operator_full_control_verified":True,"operator_full_control_proof":proof})
    return {"page_owned_by_business":True,"operator_full_control_verified":True,
        "operator_full_control_proof":proof,"transport":MODE}
