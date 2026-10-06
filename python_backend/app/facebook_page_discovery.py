from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .facebook_docids import (
    DocIdCandidate,
    list_candidates,
    record_result,
    upsert_candidate,
)
from .facebook_query_discovery import discover_persisted_query


class PageDiscoveryError(RuntimeError):
    pass


@dataclass(slots=True)
class PageDiscoveryResult:
    pages: list[dict[str, Any]]
    source: str
    candidate: DocIdCandidate | None = None
    diagnostics: list[str] = field(default_factory=list)
    inventory_complete: bool = False


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalize_page(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None

    page_id = _clean(
        row.get("id")
        or row.get("page_id")
        or row.get("pageID")
        or row.get("pageId")
    )
    name = _clean(
        row.get("name")
        or row.get("page_name")
        or row.get("pageName")
    )

    if not page_id.isdigit() or not name:
        return None

    output: dict[str, Any] = {
        "id": page_id,
        "name": name,
        "category": _clean(row.get("category")),
    }

    tasks = row.get("tasks")
    if isinstance(tasks, list):
        output["tasks"] = [
            str(item)
            for item in tasks
            if isinstance(item, (str, int))
        ]

    business = row.get("business")
    if isinstance(business, dict):
        business_id = _clean(business.get("id"))
        if business_id:
            output["business"] = {
                "id": business_id,
                "name": _clean(business.get("name")),
            }
            output["business_id"] = business_id

    if isinstance(row.get("is_owned"), bool):
        output["is_owned"] = bool(row.get("is_owned"))

    restriction = row.get("advertising_restriction_info")
    if isinstance(restriction, dict):
        output["advertising_restriction_info"] = {
            "is_restricted": restriction.get("is_restricted"),
            "restriction_type": _clean(restriction.get("restriction_type")),
        }

    business = row.get("business")
    if isinstance(business, dict):
        business_id = _clean(business.get("id"))
        if business_id:
            output["business_id"] = business_id
    elif isinstance(business, (str, int)):
        business_id = _clean(business)
        if business_id:
            output["business_id"] = business_id

    if isinstance(row.get("is_owned"), bool):
        output["is_owned"] = bool(row.get("is_owned"))

    for key in ("ownership_verified", "ownership_source", "profile_id"):
        if key in row:
            output[key] = row[key]
    return output


def _iter_connection_rows(value: Any):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
        return

    if not isinstance(value, dict):
        return

    nodes = value.get("nodes")
    if isinstance(nodes, list):
        for item in nodes:
            if isinstance(item, dict):
                yield item

    edges = value.get("edges")
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node")
            if isinstance(node, dict):
                yield node

    data = value.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item


def _page_like(row: dict[str, Any]) -> bool:
    typename = _clean(
        row.get("__typename")
        or row.get("type")
        or row.get("entity_type")
    ).lower()

    if typename and "page" in typename and "business" not in typename:
        return True

    page_hint_keys = {
        "page_id",
        "pageID",
        "pageId",
        "category",
        "tasks",
        "advertising_restriction_info",
        "is_owned",
        "can_post",
        "followers_count",
        "fan_count",
    }
    return any(key in row for key in page_hint_keys)


def _dedupe_pages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    index: dict[str, int] = {}

    for raw in rows:
        page = _normalize_page(raw)
        if not page:
            continue

        page_id = page["id"]
        if page_id not in index:
            index[page_id] = len(output)
            output.append(page)
            continue

        existing = output[index[page_id]]
        # Merge richer representations of the same Page.
        for key, value in page.items():
            if key not in existing or existing.get(key) in ("", None, [], {}):
                existing[key] = value

    return output


MANAGED_PAGE_KEYS = frozenset({
    "pages_can_administer", "pages_you_manage", "managed_pages", "owned_pages",
    "client_pages", "pages_can_manage", "your_pages",
})


def _page_management_proven(row: dict[str, Any]) -> bool:
    return any(row.get(key) is True for key in (
        "is_owned", "is_admin", "viewer_can_manage", "can_manage", "can_post",
    )) or any(str(task).upper() in {"MANAGE", "ADMINISTER", "CREATE_CONTENT"}
              for task in (row.get("tasks") if isinstance(row.get("tasks"), list) else []))


def _extract_known_page_lists(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return exact Page objects with management evidence, never nearby IDs."""
    output: list[dict[str, Any]] = []

    def add(row: Any, source: str) -> None:
        if not isinstance(row, dict):
            return
        typename = _clean(row.get("__typename") or row.get("type")).lower()
        if typename and "page" not in typename:
            return
        page = _normalize_page(row)
        if page:
            page.update(ownership_verified=True, ownership_source=source)
            output.append(page)

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, dict):
            if _page_like(value) and _page_management_proven(value):
                add(value, "explicit_page_management")
            for key, child in value.items():
                if key == "additional_profiles_with_biz_tools":
                    # New Pages Experience returns a User/profile wrapper.
                    # Business assets use its explicit delegate Page, not the
                    # wrapper ID or the Promote link's ID.
                    for profile in _iter_connection_rows(child):
                        delegate = profile.get("delegate_page")
                        delegate = delegate if isinstance(delegate, dict) else {}
                        delegate_id = _clean(delegate.get("id") or profile.get("delegate_page_id"))
                        if not delegate_id.isdigit():
                            continue
                        page = dict(delegate)
                        page.update(id=delegate_id, name=delegate.get("name") or profile.get("name"),
                                    profile_id=_clean(profile.get("id")))
                        add(page, "additional_profiles_with_biz_tools.delegate_page")
                elif str(key).lower() in MANAGED_PAGE_KEYS:
                    for row in _iter_connection_rows(child):
                        add(row, str(key).lower())
                else:
                    walk(child)
        elif isinstance(value, str) and value.startswith(("{", "[")):
            try:
                walk(json.loads(value))
            except ValueError:
                pass

    walk(payload)
    return _dedupe_pages(output)


def _errors(payload: dict[str, Any]) -> list[Any]:
    raw = payload.get("errors")
    if isinstance(raw, list):
        return raw
    if raw:
        return [raw]
    error = payload.get("error")
    if error:
        return [error]
    return []


def _private_page_inventory_complete(payload: dict[str, Any], actor_id: str) -> bool:
    """Only a fully read actor-admin connection can establish absence."""
    if _errors(payload):
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False

    def valid_page(row: Any) -> bool:
        if _normalize_page(row) is None:
            return False
        typename = _clean(row.get("__typename") or row.get("type")).lower()
        return not typename or ("page" in typename and "business" not in typename)

    def complete(value: Any) -> bool:
        if isinstance(value, list):
            return all(valid_page(row) for row in value)
        if not isinstance(value, dict):
            return False
        page_info = value.get("page_info") or value.get("pageInfo")
        if not isinstance(page_info, dict) or (
            page_info.get("has_next_page", page_info.get("hasNextPage")) is not False
        ):
            return False
        if "edges" in value:
            edges = value["edges"]
            return isinstance(edges, list) and all(
                isinstance(edge, dict) and valid_page(edge.get("node"))
                for edge in edges
            )
        nodes = value.get("nodes")
        return isinstance(nodes, list) and all(valid_page(row) for row in nodes)

    def walk(value: Any) -> bool:
        if isinstance(value, list):
            return any(walk(child) for child in value)
        if not isinstance(value, dict):
            return False
        identity = _clean(value.get("id"))
        if identity and identity != actor_id:
            return False
        if "pages_can_administer" in value:
            return complete(value["pages_can_administer"])
        return any(walk(child) for child in value.values())

    return walk(data)


def business_page_relation_proven(payload: Any, business_id: str, page_id: str, *, request_scoped: bool = False) -> bool:
    """Confirm an exact asset relationship; unrelated Page occurrences fail."""
    business, page = str(business_id), str(page_id)
    connections = {"owned_pages", "client_pages", "business_assets", "page_assets", "bizkit_business_assets"}
    def walk(value: Any, business_scoped: bool = False) -> bool:
        if isinstance(value, list):
            return any(walk(child, business_scoped) for child in value)
        if not isinstance(value, dict):
            return False
        object_id = str(value.get("id") or "")
        if object_id and object_id != business and (
            "business" in str(value.get("__typename") or "").lower()
            or any(key in value for key in connections | {"primary_page"})):
            return False
        local = business_scoped or (str(value.get("id") or "") == business and (
            "business" in str(value.get("__typename") or "").lower()
            or any(key in value for key in connections | {"primary_page"})))
        if local and isinstance(value.get("primary_page"), dict) and str(value["primary_page"].get("id")) == page:
            return True
        for key, child in value.items():
            if key in connections and (local or request_scoped):
                for row in _iter_connection_rows(child):
                    asset = row.get("asset") if isinstance(row.get("asset"), dict) else row
                    typename = str(asset.get("__typename") or asset.get("asset_type") or asset.get("type") or "").lower()
                    if str(asset.get("id") or asset.get("page_id") or "") == page and (key in {"owned_pages", "client_pages", "page_assets"} or "page" in typename):
                        return True
            if walk(child, local):
                return True
        return False
    return walk(payload)


def browser_business_page_relation_proven(document: str, business_id: str, page_id: str) -> bool:
    for text in _browser_source_variants(document):
        for match in re.finditer(r'<script\b[^>]*>(.*?)</script\s*>', text, re.I | re.S):
            try:
                payload = json.loads(match.group(1).strip())
            except ValueError:
                continue
            if business_page_relation_proven(payload, business_id, page_id):
                return True
    return False


def _diagnostic_text(payload: dict[str, Any]) -> str:
    values: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {
                    "message",
                    "description",
                    "summary",
                    "error_user_msg",
                    "error_user_title",
                    "type",
                }:
                    text = _clean(child)
                    if text:
                        values.append(text)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return " | ".join(values)


def _looks_stale(payload: dict[str, Any]) -> bool:
    text = _diagnostic_text(payload).lower()
    return any(
        marker in text
        for marker in (
            "persistedquerynotfound",
            "persisted query",
            "query not found",
            "unknown query",
            "unknown document",
            "document id",
            "doc_id",
            "invalid document",
            "unknown argument",
            "unknown field",
            "variable ",
            "was not provided",
            "expected type",
            "cannot query field",
            "operation not found",
        )
    )


def _variables_for(
    candidate: DocIdCandidate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    if candidate.variables_mode == "account_quality_user_pages_v1":
        return {
            "assetOwnerId": actor_id,
        }

    raise PageDiscoveryError(
        f"Unsupported LIST_PAGES variables_mode: {candidate.variables_mode}"
    )


async def list_pages_via_private_graphql(
    session: Any,
) -> PageDiscoveryResult:
    diagnostics: list[str] = []
    candidates = list_candidates("LIST_PAGES")

    if not candidates:
        # REMASK_LIST_PAGES_SELF_HEAL_V2
        # Recover the current read-only Page query BEFORE paying the relatively
        # expensive fb_dtsg bootstrap cost. On slower profile proxies bootstrap
        # can consume most of the Sync budget by itself.
        try:
            discovered = await asyncio.wait_for(
                discover_current_list_pages_docid_by_marker(
                    session,
                    max_entries=2,
                    per_entry_timeout=2.2,
                ),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            discovered = None
            diagnostics.append("runtime LIST_PAGES marker discovery timed out")

        if discovered is not None:
            candidates = [discovered]
            diagnostics.append(
                "runtime LIST_PAGES candidate recovered "
                f"doc_id={discovered.doc_id}"
            )

    if not candidates:
        raise PageDiscoveryError(
            "No LIST_PAGES doc_id candidates configured after fast runtime discovery"
        )

    # Bootstrap only once we actually have a query worth sending.
    bootstrap = await session.bootstrap()
    actor_id = _clean(getattr(bootstrap, "actor_id", ""))

    if not actor_id:
        raise PageDiscoveryError("Facebook actor_id is missing")

    for candidate in candidates:
        try:
            variables = _variables_for(
                candidate,
                actor_id=actor_id,
            )
        except PageDiscoveryError as exc:
            diagnostics.append(
                f"{candidate.doc_id}: {exc}"
            )
            continue

        try:
            response = await session.graphql(
                candidate.doc_id,
                variables,
                friendly_name=candidate.friendly_name,
                endpoint_url=candidate.endpoint_url,
            )
        except Exception as exc:
            payload = getattr(exc, "meta_payload", None)
            if isinstance(payload, dict) and _looks_stale(payload):
                reason = (
                    f"stale doc_id={candidate.doc_id} "
                    f"friendly={candidate.friendly_name or '-'} "
                    f"message={exc}"
                )
                record_result(
                    "LIST_PAGES",
                    candidate,
                    success=False,
                    reason=reason,
                )
                diagnostics.append(reason)
                continue

            diagnostics.append(
                f"{candidate.doc_id}: transport error: {exc}"
            )
            continue

        pages = _extract_known_page_lists(response)
        inventory_complete = _private_page_inventory_complete(response, actor_id)

        if pages:
            record_result(
                "LIST_PAGES",
                candidate,
                success=True,
                response_path="data.*.pages_can_administer",
            )
            return PageDiscoveryResult(
                pages=pages,
                source="facebook_web_graphql",
                candidate=candidate,
                diagnostics=diagnostics,
                inventory_complete=inventory_complete,
            )

        if _errors(response) and _looks_stale(response):
            reason = (
                f"stale doc_id={candidate.doc_id} "
                f"friendly={candidate.friendly_name or '-'} "
                f"message={_diagnostic_text(response)}"
            )
            record_result(
                "LIST_PAGES",
                candidate,
                success=False,
                reason=reason,
            )
            diagnostics.append(reason)
            continue

        # A generic data object, a null connection or a paginated response is
        # not proof of absence. Preserve it for Sync, but expose completeness
        # separately so CREATE reconciliation cannot release a duplicate guard.
        if isinstance(response.get("data"), dict):
            record_result(
                "LIST_PAGES",
                candidate,
                success=True,
                response_path="data",
            )
            return PageDiscoveryResult(
                pages=[],
                source="facebook_web_graphql",
                candidate=candidate,
                diagnostics=diagnostics,
                inventory_complete=inventory_complete,
            )

        diagnostics.append(
            f"{candidate.doc_id}: unrecognized LIST_PAGES response"
        )

    raise PageDiscoveryError(
        "No usable private LIST_PAGES candidate. "
        + " || ".join(diagnostics[-8:])
    )


def _extract_page_query_near_markers(
    source: str,
) -> tuple[str, str]:
    text = str(source or "")
    if "pages_can_administer" not in text or "assetOwnerId" not in text:
        return "", ""

    best: tuple[int, str, str] | None = None
    for marker in re.finditer("pages_can_administer", text):
        left = max(0, marker.start() - 7000)
        right = min(len(text), marker.end() + 7000)
        window = text[left:right]

        if "assetOwnerId" not in window:
            continue

        # Only explicit GraphQL document-id fields are safe here.
        # A generic "id" near pages_can_administer can be assetOwnerId,
        # Page ID, actor ID, etc. Generic Relay operation "id" extraction is
        # handled by discover_persisted_query(), which scopes it to the
        # operation/friendly-name envelope.
        doc_matches = list(
            re.finditer(
                r'(?:"|\')?(?:doc_id|docID)(?:"|\')?\s*[:=]\s*'
                r'(?:"|\')([0-9]{5,40})(?:"|\')',
                window,
                flags=re.IGNORECASE,
            )
        )
        if not doc_matches:
            continue

        friendly = ""
        friendly_patterns = (
            r'fb_api_req_friendly_name(?:"|\')?\s*[:=]\s*["\']([^"\']+Query)["\']',
            r'["\']name["\']\s*:\s*["\']([^"\']+Query)["\']',
            r'["\']([^"\']*(?:Page|Pages)[^"\']*Query)["\']',
        )
        for pattern in friendly_patterns:
            match = re.search(pattern, window, flags=re.IGNORECASE)
            if match:
                friendly = _clean(match.group(1))
                break

        for doc_match in doc_matches:
            absolute = left + doc_match.start(1)
            distance = abs(absolute - marker.start())
            candidate = (distance, doc_match.group(1), friendly)
            if best is None or candidate[0] < best[0]:
                best = candidate

    if best is None:
        return "", ""
    return best[1], best[2]


async def discover_current_list_pages_docid_by_marker(
    session: Any,
    *,
    max_scripts: int = 0,
    max_entries: int | None = None,
    per_entry_timeout: float | None = None,
) -> DocIdCandidate | None:
    """
    Lightweight v14 LIST_PAGES marker discovery.

    Only the initial Facebook HTML document and its response headers are
    inspected. JavaScript bundle URLs are never followed or downloaded.
    """
    del max_scripts

    entry_urls = (
        # Page-specific surfaces first: they are the most likely to contain the
        # current Page inventory Relay operation and avoid wasting Sync budget
        # on unrelated Facebook home documents.
        "https://www.facebook.com/pages/?category=your_pages",
        "https://www.facebook.com/pages/?category=your_pages&ref=bookmarks",
        "https://www.facebook.com/accountquality/?landing_page=insights",
        "https://business.facebook.com/latest/home",
        "https://www.facebook.com/",
    )
    bounded_urls = (
        entry_urls[:max(1, int(max_entries))]
        if max_entries is not None
        else entry_urls
    )

    for entry_url in bounded_urls:
        try:
            async def fetch_entry():
                if hasattr(session, "fetch_text_with_headers"):
                    return await session.fetch_text_with_headers(
                        entry_url,
                        max_bytes=3_000_000,
                    )
                status, document, final_url = await session.fetch_text(
                    entry_url,
                    max_bytes=3_000_000,
                )
                return status, document, final_url, {}

            if per_entry_timeout is not None:
                status, document, final_url, headers = await asyncio.wait_for(
                    fetch_entry(),
                    timeout=max(0.5, float(per_entry_timeout)),
                )
            else:
                status, document, final_url, headers = await fetch_entry()
        except (asyncio.TimeoutError, Exception):
            continue

        if status >= 400:
            continue

        doc_id, friendly = _extract_page_query_near_markers(document)
        if doc_id:
            return upsert_candidate(
                "LIST_PAGES",
                doc_id=doc_id,
                friendly_name=friendly,
                endpoint_url="https://www.facebook.com/api/graphql/",
                variables_mode="account_quality_user_pages_v1",
                source="runtime_marker_html",
                priority=8_400,
                observed_at=str(int(time.time())),
            )

        header_blob = "\n".join(
            f"{key}: {value}"
            for key, value in dict(headers or {}).items()
        )
        doc_id, friendly = _extract_page_query_near_markers(header_blob)
        if doc_id:
            return upsert_candidate(
                "LIST_PAGES",
                doc_id=doc_id,
                friendly_name=friendly,
                endpoint_url="https://www.facebook.com/api/graphql/",
                variables_mode="account_quality_user_pages_v1",
                source="runtime_marker_response_headers",
                priority=8_400,
                observed_at=str(int(time.time())),
            )

    return None


async def discover_current_list_pages_docid(
    session: Any,
    *,
    max_scripts: int = 18,
) -> DocIdCandidate | None:
    friendly_name = "AccountQualityUserPagesWrapper_UserPageQuery"

    discovered = await discover_persisted_query(
        session,
        friendly_name=friendly_name,
        entry_urls=[
            "https://www.facebook.com/",
            "https://business.facebook.com/latest/home",
            "https://www.facebook.com/accountquality/?landing_page=insights",
            "https://www.facebook.com/pages/?category=your_pages",
        ],
        max_scripts_per_entry=max_scripts,
    )

    if discovered is not None:
        return upsert_candidate(
            "LIST_PAGES",
            doc_id=discovered.doc_id,
            friendly_name=friendly_name,
            endpoint_url="https://www.facebook.com/api/graphql/",
            variables_mode="account_quality_user_pages_v1",
            source=f"runtime_{discovered.source_kind}",
            priority=8_500,
            observed_at=str(int(time.time())),
        )

    return await discover_current_list_pages_docid_by_marker(
        session,
        max_scripts=max(24, int(max_scripts)),
    )


def _browser_source_variants(source: str) -> list[str]:
    raw = str(source or "")
    variants = [raw]
    decoded = raw
    for _ in range(2):
        try:
            value = json.loads(decoded)
        except ValueError:
            break
        if not isinstance(value, str):
            break
        decoded = value
        if decoded not in variants:
            variants.append(decoded)

    entity_decoded = html_lib.unescape(raw)
    if entity_decoded not in variants:
        variants.append(entity_decoded)

    def decode_ascii_unicode(match: re.Match[str]) -> str:
        codepoint = int(match.group(1), 16)
        return chr(codepoint) if codepoint <= 0x7F else match.group(0)

    js_decoded = re.sub(
        r'\\u([0-9a-fA-F]{4})',
        decode_ascii_unicode,
        entity_decoded,
    )
    js_decoded = re.sub(
        r'\\x([0-9a-fA-F]{2})',
        lambda match: chr(int(match.group(1), 16)),
        js_decoded,
    )
    js_decoded = (
        js_decoded
        .replace(r'\\/', '/')
        .replace(r'\\"', '"')
        .replace(r"\\'", "'")
    )
    if js_decoded not in variants:
        variants.append(js_decoded)

    return variants


def _extract_pages_from_browser_document(source: str) -> list[dict[str, Any]]:
    """Parse JSON object boundaries and explicit managed-page connections.

    Regex proximity is not identity or ownership evidence: a Page marker can
    sit beside User, Business, recommendation and picture IDs in Relay state.
    """
    rows: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    managed_pattern = re.compile(r'["\'](' + '|'.join(sorted(MANAGED_PAGE_KEYS | {"additional_profiles_with_biz_tools"})) + r')["\']\s*:\s*', re.I)
    for text in _browser_source_variants(source):
        for match in managed_pattern.finditer(text):
            try:
                value, _ = decoder.raw_decode(text, match.end())
            except ValueError:
                continue
            rows.extend(_extract_known_page_lists({match.group(1).lower(): value}))
        # Standalone JSON scripts may contain explicit ownership flags instead
        # of a named connection. Decode the complete object, not a text window.
        for match in re.finditer(r'<script\b[^>]*>(.*?)</script\s*>', text, re.I | re.S):
            body = match.group(1).strip()
            try:
                value = json.loads(body)
            except ValueError:
                continue
            if isinstance(value, (dict, list)):
                rows.extend(_extract_known_page_lists(value))
    return _dedupe_pages(rows)


def _browser_page_candidate_diagnostic(source: str) -> list[dict[str, Any]]:
    """Non-secret shape evidence for unsupported live Page connections."""
    output = {}
    def walk(value: Any, path: tuple[str, ...] = (), parent_keys: tuple[str, ...] = ()) -> None:
        if isinstance(value, list):
            for child in value:
                walk(child, (*path, "[]"), parent_keys)
        elif isinstance(value, dict):
            page_id = _clean(value.get("page_id") or value.get("pageId") or value.get("pageID") or value.get("id"))
            if value.get("name") or any(key in value for key in ("page_id", "pageID", "pageId")):
                if page_id.isdigit() and len(output) < 35:
                    output[(page_id, path[-6:])] = {
                        "id": page_id, "typename": _clean(value.get("__typename")), "path": ".".join(path[-6:]),
                        "keys": sorted(value.keys())[:55], "parent_keys": list(parent_keys)[:30],
                        "flags": {key: val for key, val in value.items() if isinstance(val, bool)},
                        "delegate_page_id": _clean(value.get("delegate_page_id")),
                        "delegate_page": {key: value["delegate_page"].get(key) for key in ("id", "__typename")} if isinstance(value.get("delegate_page"), dict) else None,
                    }
            for key, child in value.items():
                walk(child, (*path, str(key)), tuple(sorted(value.keys())))
        elif isinstance(value, str) and value.startswith(("{", "[")) and "__typename" in value:
            try:
                walk(json.loads(value), (*path, "json_string"), parent_keys)
            except ValueError:
                pass
    for text in _browser_source_variants(source):
        for match in re.finditer(r'<script\b[^>]*>(.*?)</script\s*>', text, re.I | re.S):
            try:
                walk(json.loads(match.group(1).strip()))
            except ValueError:
                pass
    return list(output.values())


async def discover_pages_from_browser_html(
    session: Any,
) -> PageDiscoveryResult:
    entry_urls = (
        "https://www.facebook.com/",
        "https://business.facebook.com/latest/home",
        "https://www.facebook.com/pages/?category=your_pages",
        "https://www.facebook.com/pages/?category=your_pages&ref=bookmarks",
        "https://www.facebook.com/pages/",
        "https://business.facebook.com/latest/settings/pages",
    )

    diagnostics: list[str] = []

    for entry_url in entry_urls:
        try:
            status, document, final_url = await session.fetch_text(
                entry_url,
                max_bytes=4_000_000,
            )
        except Exception as exc:
            diagnostics.append(
                f"{entry_url}: fetch error {exc.__class__.__name__}"
            )
            continue

        lower_url = str(final_url or "").lower()
        lower_body = str(document or "").lower()
        if (
            "/login" in lower_url
            or "/checkpoint" in lower_url
            or "login_form" in lower_body
        ):
            diagnostics.append(
                f"{entry_url}: login/checkpoint redirect"
            )
            continue

        if status >= 400:
            diagnostics.append(
                f"{entry_url}: HTTP {status}"
            )
            continue

        pages = _extract_pages_from_browser_document(document)
        diagnostics.append(
            f"{entry_url}: HTTP {status} final={final_url} "
            f"bytes={len(document)} pages={len(pages)}"
        )

        if pages:
            return PageDiscoveryResult(
                pages=pages,
                source="facebook_browser_pages_html",
                candidate=None,
                diagnostics=diagnostics[-8:],
            )

    raise PageDiscoveryError(
        "Authenticated Facebook Pages surfaces returned no parseable Fan Pages. "
        + " || ".join(diagnostics[-8:])
    )


async def discover_pages_via_web(
    session: Any,
    *,
    try_runtime_discovery: bool = True,
) -> PageDiscoveryResult:
    diagnostics: list[str] = []
    first_result: PageDiscoveryResult | None = None

    try:
        first_result = await list_pages_via_private_graphql(session)
        diagnostics.extend(first_result.diagnostics)
        if first_result.pages:
            return first_result
    except PageDiscoveryError as exc:
        diagnostics.append(str(exc))

    if try_runtime_discovery:
        try:
            candidate = await discover_current_list_pages_docid(session)
        except Exception as exc:
            candidate = None
            diagnostics.append(f"LIST_PAGES runtime discovery failed: {exc}")

        if candidate is not None:
            try:
                refreshed = await list_pages_via_private_graphql(session)
                diagnostics.extend(refreshed.diagnostics)
                if refreshed.pages:
                    return refreshed
                first_result = refreshed
            except PageDiscoveryError as exc:
                diagnostics.append(str(exc))

    # The 2023 Account Quality persisted query is only one historical route.
    # A real FB profile can still have Pages even when that query is stale,
    # renamed, or its Relay response shape changed. Fall back to the actual
    # authenticated browser Pages surface before concluding "0 Pages".
    try:
        html_result = await discover_pages_from_browser_html(session)
        html_result.diagnostics = (
            diagnostics + html_result.diagnostics
        )[-12:]
        return html_result
    except PageDiscoveryError as exc:
        diagnostics.append(str(exc))

    if first_result is not None:
        first_result.diagnostics = diagnostics[-12:]
        return first_result

    raise PageDiscoveryError(
        "No browser-session Fan Pages were discoverable. "
        + " || ".join(diagnostics[-12:])
    )


__all__ = [
    "PageDiscoveryError",
    "PageDiscoveryResult",
    "discover_pages_via_web",
    "discover_pages_from_browser_html",
    "discover_current_list_pages_docid",
    "discover_current_list_pages_docid_by_marker",
    "list_pages_via_private_graphql",
]
