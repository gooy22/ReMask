# python_backend/app/facebook_docids.py

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    import aiohttp
except ImportError:
    aiohttp = None


DATA_ROOT = (
    os.getenv("REMASK_DATA_DIR")
    or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    or "/var/lib/remask"
)

STORE_PATH = Path(
    os.getenv(
        "REMASK_DOC_ID_STORE",
        os.path.join(
            DATA_ROOT,
            "facebook-web",
            "docids.json",
        ),
    )
)

_STORE_LOCK = threading.Lock()


@dataclass(
    frozen=True,
    slots=True,
)
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

    def as_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


STATIC_CANDIDATES: dict[
    str,
    list[DocIdCandidate],
] = {
    "CREATE_BM": [],
    "SET_PRIMARY_PAGE": [],
    "LIST_PAGES": [],
}


def _now() -> int:
    return int(
        time.time()
    )


def _clean_operation(
    value: Any,
) -> str:
    return str(
        value
        or ""
    ).strip().upper()


def _clean_doc_id(
    value: Any,
) -> str:
    doc_id = str(
        value
        or ""
    ).strip()

    if not re_fullmatch_doc_id(
        doc_id
    ):
        raise ValueError(
            f"invalid doc_id: {doc_id!r}"
        )

    return doc_id


def re_fullmatch_doc_id(
    value: str,
) -> bool:
    return (
        value.isdigit()
        and 5
        <= len(
            value
        )
        <= 40
    )


def _clean_endpoint(
    value: Any,
) -> str:
    endpoint = str(
        value
        or (
            "https://business.facebook.com/api/graphql/"
        )
    ).strip()

    parsed = urlsplit(
        endpoint
    )

    allowed_hosts = {
        "www.facebook.com",
        "business.facebook.com",
        "adsmanager.facebook.com",
    }

    if (
        parsed.scheme
        != "https"
        or parsed.hostname
        not in allowed_hosts
    ):
        raise ValueError(
            f"unsupported Facebook GraphQL endpoint: {endpoint!r}"
        )

    if not parsed.path.startswith(
        "/api/graphql"
    ):
        raise ValueError(
            f"unsupported GraphQL path: {parsed.path!r}"
        )

    return endpoint


def _candidate_identity(
    candidate: DocIdCandidate,
) -> str:
    raw = "\x1f".join(
        (
            _clean_operation(
                candidate.operation
            ),
            str(
                candidate.doc_id
            ).strip(),
            str(
                candidate.friendly_name
            ).strip(),
            str(
                candidate.variables_mode
            ).strip(),
        )
    )

    digest = hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()

    return digest


def _default_store() -> dict[str, Any]:
    return {
        "version": 2,
        "updated_at": _now(),
        "candidates": {},
        "results": {},
    }


def _load_store_unlocked() -> dict[str, Any]:
    if not STORE_PATH.is_file():
        return _default_store()

    try:
        payload = json.loads(
            STORE_PATH.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ):
        return _default_store()

    if not isinstance(
        payload,
        dict,
    ):
        return _default_store()

    payload.setdefault(
        "version",
        2,
    )

    payload.setdefault(
        "updated_at",
        _now(),
    )

    payload.setdefault(
        "candidates",
        {},
    )

    payload.setdefault(
        "results",
        {},
    )

    return payload


def _save_store_unlocked(
    payload: dict[str, Any],
) -> None:
    STORE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload[
        "version"
    ] = 2

    payload[
        "updated_at"
    ] = _now()

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    tmp = STORE_PATH.with_suffix(
        STORE_PATH.suffix
        + (
            f".tmp.{os.getpid()}."
            f"{threading.get_ident()}"
        )
    )

    tmp.write_text(
        encoded,
        encoding="utf-8",
    )

    os.replace(
        tmp,
        STORE_PATH,
    )


