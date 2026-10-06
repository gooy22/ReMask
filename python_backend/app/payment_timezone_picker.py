"""Observe and select the requested billing timezone in Meta's rendered picker."""
from __future__ import annotations
import asyncio
import re
import time
import unicodedata
from typing import Any

ALIASES = {"europe/kyiv", "europe/kiev"}
CITY = re.compile(r"Kyiv|Kiev|Київ|Киев", re.I)
ZONE_LABEL = re.compile(r"time\s*zone|часов(?:ой|ий)\s+пояс", re.I)
OFFSET = re.compile(r"\(?\b(?:GMT|UTC)\s*([+-])(\d{1,2})(?::?(\d{2}))?\)?", re.I)
BEFORE = "data-remask-timezone-before"
OPTION = "data-remask-timezone-option"


def clean_label(value: Any) -> str:
    return " ".join(re.sub(r"[\u200b-\u200d\ufeff]", "", unicodedata.normalize("NFKC", str(value or ""))).split())


def kyiv_label(value: Any) -> bool:
    label = clean_label(value)
    if label.casefold() in ALIASES:
        return True
    label = re.sub(r"^(?:set\s+)?(?:time\s*zone|часов(?:ой|ий)\s+пояс)\s*:?\s*", "", label, flags=re.I)
    offsets = OFFSET.findall(label)
    if any(sign != "+" or int(hour) not in {2, 3} or int(minute or "0") != 0
           for sign, hour, minute in offsets):
        return False
    words = re.sub(r"[/,():\[\]—–-]", " ", OFFSET.sub("", label)).split()
    cities = [word for word in words if CITY.fullmatch(word)]
    rest = [word.casefold() for word in words if not CITY.fullmatch(word)]
    return len(cities) == 1 and all(word in {
        "europe", "европа", "європа", "ukraine", "украина", "україна",
    } for word in rest)


def _selected(info: dict) -> bool:
    explicit = clean_label(info.get("value")).casefold()
    if "/" in explicit and explicit not in ALIASES:
        return False
    values = [clean_label(line) for line in str(info.get("text") or "").splitlines()]
    values = [line for line in values if line and not ZONE_LABEL.fullmatch(line)]
    return bool(values) and kyiv_label(" ".join(values))


async def _timezone_control(scope: Any) -> tuple[Any, dict]:
    controls = scope.locator('select,button,[role="button"],[role="combobox"]').filter(visible=True)
    matches = []
    for index in range(min(await controls.count(), 60)):
        item = controls.nth(index)
        info = await item.evaluate("""el => ({
            text:el.tagName==='SELECT' ? (el.selectedOptions[0]?.textContent||'') : (el.innerText||el.getAttribute('aria-label')||''),
            field:[el.getAttribute('aria-label')||'', ...Array.from(el.labels||[]).map(l=>l.innerText||'')].join(' '),
            value:el.tagName==='SELECT' ? el.value : (el.getAttribute('data-timezone')||el.getAttribute('data-value')||''),
            native:el.tagName==='SELECT',
            option:!!el.closest('[role="option"],[role="menuitem"],[role="radio"]')
        })""")
        text = clean_label(info.get("text"))
        if not info.get("option") and (
            ZONE_LABEL.search(str(info.get("field") or "")) or ZONE_LABEL.search(text)
            or OFFSET.search(text) or text.casefold() in ALIASES
        ):
            matches.append((item, info))
    if len(matches) != 1:
        return None, {"reason": "timezone_control_not_unique", "control_count": len(matches)}
    return matches[0]


async def payment_timezone_selected(scope: Any) -> bool:
    control, info = await _timezone_control(scope)
    return control is not None and _selected(info)


async def _new_picker_search(page: Any) -> Any:
    fields = page.locator('input:not([type="hidden"]),textarea,[contenteditable="true"]').filter(visible=True)
    found = []
    for index in range(min(await fields.count(), 50)):
        field = fields.nth(index)
        info = await field.evaluate("""el => ({
            before:el.hasAttribute('data-remask-timezone-before'),
            label:[el.getAttribute('placeholder')||'',el.getAttribute('aria-label')||'',
                ...Array.from(el.labels||[]).map(l=>l.innerText||'')].join(' '),
            type:el.type||''
        })""")
        if info["before"] or re.search(r"account|business|card|аккаунт|облік|бізнес|карт", info["label"], re.I):
            continue
        if re.search(r"search|find.*city|city.*find|поиск|пошук|найти|знайти", info["label"], re.I) and await field.is_editable():
            found.append(field)
    return found[0] if len(found) == 1 else None


