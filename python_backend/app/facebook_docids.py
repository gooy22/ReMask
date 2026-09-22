from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DATA_ROOT = (
    os.getenv("REMASK_DATA_DIR")
    or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    or "/var/lib/remask"
)
STORE_PATH = Path(
    os.getenv(
        "REMASK_DOC_ID_STORE",
        os.path.join(DATA_ROOT, "facebook-web", "docids.json"),
    )
)

_STORE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class DocIdCandidate:
    operation: str
    doc_id: str
    friendly_name: str
    endpoint_url: str
    variables_mode: str
    source: str
    priority: int = 0
    observed_at: str = ""
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


STATIC_CANDIDATES: dict[str, list[DocIdCandidate]] = {
    "CREATE_BM": [
        DocIdCandidate(
            operation="CREATE_BM",
            doc_id="10024830640911292",
            friendly_name="useBusinessCreationMutationMutation",
            endpoint_url="https://business.facebook.com/api/graphql/",
            variables_mode="scope_selector_business_creation_v1",
            source="community_observed_business_creation",
            priority=200,
            observed_at="2025",
        ),
        DocIdCandidate(
            operation="CREATE_BM",
            doc_id="739201948201938",
            friendly_name="BusinessManagerCreateMutation",
            endpoint_url="https://business.facebook.com/api/graphql/",
            variables_mode="legacy_primary_page_v1",
            source="remask_legacy",
            priority=100,
            observed_at="legacy",
        ),
    ],
}


def _clean_operation(value: Any) -> str:
    return str(value or "").strip().upper()


def _clean_doc_id(value: Any) -> str:
    doc_id = str(value or "").strip()
    if not doc_id.isdigit() or not (5 <= len(doc_id) <= 40):
        raise ValueError(f"invalid doc_id: {doc_id!r}")
    return doc_id


def _clean_endpoint(value: Any) -> str:
    endpoint = str(
        value or "https://business.facebook.com/api/graphql/"
    ).strip()
    parsed = urlsplit(endpoint)
    allowed_hosts = {
        "www.facebook.com",
        "business.facebook.com",
        "adsmanager.facebook.com",
        "graph.facebook.com",
    }
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise ValueError(f"unsupported Facebook GraphQL endpoint: {endpoint!r}")
    if not parsed.path.startswith("/api/graphql") and parsed.hostname != "graph.facebook.com":
        raise ValueError(f"unsupported GraphQL path: {parsed.path!r}")
    return endpoint


def _default_store() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": int(time.time()),
        "candidates": {},
        "results": {},
    }


def _load_store_unlocked() -> dict[str, Any]:
    if not STORE_PATH.is_file():
        return _default_store()

    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return _default_store()

    if not isinstance(payload, dict):
        return _default_store()

    payload.setdefault("version", 1)
    payload.setdefault("updated_at", int(time.time()))
    payload.setdefault("candidates", {})
    payload.setdefault("results", {})
    return payload


def _save_store_unlocked(payload: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = int(time.time())
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    tmp = STORE_PATH.with_suffix(
        STORE_PATH.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}"
    )
    tmp.write_text(encoded, encoding="utf-8")
    os.replace(tmp, STORE_PATH)


def _candidate_from_mapping(
    operation: str,
    raw: dict[str, Any],
    *,
    default_source: str,
    default_priority: int,
) -> DocIdCandidate:
    return DocIdCandidate(
        operation=operation,
        doc_id=_clean_doc_id(raw.get("doc_id")),
        friendly_name=str(raw.get("friendly_name") or "").strip(),
        endpoint_url=_clean_endpoint(raw.get("endpoint_url")),
        variables_mode=str(
            raw.get("variables_mode")
            or "scope_selector_business_creation_v1"
        ).strip(),
        source=str(raw.get("source") or default_source).strip(),
        priority=int(raw.get("priority") or default_priority),
        observed_at=str(raw.get("observed_at") or "").strip(),
        enabled=bool(raw.get("enabled", True)),
    )


