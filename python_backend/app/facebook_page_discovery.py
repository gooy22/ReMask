from __future__ import annotations

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


def _extract_known_page_lists(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Parse both the historical Account Quality response and newer Relay
    connection shapes (nodes / edges -> node) without treating unrelated
    numeric-id objects as Pages.
    """
    output: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(row: Any) -> None:
        page = _normalize_page(row)
        if not page:
            return
        if page["id"] in seen:
            return
        seen.add(page["id"])
        output.append(page)

    def walk(value: Any, *, in_page_branch: bool = False) -> None:
        if isinstance(value, list):
            for child in value:
                walk(child, in_page_branch=in_page_branch)
            return

        if not isinstance(value, dict):
            return

        if in_page_branch:
            add(value)

        for raw_key, child in value.items():
            key = str(raw_key or "").lower()
            next_page_branch = (
                in_page_branch
                or "page" in key
                or key in {"owned_pages", "client_pages"}
            )

            if key == "node" and in_page_branch:
                walk(child, in_page_branch=True)
                continue

            if key in {"nodes", "edges", "data"}:
                walk(child, in_page_branch=next_page_branch)
                continue

            walk(child, in_page_branch=next_page_branch)

    # Fast path for the long-lived Account Quality contract.
    for path in (
        ("data", "userData", "pages_can_administer"),
        ("data", "user", "pages_can_administer"),
        ("data", "viewer", "pages_can_administer"),
        ("data", "pages_can_administer"),
    ):
        node: Any = payload
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, list):
            for row in node:
                add(row)

    # Compatibility path for current/future Relay connection wrappers.
    walk(payload.get("data"), in_page_branch=False)
    return output


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
    bootstrap = await session.bootstrap()
    actor_id = _clean(getattr(bootstrap, "actor_id", ""))

    if not actor_id:
        raise PageDiscoveryError("Facebook actor_id is missing")

    diagnostics: list[str] = []
    candidates = list_candidates("LIST_PAGES")

    if not candidates:
        raise PageDiscoveryError("No LIST_PAGES doc_id candidates configured")

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

        # This query successfully executed and authoritatively returned no
        # administered Pages. Since this is a read-only operation, preserve the
        # result instead of inventing IDs from unrelated response objects.
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

        doc_matches = list(
            re.finditer(
                r'(?:"|\')?(?:doc_id|docID|id)(?:"|\')?\s*[:=]\s*'
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
) -> DocIdCandidate | None:
    """
    Lightweight v14 LIST_PAGES marker discovery.

    Only the initial Facebook HTML document and its response headers are
    inspected. JavaScript bundle URLs are never followed or downloaded.
    """
    del max_scripts

    entry_urls = (
        "https://www.facebook.com/accountquality/?landing_page=insights",
        "https://www.facebook.com/pages/?category=your_pages",
        "https://www.facebook.com/pages/?category=your_pages&ref=bookmarks",
    )

    for entry_url in entry_urls:
        try:
            if hasattr(session, "fetch_text_with_headers"):
                status, document, final_url, headers = (
                    await session.fetch_text_with_headers(
                        entry_url,
                        max_bytes=3_000_000,
                    )
                )
            else:
                status, document, final_url = await session.fetch_text(
                    entry_url,
                    max_bytes=3_000_000,
                )
                headers = {}
        except Exception:
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
    rows: list[dict[str, Any]] = []

    id_patterns = (
        r"""["']page_id["']\s*:\s*["']?(\d{5,25})["']?""",
        r"""["']pageID["']\s*:\s*["']?(\d{5,25})["']?""",
        r"""["']pageId["']\s*:\s*["']?(\d{5,25})["']?""",
    )
    generic_id_pattern = r"""["']id["']\s*:\s*["'](\d{5,25})["']"""
    name_patterns = (
        r"""["']name["']\s*:\s*["']([^"']{1,240})["']""",
        r"""["']page_name["']\s*:\s*["']([^"']{1,240})["']""",
        r"""["']pageName["']\s*:\s*["']([^"']{1,240})["']""",
    )
    category_pattern = r"""["']category["']\s*:\s*["']([^"']{1,160})["']"""

    for text in _browser_source_variants(source):
        marker_matches = list(
            re.finditer(
                r"""page_id|pageID|pageId|__typename["']?\s*:\s*["']Page["']""",
                text,
                flags=re.IGNORECASE,
            )
        )

        for marker in marker_matches:
            left = max(0, marker.start() - 1800)
            right = min(len(text), marker.end() + 2600)
            window = text[left:right]

            page_id = ""
            for pattern in id_patterns:
                match = re.search(pattern, window, flags=re.IGNORECASE)
                if match:
                    page_id = _clean(match.group(1))
                    break

            if not page_id and re.search(
                r"""__typename["']?\s*:\s*["']Page["']""",
                window,
                flags=re.IGNORECASE,
            ):
                match = re.search(
                    generic_id_pattern,
                    window,
                    flags=re.IGNORECASE,
                )
                if match:
                    page_id = _clean(match.group(1))

            if not page_id:
                continue

            page_name = ""
            for pattern in name_patterns:
                match = re.search(pattern, window, flags=re.IGNORECASE)
                if match:
                    page_name = html_lib.unescape(_clean(match.group(1)))
                    break

            if not page_name:
                continue

            category = ""
            category_match = re.search(
                category_pattern,
                window,
                flags=re.IGNORECASE,
            )
            if category_match:
                category = html_lib.unescape(_clean(category_match.group(1)))

            rows.append({
                "id": page_id,
                "name": page_name,
                "category": category,
            })

    return _dedupe_pages(rows)


async def discover_pages_from_browser_html(
    session: Any,
) -> PageDiscoveryResult:
    entry_urls = (
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
