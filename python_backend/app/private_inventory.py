from __future__ import annotations

import html
import json
import re
from typing import Any

from .facebook_business_browser import (
    _extract_business_inventory_rows,
    _extract_inventory_ad_account_rows,
    _has_ad_account_inventory_container,
)

_JSON_SCRIPT_RE = re.compile(
    r"<script[^>]+type=[\"']application/json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _clean_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("act_"):
        text = text[4:]
    return text if text.isdigit() else ""


def _auth_gate(url: str, body: str) -> str:
    folded_url = str(url or "").casefold()
    folded_body = str(body or "").casefold()
    if "/checkpoint" in folded_url:
        return "CHECKPOINT_REQUIRED"
    if "/login" in folded_url or "login_form" in folded_body:
        if "business.facebook.com/business/loginpage" in folded_url:
            return "BUSINESS_LOGIN_GATE"
        return "SESSION_EXPIRED"
    return ""


def _json_payloads(body: str) -> list[Any]:
    payloads: list[Any] = []
    for match in _JSON_SCRIPT_RE.finditer(str(body or "")):
        raw = html.unescape(str(match.group(1) or "")).strip()
        if not raw:
            continue
        try:
            payloads.append(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return payloads


async def _fetch_payloads(web, url: str) -> tuple[list[Any], dict[str, Any]]:
    status, body, final_url = await web.fetch_text(url, max_bytes=5_000_000)
    gate = _auth_gate(final_url, body)
    return _json_payloads(body), {
        "requested_url": url,
        "final_url": final_url,
        "http_status": int(status),
        "auth_gate": gate,
        "bytes": len(body),
    }


async def private_inventory_snapshot(
    web,
    *,
    known_accounts_by_business: dict[str, set[str]],
    known_business_ids: set[str],
    personal_scope_id: str = "",
) -> dict[str, Any]:
    """Read Meta inventory without Chromium.

    This path does not invent persisted-query contracts. It reads the JSON/Relay
    payloads Meta embeds in authenticated Business/Ads Manager HTML and reuses
    the same structural inventory parsers as the browser observer.
    """

    diagnostics: list[dict[str, Any]] = []
    businesses: dict[str, str] = {}

    for url in (
        "https://business.facebook.com/latest/home",
        "https://business.facebook.com/latest/overview",
    ):
        try:
            payloads, diag = await _fetch_payloads(web, url)
        except Exception as exc:
            diagnostics.append({
                "phase": "business_discovery",
                "url": url,
                "error": f"{exc.__class__.__name__}: {exc}"[:500],
            })
            continue
        diagnostics.append({"phase": "business_discovery", **diag})
        if diag.get("auth_gate"):
            continue
        for payload in payloads:
            for row in _extract_business_inventory_rows(payload):
                business_id = _clean_id(row.get("id"))
                if not business_id or business_id == personal_scope_id:
                    continue
                businesses[business_id] = str(row.get("name") or business_id).strip()

    targets = set(known_business_ids)
    targets.update(businesses)
    targets.discard(str(personal_scope_id or ""))

    rows: list[dict[str, Any]] = []
    confirmed_businesses: set[str] = set()
    inconclusive_businesses: set[str] = set()

    for business_id in sorted(targets)[:25]:
        expected = {
            _clean_id(value)
            for value in known_accounts_by_business.get(business_id, set())
            if _clean_id(value)
        }
        account_rows: dict[str, dict[str, Any]] = {}
        authoritative_container = False
        business_diags: list[dict[str, Any]] = []

        urls = (
            "https://business.facebook.com/latest/settings/ad_accounts/"
            f"?nav_ref=bm_settings_redirect_migration&bm_redirect_migration=true&business_id={business_id}",
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
            f"?business_id={business_id}",
        )
        for url in urls:
            try:
                payloads, diag = await _fetch_payloads(web, url)
            except Exception as exc:
                business_diags.append({
                    "url": url,
                    "error": f"{exc.__class__.__name__}: {exc}"[:500],
                })
                continue

            business_diags.append(diag)
            if diag.get("auth_gate"):
                continue

            request_scoped = "settings/ad_accounts" in url
            for payload in payloads:
                authoritative_container = (
                    authoritative_container
                    or _has_ad_account_inventory_container(
                        payload,
                        request_scoped=request_scoped,
                    )
                )
                for account in _extract_inventory_ad_account_rows(
                    payload,
                    request_scoped=request_scoped,
                ):
                    account_id = _clean_id(
                        account.get("id")
                        or account.get("account_id")
                        or account.get("ad_account_id")
                    )
                    if not account_id:
                        continue
                    row = dict(account)
                    row["id"] = account_id
                    row["account_id"] = account_id
                    row.setdefault("business_id", business_id)
                    account_rows[account_id] = row

        confirmed_expected = sorted(expected.intersection(account_rows))
        ready = bool(account_rows) and (
            not expected or bool(confirmed_expected)
        )
        confirmed_empty = bool(authoritative_container and not account_rows)

        if ready or confirmed_empty:
            confirmed_businesses.add(business_id)
        else:
            inconclusive_businesses.add(business_id)

        rows.append({
            "id": business_id,
            "name": businesses.get(business_id, business_id),
            "ad_accounts": [account_rows[key] for key in sorted(account_rows)],
            "ad_accounts_count": len(account_rows),
            "ad_accounts_ready": bool(ready or confirmed_empty),
            "ad_accounts_partial": bool(account_rows and not authoritative_container),
            "ad_accounts_source": (
                "private_http_relay_inventory"
                if ready or confirmed_empty
                else "private_http_relay_inconclusive"
            ),
            "confirmed_empty": confirmed_empty,
            "expected_account_ids": sorted(expected),
            "confirmed_expected_account_ids": confirmed_expected,
            "diagnostics": business_diags,
            "attempts": [],
            "section_diagnostic": {},
            "ads_manager_diagnostic": {},
        })

    known_targets = {
        value for value in known_business_ids
        if value and value != personal_scope_id
    }
    ready = bool(known_targets) and known_targets.issubset(confirmed_businesses)

    return {
        "ready": ready,
        "businesses": rows,
        "businesses_count": len(rows),
        "confirmed_business_ids": sorted(confirmed_businesses),
        "inconclusive_business_ids": sorted(inconclusive_businesses),
        "discovered_business_ids": sorted(businesses),
        "discovery_complete": bool(businesses),
        "source": "private_http_relay_inventory",
        "diagnostics": diagnostics,
    }