def _env_candidates(operation: str) -> list[DocIdCandidate]:
    if operation != "CREATE_BM":
        return []

    output: list[DocIdCandidate] = []

    primary = str(os.getenv("REMASK_DOC_ID_CREATE_BM") or "").strip()
    legacy_primary = str(os.getenv("REMASK_BM_DOC_ID") or "").strip()
    doc_id = primary or legacy_primary

    if doc_id:
        output.append(
            DocIdCandidate(
                operation=operation,
                doc_id=_clean_doc_id(doc_id),
                friendly_name=str(
                    os.getenv("REMASK_CREATE_BM_FRIENDLY_NAME")
                    or "useBusinessCreationMutationMutation"
                ).strip(),
                endpoint_url=str(
                    os.getenv("REMASK_CREATE_BM_GRAPHQL_URL")
                    or "https://business.facebook.com/api/graphql/"
                ).strip(),
                variables_mode=str(
                    os.getenv("REMASK_CREATE_BM_VARIABLES_MODE")
                    or "scope_selector_business_creation_v1"
                ).strip(),
                source="environment",
                priority=10_000,
                observed_at="runtime",
            )
        )

    raw_candidates = str(
        os.getenv("REMASK_DOC_ID_CREATE_BM_CANDIDATES_JSON") or ""
    ).strip()

    if raw_candidates:
        parsed = json.loads(raw_candidates)
        if not isinstance(parsed, list):
            raise ValueError(
                "REMASK_DOC_ID_CREATE_BM_CANDIDATES_JSON must be a JSON list"
            )

        for index, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            output.append(
                _candidate_from_mapping(
                    operation,
                    item,
                    default_source="environment_list",
                    default_priority=9_000 - index,
                )
            )

    return output


def list_candidates(operation: str) -> list[DocIdCandidate]:
    key = _clean_operation(operation)
    if not key:
        return []

    merged: list[DocIdCandidate] = []
    merged.extend(_env_candidates(key))

    with _STORE_LOCK:
        store = _load_store_unlocked()

    stored = store.get("candidates")
    if isinstance(stored, dict):
        rows = stored.get(key)
        if isinstance(rows, list):
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                try:
                    merged.append(
                        _candidate_from_mapping(
                            key,
                            row,
                            default_source="persisted",
                            default_priority=5_000 - index,
                        )
                    )
                except (TypeError, ValueError):
                    continue

    merged.extend(STATIC_CANDIDATES.get(key, []))

    # De-duplicate by doc_id + variables_mode + endpoint so the same persisted
    # query can still coexist with a different variable contract.
    deduped: dict[tuple[str, str, str], DocIdCandidate] = {}
    for candidate in merged:
        if not candidate.enabled:
            continue
        dedupe_key = (
            candidate.doc_id,
            candidate.variables_mode,
            candidate.endpoint_url,
        )
        current = deduped.get(dedupe_key)
        if current is None or candidate.priority > current.priority:
            deduped[dedupe_key] = candidate

    results = list(deduped.values())

    # Prefer candidates that previously succeeded, then explicit priority.
    with _STORE_LOCK:
        store = _load_store_unlocked()
        stats = store.get("results") if isinstance(store, dict) else {}

    def score(candidate: DocIdCandidate) -> tuple[int, int, int]:
        operation_stats = (
            stats.get(key, {})
            if isinstance(stats, dict)
            else {}
        )
        row = (
            operation_stats.get(candidate.doc_id, {})
            if isinstance(operation_stats, dict)
            else {}
        )
        success_count = int(row.get("success_count") or 0) if isinstance(row, dict) else 0
        failure_count = int(row.get("failure_count") or 0) if isinstance(row, dict) else 0
        return (
            1 if success_count > 0 else 0,
            success_count - failure_count,
            candidate.priority,
        )

    results.sort(key=score, reverse=True)
    return results


