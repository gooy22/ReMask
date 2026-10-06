"""Exact Page lookup identity shared by the fill and selection steps."""
from __future__ import annotations
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit


def owned_page_actor(pages: Any, page_id: str) -> str:
    actors = {
        str(row.get("profile_id") or "").strip()
        for row in (pages if isinstance(pages, list) else [])
        if isinstance(row, dict)
        and str(row.get("id") or row.get("page_id") or "").strip() == page_id
        and row.get("ownership_verified") is True
        and str(row.get("profile_id") or "").strip().isdigit()
    }
    return next(iter(actors)) if len(actors) == 1 else ""


def page_lookup_url(pages: Any, page_id: str) -> str:
    actor = owned_page_actor(pages, str(page_id))
    return (f"https://www.facebook.com/profile.php?id={actor}" if actor
            else f"https://www.facebook.com/{page_id}")


def page_query_kind(pages: Any, page_id: str, name: str, query: str) -> str:
    if not page_id.isdigit() or not name:
        return ""
    actor = owned_page_actor(pages, page_id)
    if query == name and actor and any(
        isinstance(row, dict)
        and str(row.get("id") or row.get("page_id") or "").strip() == page_id
        and str(row.get("name") or "").strip() == name
        and row.get("ownership_verified") is True
        for row in (pages if isinstance(pages, list) else [])
    ):
        return "verified_name"
    try:
        url = urlsplit(query)
        if url.scheme not in {"http", "https"} or url.username or url.password:
            return ""
        if url.hostname not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
            return ""
        if url.path.rstrip("/") == f"/{page_id}" and not url.query:
            return "asset_url"
        ids = parse_qs(url.query).get("id") or []
        if url.path == "/profile.php" and len(ids) == 1 and ids[0] in {page_id, actor} - {""}:
            return "profile_url"
    except ValueError:
        pass
    return ""


async def click_exact_page_search_result(page: Any, pages: Any, page_id: str,
                                         name: str, *, wait_seconds: float = 3.5) -> tuple[bool, dict]:
    marker = "data-remask-exact-page-search"
    diagnostic: dict = {"page_id": page_id, "page_name": name, "reason": "field_not_unique"}
    if page is None:
        return False, diagnostic
    fields = page.get_by_placeholder("Facebook Page name or URL", exact=True).filter(visible=True)
    diagnostic["field_count"] = await fields.count()
    if diagnostic["field_count"] != 1:
        return False, diagnostic
    query = (await fields.input_value()).strip()
    kind = page_query_kind(pages, page_id, name, query)
    diagnostic.update(query=query[:300], query_kind=kind, reason="query_identity_mismatch")
    if not kind:
        return False, diagnostic
    actor = owned_page_actor(pages, page_id)
    known_ids = sorted({page_id, actor} - {""})
    controls = " ".join(filter(None, [
        await fields.get_attribute("aria-controls"), await fields.get_attribute("aria-owns"),
    ])).split()
    deadline = time.monotonic() + max(0.05, wait_seconds)
    try:
        for poll in range(1, 11):
            await page.locator(f"[{marker}]").evaluate_all(
                "(els, attr) => els.forEach(el => el.removeAttribute(attr))", marker)
            labels = page.get_by_text(name, exact=True).filter(visible=True)
            count = await labels.count()
            candidates: dict[str, dict] = {}
            for index in range(min(count, 24)):
                row = await labels.nth(index).evaluate(r"""(el, args) => {
                    const visible = n => n && n.getBoundingClientRect().width > 0
                        && n.getBoundingClientRect().height > 0
                        && getComputedStyle(n).visibility !== 'hidden';
                    let target = el.closest('[role="option"],[role="button"],button,[role="link"],a[href]');
                    if (target && target.closest('[role="option"]')) target = target.closest('[role="option"]');
                    const interactive = !!target;
                    if (!target) {
                        if (/^H[1-6]$|^LABEL$/.test(el.tagName)) return null;
                        if (!el.closest('[role="listbox"]') && args.labelCount !== 1) return null;
                        target = el;
                    }
                    if (!visible(target) || target.disabled || target.getAttribute('aria-disabled') === 'true') return null;
                    const controlled = args.controls.map(id => document.getElementById(id)).filter(visible);
                    const popups = [...document.querySelectorAll('[role="listbox"]')].filter(n =>
                        visible(n) && (n.innerText || n.textContent || '').includes(args.name));
                    const scopes = controlled.length ? controlled : popups;
                    if (scopes.length && !scopes.some(root => root.contains(target))) return null;
                    const explicit = [
                        target.getAttribute('data-page-id'), target.getAttribute('data-profile-id'),
                    ].filter(value => value && /^\d+$/.test(value));
                    const links = [
                        ...(target.matches('a[href]') ? [target] : []),
                        ...target.querySelectorAll('a[href]'),
                    ].map(a => a.getAttribute('href') || '');
                    for (const href of links) {
                        try {
                            const u = new URL(href, 'https://www.facebook.com/');
                            if (!['facebook.com','www.facebook.com','m.facebook.com'].includes(u.hostname)) continue;
                            const pathId = u.pathname.match(/^\/(\d+)\/?$/);
                            const profileId = u.pathname === '/profile.php' ? u.searchParams.get('id') : '';
                            if (pathId) explicit.push(pathId[1]);
                            if (profileId && /^\d+$/.test(profileId)) explicit.push(profileId);
                        } catch (_) {}
                    }
                    if (explicit.some(id => !args.ids.includes(id))) return null;
                    const hay = [
                        target.getAttribute('data-key') || '',
                        target.getAttribute('data-testid') || '',
                        target.getAttribute('aria-label') || '',
                        target.innerText || target.textContent || '', ...links,
                    ].join(' | ');
                    const idMatch = explicit.some(id => args.ids.includes(id))
                        || args.ids.some(id => new RegExp('(^|\\D)' + id + '(\\D|$)').test(hay));
                    if (args.kind === 'verified_name' && !idMatch) return null;
                    let key = target.getAttribute(args.marker);
                    if (!key) {
                        key = String(args.index);
                        target.setAttribute(args.marker, key);
                    }
                    return {key, id_match:idMatch, interactive,
                        text:(target.innerText || target.textContent || '').trim().slice(0,180)};
                }""", {"ids": known_ids, "controls": controls, "kind": kind, "name": name,
                       "marker": marker, "index": index, "labelCount": count})
                if isinstance(row, dict):
                    candidates[row["key"]] = row
            rows = list(candidates.values())
            identified = [row for row in rows if row["id_match"]]
            eligible = identified if identified else rows
            diagnostic.update(reason="result_not_unique" if len(eligible) > 1 else "result_missing",
                              polls=poll, text_matches=count, candidates=rows[:12])
            if len(eligible) == 1:
                target = page.locator(f'[{marker}="{eligible[0]["key"]}"]')
                try:
                    await target.click(timeout=3000)
                except Exception as exc:
                    diagnostic.update(reason="result_click_failed", error_type=exc.__class__.__name__)
                    return False, diagnostic
                diagnostic.update(reason="selected", selected=eligible[0])
                return True, diagnostic
            if time.monotonic() >= deadline:
                break
            await page.wait_for_timeout(min(350, max(1, int((deadline-time.monotonic())*1000))))
        return False, diagnostic
    finally:
        try:
            await page.locator(f"[{marker}]").evaluate_all(
                "(els, attr) => els.forEach(el => el.removeAttribute(attr))", marker)
        except Exception:
            pass  # A closed page must not replace the selection outcome.
