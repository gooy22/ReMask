from __future__ import annotations

import html
import json
import re
from typing import Any

from .facebook_business_browser import (
    _extract_business_inventory_rows,
    _extract_inventory_ad_account_rows,
)

_JSON_SCRIPT_RE = re.compile(
    r"<script[^>]+type=[\"']application/json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_REQUEST_KEYS = {"variables", "params", "request", "input", "query", "preloadparams"}


def _response_only(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _response_only(child)
            for key, child in value.items()
            if str(key).replace("_", "").lower() not in _REQUEST_KEYS
        }
    if isinstance(value, list):
        return [_response_only(child) for child in value]
    return value


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
            payloads.append(_response_only(json.loads(raw)))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return payloads


def _business_payloads(payload: Any, business_id: str) -> list[dict[str, Any]]:
    """Only response subtrees which identify the exact portfolio are scoped.

    Request variables and the requested URL are lookup hints, never evidence.
    A selected Ads Manager account may belong to a different portfolio.
    """
    output: list[dict[str, Any]] = []
    ignored = {"variables", "params", "request", "input", "query", "preloadparams"}

    def portfolio_only(value, key=""):
        if isinstance(value, dict):
            typename = str(value.get("__typename") or "").replace("_", "").lower()
            is_business = typename in {"business", "businessportfolio"} or key.lower() in {
                "business", "bizkit_business", "business_portfolio", "businessportfolio",
            }
            if is_business and _clean_id(value.get("id")) not in {"", business_id}:
                return {}
            return {child_key: portfolio_only(child, str(child_key))
                    for child_key, child in value.items()}
        if isinstance(value, list):
            return [portfolio_only(child, key) for child in value]
        return value

    def walk(value, key=""):
        if isinstance(value, dict):
            typename = str(value.get("__typename") or "").replace("_", "").lower()
            business_node = typename in {"business", "businessportfolio"} or key.lower() in {
                "business", "bizkit_business", "business_portfolio", "businessportfolio",
            }
            if business_node and _clean_id(value.get("id")) == business_id:
                output.append(portfolio_only(value, key))
                return
            for child_key, child in value.items():
                if str(child_key).replace("_", "").lower() not in ignored:
                    walk(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                walk(child, key)
    walk(payload)
    return output


def _has_complete_scoped_inventory(payload: Any) -> bool:
    """Require every observed relevant collection to be complete.

    An empty owned collection cannot prove absence while the client collection
    is paginated or unhydrated. Request/input branches never supply evidence.
    """
    collection_keys = {"adaccounts", "ownedadaccounts", "clientadaccounts", "advertisingaccounts"}
    observations: list[bool] = []
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                compact = str(key).replace("_", "").lower()
                if compact in {"variables", "params", "request", "input", "query"}:
                    continue
                if compact in collection_keys:
                    if isinstance(child, list):
                        observations.append(True)
                    elif isinstance(child, dict):
                        page_info = child.get("page_info", child.get("pageInfo", {}))
                        observations.append(bool(isinstance(page_info, dict)
                            and page_info.get("has_next_page", page_info.get("hasNextPage")) is False
                            and page_info.get("has_previous_page", page_info.get("hasPreviousPage", False)) is False
                            and any(isinstance(child.get(field), list) for field in ("edges", "nodes", "items", "results"))))
                    else:
                        observations.append(False)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(payload)
    return bool(observations) and all(observations)


async def _fetch_payloads(web, url: str) -> tuple[list[Any], dict[str, Any]]:
    status, body, final_url = await web.fetch_text(url, max_bytes=5_000_000)
    gate = _auth_gate(final_url, body)
    return _json_payloads(body), {
        "requested_url": url,
        "final_url": final_url,
        "http_status": int(status),
        "auth_gate": gate,
        "bytes": len(body),
        "usable": 200 <= int(status) < 300 and not gate,
    }


async def private_inventory_snapshot(
    web,
    *,
    known_accounts_by_business: dict[str, set[str]],
    known_business_ids: set[str],
    personal_scope_id: str = "",
    discover_businesses: bool = True,
) -> dict[str, Any]:
    """Read Meta inventory without Chromium.

    This path does not invent persisted-query contracts. It reads the JSON/Relay
    payloads Meta embeds in authenticated Business/Ads Manager HTML and reuses
    the same structural inventory parsers as the browser observer.
    """

    diagnostics: list[dict[str, Any]] = []
    businesses: dict[str, str] = {}

    discovery_urls = (
        "https://business.facebook.com/latest/home",
        "https://business.facebook.com/latest/overview",
    ) if discover_businesses else ()
    for url in discovery_urls:
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
        if not diag.get("usable"):
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

        urls = [
            "https://business.facebook.com/latest/settings/ad_accounts/"
            f"?nav_ref=bm_settings_redirect_migration&bm_redirect_migration=true&business_id={business_id}",
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
            f"?business_id={business_id}",
        ]
        # Exact RK revalidation: when we already have a last-live-confirmed
        # BM->RK pair, ask Ads Manager to open that exact account under the BM.
        # We still require fresh authenticated response evidence; the hint is
        # never accepted from storage alone.
        for expected_id in sorted(expected)[:4]:
            urls.extend([
                "https://www.facebook.com/adsmanager/manage/campaigns"
                f"?act={expected_id}&business_id={business_id}",
                "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
                f"?act={expected_id}&business_id={business_id}",
            ])
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
            if not diag.get("usable"):
                continue

            request_scoped = "settings/ad_accounts" in url
            for payload in payloads:
                scoped = _business_payloads(payload, business_id)
                authoritative_container = authoritative_container or any(
                    _has_complete_scoped_inventory(value)
                    for value in scoped
                )
                scoped_ids = {
                    _clean_id(account.get("id") or account.get("account_id"))
                    for value in scoped
                    for account in _extract_inventory_ad_account_rows(value, request_scoped=request_scoped)
                }
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
                    row_business = _clean_id(account.get("business_id"))
                    if row_business and row_business != business_id:
                        continue
                    if not row_business and account_id not in scoped_ids:
                        continue
                    row = dict(account)
                    row["id"] = account_id
                    row["account_id"] = account_id
                    row["business_id"] = business_id
                    account_rows[account_id] = row

            # Stop read-only probes as soon as all exact known pairs are
            # confirmed, or this portfolio exposes an authoritative inventory.
            if (expected and expected.issubset(account_rows)) or authoritative_container:
                break

        confirmed_expected = sorted(expected.intersection(account_rows))
        ready = bool(account_rows) and (
            not expected or expected.issubset(account_rows)
        )
        confirmed_empty = bool(authoritative_container and not account_rows and not expected)

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
            "inventory_complete": bool(authoritative_container),
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
    required_targets = known_targets | set(businesses)
    ready = bool(required_targets) and required_targets.issubset(confirmed_businesses)

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