def _candidate_from_mapping(
    operation: str,
    raw: dict[str, Any],
    *,
    default_source: str,
    default_priority: int,
) -> DocIdCandidate:
    return DocIdCandidate(
        operation=(
            _clean_operation(
                operation
            )
        ),
        doc_id=(
            _clean_doc_id(
                raw.get(
                    "doc_id"
                )
            )
        ),
        friendly_name=str(
            raw.get(
                "friendly_name"
            )
            or ""
        ).strip(),
        endpoint_url=(
            _clean_endpoint(
                raw.get(
                    "endpoint_url"
                )
            )
        ),
        variables_mode=str(
            raw.get(
                "variables_mode"
            )
            or (
                "scope_selector_business_creation_v1"
            )
        ).strip(),
        source=str(
            raw.get(
                "source"
            )
            or default_source
        ).strip(),
        priority=int(
            raw.get(
                "priority"
            )
            or default_priority
        ),
        observed_at=str(
            raw.get(
                "observed_at"
            )
            or ""
        ).strip(),
        enabled=bool(
            raw.get(
                "enabled",
                True,
            )
        ),
    )


def _env_candidates(
    operation: str,
) -> list[DocIdCandidate]:
    key = _clean_operation(
        operation
    )

    output: list[
        DocIdCandidate
    ] = []

    if key == "CREATE_BM":
        doc_id = str(
            os.getenv(
                "REMASK_DOC_ID_CREATE_BM"
            )
            or ""
        ).strip()

        if doc_id:
            output.append(
                DocIdCandidate(
                    operation=key,
                    doc_id=(
                        _clean_doc_id(
                            doc_id
                        )
                    ),
                    friendly_name=str(
                        os.getenv(
                            "REMASK_CREATE_BM_FRIENDLY_NAME"
                        )
                        or (
                            "useBusinessCreationMutationMutation"
                        )
                    ).strip(),
                    endpoint_url=(
                        _clean_endpoint(
                            os.getenv(
                                "REMASK_CREATE_BM_GRAPHQL_URL"
                            )
                        )
                    ),
                    variables_mode=str(
                        os.getenv(
                            "REMASK_CREATE_BM_VARIABLES_MODE"
                        )
                        or (
                            "scope_selector_business_creation_v1"
                        )
                    ).strip(),
                    source="environment",
                    priority=10_000,
                    observed_at="runtime",
                )
            )

        raw_candidates = str(
            os.getenv(
                "REMASK_DOC_ID_CREATE_BM_CANDIDATES_JSON"
            )
            or ""
        ).strip()

    elif key == "SET_PRIMARY_PAGE":
        doc_id = str(
            os.getenv(
                "REMASK_DOC_ID_SET_PRIMARY_PAGE"
            )
            or ""
        ).strip()

        if doc_id:
            output.append(
                DocIdCandidate(
                    operation=key,
                    doc_id=(
                        _clean_doc_id(
                            doc_id
                        )
                    ),
                    friendly_name=str(
                        os.getenv(
                            "REMASK_SET_PRIMARY_PAGE_FRIENDLY_NAME"
                        )
                        or (
                            "BizKitSettingsUpdateBusinessBasicInfoMutation"
                        )
                    ).strip(),
                    endpoint_url=(
                        _clean_endpoint(
                            os.getenv(
                                "REMASK_SET_PRIMARY_PAGE_GRAPHQL_URL"
                            )
                        )
                    ),
                    variables_mode=str(
                        os.getenv(
                            "REMASK_SET_PRIMARY_PAGE_VARIABLES_MODE"
                        )
                        or (
                            "bizkit_settings_update_business_basic_info_v1"
                        )
                    ).strip(),
                    source="environment",
                    priority=10_000,
                    observed_at="runtime",
                )
            )

        raw_candidates = str(
            os.getenv(
                "REMASK_DOC_ID_SET_PRIMARY_PAGE_CANDIDATES_JSON"
            )
            or ""
        ).strip()

    elif key == "LIST_PAGES":
        raw_candidates = ""

    else:
        return []

    if raw_candidates:
        parsed = json.loads(
            raw_candidates
        )

        if not isinstance(
            parsed,
            list,
        ):
            raise ValueError(
                (
                    f"{key} candidate environment "
                    "must contain a JSON list"
                )
            )

        for index, item in enumerate(
            parsed
        ):
            if not isinstance(
                item,
                dict,
            ):
                continue

            output.append(
                _candidate_from_mapping(
                    key,
                    item,
                    default_source=(
                        "environment_list"
                    ),
                    default_priority=(
                        9_000
                        - index
                    ),
                )
            )

    return output