def upsert_candidate(
    operation: str,
    *,
    doc_id: str,
    friendly_name: str = "",
    endpoint_url: str = "https://business.facebook.com/api/graphql/",
    variables_mode: str = "scope_selector_business_creation_v1",
    source: str = "manual_capture",
    priority: int = 7_500,
    observed_at: str = "",
) -> DocIdCandidate:
    key = _clean_operation(operation)
    if not key:
        raise ValueError("operation is required")

    candidate = DocIdCandidate(
        operation=key,
        doc_id=_clean_doc_id(doc_id),
        friendly_name=str(friendly_name or "").strip(),
        endpoint_url=_clean_endpoint(endpoint_url),
        variables_mode=str(variables_mode or "").strip(),
        source=str(source or "manual_capture").strip(),
        priority=int(priority),
        observed_at=str(observed_at or "").strip(),
        enabled=True,
    )

    with _STORE_LOCK:
        store = _load_store_unlocked()
        candidates = store.setdefault("candidates", {})
        rows = candidates.setdefault(key, [])
        if not isinstance(rows, list):
            rows = []
            candidates[key] = rows

        replacement = candidate.as_dict()
        updated = False
        for index, row in enumerate(rows):
            if (
                isinstance(row, dict)
                and str(row.get("doc_id") or "") == candidate.doc_id
                and str(row.get("variables_mode") or "") == candidate.variables_mode
            ):
                rows[index] = replacement
                updated = True
                break

        if not updated:
            rows.insert(0, replacement)

        _save_store_unlocked(store)

    return candidate


def record_result(
    operation: str,
    candidate: DocIdCandidate,
    *,
    success: bool,
    reason: str = "",
    response_path: str = "",
) -> None:
    key = _clean_operation(operation)

    with _STORE_LOCK:
        store = _load_store_unlocked()
        results = store.setdefault("results", {})
        operation_stats = results.setdefault(key, {})
        row = operation_stats.setdefault(
            candidate.doc_id,
            {
                "success_count": 0,
                "failure_count": 0,
                "last_success_at": None,
                "last_failure_at": None,
                "last_reason": "",
                "last_response_path": "",
                "friendly_name": candidate.friendly_name,
                "variables_mode": candidate.variables_mode,
                "endpoint_url": candidate.endpoint_url,
                "source": candidate.source,
            },
        )

        now = int(time.time())
        if success:
            row["success_count"] = int(row.get("success_count") or 0) + 1
            row["last_success_at"] = now
        else:
            row["failure_count"] = int(row.get("failure_count") or 0) + 1
            row["last_failure_at"] = now

        row["last_reason"] = str(reason or "")[:2000]
        row["last_response_path"] = str(response_path or "")[:300]
        row["friendly_name"] = candidate.friendly_name
        row["variables_mode"] = candidate.variables_mode
        row["endpoint_url"] = candidate.endpoint_url
        row["source"] = candidate.source

        _save_store_unlocked(store)


def registry_view(operation: str | None = None) -> dict[str, Any]:
    operations = [_clean_operation(operation)] if operation else sorted(
        set(STATIC_CANDIDATES) | {"CREATE_BM"}
    )

    with _STORE_LOCK:
        store = _load_store_unlocked()

    output: dict[str, Any] = {
        "store_path": str(STORE_PATH),
        "updated_at": store.get("updated_at"),
        "operations": {},
    }

    stats = store.get("results") if isinstance(store, dict) else {}

    for key in operations:
        if not key:
            continue
        rows = []
        for candidate in list_candidates(key):
            stat = {}
            if isinstance(stats, dict):
                op_stats = stats.get(key)
                if isinstance(op_stats, dict):
                    item = op_stats.get(candidate.doc_id)
                    if isinstance(item, dict):
                        stat = item
            rows.append(
                {
                    **candidate.as_dict(),
                    "stats": stat,
                }
            )
        output["operations"][key] = rows

    return output
