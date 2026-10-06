"""Exact, read-only identity of a Business Settings ad-account details pane."""
from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urlsplit


def ad_account_route(url: Any, business_id: str) -> bool:
    parsed = urlsplit(str(url or ""))
    return (
        parsed.scheme == "https"
        and parsed.hostname == "business.facebook.com"
        and parse_qs(parsed.query).get("business_id") == [str(business_id)]
        and any(part in parsed.path for part in (
            "/settings/ad_accounts", "/settings/ad-accounts",
        ))
    )


def _clean(value: Any) -> str:
    return " ".join(re.sub(r"[\u200b-\u200d\ufeff]", "", unicodedata.normalize(
        "NFKC", str(value or ""),
    )).split())


def details_candidate(result: Any, *, business_id: str, account_name: str) -> str:
    """Saved UI evidence is only a candidate; a fresh live lookup must verify it."""
    if not isinstance(result, dict):
        return ""
    diagnostics = [result.get("browser_diagnostic")]
    diagnostics.extend(row.get("diagnostic") for row in result.get("capture_failures", [])
                       if isinstance(row, dict))
    ids = set()
    expected = _clean(account_name).casefold()
    for diag in diagnostics:
        if not isinstance(diag, dict):
            continue
        state = diag.get("ui_state")
        if not isinstance(state, dict) or not ad_account_route(state.get("url"), business_id):
            continue
        controls = state.get("controls") or []
        headings = [_clean(str(row).split(" [tag=", 1)[0]).casefold()
                    for row in controls if "role=heading" in str(row)]
        rows = [_clean(str(row).split(" [tag=", 1)[0]).casefold()
                for row in controls if "role=row " in str(row)]
        # The exact named row and exact details heading must both be present.
        if expected not in headings or not any(
            row.startswith(expected + " ") for row in rows
        ):
            continue
        for row in controls:
            if "[tag=A role=link " not in str(row):
                continue
            text = _clean(str(row).split(" [tag=", 1)[0])
            if re.fullmatch(r"\d{8,30}", text) and text != str(business_id):
                ids.add("act_" + text)
    return next(iter(ids)) if len(ids) == 1 else ""


IDENTITY_SNAPSHOT = r"""(expectedName) => {
    const clean = value => String(value || '').normalize('NFKC')
        .replace(/[\u200b-\u200d\ufeff]/g, '').replace(/\s+/g, ' ').trim();
    const same = value => clean(value).toLowerCase() === clean(expectedName).toLowerCase();
    const visible = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none'
            && s.visibility !== 'hidden' && !el.closest('[hidden]');
    };
    const modal = el => el.closest('[role="dialog"],[aria-modal="true"]');
    const headingSelector = '[role="heading"],h1,h2,h3,h4';
    const rows = [...document.querySelectorAll('tr,[role="row"]')]
        .filter(el => visible(el) && !modal(el)
            && el.closest('table,[role="grid"],[role="table"]')
            && !el.querySelector('th,[role="columnheader"]'));
    const namedRows = rows.filter(el => [...el.querySelectorAll(
        headingSelector + ',[role="gridcell"],td'
    )].some(node => visible(node) && same(node.innerText || node.textContent)));
    const ids = new Set();
    const headings = [...document.querySelectorAll(headingSelector)]
        .filter(el => visible(el) && !modal(el) && !el.closest('table,[role="grid"],[role="table"]')
            && same(el.innerText || el.textContent));
    const panes = [];
    for (const heading of headings) {
        let pane = heading.parentElement;
        for (let depth = 0; pane && depth < 6; depth++, pane = pane.parentElement) {
            if (pane === document.body || pane === document.documentElement
                || pane.querySelector('table,[role="grid"],[role="table"]') || modal(pane)) break;
            const links = [...pane.querySelectorAll('a[href]')].filter(visible);
            const candidates = new Set();
            for (const link of links) {
                const label = clean(link.innerText || link.textContent);
                // A numeric account link in the same header is canonical.
                // selected_asset_id and generic DOM data-id are never account IDs.
                if (!/^\d{8,30}$/.test(label)) continue;
                let url;
                try { url = new URL(link.href); } catch { continue; }
                if (url.protocol !== 'https:' || ![
                    'business.facebook.com','adsmanager.facebook.com','www.facebook.com'
                ].includes(url.hostname)) continue;
                const scoped = ['act','ad_account_id'].flatMap(k => url.searchParams.getAll(k));
                if (scoped.length && (scoped.length !== 1 || scoped[0] !== label)) continue;
                if (!scoped.length && !/adsmanager|ad.accounts|ad-accounts/i.test(url.pathname)) continue;
                candidates.add(label);
            }
            if (candidates.size) {
                for (const id of candidates) ids.add(id);
                panes.push(clean(pane.innerText).slice(0, 350));
                break;
            }
        }
    }
    return {nonempty: rows.length > 0, exact_name_rows: namedRows.length,
        ids: [...ids], headings: headings.length, panes};
}"""


async def read_ad_account_identity(page: Any, *, business_id: str,
                                   account_name: str) -> dict[str, Any]:
    base = {"confirmed": False, "confirmed_empty": False,
            "business_id": str(business_id), "source": "business_settings_exact_details"}
    if page is None or not ad_account_route(getattr(page, "url", ""), business_id):
        return {**base, "reason": "wrong_business_route"}
    try:
        snapshot = await page.evaluate(IDENTITY_SNAPSHOT, account_name)
    except Exception as exc:
        return {**base, "reason": type(exc).__name__}
    # Check the scope again after asynchronous DOM collection.
    if not isinstance(snapshot, dict) or not ad_account_route(getattr(page, "url", ""), business_id):
        return {**base, "reason": "unavailable_or_changed_scope"}
    ids = {"act_" + str(value) for value in snapshot.get("ids", [])
           if re.fullmatch(r"\d{8,30}", str(value)) and str(value) != str(business_id)}
    confirmed = snapshot.get("exact_name_rows") == 1 and len(ids) == 1 and snapshot.get("headings", 0) >= 1
    return {**base, "confirmed": confirmed,
            "ad_account_id": next(iter(ids)) if confirmed else "",
            "nonempty": bool(snapshot.get("nonempty")), "evidence": snapshot}