def _stats_for_candidate(
    store: dict[str, Any],
    candidate: DocIdCandidate,
) -> dict[str, Any]:
    results = store.get(
        "results"
    )

    if not isinstance(
        results,
        dict,
    ):
        return {}

    operation_stats = results.get(
        _clean_operation(
            candidate.operation
        )
    )

    if not isinstance(
        operation_stats,
        dict,
    ):
        return {}

    identity = _candidate_identity(
        candidate
    )

    row = operation_stats.get(
        identity
    )

    if isinstance(
        row,
        dict,
    ):
        return row

    legacy = operation_stats.get(
        candidate.doc_id
    )

    if (
        isinstance(
            legacy,
            dict,
        )
        and str(
            legacy.get(
                "friendly_name"
            )
            or candidate.friendly_name
        ).strip()
        == candidate.friendly_name
        and str(
            legacy.get(
                "variables_mode"
            )
            or candidate.variables_mode
        ).strip()
        == candidate.variables_mode
    ):
        return legacy

    return {}


def list_candidates(
    operation: str,
    *,
    confirmed_only: bool = False,
) -> list[DocIdCandidate]:
    key = _clean_operation(
        operation
    )

    if not key:
        return []

    merged: list[
        DocIdCandidate
    ] = []

    merged.extend(
        _env_candidates(
            key
        )
    )

    with _STORE_LOCK:
        store = (
            _load_store_unlocked()
        )

    candidates_root = store.get(
        "candidates"
    )

    if isinstance(
        candidates_root,
        dict,
    ):
        rows = candidates_root.get(
            key
        )

        if isinstance(
            rows,
            list,
        ):
            for index, row in enumerate(
                rows
            ):
                if not isinstance(
                    row,
                    dict,
                ):
                    continue

                try:
                    merged.append(
                        _candidate_from_mapping(
                            key,
                            row,
                            default_source=(
                                "persisted"
                            ),
                            default_priority=(
                                5_000
                                - index
                            ),
                        )
                    )

                except (
                    TypeError,
                    ValueError,
                ):
                    continue

    merged.extend(
        STATIC_CANDIDATES.get(
            key,
            [],
        )
    )

    deduped: dict[
        tuple[
            str,
            str,
            str,
            str,
        ],
        DocIdCandidate,
    ] = {}

    for candidate in merged:
        if not candidate.enabled:
            continue

        dedupe_key = (
            candidate.doc_id,
            candidate.friendly_name,
            candidate.variables_mode,
            candidate.endpoint_url,
        )

        current = deduped.get(
            dedupe_key
        )

        if (
            current is None
            or candidate.priority
            > current.priority
        ):
            deduped[
                dedupe_key
            ] = candidate

    output: list[
        DocIdCandidate
    ] = []

    for candidate in deduped.values():
        stat = _stats_for_candidate(
            store,
            candidate,
        )

        if bool(
            stat.get(
                "disabled"
            )
        ):
            continue

        success_count = int(
            stat.get(
                "success_count"
            )
            or 0
        )

        if (
            confirmed_only
            and success_count <= 0
        ):
            continue

        output.append(
            candidate
        )

    def score(
        candidate: DocIdCandidate,
    ) -> tuple[
        int,
        int,
        int,
    ]:
        stat = _stats_for_candidate(
            store,
            candidate,
        )

        success_count = int(
            stat.get(
                "success_count"
            )
            or 0
        )

        stale_failure_count = int(
            stat.get(
                "stale_failure_count"
            )
            or 0
        )

        return (
            1
            if success_count > 0
            else 0,
            (
                success_count
                - stale_failure_count
            ),
            candidate.priority,
        )

    output.sort(
        key=score,
        reverse=True,
    )

    return output


