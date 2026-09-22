from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from .facebook_docids import (
    DocIdCandidate,
    list_candidates,
    record_result,
    upsert_candidate,
)


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

    page_id = _clean(row.get("id"))
    name = _clean(row.get("name"))

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

    restriction = row.get("advertising_restriction_info")
    if isinstance(restriction, dict):
        output["advertising_restriction_info"] = {
            "is_restricted": restriction.get("is_restricted"),
            "restriction_type": _clean(restriction.get("restriction_type")),
        }

    return output


def _extract_known_page_lists(payload: dict[str, Any]) -> list[dict[str, Any]]:
    paths = (
        ("data", "userData", "pages_can_administer"),
        ("data", "user", "pages_can_administer"),
        ("data", "viewer", "pages_can_administer"),
        ("data", "pages_can_administer"),
    )

    def get_path(path: tuple[str, ...]) -> Any:
        node: Any = payload
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node

    output: list[dict[str, Any]] = []
    seen: set[str] = set()

    for path in paths:
        rows = get_path(path)
        if not isinstance(rows, list):
            continue

        for row in rows:
            page = _normalize_page(row)
            if not page:
                continue
            if page["id"] in seen:
                continue
            seen.add(page["id"])
            output.append(page)

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


def _script_urls(document: str, base_url: str) -> list[str]:
    normalized = html.unescape(document or "").replace("\\/", "/")
    found: list[str] = []

    patterns = (
        r'<script[^>]+src=["\']([^"\']+\.js[^"\']*)["\']',
        r'["\'](https://[^"\']+\.js[^"\']*)["\']',
    )

    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            raw = html.unescape(match.group(1)).replace("\\/", "/")
            url = urljoin(base_url, raw)
            if url in seen:
                continue
            if not (
                "facebook.com" in url
                or "fbcdn.net" in url
            ):
                continue
            seen.add(url)
            found.append(url)

    return found


def _doc_id_near_friendly_name(
    source: str,
    friendly_name: str,
) -> str:
    if not source or friendly_name not in source:
        return ""

    best: tuple[int, str] | None = None
    start = 0

    while True:
        index = source.find(friendly_name, start)
        if index < 0:
            break

        left = max(0, index - 2500)
        right = min(len(source), index + len(friendly_name) + 2500)
        window = source[left:right]

        patterns = (
            r'(?:"|\')?(?:doc_id|docID|id)(?:"|\')?\s*[:=]\s*(?:"|\')([0-9]{5,40})(?:"|\')',
            r'params\s*:\s*\{.{0,1200}?id\s*:\s*["\']([0-9]{5,40})["\']',
        )

        for pattern in patterns:
            for match in re.finditer(
                pattern,
                window,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                value = match.group(1)
                absolute = left + match.start(1)
                distance = abs(absolute - index)

                if best is None or distance < best[0]:
                    best = (distance, value)

        start = index + len(friendly_name)

    return best[1] if best else ""


async def discover_current_list_pages_docid(
    session: Any,
    *,
    max_scripts: int = 18,
) -> DocIdCandidate | None:
    friendly_name = "AccountQualityUserPagesWrapper_UserPageQuery"
    entry_url = "https://www.facebook.com/accountquality/?landing_page=insights"

    status, document, final_url = await session.fetch_text(
        entry_url,
        max_bytes=2_000_000,
    )

    if status >= 400:
        return None

    direct = _doc_id_near_friendly_name(
        document,
        friendly_name,
    )

    if direct:
        return upsert_candidate(
            "LIST_PAGES",
            doc_id=direct,
            friendly_name=friendly_name,
            endpoint_url="https://www.facebook.com/api/graphql/",
            variables_mode="account_quality_user_pages_v1",
            source="runtime_account_quality_html",
            priority=8_500,
            observed_at=str(int(time.time())),
        )

    for script_url in _script_urls(document, final_url)[:max(1, max_scripts)]:
        try:
            script_status, body, _ = await session.fetch_text(
                script_url,
                max_bytes=1_500_000,
                referer=final_url,
            )
        except Exception:
            continue

        if script_status >= 400 or friendly_name not in body:
            continue

        doc_id = _doc_id_near_friendly_name(
            body,
            friendly_name,
        )

        if not doc_id:
            continue

        return upsert_candidate(
            "LIST_PAGES",
            doc_id=doc_id,
            friendly_name=friendly_name,
            endpoint_url="https://www.facebook.com/api/graphql/",
            variables_mode="account_quality_user_pages_v1",
            source="runtime_account_quality_bundle",
            priority=8_500,
            observed_at=str(int(time.time())),
        )

    return None


async def discover_pages_via_web(
    session: Any,
    *,
    try_runtime_discovery: bool = True,
) -> PageDiscoveryResult:
    try:
        return await list_pages_via_private_graphql(session)
    except PageDiscoveryError as first_error:
        if not try_runtime_discovery:
            raise

        candidate = await discover_current_list_pages_docid(session)
        if candidate is None:
            raise PageDiscoveryError(
                f"{first_error}; current LIST_PAGES doc_id was not discoverable"
            ) from first_error

        return await list_pages_via_private_graphql(session)


__all__ = [
    "PageDiscoveryError",
    "PageDiscoveryResult",
    "discover_pages_via_web",
    "discover_current_list_pages_docid",
    "list_pages_via_private_graphql",
]
