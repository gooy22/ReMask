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
    paths = (
        ("data", "userData", "pages_can_administer"),
        ("data", "user", "pages_can_administer"),
        ("data", "viewer", "pages_can_administer"),
        ("data", "pages_can_administer"),
        ("data", "userData", "pages"),
        ("data", "user", "pages"),
        ("data", "viewer", "pages"),
        ("data", "pages"),
    )

    def get_path(path: tuple[str, ...]) -> Any:
        node: Any = payload
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node

    candidates: list[dict[str, Any]] = []

    for path in paths:
        value = get_path(path)
        for row in _iter_connection_rows(value):
            candidates.append(row)

    # Relay shapes change often. As a conservative fallback, recursively scan
    # only objects that have a numeric id/name AND Page-specific evidence.
    stack: list[Any] = [payload]
    seen_objects: set[int] = set()

    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            object_id = id(node)
            if object_id in seen_objects:
                continue
            seen_objects.add(object_id)

            if _page_like(node):
                candidates.append(node)

            for child in node.values():
                if isinstance(child, (dict, list)):
                    stack.append(child)

        elif isinstance(node, list):
            for child in node:
                if isinstance(child, (dict, list)):
                    stack.append(child)

    return _dedupe_pages(candidates)


def _extract_pages_from_html(document: str) -> list[dict[str, Any]]:
    source = html_lib.unescape(str(document or "")).replace("\\/", "/")
    candidates: list[dict[str, Any]] = []

    script_patterns = (
        r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
        r'<script[^>]*data-sjs[^>]*>(.*?)</script>',
    )

    decoded = 0
    for pattern in script_patterns:
        for match in re.finditer(
            pattern,
            source,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            body = html_lib.unescape(match.group(1)).strip()
            if body.startswith("for (;;);"):
                body = body[len("for (;;);"):].lstrip()
            if not body or body[0] not in "[{":
                continue

            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                continue

            decoded += 1
            if isinstance(payload, dict):
                candidates.extend(_extract_known_page_lists(payload))
            elif isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        candidates.extend(_extract_known_page_lists(item))

            if decoded >= 250:
                break
        if decoded >= 250:
            break

    # Some Facebook bootstraps serialize page objects inside non-JSON script
    # wrappers. Do not take arbitrary numeric ids: require an explicit Page
    # typename close to id+name.
    typename_pattern = re.compile(
        r'\{[^{}]{0,2500}?"__typename"\s*:\s*"(?P<type>[^"]*Page[^"]*)"'
        r'[^{}]{0,2500}?\}',
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in typename_pattern.finditer(source):
        fragment = match.group(0)
        id_match = re.search(
            r'"(?:id|page_id|pageID|pageId)"\s*:\s*"?(\d{5,30})"?',
            fragment,
        )
        name_match = re.search(
            r'"(?:name|page_name|pageName)"\s*:\s*"([^"]{1,300})"',
            fragment,
        )
        if not id_match or not name_match:
            continue
        candidates.append(
            {
                "id": id_match.group(1),
                "name": html_lib.unescape(name_match.group(1)),
                "__typename": match.group("type"),
            }
        )

    return _dedupe_pages(candidates)


async def discover_pages_from_browser_html(
    session: Any,
) -> PageDiscoveryResult:
    diagnostics: list[str] = []
    urls = (
        "https://www.facebook.com/pages/?category=your_pages",
        "https://www.facebook.com/pages/?category=your_pages&ref=bookmarks",
        "https://www.facebook.com/pages/?category=your_pages&nav_ref=bookmarks",
    )

    for url in urls:
        try:
            status, document, final_url = await session.fetch_text(
                url,
                max_bytes=5_000_000,
            )
        except Exception as exc:
            diagnostics.append(f"{url}: fetch failed: {exc}")
            continue

        lowered_url = str(final_url or "").lower()
        if "login" in lowered_url or "checkpoint" in lowered_url:
            diagnostics.append(
                f"{url}: redirected to login/checkpoint"
            )
            continue

        if status >= 400:
            diagnostics.append(f"{url}: HTTP {status}")
            continue

        pages = _extract_pages_from_html(document)
        if pages:
            return PageDiscoveryResult(
                pages=pages,
                source="facebook_web_html",
                candidate=None,
                diagnostics=diagnostics,
            )

        diagnostics.append(
            f"{url}: authenticated HTML contained no recognized Page objects"
        )

    raise PageDiscoveryError(
        "Browser-session Page HTML discovery returned no Pages. "
        + " || ".join(diagnostics[-8:])
    )


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

    if discovered is None:
        return None

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
    "list_pages_via_private_graphql",
]