def upsert_candidate(
    operation: str,
    *,
    doc_id: str,
    friendly_name: str = "",
    endpoint_url: str = (
        "https://business.facebook.com/api/graphql/"
    ),
    variables_mode: str = (
        "scope_selector_business_creation_v1"
    ),
    source: str = "manual_capture",
    priority: int = 7_500,
    observed_at: str = "",
) -> DocIdCandidate:
    key = _clean_operation(
        operation
    )

    if not key:
        raise ValueError(
            "operation is required"
        )

    candidate = DocIdCandidate(
        operation=key,
        doc_id=(
            _clean_doc_id(
                doc_id
            )
        ),
        friendly_name=str(
            friendly_name
            or ""
        ).strip(),
        endpoint_url=(
            _clean_endpoint(
                endpoint_url
            )
        ),
        variables_mode=str(
            variables_mode
            or ""
        ).strip(),
        source=str(
            source
            or "manual_capture"
        ).strip(),
        priority=int(
            priority
        ),
        observed_at=str(
            observed_at
            or ""
        ).strip(),
        enabled=True,
    )

    with _STORE_LOCK:
        store = (
            _load_store_unlocked()
        )

        root = store.setdefault(
            "candidates",
            {},
        )

        rows = root.setdefault(
            key,
            [],
        )

        if not isinstance(
            rows,
            list,
        ):
            rows = []
            root[
                key
            ] = rows

        replacement = (
            candidate.as_dict()
        )

        updated = False

        for index, row in enumerate(
            rows
        ):
            if not isinstance(
                row,
                dict,
            ):
                continue

            if (
                str(
                    row.get(
                        "doc_id"
                    )
                    or ""
                )
                == candidate.doc_id
                and str(
                    row.get(
                        "friendly_name"
                    )
                    or ""
                )
                == candidate.friendly_name
                and str(
                    row.get(
                        "variables_mode"
                    )
                    or ""
                )
                == candidate.variables_mode
                and str(
                    row.get(
                        "endpoint_url"
                    )
                    or ""
                )
                == candidate.endpoint_url
            ):
                rows[
                    index
                ] = replacement

                updated = True
                break

        if not updated:
            rows.insert(
                0,
                replacement,
            )

        _save_store_unlocked(
            store
        )

    return candidate


def _extract_meta_codes(
    payload: Any,
) -> set[int]:
    codes: set[
        int
    ] = set()

    def walk(
        value: Any,
    ) -> None:
        if isinstance(
            value,
            dict,
        ):
            for key in ("code", "error"):
                raw_code = value.get(key)

                if isinstance(raw_code, (dict, list)):
                    continue

                try:
                    if raw_code is not None:
                        codes.add(int(raw_code))
                except (TypeError, ValueError):
                    pass

            for child in value.values():
                walk(
                    child
                )

        elif isinstance(
            value,
            list,
        ):
            for child in value:
                walk(
                    child
                )

    walk(
        payload
    )

    return codes


