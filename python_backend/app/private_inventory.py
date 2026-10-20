from __future__ import annotations

import html
import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

from .facebook_business_browser import (
    _extract_business_inventory_rows,
    _extract_inventory_ad_account_rows,
)

_JSON_SCRIPT_RE = re.compile(
    r"<script\b[^>]*>(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_REQUEST_KEYS = {"variables", "params", "request", "input", "query", "preloadparams"}


def _response_only(value: Any, depth: int = 0, decode_string: bool = False) -> Any:
    if depth > 64:
        return None
    if isinstance(value, dict):
        return {
            key: _response_only(child, depth + 1, str(key).replace("_", "").lower()
                in {"result", "response", "payload", "data", "json"})
            for key, child in value.items()
            if str(key).replace("_", "").lower() not in _REQUEST_KEYS
        }
    if isinstance(value, list):
        return [_response_only(child, depth + 1, decode_string) for child in value]
    if decode_string and isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            return value
        if isinstance(decoded, (dict, list)):
            return _response_only(decoded, depth + 1)
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
    login_form = bool(re.search(r"<form\b[^>]*(?:\bid\s*=\s*['\"]login_form['\"]|\baction\s*=\s*['\"][^'\"]*/login)", folded_body))
    if "/login" in folded_url or login_form:
        if "business.facebook.com/business/loginpage" in folded_url:
            return "BUSINESS_LOGIN_GATE"
        return "SESSION_EXPIRED"
    return ""


def _json_payloads(body: str) -> list[Any]:
    payloads: list[Any] = []
    document = str(body or "").strip()
    fragments = [match.group(1).strip() for match in _JSON_SCRIPT_RE.finditer(document)]
    if document.startswith("for (;;);"):
        document = document[len("for (;;);"):].lstrip()
    if document.startswith(("{", "[")):
        fragments.append(document)
    for raw in fragments:
        if not raw:
            continue
        # Parse data only, never JS statements or an object literal guessed
        # from a script. Raw JSON comes first so HTML entities in names survive.
        for candidate in dict.fromkeys((raw, html.unescape(raw))):
            try:
                decoded = json.loads(candidate)
                if isinstance(decoded, (dict, list)):
                    payloads.append(_response_only(decoded))
                    break
            except (TypeError, ValueError, RecursionError):
                pass
    return payloads


def _inventory_shape(payloads: list[Any]) -> list[dict[str, Any]]:
    """Non-secret structural evidence; never log response values or tokens."""
    output = []
    semantic_keys = {"business", "businesses", "businessportfolios", "ownedbusinesses",
        "clientbusinesses", "adaccounts", "ownedadaccounts", "clientadaccounts",
        "advertisingaccounts", "assets", "businessassets", "bizkitbusiness", "connectedobjects"}
    allowed_fields = {"id", "__typename", "page_info", "pageInfo", "edges", "nodes",
        "items", "results", "ad_accounts", "owned_ad_accounts", "client_ad_accounts",
        "business_id", "business", "businesses", "assets", "connected_objects"}
    pending = list(payloads)
    visited = 0
    while pending and visited < 10000 and len(output) < 20:
        value = pending.pop()
        visited += 1
        if isinstance(value, dict):
            for key, child in value.items():
                compact = str(key).replace("_", "").lower()
                if compact in semantic_keys:
                    row = {"field": str(key)[:80], "kind": type(child).__name__}
                    if isinstance(child, list):
                        row["count"] = len(child)
                    elif isinstance(child, dict):
                        row["fields"] = sorted(set(child) & allowed_fields)
                    output.append(row)
                if isinstance(child, (dict, list)):
                    pending.append(child)
        elif isinstance(value, list):
            pending.extend(child for child in value if isinstance(child, (dict, list)))
    return output


def inventory_diagnostic_summary(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics = list(snapshot.get("diagnostics", []))
    for business in snapshot.get("businesses", []):
        diagnostics.extend(business.get("diagnostics", []))
    return [{key: row[key] for key in ("requested_url", "final_url", "http_status",
        "auth_gate", "bytes", "usable", "payload_count", "inventory_shape", "error_type",
        "phase", "operation_kind", "contracts", "query_posts", "query_attempts", "query_names", "modules", "modules_scanned", "scripts") if key in row}
        for row in diagnostics if isinstance(row, dict)][-12:]


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
            is_business = typename in {"business", "businessportfolio", "adbusiness"} or key.lower() in {
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
            business_node = typename in {"business", "businessportfolio", "adbusiness"} or key.lower() in {
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


def _normalize_connected_inventory(payloads: list[Any], business_id: str) -> list[dict[str, Any]]:
    """Normalize Meta's observed AdBusiness.connected_objects RK read schema.

    Only exact response Business nodes count. Relay's UI/node id never takes
    precedence over explicit business_object_id/assetID. Unknown or conflicting
    edges make absence inconclusive; nested owner/Page/phone nodes are not RK.
    """
    output = []
    for payload in payloads:
        for business in _business_payloads(payload, business_id):
            connection = business.get("connected_objects")
            if not isinstance(connection, dict) or not isinstance(connection.get("edges"), list):
                continue
            accounts, invalid = [], False
            for edge in connection["edges"]:
                node = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(node, dict):
                    invalid = True
                    continue
                typename = str(node.get("__typename") or "").lower()
                kind = str(node.get("assetType") or node.get("business_asset_type") or "")
                if any(node.get(key) and not _clean_id(node.get(key)) for key in ("business_object_id", "assetID")):
                    invalid = True
                    continue
                explicit_ids = {_clean_id(node.get(key)) for key in ("business_object_id", "assetID") if node.get(key)}
                explicit_ids.discard("")
                account_id = next(iter(explicit_ids), "") if len(explicit_ids) == 1 else ""
                if not explicit_ids and typename == "adaccount":
                    account_id = _clean_id(node.get("id"))
                relationship = str(node.get("business_object_relationship_to_business") or "").upper()
                if (len(explicit_ids) > 1 or not account_id or account_id == business_id
                        or typename in {"page", "user", "adbusiness", "business", "pixel", "instagramaccount"}
                        or (kind and kind != "AD_ACCOUNT") or (not kind and typename != "adaccount")
                        or relationship in {"NONE", "REQUESTED", "PENDING", "DISCOVERED"}):
                    invalid = True
                    continue
                name = str(node.get("business_object_name") or node.get("name") or "")
                ui_ids = {_clean_id(node.get("business_object_ui_id"))}
                ui_ids.discard("")
                pending = [edge.get("nameColumn", {})]
                visited = 0
                while pending and visited < 64:
                    value = pending.pop()
                    visited += 1
                    if not isinstance(value, dict):
                        continue
                    value_id = _clean_id(value.get("business_object_id") or value.get("assetID") or value.get("id"))
                    if value_id == account_id:
                        name = str(value.get("business_object_name") or value.get("name") or name)
                        ui_id = _clean_id(value.get("business_object_ui_id"))
                        if ui_id:
                            ui_ids.add(ui_id)
                    pending.extend(child for key, child in value.items() if isinstance(child, dict)
                        and key not in {"owning_business", "business", "phone_numbers"})
                if len(ui_ids) > 1:
                    invalid = True
                    continue
                accounts.append({"node": {"__typename": "AdAccount", "id": account_id, "name": name,
                    "business_id": business_id,
                    "business_object_ui_id": next(iter(ui_ids), "")}})
            existence = business.get("business_ad_accounts")
            if isinstance(existence, dict) and existence.get("edges") and not accounts:
                # The observed query separately reports first:1 RK existence.
                # A hidden/excluded RK must not become complete absence.
                invalid = True
            page_info = connection.get("page_info", {})
            page_info = dict(page_info) if isinstance(page_info, dict) else {}
            if invalid:
                page_info["has_next_page"] = True
            output.append({"data": {"business": {"__typename": "Business", "id": business_id,
                "ad_accounts": {"edges": accounts, "page_info": page_info}}}})
    return output


def _scoped_collection_observations(payload: Any, asset_scope: bool = False) -> list[bool]:
    """Require every observed relevant collection to be complete.

    An empty owned collection cannot prove absence while the client collection
    is paginated or unhydrated. Request/input branches never supply evidence.
    """
    collection_keys = {"adaccounts", "ownedadaccounts", "clientadaccounts", "advertisingaccounts"}
    if asset_scope:
        collection_keys.update({"assets", "businessassets", "assignedassets", "businesssettingsassets"})
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
    return observations


def _has_complete_scoped_inventory(payload: Any) -> bool:
    observations = _scoped_collection_observations(payload)
    return bool(observations) and all(observations)


async def _fetch_payloads(web, url: str, *, refresh_modules: bool = True) -> tuple[list[Any], dict[str, Any]]:
    started = time.monotonic()
    status, body, final_url = await web.fetch_text(url, max_bytes=5_000_000)
    gate = _auth_gate(final_url, body)
    payloads = _json_payloads(body)
    target, final = urlsplit(url), urlsplit(final_url)
    trusted = final.scheme == "https" and final.hostname in {"facebook.com", "www.facebook.com",
        "business.facebook.com", "adsmanager.facebook.com"}
    if refresh_modules and status == 200 and not gate and trusted:
        from .contract_maintenance.module_refresh import refresh_due
        refresh = getattr(web, 'refresh_reviewed_modules', None)
        if callable(refresh) and (not hasattr(web, '_module_collection_progress') or refresh_due(web)):
            await refresh(body, final_url)
    return payloads, {
        "requested_url": target.scheme + "://" + (target.hostname or "") + target.path,
        "final_url": final.scheme + "://" + (final.hostname or "") + final.path,
        "http_status": int(status),
        "auth_gate": gate,
        "bytes": len(body.encode("utf-8")),
        "usable": 200 <= int(status) < 300 and not gate and trusted,
        "payload_count": len(payloads),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "inventory_shape": _inventory_shape(payloads) if trusted and not gate else [],
        "_document": body if trusted and not gate and 200 <= int(status) < 300 else "",
    }


async def private_inventory_snapshot(
    web,
    *,
    known_accounts_by_business: dict[str, set[str]],
    known_business_ids: set[str],
    personal_scope_id: str = "",
    discover_businesses: bool = True,
    execute_read_queries: bool = False,
    fast_sync: bool = False,
) -> dict[str, Any]:
    """Read Meta inventory without Chromium.

    This path does not invent persisted-query contracts. It reads the JSON/Relay
    payloads Meta embeds in authenticated Business/Ads Manager HTML and reuses
    the same structural inventory parsers as the browser observer.
    """

    started = time.monotonic()
    diagnostics: list[dict[str, Any]] = []
    businesses: dict[str, str] = {}
    discovery_complete = False
    discovery_auth_blocked = False

    # Business Suite HTML can be only a logged-in shell with business_id=null.
    # Reuse the reviewed selector read already used by BM provisioning instead
    # of treating that shell as the profile's complete inventory.
    if discover_businesses and execute_read_queries and callable(getattr(web, 'graphql', None)):
        try:
            from .static_meta_inventory import read_business_inventory
            proof = await asyncio.wait_for(read_business_inventory(web), timeout=18)
            businesses.update({str(key): str(name) for key, name in proof.get('rows', {}).items()
                if _clean_id(key) and str(key) != personal_scope_id})
            discovery_complete = proof.get('complete') is True
            diagnostics.append({'phase': 'business_discovery_query', 'operation_kind': 'query',
                'usable': True, 'complete': discovery_complete, 'business_count': len(businesses),
                'code': '' if discovery_complete else 'PRIVATE_BM_INVENTORY_INCOMPLETE',
                'source': proof.get('source', ''), 'queries': proof.get('diagnostics', [])})
        except Exception as exc:
            from .private_auth import private_auth_error
            from .private_transport_error import private_transport_error
            auth = private_auth_error(exc)
            _, failure = private_transport_error(exc)
            discovery_auth_blocked = auth is not None
            diagnostics.append({'phase': 'business_discovery_query', 'operation_kind': 'query',
                'code': auth.code if auth else failure['code'],
                'auth_gate': auth.code if auth else '', 'error_type': type(exc).__name__})

    discovery_urls = (
        "https://business.facebook.com/latest/home",
        "https://business.facebook.com/latest/overview",
    ) if discover_businesses and not fast_sync and not discovery_complete and not discovery_auth_blocked else ()
    for url in discovery_urls:
        try:
            payloads, diag = await _fetch_payloads(web, url, refresh_modules=not fast_sync)
        except Exception as exc:
            from .private_transport_error import private_transport_error
            _, failure = private_transport_error(exc)
            diagnostics.append({
                "phase": "business_discovery",
                "code": failure['code'],
                "error_type": exc.__class__.__name__,
            })
            continue
        diag.pop("_document", None)
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
    discovery_ms = int((time.monotonic() - started) * 1000)
    timings = {'business_discovery': discovery_ms, 'account_inventory': []}
    if discovery_auth_blocked:
        return {'ready': False, 'businesses': [], 'confirmed_business_ids': [],
            'inconclusive_business_ids': sorted(targets), 'discovered_business_ids': [],
            'discovery_complete': False, 'diagnostics': diagnostics,
            'timings_ms': {**timings, 'total': int((time.monotonic() - started) * 1000)}}

    rows: list[dict[str, Any]] = []
    confirmed_businesses: set[str] = set()
    inconclusive_businesses: set[str] = set()

    for business_id in sorted(targets)[:25]:
        account_started = time.monotonic()
        expected = {
            _clean_id(value)
            for value in known_accounts_by_business.get(business_id, set())
            if _clean_id(value)
        }
        account_rows: dict[str, dict[str, Any]] = {}
        authoritative_container = False
        collection_observations: list[bool] = []
        business_diags: list[dict[str, Any]] = []
        settings_document = ""
        settings_url = ""

        def accept(payloads, *, request_scoped, asset_scope=False, has_errors=False):
            nonlocal authoritative_container
            for payload in payloads:
                scoped = _business_payloads(payload, business_id)
                for value in scoped:
                    collection_observations.extend(_scoped_collection_observations(value, asset_scope))
                if has_errors:
                    collection_observations.append(False)
                authoritative_container = bool(collection_observations) and all(collection_observations)
                scoped_ids = {
                    _clean_id(account.get("id") or account.get("account_id"))
                    for value in scoped
                    for account in _extract_inventory_ad_account_rows(value, request_scoped=request_scoped)
                }
                for account in _extract_inventory_ad_account_rows(payload, request_scoped=request_scoped):
                    account_id = _clean_id(account.get("id") or account.get("account_id") or account.get("ad_account_id"))
                    if not account_id:
                        continue
                    row_business = _clean_id(account.get("business_id"))
                    if row_business and row_business != business_id:
                        continue
                    if not row_business and account_id not in scoped_ids:
                        continue
                    account_rows[account_id] = {**account, "id": account_id, "account_id": account_id, "business_id": business_id}

        # Interactive Sync prefers reviewed read-only GraphQL contracts.
        # Never consider a partial result complete and never erase durable hints.
        static_verified = False
        if fast_sync and execute_read_queries and callable(getattr(web, "graphql", None)):
            try:
                from .static_meta_inventory import read_ad_account_inventory
                proof = await asyncio.wait_for(
                    read_ad_account_inventory(web, business_id, ""), timeout=6.0,
                )
                for item in proof.get("account_rows", []):
                    if not isinstance(item, dict):
                        continue
                    account_id = _clean_id(item.get("id") or item.get("account_id"))
                    if not account_id or _clean_id(item.get("business_id")) != business_id:
                        continue
                    account_rows[account_id] = {
                        **item, "id": account_id,
                        "account_id": account_id, "business_id": business_id,
                    }
                authoritative_container = proof.get("complete") is True
                static_verified = (
                    bool(expected) and expected.issubset(account_rows)
                ) or (
                    authoritative_container and (
                        not expected or expected.issubset(account_rows)
                    )
                )
                business_diags.append({
                    "phase": "static_graphql_inventory",
                    "source": proof.get("source", ""),
                    "complete": authoritative_container,
                    "confirmed_expected_account_ids": sorted(expected.intersection(account_rows)),
                    "account_count": len(account_rows),
                })
            except Exception as exc:
                from .private_auth import private_auth_error
                from .private_transport_error import private_transport_error
                auth = private_auth_error(exc)
                _, failure = private_transport_error(exc)
                business_diags.append({
                    "phase": "static_graphql_inventory",
                    "code": auth.code if auth else failure["code"],
                    "auth_gate": auth.code if auth else "",
                    "error_type": type(exc).__name__,
                })
                if auth:
                    diagnostics.extend(business_diags)
                    timings['account_inventory'].append({'business_id': business_id,
                        'elapsed_ms': int((time.monotonic() - account_started) * 1000)})
                    return {'ready': False, 'businesses': rows,
                        'confirmed_business_ids': sorted(confirmed_businesses),
                        'inconclusive_business_ids': sorted(targets - confirmed_businesses),
                        'discovered_business_ids': sorted(businesses),
                        'discovery_complete': discovery_complete, 'diagnostics': diagnostics,
                        'timings_ms': {**timings, 'total': int((time.monotonic() - started) * 1000)}}

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
        for url in ([] if static_verified else urls):
            try:
                payloads, diag = await _fetch_payloads(web, url, refresh_modules=not fast_sync)
            except Exception as exc:
                from .private_transport_error import private_transport_error
                _, failure = private_transport_error(exc)
                business_diags.append({
                    "code": failure['code'],
                    "error_type": exc.__class__.__name__,
                })
                continue

            document = diag.pop("_document", "")
            if "settings/ad_accounts" in url and diag.get("usable"):
                settings_document, settings_url = document, url
            business_diags.append(diag)
            if not diag.get("usable"):
                continue

            accept(payloads, request_scoped="settings/ad_accounts" in url)

            # Stop read-only probes as soon as all exact known pairs are
            # confirmed, or this portfolio exposes an authoritative inventory.
            if (expected and expected.issubset(account_rows)) or authoritative_container:
                break

        if (execute_read_queries and not fast_sync and settings_document and not authoritative_container
                and not (expected and expected.issubset(account_rows))
                and not any(row.get("auth_gate") for row in business_diags)):
            from .private_inventory_queries import read_private_inventory_queries
            query_results, query_diagnostic = await read_private_inventory_queries(web,
                business_id=business_id, document=settings_document, entry_url=settings_url)
            business_diags.append(query_diagnostic)
            for result in query_results:
                accept(result["payloads"], request_scoped=True, asset_scope=result["asset_scope"], has_errors=result["has_errors"])

        confirmed_expected = sorted(expected.intersection(account_rows))
        # A partial collection can confirm an expected identity, but must
        # never be treated as the complete inventory in the fast Sync path.
        # The legacy path retains its previous reconciliation semantics.
        ready = bool(account_rows) and (
            not expected or expected.issubset(account_rows)
        ) and (not fast_sync or authoritative_container)
        confirmed_empty = bool(authoritative_container and not account_rows and not expected)

        if ready or confirmed_empty:
            confirmed_businesses.add(business_id)
        else:
            inconclusive_businesses.add(business_id)

        timings['account_inventory'].append({'business_id': business_id,
            'elapsed_ms': int((time.monotonic() - account_started) * 1000)})
        rows.append({
            "id": business_id,
            "name": businesses.get(business_id, business_id),
            "ad_accounts": [account_rows[key] for key in sorted(account_rows)],
            "ad_accounts_count": len(account_rows),
            "ad_accounts_ready": bool(ready or confirmed_empty),
            "ad_accounts_partial": bool(account_rows and not authoritative_container),
            "ad_accounts_source": (
                ("static_graphql_inventory" if static_verified else "private_http_relay_inventory")
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
    confirmed_empty = discovery_complete and not required_targets
    ready = confirmed_empty or (bool(required_targets) and required_targets.issubset(confirmed_businesses))
    # Positive proof for every known target cannot establish that unobserved
    # portfolios do not exist. A full interactive Sync needs complete BM scope
    # enumeration too; selected-BM reads retain their explicit limited scope.
    if fast_sync and discover_businesses and not discovery_complete:
        ready = False

    return {
        "ready": ready,
        "businesses": rows,
        "businesses_count": len(rows),
        "confirmed_business_ids": sorted(confirmed_businesses),
        "inconclusive_business_ids": sorted(inconclusive_businesses),
        "discovered_business_ids": sorted(businesses),
        "discovery_complete": discovery_complete,
        "inventory_scope": "profile" if discover_businesses else "selected_businesses",
        "business_inventory_confirmed_empty": confirmed_empty,
        "source": "private_http_relay_inventory",
        "diagnostics": diagnostics,
        "timings_ms": {**timings, 'total': int((time.monotonic() - started) * 1000)},
    }