async def _timezone_options(page: Any) -> list[dict]:
    await page.locator(f"[{OPTION}]").evaluate_all("(els, attr)=>els.forEach(el=>el.removeAttribute(attr))", OPTION)
    locators = [page.get_by_role(role, name=CITY).filter(visible=True) for role in ("option", "menuitem", "radio")]
    locators.append(page.get_by_text(CITY).filter(visible=True))
    candidates = {}
    serial = 0
    for locator in locators:
        for index in range(min(await locator.count(), 40)):
            serial += 1
            info = await locator.nth(index).evaluate("""(el,args) => {
                const target=el.closest('[role="option"],[role="menuitem"],[role="radio"],button,[role="button"]')||el;
                if(target.hasAttribute(args.before)||el.hasAttribute(args.before)
                    ||target.disabled||target.getAttribute('aria-disabled')==='true'
                    ||/^(H[1-6]|LABEL)$/.test(target.tagName))return null;
                let key=target.getAttribute(args.option);
                if(!key){key=String(args.serial);target.setAttribute(args.option,key);}
                return {key,text:target.innerText||target.textContent||'',
                    value:target.getAttribute('data-timezone')||target.getAttribute('data-value')||''};
            }""", {"before": BEFORE, "option": OPTION, "serial": serial})
            if isinstance(info, dict) and _selected(info):
                candidates[info["key"]] = info
    return list(candidates.values())


async def choose_payment_timezone(scope: Any, page: Any, *, wait_seconds: float = 8.0) -> tuple[bool, dict]:
    control, info = await _timezone_control(scope)
    diagnostic = {"reason": "timezone_control_not_unique"}
    if control is None:
        return False, info
    diagnostic.update(current=re.sub(r"\d{6,}", "[redacted]", clean_label(info.get("text")))[:120], searches=[])
    if _selected(info):
        return True, {**diagnostic, "reason": "already_selected"}
    if not await control.is_enabled():
        return False, {**diagnostic, "reason": "timezone_control_disabled"}
    if info.get("native"):
        rows = await control.locator("option").evaluate_all("(es)=>es.map(e=>({value:e.value,text:e.textContent||''}))")
        matches = [row for row in rows if _selected(row)]
        canonical = [row for row in matches if row["value"].casefold() == "europe/kyiv"]
        matches = canonical or matches
        if len(matches) != 1:
            return False, {**diagnostic, "reason": "timezone_option_not_unique", "candidate_count": len(matches)}
        await control.select_option(matches[0]["value"], timeout=3000)
        selected = await payment_timezone_selected(scope)
        return selected, {**diagnostic, "reason": "selected" if selected else "timezone_value_unchanged"}
    try:
        # Only inputs/choices that appear after opening this control belong to
        # the picker. A persistent Billing account search is never a fallback.
        await page.locator('input,textarea,[contenteditable="true"],button,[role="button"],[role="option"],[role="menuitem"],[role="radio"]').filter(visible=True).evaluate_all(
            "(els,attr)=>els.forEach(e=>e.setAttribute(attr,'1'))", BEFORE)
        await page.get_by_text(CITY).filter(visible=True).evaluate_all(
            "(els,attr)=>els.forEach(e=>e.setAttribute(attr,'1'))", BEFORE)
        try:
            await control.click(timeout=5000)
        except Exception as exc:
            # Observe the one attempt; a transition timeout may have opened it.
            diagnostic["open_error_type"] = type(exc).__name__
        deadline = time.monotonic() + max(0.1, wait_seconds)
        searched_at = 0.0
        while time.monotonic() < deadline:
            options = await _timezone_options(page)
            diagnostic["candidate_count"] = len(options)
            diagnostic["option_labels"] = [clean_label(row["text"])[:120] for row in options[:6]]
            if len(options) == 1:
                try:
                    await page.locator(f'[{OPTION}="{options[0]["key"]}"]').click(timeout=3000)
                except Exception as exc:
                    diagnostic["selection_error_type"] = type(exc).__name__
                # A click alone is not proof. Re-resolve the closed city button.
                confirm_deadline = time.monotonic() + 3.0
                while time.monotonic() < confirm_deadline:
                    if await payment_timezone_selected(scope):
                        return True, {**diagnostic, "reason": "selected"}
                    await asyncio.sleep(0.2)
                return False, {**diagnostic, "reason": "timezone_value_unchanged"}
            if len(options) > 1:
                # Hydration can briefly leave the old and new option mounted.
                # Wait for one exact row; never choose the first duplicate.
                await asyncio.sleep(0.2)
                continue
            search = await _new_picker_search(page)
            aliases = diagnostic["searches"]
            if search is not None and (not aliases or (len(aliases) == 1 and time.monotonic() - searched_at > 1.8)):
                query = "Kyiv" if not aliases else "Kiev"
                await search.fill("", timeout=2000)
                await search.press_sequentially(query, delay=20, timeout=3000)
                aliases.append(query)
                searched_at = time.monotonic()
            await asyncio.sleep(0.2)
        return False, {**diagnostic, "reason": "timezone_option_not_unique" if diagnostic.get("candidate_count", 0) > 1 else "timezone_option_missing"}
    finally:
        for marker in (BEFORE, OPTION):
            try:
                await page.locator(f"[{marker}]").evaluate_all(
                    "(els,attr)=>els.forEach(el=>el.removeAttribute(attr))", marker)
            except Exception:
                pass