def _payload_text(
    payload: Any,
    message: str = "",
) -> str:
    parts: list[
        str
    ] = []

    try:
        if payload:
            parts.append(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
    except Exception:
        parts.append(
            repr(
                payload
            )
        )

    if message:
        parts.append(
            str(
                message
            )
        )

    return " ".join(
        parts
    ).lower()


def classify_cache_failure(
    *,
    exception: Exception | None = None,
    payload: dict[str, Any] | None = None,
    message: str = "",
    http_status: int | None = None,
) -> str:
    body = (
        payload
        if isinstance(
            payload,
            dict,
        )
        else {}
    )

    text = _payload_text(
        body,
        message,
    )

    status = http_status

    if (
        status is None
        and exception is not None
    ):
        status = getattr(
            exception,
            "status",
            None,
        )

        if status is None:
            status = getattr(
                exception,
                "http_status",
                None,
            )

    try:
        status_int = (
            int(
                status
            )
            if status is not None
            else None
        )
    except (
        TypeError,
        ValueError,
    ):
        status_int = None

    network_markers = (
        "timeout",
        "timed out",
        "proxy",
        "dns",
        "name resolution",
        "connection reset",
        "connection refused",
        "network failure",
        "clientconnectorerror",
        "server disconnected",
        "cannot connect",
    )

    exception_is_network = (
        isinstance(
            exception,
            asyncio.TimeoutError,
        )
        or (
            aiohttp is not None
            and isinstance(
                exception,
                aiohttp.ClientError,
            )
        )
    )

    if (
        exception_is_network
        or status_int == 429
        or (
            status_int is not None
            and status_int >= 500
        )
        or any(
            marker in text
            for marker in network_markers
        )
    ):
        return "network"

    account_markers = (
        "checkpoint",
        "account restricted",
        "account_restricted",
        "restricted account",
        "temporarily blocked",
        "login required",
        "session expired",
        "authentication",
        "two-factor",
        "2fa",
        "challenge",
        "suspicious activity",
        "confirm your identity",
        "disabled account",
        "account disabled",
        "зрд",
    )

    if any(
        marker in text
        for marker in account_markers
    ):
        return "account"

    codes = _extract_meta_codes(
        body
    )

    stale_markers = (
        "persistedquerynotfound",
        "persisted query not found",
        "unknown field",
        "unknown argument",
        "unknown document",
        "cannot query field",
    )

    has_stale_marker = any(
        marker in text
        for marker in stale_markers
    )

    if (
        1357054 in codes
        and has_stale_marker
    ):
        return "stale_schema"

    return "other"


def record_result(
    operation: str,
    candidate: DocIdCandidate,
    *,
    success: bool,
    reason: str = "",
    response_path: str = "",
    profile_id: str = "",
    failure_kind: str = "",
    stale_failure: bool | None = None,
) -> None:
    key = _clean_operation(
        operation
    )

    if not key:
        return

    clean_profile = str(
        profile_id
        or ""
    ).strip()

    if (
        stale_failure is True
        and not failure_kind
    ):
        compatibility_text = str(reason or "").lower()
        compatibility_markers = (
            "persistedquerynotfound",
            "persisted query not found",
            "unknown field",
            "unknown argument",
            "unknown document",
            "cannot query field",
        )
        if (
            "1357054" in compatibility_text
            and any(
                marker in compatibility_text
                for marker in compatibility_markers
            )
        ):
            failure_kind = "stale_schema"
        else:
            failure_kind = "other"

    normalized_failure_kind = str(
        failure_kind
        or ""
    ).strip().lower()

    identity = _candidate_identity(
        candidate
    )

    with _STORE_LOCK:
        store = (
            _load_store_unlocked()
        )

        results = store.setdefault(
            "results",
            {},
        )

        operation_stats = (
            results.setdefault(
                key,
                {},
            )
        )

        row = operation_stats.get(
            identity
        )

        if not isinstance(
            row,
            dict,
        ):
            row = {
                "operation": key,
                "doc_id": (
                    candidate.doc_id
                ),
                "friendly_name": (
                    candidate.friendly_name
                ),
                "variables_mode": (
                    candidate.variables_mode
                ),
                "endpoint_url": (
                    candidate.endpoint_url
                ),
                "source": (
                    candidate.source
                ),
                "success_count": 0,
                "stale_failure_count": 0,
                "network_error_count": 0,
                "account_error_count": 0,
                "other_error_count": 0,
                "last_success_at": None,
                "last_failure_at": None,
                "last_reason": "",
                "last_response_path": "",
                "consecutive_stale_failures": 0,
                "stale_failure_profiles": [],
                "disabled": False,
                "disabled_reason": "",
            }

            operation_stats[
                identity
            ] = row

        now = _now()

        if success:
            row[
                "success_count"
            ] = (
                int(
                    row.get(
                        "success_count"
                    )
                    or 0
                )
                + 1
            )

            row[
                "last_success_at"
            ] = now

            row[
                "consecutive_stale_failures"
            ] = 0

            row[
                "stale_failure_profiles"
            ] = []

            row[
                "disabled"
            ] = False

            row[
                "disabled_reason"
            ] = ""

        else:
            row[
                "last_failure_at"
            ] = now

            if (
                normalized_failure_kind
                == "stale_schema"
            ):
                raw_profiles = row.get(
                    "stale_failure_profiles"
                )

                profiles = (
                    [
                        str(
                            value
                        ).strip()
                        for value in raw_profiles
                        if str(
                            value
                        ).strip()
                    ]
                    if isinstance(
                        raw_profiles,
                        list,
                    )
                    else []
                )

                if (
                    clean_profile
                    and clean_profile
                    not in profiles
                ):
                    profiles.append(
                        clean_profile
                    )

                    row[
                        "stale_failure_count"
                    ] = (
                        int(
                            row.get(
                                "stale_failure_count"
                            )
                            or 0
                        )
                        + 1
                    )

                row[
                    "stale_failure_profiles"
                ] = profiles

                unique_profiles = list(
                    dict.fromkeys(
                        profiles
                    )
                )

                row[
                    "consecutive_stale_failures"
                ] = len(
                    unique_profiles
                )

                if len(
                    unique_profiles
                ) >= 3:
                    row[
                        "disabled"
                    ] = True

                    row[
                        "disabled_reason"
                    ] = (
                        "3_cross_profile_stale_failures"
                    )

            elif (
                normalized_failure_kind
                == "network"
            ):
                row[
                    "network_error_count"
                ] = (
                    int(
                        row.get(
                            "network_error_count"
                        )
                        or 0
                    )
                    + 1
                )

            elif (
                normalized_failure_kind
                == "account"
            ):
                row[
                    "account_error_count"
                ] = (
                    int(
                        row.get(
                            "account_error_count"
                        )
                        or 0
                    )
                    + 1
                )

            else:
                row[
                    "other_error_count"
                ] = (
                    int(
                        row.get(
                            "other_error_count"
                        )
                        or 0
                    )
                    + 1
                )

        row[
            "last_reason"
        ] = str(
            reason
            or ""
        )[:4000]

        row[
            "last_response_path"
        ] = str(
            response_path
            or ""
        )[:300]

        row[
            "friendly_name"
        ] = candidate.friendly_name

        row[
            "variables_mode"
        ] = candidate.variables_mode

        row[
            "endpoint_url"
        ] = candidate.endpoint_url

        row[
            "source"
        ] = candidate.source

        _save_store_unlocked(
            store
        )


def registry_view(
    operation: str | None = None,
) -> dict[str, Any]:
    if operation:
        operations = [
            _clean_operation(
                operation
            )
        ]
    else:
        operations = sorted(
            {
                "CREATE_BM",
                "SET_PRIMARY_PAGE",
                "LIST_PAGES",
            }
            | set(
                STATIC_CANDIDATES
            )
        )

    with _STORE_LOCK:
        store = (
            _load_store_unlocked()
        )

    output: dict[str, Any] = {
        "store_path": str(
            STORE_PATH
        ),
        "updated_at": store.get(
            "updated_at"
        ),
        "operations": {},
    }

    for key in operations:
        if not key:
            continue

        rows: list[
            dict[str, Any]
        ] = []

        for candidate in list_candidates(
            key,
            confirmed_only=False,
        ):
            stat = _stats_for_candidate(
                store,
                candidate,
            )

            rows.append(
                {
                    **candidate.as_dict(),
                    "candidate_key": (
                        _candidate_identity(
                            candidate
                        )
                    ),
                    "stats": stat,
                }
            )

        output[
            "operations"
        ][
            key
        ] = rows

    return output


__all__ = [
    "DocIdCandidate",
    "STORE_PATH",
    "classify_cache_failure",
    "list_candidates",
    "record_result",
    "registry_view",
    "upsert_candidate",
]
