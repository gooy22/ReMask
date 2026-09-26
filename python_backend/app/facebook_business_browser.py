from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, unquote, unquote_plus, urlencode, urlsplit


CheckpointCallback = Callable[[dict[str, Any]], Awaitable[None]]


class BrowserBusinessError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        diagnostic: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "BROWSER_BUSINESS_ERROR").strip()
        self.retryable = bool(retryable)
        self.diagnostic = diagnostic or {}


@dataclass(slots=True)
class BrowserPreflightResult:
    ready: bool
    current_url: str = ""
    create_surface_ready: bool = False
    account_id: str = ""
    diagnostics: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BrowserCreateResult:
    business_id: str
    before_ids: list[str]
    after_ids: list[str]
    response_business_id: str = ""
    response_friendly_name: str = ""
    response_path: str = ""
    recovered: bool = False


@dataclass(slots=True)
class BrowserAdAccountResult:
    business_id: str
    ad_account_id: str
    response_friendly_name: str = ""
    response_doc_id: str = ""
    response_path: str = ""


@dataclass(slots=True)
class BrowserPageResult:
    business_id: str
    page_id: str
    already_attached: bool = False


# Meta Business Suite Chromium is memory-heavy. ReMask production currently
# runs in a ~1 GB container; two concurrent renderers can push the service to
# the memory ceiling and crash a page mid-wizard. Default to one browser lease
# at a time and allow larger hosts to opt in explicitly via the env override.
_BROWSER_LIMIT = max(1, int(os.getenv("REMASK_BM_BROWSER_CONCURRENCY") or "1"))
_BROWSER_SEMAPHORE = asyncio.Semaphore(_BROWSER_LIMIT)
_PROFILE_LOCKS: dict[str, asyncio.Lock] = {}
_PROFILE_LOCKS_GUARD = asyncio.Lock()


async def _get_profile_lock(profile_id: str) -> asyncio.Lock:
    key = _clean(profile_id) or "unknown"
    async with _PROFILE_LOCKS_GUARD:
        current = _PROFILE_LOCKS.get(key)
        if current is None:
            current = asyncio.Lock()
            _PROFILE_LOCKS[key] = current
        return current


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _digits(value: Any) -> str:
    text = _clean(value)
    return text if re.fullmatch(r"\d{5,30}", text) else ""


def _request_graphql_meta(request: Any) -> dict[str, Any]:
    """Parse Meta GraphQL request metadata without exposing auth fields."""
    method = ""
    url = ""
    raw = ""
    body_decodable = True

    try:
        method = _clean(getattr(request, "method", "")).upper()
        url = _clean(getattr(request, "url", ""))
        raw_buffer = getattr(request, "post_data_buffer", None)
        if raw_buffer:
            if isinstance(raw_buffer, bytes):
                raw = raw_buffer.decode("utf-8")
            else:
                raw = str(raw_buffer)
        else:
            raw = str(getattr(request, "post_data", "") or "")
    except (UnicodeDecodeError, UnicodeError):
        body_decodable = False
        try:
            raw = str(getattr(request, "post_data", "") or "")
        except Exception:
            raw = ""
    except Exception:
        body_decodable = False
        try:
            raw = str(getattr(request, "post_data", "") or "")
        except Exception:
            raw = ""

    parsed = parse_qs(raw, keep_blank_values=True) if raw else {}
    try:
        url_query = parse_qs(
            urlsplit(url).query,
            keep_blank_values=True,
        )
    except Exception:
        url_query = {}

    # Meta may encode Relay GraphQL metadata in the URL query even when the
    # browser-level request method is GET (for example graph.facebook.com/graphql
    # with method=post). Merge query params without overwriting an explicit
    # request-body value.
    for key, values in url_query.items():
        if key not in parsed and isinstance(values, list):
            parsed[key] = values

    effective_method = method
    query_method = _clean((parsed.get("method") or [""])[0]).upper()
    if method == "GET" and query_method == "POST":
        effective_method = "POST"

    friendly = _clean(
        (parsed.get("fb_api_req_friendly_name") or [""])[0]
    )
    if not friendly:
        try:
            headers = getattr(request, "headers", {}) or {}
            friendly = _clean(
                headers.get("x-fb-friendly-name")
                or headers.get("X-FB-Friendly-Name")
            )
        except Exception:
            friendly = ""
    doc_id = _clean((parsed.get("doc_id") or [""])[0])

    variables: dict[str, Any] = {}
    variables_raw = _clean((parsed.get("variables") or [""])[0])
    if variables_raw:
        try:
            decoded_variables = json.loads(variables_raw)
            if isinstance(decoded_variables, dict):
                variables = decoded_variables
        except (ValueError, json.JSONDecodeError):
            variables = {}

    raw_input = variables.get("input")
    input_data = raw_input if isinstance(raw_input, dict) else {}

    return {
        "method": effective_method,
        "browser_method": method,
        "url": url,
        "friendly_name": friendly,
        "doc_id": doc_id,
        "variables": variables,
        "input": input_data,
        "decoded_raw": unquote_plus(raw) if raw else "",
        "body_decodable": body_decodable,
    }


def _ad_account_required_attribution_post_data(
    request: Any,
    *,
    end_advertiser: str = "NONE",
    media_agency: str = "NONE",
    partner: str = "NONE",
    currency: str = "",
    timezone_id: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Fill Meta-required attribution fields on its own Add-RK GraphQL request.

    Meta's Business /adaccount contract requires end_advertiser,
    media_agency and partner even when no external entity is involved.  The
    Business Settings frontend can currently emit the private CREATE mutation
    without those optional-looking UI choices.  Preserve Meta's live request
    verbatim and only add missing required values to variables.input.
    """
    try:
        raw_buffer = getattr(request, "post_data_buffer", None)
        if raw_buffer:
            raw = (
                raw_buffer.decode("utf-8")
                if isinstance(raw_buffer, bytes)
                else str(raw_buffer)
            )
        else:
            raw = str(getattr(request, "post_data", "") or "")
    except Exception:
        return "", {}

    if not raw:
        return "", {}

    parsed = parse_qs(raw, keep_blank_values=True)
    variables_raw = _clean((parsed.get("variables") or [""])[0])
    if not variables_raw:
        return raw, {}

    try:
        variables = json.loads(variables_raw)
    except (ValueError, json.JSONDecodeError):
        return raw, {}
    if not isinstance(variables, dict):
        return raw, {}

    input_data = variables.get("input")
    if not isinstance(input_data, dict):
        # New Relay variants may wrap the create payload under names such as
        # adAccountData while keeping businessID at a sibling/top level.
        # Select exactly one strong creation-shaped dict: currency + timezone,
        # with no existing account ID. Never patch an ambiguous payload.
        candidates: list[dict[str, Any]] = []

        def walk_dicts(value: Any) -> None:
            if isinstance(value, dict):
                keys = {str(key) for key in value.keys()}
                has_currency = "currency" in keys
                has_timezone = bool(
                    {
                        "timezone_id",
                        "time_zone_id",
                        "timezone",
                        "time_zone",
                        "timezoneId",
                        "timeZoneId",
                    }.intersection(keys)
                )
                has_existing = any(
                    _clean(value.get(key))
                    for key in (
                        "account_id",
                        "ad_account_id",
                        "adAccountId",
                        "adaccount_id",
                    )
                    if key in value
                )
                if has_currency and has_timezone and not has_existing:
                    candidates.append(value)
                for child in value.values():
                    walk_dicts(child)
            elif isinstance(value, list):
                for child in value:
                    walk_dicts(child)

        walk_dicts(variables)
        unique_candidates: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for candidate in candidates:
            ident = id(candidate)
            if ident in seen_ids:
                continue
            seen_ids.add(ident)
            unique_candidates.append(candidate)

        if len(unique_candidates) != 1:
            return raw, {}
        input_data = unique_candidates[0]

    defaults = {
        "end_advertiser": _clean(end_advertiser) or "NONE",
        "media_agency": _clean(media_agency) or "NONE",
        "partner": _clean(partner) or "NONE",
    }
    applied: dict[str, Any] = {}
    for key, value in defaults.items():
        current = input_data.get(key)
        if current is None or (isinstance(current, str) and not current.strip()):
            input_data[key] = value
            applied[key] = value

    # Keep Meta's own live mutation shape/doc_id, but make the user-selected
    # immutable account settings effective when those keys are present in the
    # request. We deliberately do not invent new schema fields.
    requested_currency = _clean(currency).upper()
    if requested_currency and "currency" in input_data:
        if _clean(input_data.get("currency")).upper() != requested_currency:
            input_data["currency"] = requested_currency
            applied["currency"] = requested_currency

    timezone_key = next(
        (
            key
            for key in (
                "timezone_id",
                "time_zone_id",
                "timezoneId",
                "timeZoneId",
            )
            if key in input_data
        ),
        "",
    )
    if timezone_id is not None and timezone_key:
        try:
            requested_timezone = int(timezone_id)
        except (TypeError, ValueError):
            requested_timezone = None
        if (
            requested_timezone is not None
            and input_data.get(timezone_key) != requested_timezone
        ):
            input_data[timezone_key] = requested_timezone
            applied[timezone_key] = requested_timezone

    if not applied:
        return raw, {}

    parsed["variables"] = [
        json.dumps(variables, ensure_ascii=False, separators=(",", ":"))
    ]
    return urlencode(parsed, doseq=True), applied


def _proxy_config(raw_proxy: str | None) -> dict[str, str] | None:
    raw = _clean(raw_proxy)
    if not raw:
        return None

    proxy_url = raw if "://" in raw else f"http://{raw}"
    parts = urlsplit(proxy_url)
    if not parts.hostname or not parts.port:
        raise BrowserBusinessError(
            "PROXY_INVALID",
            "Facebook profile proxy is invalid.",
            retryable=False,
        )

    result = {
        "server": f"{parts.scheme or 'http'}://{parts.hostname}:{parts.port}"
    }
    if parts.username:
        result["username"] = unquote(parts.username)
    if parts.password:
        result["password"] = unquote(parts.password)
    return result


def _decode_graphql_text(raw: str) -> Any:
    body = _clean(raw)
    if body.startswith("for (;;);"):
        body = body[len("for (;;);"):].lstrip()
    if not body:
        return None

    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        pass

    # Facebook/Relay may stream several JSON payloads as newline-delimited
    # chunks. Keep all successfully decoded chunks: downstream ID/error
    # walkers already recurse through lists.
    chunks: list[Any] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("for (;;);"):
            line = line[len("for (;;);"):].lstrip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue

    if len(chunks) == 1:
        return chunks[0]
    if chunks:
        return chunks
    return None


def _graphql_error_details(payload: Any) -> list[dict[str, Any]]:
    """Extract compact, non-secret GraphQL error details from Meta responses."""
    output: list[dict[str, Any]] = []

    def add_error(value: Any) -> None:
        if not isinstance(value, dict):
            return

        message = _clean(
            value.get("message")
            or value.get("summary")
            or value.get("errorSummary")
            or value.get("error_summary")
            or value.get("errorDescription")
            or value.get("error_description")
            or value.get("description")
            or value.get("description_raw")
            or value.get("error_user_msg")
            or value.get("error_user_title")
        )

        extensions = value.get("extensions")
        if not isinstance(extensions, dict):
            extensions = {}

        code = _clean(
            value.get("code")
            or value.get("error")
            or extensions.get("code")
            or extensions.get("error_code")
        )
        subcode = _clean(
            value.get("error_subcode")
            or value.get("subcode")
            or extensions.get("error_subcode")
        )
        error_type = _clean(
            value.get("type")
            or extensions.get("type")
            or extensions.get("classification")
        )

        if not message and not code and not subcode:
            return

        row = {
            "message": message[:1000],
            "code": code[:120],
            "subcode": subcode[:120],
            "type": error_type[:120],
        }
        if row not in output:
            output.append(row)

    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list):
            for item in errors[:20]:
                add_error(item)

        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            add_error(raw_error)
        elif raw_error is not None:
            add_error(
                {
                    "error": raw_error,
                    "message": (
                        payload.get("errorDescription")
                        or payload.get("error_summary")
                        or payload.get("errorSummary")
                    ),
                }
            )

        if any(
            key in payload
            for key in (
                "errorDescription",
                "error_description",
                "errorSummary",
                "error_summary",
                "error_user_msg",
            )
        ):
            add_error(payload)

    elif isinstance(payload, list):
        for item in payload[:20]:
            for row in _graphql_error_details(item):
                if row not in output:
                    output.append(row)

    return output[:10]


def _meta_error_retryable(errors: list[dict[str, Any]]) -> bool:
    text = " ".join(
        " ".join(
            _clean(row.get(key))
            for key in ("message", "code", "subcode", "type")
        )
        for row in errors
        if isinstance(row, dict)
    ).casefold()

    non_retryable = (
        "permission",
        "not allowed",
        "not eligible",
        "restricted",
        "restriction",
        "checkpoint",
        "confirm your",
        "verify your",
        "business limit",
        "maximum",
        "too many business",
        "temporarily blocked",
        "misusing this feature",
    )
    if any(marker in text for marker in non_retryable):
        return False

    retryable = (
        "rate limit",
        "try again",
        "temporarily unavailable",
        "server error",
        "timeout",
        "timed out",
        "please retry",
    )
    return any(marker in text for marker in retryable)


def _extract_created_business_id(payload: Any) -> tuple[str, str]:
    """Extract only IDs from known Meta Business CREATE response shapes."""
    known_paths = (
        ("data", "business_create", "business", "id"),
        ("data", "business_create", "id"),
        ("data", "bizkit_create_business", "business", "id"),
        ("data", "bizkit_create_business", "id"),
        ("data", "business_manager_create", "business", "id"),
        ("data", "business_manager_create", "id"),
    )

    chunks = payload if isinstance(payload, list) else [payload]
    found: list[tuple[str, str]] = []

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        for path in known_paths:
            current: Any = chunk
            valid = True
            for key in path:
                if not isinstance(current, dict) or key not in current:
                    valid = False
                    break
                current = current[key]
            if not valid:
                continue
            candidate = _digits(current)
            if candidate:
                row = (candidate, ".".join(path))
                if row not in found:
                    found.append(row)

    unique_ids = sorted({business_id for business_id, _ in found})
    if len(unique_ids) != 1:
        return "", ""

    business_id = unique_ids[0]
    response_path = next(
        path
        for candidate, path in found
        if candidate == business_id
    )
    return business_id, response_path


def _normalize_ad_account_id(value: Any) -> str:
    raw = _clean(value)
    if raw.lower().startswith("act_"):
        raw = raw[4:]
    if not raw.isdigit() or not (5 <= len(raw) <= 30):
        return ""
    return "act_" + raw


def _extract_created_ad_account_id(payload: Any) -> tuple[str, str]:
    """Extract exactly one Ad Account ID from a matched CREATE response.

    Known response nodes are preferred. A conservative recursive fallback
    tolerates Relay node renames but only accepts account_id/ad_account_id
    fields or an id whose immediate parent explicitly names an ad account.
    """
    known_nodes = (
        "ad_account_create",
        "business_ad_account_create",
        "bizkit_create_ad_account",
        "create_ad_account",
        "adaccount_create",
    )
    chunks = payload if isinstance(payload, list) else [payload]
    matches: list[tuple[str, str]] = []

    def add(candidate: Any, path: str) -> None:
        account_id = _normalize_ad_account_id(candidate)
        if not account_id:
            return
        row = (account_id, path)
        if row not in matches:
            matches.append(row)

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        data = chunk.get("data")
        if not isinstance(data, dict):
            continue

        for node_name in known_nodes:
            node = data.get(node_name)
            if not isinstance(node, dict):
                continue
            add(
                node.get("id") or node.get("account_id"),
                f"data.{node_name}.id",
            )
            for child_name in ("ad_account", "account"):
                child = node.get(child_name)
                if not isinstance(child, dict):
                    continue
                add(
                    child.get("id") or child.get("account_id"),
                    f"data.{node_name}.{child_name}.id",
                )

        def walk(value: Any, path: str = "data") -> None:
            if isinstance(value, dict):
                parent_key = path.rsplit(".", 1)[-1].casefold()
                for key, child in value.items():
                    key_text = str(key or "")
                    key_folded = key_text.casefold()
                    child_path = f"{path}.{key_text}"

                    if key_folded in {"account_id", "ad_account_id"}:
                        add(child, child_path)
                    elif (
                        key_folded == "id"
                        and (
                            "ad_account" in parent_key
                            or "adaccount" in parent_key
                            or "advertising_account" in parent_key
                        )
                    ):
                        add(child, child_path)

                    walk(child, child_path)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{path}[{index}]")

        walk(data)

    unique = sorted({account_id for account_id, _ in matches})
    if len(unique) != 1:
        return "", ""

    account_id = unique[0]
    response_path = next(
        path
        for candidate, path in matches
        if candidate == account_id
    )
    return account_id, response_path

def _walk_business_ids(value: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    if isinstance(value, dict):
        for key, child in value.items():
            clean_key = str(key or "").lower()
            child_path = f"{path}.{clean_key}" if path else clean_key

            if clean_key in {"business_id", "businessid"}:
                candidate = _digits(child)
                if candidate:
                    found.append((candidate, child_path))

            if clean_key == "id":
                candidate = _digits(child)
                if candidate and any(
                    marker in path.lower()
                    for marker in (
                        "business",
                        "bizkit_create",
                        "business_creation",
                        "create_business",
                    )
                ):
                    found.append((candidate, child_path))

            found.extend(_walk_business_ids(child, child_path))

    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_business_ids(child, f"{path}[{index}]"))

    return found


def _business_ids_from_text(text: str) -> set[str]:
    raw = str(text or "")
    output: set[str] = set()

    patterns = (
        r"[?&]business_id=(\d{5,30})",
        r"[?&]businessId=(\d{5,30})",
        r"\/businesses\/(\d{5,30})(?:[\/?#\"']|$)",
        r"[\"']business_id[\"']\s*[:=]\s*[\"']?(\d{5,30})",
        r"[\"']businessId[\"']\s*[:=]\s*[\"']?(\d{5,30})",
        r"[\"']selectedBusinessID[\"']\s*[:=]\s*[\"']?(\d{5,30})",
    )
    for pattern in patterns:
        output.update(re.findall(pattern, raw, flags=re.IGNORECASE))

    return {value for value in output if _digits(value)}



def _extract_named_ad_account_ids(
    payload: Any,
    account_name: str,
) -> list[str]:
    """Extract numeric RK ids only from nodes matching the exact account name."""
    expected = _clean(account_name).casefold()
    if not expected:
        return []

    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            node_name = _clean(
                value.get("name")
                or value.get("account_name")
                or value.get("ad_account_name")
                or value.get("adAccountName")
            ).casefold()

            if node_name == expected:
                for key in (
                    "id",
                    "account_id",
                    "ad_account_id",
                    "accountId",
                    "adAccountId",
                ):
                    normalized = _normalize_ad_account_id(value.get(key))
                    if normalized:
                        found.add(normalized)

                for child_key in (
                    "ad_account",
                    "adAccount",
                    "advertising_account",
                    "account",
                ):
                    child = value.get(child_key)
                    if isinstance(child, dict):
                        for key in (
                            "id",
                            "account_id",
                            "ad_account_id",
                            "accountId",
                            "adAccountId",
                        ):
                            normalized = _normalize_ad_account_id(
                                child.get(key)
                            )
                            if normalized:
                                found.add(normalized)

            for child in value.values():
                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return sorted(found)


def _extract_inventory_ad_account_ids(payload: Any) -> list[str]:
    """Extract RK ids from structurally identified ad-account inventory nodes.

    Generic numeric id values are ignored unless their parent/path clearly
    belongs to an ad-account collection/node. This keeps Business/Page IDs out
    of reconciliation while allowing Relay inventory shapes where the account
    name is absent or localized differently.
    """
    found: set[str] = set()

    ad_markers = (
        "ad_account",
        "adaccount",
        "ad_accounts",
        "adaccounts",
        "advertising_account",
        "advertisingaccount",
    )

    def path_is_ad_account(path: str, value: dict[str, Any]) -> bool:
        folded = path.casefold()
        typename = _clean(value.get("__typename")).casefold()
        return any(
            marker in folded or marker in typename
            for marker in ad_markers
        )

    def add(value: Any) -> None:
        normalized = _normalize_ad_account_id(value)
        if normalized:
            found.add(normalized)

    def walk(value: Any, path: str = "root") -> None:
        if isinstance(value, dict):
            ad_context = path_is_ad_account(path, value)

            for key in (
                "ad_account_id",
                "adAccountId",
                "adaccount_id",
            ):
                if key in value:
                    add(value.get(key))

            if ad_context:
                for key in (
                    "id",
                    "account_id",
                    "accountId",
                ):
                    if key in value:
                        add(value.get(key))

            for key, child in value.items():
                child_path = f"{path}.{key}"
                if (
                    isinstance(child, dict)
                    and str(key).casefold() in {
                        "ad_account",
                        "adaccount",
                        "advertising_account",
                    }
                ):
                    add(
                        child.get("id")
                        or child.get("account_id")
                        or child.get("ad_account_id")
                        or child.get("adAccountId")
                    )
                walk(child, child_path)

        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(payload)
    return sorted(found)


def _has_ad_account_inventory_container(payload: Any) -> bool:
    """Return True only when a payload exposes an RK inventory collection.

    This is used to distinguish an authoritative empty Ad Accounts inventory
    from an unrelated GraphQL response that simply happens to contain no RK id.
    """
    collection_keys = (
        "ad_accounts",
        "adaccounts",
        "advertising_accounts",
        "advertisingaccounts",
        "owned_ad_accounts",
        "ownedadaccounts",
        "client_ad_accounts",
        "clientadaccounts",
    )

    def walk(value: Any) -> bool:
        if isinstance(value, dict):
            typename = _clean(value.get("__typename")).casefold()
            compact_type = (
                typename.replace("_", "").replace("-", "").replace(" ", "")
            )
            if "adaccountconnection" in compact_type:
                return True

            for key, child in value.items():
                folded = str(key).casefold()
                compact = (
                    folded.replace("_", "").replace("-", "").replace(" ", "")
                )
                plural_key = any(
                    marker.replace("_", "") in compact
                    for marker in collection_keys
                )
                connection_shape = (
                    isinstance(child, dict)
                    and any(
                        field in child
                        for field in (
                            "edges",
                            "nodes",
                            "items",
                            "count",
                            "total_count",
                            "totalCount",
                            "page_info",
                            "pageInfo",
                        )
                    )
                    and (
                        "adaccount" in compact
                        or "advertisingaccount" in compact
                    )
                )
                if (
                    isinstance(child, (dict, list))
                    and (plural_key or connection_shape)
                ):
                    return True
                if walk(child):
                    return True

        elif isinstance(value, list):
            for child in value:
                if walk(child):
                    return True

        return False

    return walk(payload)


class FacebookBusinessBrowser:
    """
    Browser-first Meta Business workflow.

    This class deliberately does NOT reconstruct or submit private GraphQL
    mutations. It drives Meta's own UI and observes the requests/responses
    generated by Meta's frontend.
    """

    ROOT_URL = "https://business.facebook.com/"
    HOME_URL = "https://business.facebook.com/latest/home"
    OVERVIEW_URL = "https://business.facebook.com/overview"
    CREATE_URL = "https://business.facebook.com/reg/"
    DIRECT_CREATE_URL = "https://business.facebook.com/create"
    ADS_MANAGER_URL = "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
    SETTINGS_PAGES_URL = (
        "https://business.facebook.com/settings/pages/?business_id={business_id}"
    )
    FAN_PAGE_CREATE_URLS = (
        "https://www.facebook.com/pages/create",
        "https://www.facebook.com/pages/creation/",
    )
    FAN_PAGE_CREATE_NAMES = (
        "Create Page",
        "Create page",
        "Создать Страницу",
        "Создать страницу",
        "Створити сторінку",
        "Créer une Page",
        "Créer la Page",
        "Seite erstellen",
        "পৃষ্ঠা তৈরি করুন",
        "Tạo Trang",
        "Tạo trang",
        "पेज बनाएँ",
        "पेज बनाएं",
    )
    SETTINGS_AD_ACCOUNTS_URLS = (
        # Exact migrated Business Settings route observed on current profiles.
        "https://business.facebook.com/latest/settings/ad_accounts/?nav_ref=bm_settings_redirect_migration&bm_redirect_migration=true&business_id={business_id}",
        "https://business.facebook.com/latest/settings/ad_accounts?business_id={business_id}",
        "https://business.facebook.com/latest/settings/ad_accounts/?business_id={business_id}",
        # Legacy settings route remains only as a final fallback.
        "https://business.facebook.com/settings/ad-accounts/?business_id={business_id}",
    )
    AD_ACCOUNT_SECTION_NAMES = (
        "Ad accounts",
        "Advertising accounts",
        "Рекламные аккаунты",
        "Рекламні акаунти",
        "Werbekonten",
        "Comptes publicitaires",
        "বিজ্ঞাপন অ্যাকাউন্ট",
        "বিজ্ঞাপন অ্যাকাউন্টসমূহ",
        "Tài khoản quảng cáo",
        "विज्ञापन खाते",
        "विज्ञापन खाता",
    )
    AD_ACCOUNT_CREATE_ENTRY_NAMES = (
        "Create a new ad account",
        "Create new ad account",
        "Create ad account",
        "Add a new ad account",
        "Создать новый рекламный аккаунт",
        "Создать рекламный аккаунт",
        "Створити новий рекламний акаунт",
        "Створити рекламний акаунт",
        "Neues Werbekonto erstellen",
        "Werbekonto erstellen",
        "Créer un nouveau compte publicitaire",
        "Créer un compte publicitaire",
        "Nouveau compte publicitaire",
        "নতুন বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন",
        "বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন",
        "Tạo tài khoản quảng cáo mới",
        "Tạo tài khoản quảng cáo",
        "नया विज्ञापन खाता बनाएँ",
        "नया विज्ञापन खाता बनाएं",
        "विज्ञापन खाता बनाएँ",
        "विज्ञापन खाता बनाएं",
    )
    AD_ACCOUNT_SUBMIT_NAMES = (
        "Create ad account",
        "Create",
        "Next",
        "Continue",
        "Создать рекламный аккаунт",
        "Создать",
        "Далее",
        "Продолжить",
        "Створити рекламний акаунт",
        "Створити",
        "Далі",
        "Продовжити",
        "Werbekonto erstellen",
        "Erstellen",
        "Weiter",
        "Créer un compte publicitaire",
        "Créer",
        "Suivant",
        "Continuer",
        "বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন",
        "তৈরি করুন",
        "পরবর্তী",
        "চালিয়ে যান",
        "Tạo tài khoản quảng cáo",
        "Tạo",
        "Tiếp",
        "Tiếp tục",
        "विज्ञापन खाता बनाएँ",
        "विज्ञापन खाता बनाएं",
        "बनाएँ",
        "बनाएं",
        "अगला",
        "आगे",
        "जारी रखें",
    )

    CREATE_NAMES = (
        "Create a business portfolio",
        "Create business portfolio",
        "Create a business",
        "Create business",
        "Create a portfolio",
        "Create portfolio",
        "Create account",
        "Создать бизнес-портфолио",
        "Создать портфолио",
        "Создать бизнес",
        "Создать аккаунт",
        "Створити бізнес-портфоліо",
        "Створити портфоліо",
        "Створити бізнес",
        "Створити обліковий запис",
        "Business-Portfolio erstellen",
        "Unternehmensportfolio erstellen",
        "Business erstellen",
        "Portfolio erstellen",
        "Créer un portefeuille business",
        "Créer un portefeuille professionnel",
        "Créer un portefeuille",
        "Créer une entreprise",
        "Créer un compte",
    )

    SUBMIT_NAMES = (
        "Create",
        "Submit",
        "Continue",
        "Создать",
        "Продолжить",
        "Створити",
        "Продовжити",
        "Erstellen",
        "Senden",
        "Weiter",
        "Créer",
        "Continuer",
        "Envoyer",
        "Valider",
    )

    ADD_NAMES = (
        "Add",
        "Add ad account",
        "Add an ad account",
        "Добавить",
        "Додати",
        "Hinzufügen",
        "Ajouter",
        "Ajouter un compte publicitaire",
        "Ajouter des comptes publicitaires",
        "যোগ করুন",
        "Thêm",
        "जोड़ें",
    )

    ADD_EXISTING_PAGE_NAMES = (
        "Add an existing Facebook Page",
        "Add a Page",
        "Add existing Page",
        "Добавить существующую Страницу Facebook",
        "Добавить Страницу",
        "Додати наявну сторінку Facebook",
        "Додати сторінку",
        "Bestehende Facebook-Seite hinzufügen",
        "Vorhandene Facebook-Seite hinzufügen",
        "Facebook-Seite hinzufügen",
        "Seite hinzufügen",
    )

    def __init__(self, context: Any, *, timeout_seconds: int = 45) -> None:
        self.context = context
        self.timeout_seconds = max(15, int(timeout_seconds))
        self.timeout_ms = self.timeout_seconds * 1000

        self._playwright = None
        self._browser = None
        self._browser_context = None
        self.page = None
        self._semaphore_acquired = False
        self._profile_lock: asyncio.Lock | None = None
        self._profile_lock_acquired = False
        self._last_selector_diagnostic: dict[str, Any] = {}
        self._last_ad_account_section_diagnostic: dict[str, Any] = {}
        self._browser_events: list[dict[str, Any]] = []
        self._ad_account_runtime_phase = "IDLE"
        self._ad_account_phase_started_at = time.monotonic()
        self._ad_account_create_sent = False
        self._ad_account_final_capture_armed = False
        self._ad_account_ui_trace: list[dict[str, Any]] = []
        self._ad_account_wizard_rect: dict[str, float] = {}
        self._lease_watchdog_task: asyncio.Task[Any] | None = None
        self._browser_slot_acquired_at = 0.0

    async def __aenter__(self) -> "FacebookBusinessBrowser":
        await self.open()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    @property
    def profile_id(self) -> str:
        return _clean(getattr(self.context, "profile_id", ""))

    async def open(self) -> None:
        if self.page is not None:
            return

        await _BROWSER_SEMAPHORE.acquire()
        self._semaphore_acquired = True
        self._browser_slot_acquired_at = time.monotonic()

        # Once a Chromium slot is acquired, it must never be held forever.
        # Queue wait is intentionally unbounded for large bulk waves, but the
        # active lease is bounded because all Meta/browser operations already
        # have much shorter step-level timeouts.
        lease_raw = _clean(
            os.getenv("REMASK_BROWSER_ACTIVE_LEASE_SECONDS") or "300"
        )
        try:
            lease_seconds = float(lease_raw)
        except (TypeError, ValueError):
            lease_seconds = 300.0
        lease_seconds = max(120.0, min(lease_seconds, 900.0))

        async def expire_browser_lease() -> None:
            try:
                await asyncio.sleep(lease_seconds)
                if self._semaphore_acquired:
                    await self.close()
            except asyncio.CancelledError:
                raise
            except BaseException:
                # Lease cleanup is best effort; close() itself is bounded and
                # the outer provisioning watchdog will still fail the task.
                pass

        self._lease_watchdog_task = asyncio.create_task(
            expire_browser_lease(),
            name=f"remask-browser-lease-{self.profile_id or 'unknown'}",
        )

        self._profile_lock = await _get_profile_lock(self.profile_id)
        try:
            await asyncio.wait_for(
                self._profile_lock.acquire(),
                timeout=30.0,
            )
        except asyncio.TimeoutError as exc:
            await self.close()
            raise BrowserBusinessError(
                "PROFILE_BROWSER_LOCK_TIMEOUT",
                (
                    "Profile browser lock did not become available within 30s. "
                    "A previous browser task may be stuck; no Meta action was sent."
                ),
                retryable=True,
            ) from exc
        self._profile_lock_acquired = True

        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            self._release_semaphore()
            raise BrowserBusinessError(
                "BROWSER_UNAVAILABLE",
                "Playwright is unavailable in the worker image.",
                retryable=False,
            ) from exc

        try:
            self._playwright = await async_playwright().start()
            executable_path = _clean(
                os.getenv("REMASK_CHROMIUM_EXECUTABLE") or "/usr/bin/chromium"
            )

            launch_kwargs: dict[str, Any] = {
                "headless": True,
                "executable_path": executable_path,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--disable-sync",
                    "--disable-translate",
                    "--disable-default-apps",
                    "--disable-component-update",
                    "--no-first-run",
                    "--renderer-process-limit=2",
                    "--blink-settings=imagesEnabled=false",
                ],
            }
            proxy = _proxy_config(getattr(self.context, "proxy", None))
            if proxy:
                launch_kwargs["proxy"] = proxy

            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            self._browser_context = await self._browser.new_context(
                user_agent=_clean(getattr(self.context, "user_agent", "")),
                locale="en-US",
                viewport={"width": 1280, "height": 800},
                service_workers="allow",
                reduced_motion="reduce",
            )

            async def block_heavy_resources(route: Any, request: Any) -> None:
                if _clean(getattr(request, "resource_type", "")).lower() in {
                    "image",
                    "media",
                    "font",
                }:
                    await route.abort()
                    return
                await route.continue_()

            await self._browser_context.route("**/*", block_heavy_resources)

            raw_cookies = getattr(self.context, "cookies", {}) or {}
            cookies: list[dict[str, Any]] = []

            if isinstance(raw_cookies, dict):
                for name, value in raw_cookies.items():
                    clean_name = _clean(name)
                    clean_value = str(value or "")
                    if not clean_name or not clean_value:
                        continue
                    cookies.append(
                        {
                            "name": clean_name,
                            "value": clean_value,
                            "domain": ".facebook.com",
                            "path": "/",
                            "secure": True,
                            "sameSite": "Lax",
                        }
                    )
            elif isinstance(raw_cookies, list):
                for row in raw_cookies:
                    if not isinstance(row, dict):
                        continue
                    clean_name = _clean(row.get("name"))
                    clean_value = str(row.get("value") or "")
                    if not clean_name or not clean_value:
                        continue
                    item = {
                        "name": clean_name,
                        "value": clean_value,
                        "domain": _clean(row.get("domain")) or ".facebook.com",
                        "path": _clean(row.get("path")) or "/",
                    }
                    for key in ("expires", "httpOnly", "secure", "sameSite"):
                        if key in row and row.get(key) is not None:
                            item[key] = row.get(key)
                    cookies.append(item)

            if not cookies:
                raise BrowserBusinessError(
                    "SESSION_COOKIES_MISSING",
                    "Facebook browser session has no cookies.",
                    retryable=False,
                )

            await self._browser_context.add_cookies(cookies)
            self.page = await self._browser_context.new_page()
            self.page.set_default_timeout(self.timeout_ms)

            def record_browser_event(kind: str, *, url: str = "", detail: str = "") -> None:
                clean_url = ""
                if url:
                    try:
                        parsed = urlsplit(url)
                        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:500]
                    except Exception:
                        clean_url = _clean(url).split("?", 1)[0][:500]
                self._browser_events.append(
                    {
                        "kind": _clean(kind)[:80],
                        "url": clean_url,
                        "detail": _clean(detail)[:500],
                    }
                )
                if len(self._browser_events) > 40:
                    del self._browser_events[:-40]

            def on_request_failed(request: Any) -> None:
                try:
                    resource_type = _clean(getattr(request, "resource_type", ""))
                    if resource_type in {"image", "media", "font"}:
                        return
                    record_browser_event(
                        "request_failed",
                        url=_clean(getattr(request, "url", "")),
                        detail=f"{resource_type}: {_clean(getattr(request, 'failure', ''))}",
                    )
                except Exception:
                    pass

            def on_page_error(error: Any) -> None:
                try:
                    record_browser_event(
                        "page_error",
                        detail=f"{error.__class__.__name__}: {error}",
                    )
                except Exception:
                    pass

            self.page.on("requestfailed", on_request_failed)
            self.page.on("pageerror", on_page_error)

        except BrowserBusinessError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise BrowserBusinessError(
                "BROWSER_START_FAILED",
                f"Profile-bound Chromium failed to start: {exc.__class__.__name__}: {exc}",
                retryable=True,
            ) from exc

    def _release_semaphore(self) -> None:
        if self._semaphore_acquired:
            self._semaphore_acquired = False
            self._browser_slot_acquired_at = 0.0
            _BROWSER_SEMAPHORE.release()

    async def close(self) -> None:
        watchdog = self._lease_watchdog_task
        self._lease_watchdog_task = None
        current = asyncio.current_task()
        if watchdog is not None and watchdog is not current:
            watchdog.cancel()

        async def bounded_cleanup(awaitable: Any, *, timeout: float) -> None:
            try:
                await asyncio.wait_for(awaitable, timeout=timeout)
            except BaseException:
                # Cleanup must never wedge the worker. A crashed Meta renderer
                # can make Playwright close calls stall; the next, broader
                # cleanup level still gets a chance to terminate Chromium.
                pass

        page = self.page
        self.page = None

        browser_context = self._browser_context
        self._browser_context = None

        browser = self._browser
        self._browser = None

        playwright = self._playwright
        self._playwright = None

        if page is not None:
            await bounded_cleanup(page.close(), timeout=1.5)

        if browser_context is not None:
            await bounded_cleanup(browser_context.close(), timeout=2.0)

        if browser is not None:
            await bounded_cleanup(browser.close(), timeout=4.0)

        if playwright is not None:
            await bounded_cleanup(playwright.stop(), timeout=2.0)

        if self._profile_lock_acquired and self._profile_lock is not None:
            self._profile_lock.release()
            self._profile_lock_acquired = False
        self._profile_lock = None

        self._release_semaphore()

    async def _goto(self, url: str) -> str:
        if self.page is None:
            await self.open()

        for attempt in range(2):
            try:
                await self.page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                await self.page.wait_for_timeout(900)
                await self._assert_authenticated()
                return _clean(self.page.url)

            except BrowserBusinessError:
                # Preserve meaningful account/session errors such as
                # SESSION_EXPIRED and CHECKPOINT_REQUIRED.
                raise

            except Exception as exc:
                text = f"{exc.__class__.__name__}: {exc}"
                lower = text.lower()

                interrupted_navigation = (
                    "net::err_aborted" in lower
                    or "frame was detached" in lower
                    or "navigation interrupted" in lower
                )
                if interrupted_navigation:
                    # Meta Business Suite is an SPA and may replace/detach the
                    # document during navigation. Playwright can surface that
                    # as ERR_ABORTED even though Meta completed the transition.
                    # No irreversible action happens in _goto(), so a single
                    # navigation retry is safe if the replacement DOM is not
                    # ready yet.
                    try:
                        await asyncio.sleep(0.45)
                        await self._assert_authenticated()
                        current_url = _clean(self.page.url)
                        current_body = await self._body_text()
                        form_ready = await self._form_ready()
                        create_surface = await self._has_create_surface()
                        facebook_surface = (
                            current_url
                            and current_url != "about:blank"
                            and "facebook.com" in current_url.lower()
                        )
                        # For read-only navigation, an authenticated Meta/Facebook
                        # URL is sufficient evidence that ERR_ABORTED came from
                        # an SPA redirect/frame replacement rather than a failed
                        # login. The caller will still verify the expected form
                        # or create surface separately.
                        if facebook_surface:
                            return current_url
                    except BrowserBusinessError:
                        raise
                    except Exception:
                        pass

                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue

                page_crashed = (
                    "page crashed" in lower
                    or "targetclosederror" in lower
                    or "target page, context or browser has been closed" in lower
                )

                if page_crashed and attempt == 0:
                    # Safe at navigation boundaries: release the crashed
                    # Chromium process and open a fresh low-memory context.
                    await self.close()
                    await asyncio.sleep(0.25)
                    await self.open()
                    continue

                try:
                    await self._diagnostic("navigation_error")
                except Exception:
                    pass
                raise BrowserBusinessError(
                    "FACEBOOK_NAVIGATION_FAILED",
                    f"Facebook navigation failed: {text}",
                    retryable=True,
                ) from exc

        raise BrowserBusinessError(
            "FACEBOOK_NAVIGATION_FAILED",
            "Facebook navigation failed after Chromium relaunch.",
            retryable=True,
        )

    async def _body_text(self) -> str:
        if self.page is None:
            return ""
        try:
            return str(await self.page.locator("body").inner_text(timeout=1500) or "")
        except Exception:
            return ""

    async def _assert_authenticated(self) -> None:
        if self.page is None:
            raise BrowserBusinessError(
                "BROWSER_NOT_OPEN",
                "Facebook browser page is not open.",
                retryable=False,
            )

        url = _clean(self.page.url)
        lower_url = url.lower()
        body = (await self._body_text()).lower()

        if "/login" in lower_url or "login.php" in lower_url:
            await self._diagnostic("login")
            raise BrowserBusinessError(
                "SESSION_EXPIRED",
                "Facebook redirected the profile to login.",
                retryable=False,
                diagnostic={"url": url},
            )

        if "/checkpoint" in lower_url or "checkpoint" in body[:4000]:
            await self._diagnostic("checkpoint")
            raise BrowserBusinessError(
                "CHECKPOINT_REQUIRED",
                "Facebook requires a checkpoint for this profile.",
                retryable=False,
                diagnostic={"url": url},
            )

        if (
            "two-factor authentication" in body
            or "enter security code" in body
            or "authentication code" in body
        ):
            await self._diagnostic("two_factor")
            raise BrowserBusinessError(
                "TWO_FACTOR_REQUIRED",
                "Facebook requires two-factor authentication.",
                retryable=False,
                diagnostic={"url": url},
            )

        if (
            "temporarily blocked" in body
            or "temporarily restricted" in body
            or "you’re temporarily blocked" in body
            or "you're temporarily blocked" in body
        ):
            await self._diagnostic("temporarily_blocked")
            raise BrowserBusinessError(
                "FACEBOOK_TEMPORARILY_BLOCKED",
                "Facebook temporarily blocked this profile action.",
                retryable=False,
                diagnostic={"url": url},
            )

    async def _diagnostic(self, stage: str) -> dict[str, Any]:
        if self.page is None:
            return {}

        result = {
            "stage": _clean(stage),
            "url": _clean(self.page.url),
        }

        # Always capture a small, non-file diagnostic surface. Production
        # normally keeps REMASK_BM_DIAGNOSTICS disabled, so returning only
        # stage+URL made live Meta UI failures impossible to distinguish.
        try:
            result["title"] = _clean(await self.page.title())[:300]
        except Exception:
            pass

        try:
            viewport = await self.page.evaluate(
                """() => ({
                    width: window.innerWidth,
                    height: window.innerHeight,
                    dpr: window.devicePixelRatio || 1
                })"""
            )
            if isinstance(viewport, dict):
                result["viewport"] = viewport
        except Exception:
            pass

        try:
            dom_state = await asyncio.wait_for(
                self.page.evaluate(
                    """() => ({
                        readyState: document.readyState,
                        bodyChildren: document.body ? document.body.children.length : -1,
                        bodyTextLength: document.body ? (document.body.innerText || '').length : -1,
                        scripts: document.scripts ? document.scripts.length : -1,
                        inputs: document.querySelectorAll('input').length,
                        interactive: document.querySelectorAll(
                            'button, [role="button"], [role="menuitem"], [aria-haspopup], a[href]'
                        ).length
                    })"""
                ),
                timeout=1.5,
            )
            if isinstance(dom_state, dict):
                result["dom_state"] = dom_state
        except Exception:
            pass

        if self._browser_events:
            result["browser_events"] = self._browser_events[-20:]

        try:
            body = " ".join((await self._body_text()).split())
            if body:
                result["body_excerpt"] = body[:3500]
        except Exception:
            pass

        try:
            lightweight = await self.page.locator(
                'button, [role="button"], [role="menuitem"], '
                '[aria-haspopup], [aria-expanded], a[href]'
            ).evaluate_all(
                """els => els.slice(0, 260).map(el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    if (!(r.width > 0 && r.height > 0)
                        || s.display === 'none'
                        || s.visibility === 'hidden') {
                        return null;
                    }
                    return {
                        tag: el.tagName,
                        role: el.getAttribute('role') || '',
                        text: (el.innerText || el.textContent || '')
                            .replace(/\\s+/g, ' ').trim().slice(0, 180),
                        aria: (el.getAttribute('aria-label') || '').slice(0, 180),
                        title: (el.getAttribute('title') || '').slice(0, 180),
                        haspopup: el.getAttribute('aria-haspopup') || '',
                        expanded: el.getAttribute('aria-expanded') || '',
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        w: Math.round(r.width),
                        h: Math.round(r.height)
                    };
                }).filter(Boolean)"""
            )
            if isinstance(lightweight, list):
                result["visible_controls"] = lightweight[:60]
                result["top_left_controls"] = [
                    row
                    for row in lightweight
                    if int(row.get("x") or 0) < 620
                    and int(row.get("y") or 0) < 420
                ][:60]
        except Exception:
            pass

        if _clean(os.getenv("REMASK_BM_DIAGNOSTICS")) not in {"1", "true", "yes"}:
            return result

        base = _clean(os.getenv("REMASK_BM_DIAG_DIR"))
        if not base:
            volume = _clean(os.getenv("RAILWAY_VOLUME_MOUNT_PATH"))
            base = f"{volume}/bm-diagnostics" if volume else "/tmp/remask-bm-diagnostics"

        safe_profile = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.profile_id or "profile")
        safe_stage = re.sub(r"[^A-Za-z0-9_.-]+", "_", stage or "stage")
        stamp = str(int(time.time() * 1000))
        root = Path(base) / safe_profile
        root.mkdir(parents=True, exist_ok=True)

        try:
            screenshot = root / f"{stamp}-{safe_stage}.png"
            await self.page.screenshot(path=str(screenshot), full_page=True)
            result["screenshot"] = str(screenshot)
        except Exception:
            pass

        try:
            text_path = root / f"{stamp}-{safe_stage}.txt"
            body = await self._body_text()
            text_path.write_text(
                f"URL: {self.page.url}\n\n{body[:50000]}",
                encoding="utf-8",
            )
            result["text"] = str(text_path)
        except Exception:
            pass

        try:
            controls = await self.page.locator(
                'button, [role="button"], [role="menuitem"], a[href]'
            ).evaluate_all(
                """els => els.slice(0, 220).map(el => {
                    const r = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return {
                        tag: el.tagName,
                        role: el.getAttribute("role") || "",
                        text: (el.innerText || el.textContent || "").trim().slice(0, 180),
                        aria: (el.getAttribute("aria-label") || "").slice(0, 180),
                        title: (el.getAttribute("title") || "").slice(0, 180),
                        haspopup: el.getAttribute("aria-haspopup") || "",
                        expanded: el.getAttribute("aria-expanded") || "",
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        w: Math.round(r.width),
                        h: Math.round(r.height),
                        visible: r.width > 0 && r.height > 0 && style.visibility !== "hidden" && style.display !== "none"
                    };
                }).filter(row => row.visible)"""
            )
            result["controls"] = controls[:80]
            result["top_left_controls"] = sorted(
                [
                    row for row in controls
                    if int(row.get("x") or 0) < 560
                    and int(row.get("y") or 0) < 360
                ],
                key=lambda row: (
                    int(row.get("y") or 0),
                    int(row.get("x") or 0),
                ),
            )[:80]
        except Exception:
            pass

        try:
            top_left = await self.page.locator(
                '[role], [aria-label], [tabindex], button, a[href]'
            ).evaluate_all(
                """els => els.map(el => {
                    const r = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    if (!(r.width > 0 && r.height > 0) || style.visibility === "hidden" || style.display === "none") return null;
                    if (r.x >= 560 || r.y >= 360) return null;
                    return {
                        tag: el.tagName,
                        role: el.getAttribute("role") || "",
                        text: (el.innerText || el.textContent || "").trim().slice(0, 220),
                        aria: (el.getAttribute("aria-label") || "").slice(0, 220),
                        title: (el.getAttribute("title") || "").slice(0, 220),
                        tabindex: el.getAttribute("tabindex") || "",
                        haspopup: el.getAttribute("aria-haspopup") || "",
                        expanded: el.getAttribute("aria-expanded") || "",
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        w: Math.round(r.width),
                        h: Math.round(r.height)
                    };
                }).filter(Boolean).sort((a,b) => (a.y-b.y) || (a.x-b.x)).slice(0, 140)"""
            )
            result["top_left_surface"] = top_left
        except Exception:
            pass

        return result

    async def _quick_surface_state(self) -> dict[str, Any]:
        if self.page is None:
            return {"blank": True}

        try:
            state = await asyncio.wait_for(
                self.page.evaluate(
                    """() => {
                        const bodyText = document.body ? (document.body.innerText || '') : '';
                        const interactive = document.querySelectorAll(
                            'button, [role="button"], [role="menuitem"], [aria-haspopup], a[href], input'
                        ).length;
                        return {
                            ready_state: document.readyState,
                            body_text_length: bodyText.trim().length,
                            interactive_count: interactive,
                            body_children: document.body ? document.body.children.length : 0,
                            scripts: document.scripts ? document.scripts.length : 0
                        };
                    }"""
                ),
                timeout=1.5,
            )
        except Exception:
            return {"blank": False, "probe_failed": True}

        if not isinstance(state, dict):
            return {"blank": False, "probe_failed": True}

        body_len = int(state.get("body_text_length") or 0)
        interactive = int(state.get("interactive_count") or 0)
        children = int(state.get("body_children") or 0)
        state["blank"] = bool(body_len < 20 and interactive == 0 and children <= 3)
        return state


    async def _has_create_surface(self) -> bool:
        if self.page is None:
            return False

        for name in self.CREATE_NAMES:
            pattern = re.compile(re.escape(name), re.IGNORECASE)
            for role in ("button", "link", "menuitem"):
                try:
                    locator = self.page.get_by_role(role, name=pattern)
                    if await locator.count() and await locator.first.is_visible():
                        return True
                except Exception:
                    continue

        # Meta Business Suite keeps the portfolio menu mounted in the SPA
        # and, on the live profile canary, the CREATE entry was discoverable
        # from rendered body text before the role locator became stable. Keep
        # this mounted-surface fallback; the actual click path still requires
        # a visible/enabled control in _click_named().
        body = (await self._body_text()).lower()
        return any(name.lower() in body for name in self.CREATE_NAMES)

    async def _form_ready(self) -> bool:
        if self.page is None:
            return False

        # Never infer the CREATE form from a raw input count. The normal Meta
        # Business Suite home page can expose four unrelated inputs (search,
        # onboarding, connection widgets, etc.), which caused profile 4 to be
        # misclassified as CREATE_FORM_READY and then fail at submit.
        body = (await self._body_text()).casefold()

        email_markers = (
            "business email",
            "business email address",
            "рабочий электронный адрес",
            "электронный адрес компании",
            "робоча електронна адреса",
            "електронна адреса компанії",
            "geschäftliche e-mail-adresse",
            "geschäftliche email-adresse",
            "geschäftliche e-mail",
            "adresse e-mail professionnelle",
            "adresse e-mail de l’entreprise",
            "adresse e-mail de l'entreprise",
            "e-mail professionnel",
        )
        name_markers = (
            "business portfolio name",
            "business name",
            "business and account name",
            "название бизнес-портфолио",
            "название компании",
            "назва бізнес-портфоліо",
            "назва компанії",
            "business-portfolio-name",
            "name des business-portfolios",
            "unternehmensname",
            "nom du portefeuille business",
            "nom du portefeuille professionnel",
            "nom de l’entreprise",
            "nom de l'entreprise",
        )

        has_email = any(marker in body for marker in email_markers)
        has_name = any(marker in body for marker in name_markers)
        if has_email and has_name:
            return True

        # Secondary semantic fallback for A/B variants where labels are
        # attached only to input attributes and are absent from body.innerText.
        semantic_email = False
        semantic_name = False
        try:
            inputs = self.page.locator("input:visible")
            count = min(await inputs.count(), 16)
            for index in range(count):
                item = inputs.nth(index)
                input_type = _clean(await item.get_attribute("type")).casefold()
                key = " ".join(
                    _clean(await item.get_attribute(attr))
                    for attr in (
                        "name",
                        "id",
                        "placeholder",
                        "aria-label",
                        "autocomplete",
                    )
                ).casefold()

                if (
                    input_type == "email"
                    and any(
                        token in key
                        for token in (
                            "business",
                            "profession",
                            "entreprise",
                            "company",
                            "geschäft",
                        )
                    )
                ):
                    semantic_email = True

                if any(
                    token in key
                    for token in (
                        "business_name",
                        "business name",
                        "portfolio",
                        "portefeuille",
                        "entreprise",
                        "unternehmensname",
                    )
                ):
                    semantic_name = True
        except Exception:
            pass

        return semantic_email and semantic_name

    async def _wait_for_create_surface(
        self,
        *,
        timeout_ms: int = 3000,
        interval_ms: int = 250,
    ) -> bool:
        deadline = time.monotonic() + max(0.25, timeout_ms / 1000)
        while time.monotonic() < deadline:
            if await self._has_create_surface():
                return True
            await self.page.wait_for_timeout(interval_ms)
        return await self._has_create_surface()

    async def _wait_for_form_ready(
        self,
        *,
        timeout_ms: int = 3500,
        interval_ms: int = 250,
    ) -> bool:
        deadline = time.monotonic() + max(0.25, timeout_ms / 1000)
        while time.monotonic() < deadline:
            if await self._form_ready():
                return True
            await self.page.wait_for_timeout(interval_ms)
        return await self._form_ready()

    async def _try_open_known_asset_selector(self) -> bool:
        """
        Open Meta's top-left account/business selector by anchoring on a Page
        that ReMask already knows belongs to this FB profile.

        This specifically handles Business Suite sessions that are pinned to a
        Page via ?asset_id=... and therefore redirect /reg/ back to Home.
        """
        if self.page is None:
            return False

        raw_pages = getattr(self.context, "pages", None) or []
        known_pages: list[tuple[str, str]] = []
        for row in raw_pages:
            if not isinstance(row, dict):
                continue
            page_id = _digits(row.get("id"))
            page_name = _clean(row.get("name") or row.get("title"))
            if page_id and page_name:
                known_pages.append((page_id, page_name))

        if not known_pages:
            return False

        current_asset = ""
        try:
            query = parse_qs(urlsplit(_clean(self.page.url)).query)
            current_asset = _digits(
                (query.get("asset_id") or query.get("assetId") or [""])[0]
            )
        except Exception:
            current_asset = ""

        # This targeted path is deliberately limited to the live failure
        # mode we observed: Meta redirected Business Suite into a known Page
        # context via ?asset_id=<PageID>. Other profiles keep the generic,
        # already-canary-tested selector path below.
        if not current_asset:
            return False

        pinned_pages = [
            row for row in known_pages
            if row[0] == current_asset
        ]
        if not pinned_pages:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "known_asset_selector": {
                    "current_asset_id": current_asset,
                    "matched_known_page": False,
                    "known_pages": [
                        {"id": page_id, "name": page_name}
                        for page_id, page_name in known_pages[:12]
                    ],
                },
            }
            return False

        known_pages = pinned_pages

        diagnostic_rows: list[dict[str, Any]] = []

        async def try_click_candidate(candidate: Any, *, label: str) -> bool:
            try:
                if not await candidate.is_visible():
                    return False

                target = candidate
                try:
                    role = _clean(await candidate.get_attribute("role")).lower()
                    tag = _clean(
                        await candidate.evaluate("(e) => e.tagName || ''")
                    ).upper()
                    has_popup = _clean(
                        await candidate.get_attribute("aria-haspopup")
                    )
                    if (
                        role not in {"button", "menuitem", "link"}
                        and tag not in {"BUTTON", "A"}
                        and not has_popup
                    ):
                        ancestor = candidate.locator(
                            'xpath=ancestor-or-self::*['
                            '@role="button" or @role="menuitem" or '
                            '@aria-haspopup or self::button or self::a'
                            '][1]'
                        )
                        if await ancestor.count():
                            target = ancestor.first
                except Exception:
                    target = candidate

                box = await target.bounding_box()
                if not box:
                    return False

                x = float(box.get("x") or 0)
                y = float(box.get("y") or 0)
                w = float(box.get("width") or 0)
                h = float(box.get("height") or 0)

                diagnostic_rows.append(
                    {
                        "label": label[:180],
                        "x": round(x),
                        "y": round(y),
                        "w": round(w),
                        "h": round(h),
                    }
                )

                # Keep the click constrained to the Business Suite top-left
                # selector zone so a same-named Page in the main content is
                # never treated as the account selector.
                if x > 330 or y > 340 or w <= 0 or h <= 0:
                    return False

                await target.click(timeout=3000)
                if await self._wait_for_create_surface(
                    timeout_ms=6500,
                    interval_ms=250,
                ):
                    self._last_selector_diagnostic = {
                        "known_asset_selector": {
                            "current_asset_id": current_asset,
                            "matched_label": label,
                            "candidates": diagnostic_rows[-12:],
                        }
                    }
                    return True

                try:
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(120)
                except Exception:
                    pass
            except Exception as exc:
                diagnostic_rows.append(
                    {
                        "label": label[:180],
                        "error": f"{exc.__class__.__name__}: {exc}"[:500],
                    }
                )
            return False

        for page_id, page_name in known_pages:
            # Role-based search first: most Meta selector variants expose the
            # current Page as an accessible button/menu item.
            name_pattern = re.compile(
                re.escape(page_name),
                re.IGNORECASE,
            )
            for role in ("button", "menuitem", "link"):
                try:
                    locator = self.page.get_by_role(role, name=name_pattern)
                    count = min(await locator.count(), 8)
                except Exception:
                    count = 0

                for index in range(count):
                    if await try_click_candidate(
                        locator.nth(index),
                        label=f"{page_name} [{page_id}]/{role}",
                    ):
                        return True

            # Some A/B variants render the Page name in a nested DIV/span and
            # put the click handler on an ancestor.
            try:
                text_nodes = self.page.get_by_text(
                    name_pattern,
                    exact=False,
                )
                count = min(await text_nodes.count(), 12)
            except Exception:
                count = 0

            for index in range(count):
                if await try_click_candidate(
                    text_nodes.nth(index),
                    label=f"{page_name} [{page_id}]/text",
                ):
                    return True

        # Last-resort bounded probe for Meta A/B variants where the current
        # Page selector has no accessible name at all. Live profile-4 evidence
        # showed an authenticated Page-pinned shell with zero name candidates,
        # so probe only interactive controls in the upper-left region and rank
        # them by asset-id/page-name/popup evidence before clicking.
        geometry_rows: list[dict[str, Any]] = []
        try:
            geometry_rows = await self.page.evaluate(
                """(args) => {
                    const assetId = String(args.assetId || '');
                    const pageNames = (args.pageNames || [])
                        .map(v => String(v || '').trim().toLowerCase())
                        .filter(Boolean);
                    const els = Array.from(document.querySelectorAll(
                        'button,[role="button"],[role="menuitem"],[aria-haspopup],'
                        + '[aria-expanded],a[href],[data-asset-id],[data-asset_id]'
                    )).slice(0, 700);
                    const rows = [];
                    for (const el of els) {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        if (!(r.width > 0 && r.height > 0)
                            || s.display === 'none'
                            || s.visibility === 'hidden'
                            || s.pointerEvents === 'none') continue;
                        if (r.x > 520 || r.y > 520 || r.right < 0 || r.bottom < 0) continue;

                        const aria = (el.getAttribute('aria-label') || '').trim();
                        const title = (el.getAttribute('title') || '').trim();
                        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                        const href = (el.getAttribute('href') || '').trim();
                        const role = (el.getAttribute('role') || '').trim();
                        const haspopup = (el.getAttribute('aria-haspopup') || '').trim();
                        const expanded = (el.getAttribute('aria-expanded') || '').trim();
                        const dataAsset = (
                            el.getAttribute('data-asset-id')
                            || el.getAttribute('data-asset_id')
                            || ''
                        ).trim();

                        const label = [aria, title, text].filter(Boolean).join(' | ').slice(0, 320);
                        const key = label.toLowerCase();
                        const assetMatch = !!assetId && (
                            href.includes(assetId)
                            || dataAsset === assetId
                            || key.includes(assetId)
                        );
                        const nameMatch = pageNames.some(name => key.includes(name));

                        let score = 0;
                        if (assetMatch) score += 500;
                        if (nameMatch) score += 320;
                        if (haspopup) score += 180;
                        if (role === 'button' || role === 'menuitem') score += 90;
                        if (expanded) score += 40;
                        if (r.x <= 360) score += 60;
                        if (r.y <= 260) score += 40;

                        if (!assetMatch && !nameMatch && /^(home|inbox|ads|content|planner|insights|notifications|settings)$/i.test(text)) {
                            score -= 300;
                        }
                        if (score < 180) continue;

                        rows.push({
                            score,
                            x: Math.round(r.x + r.width / 2),
                            y: Math.round(r.y + r.height / 2),
                            left: Math.round(r.x),
                            top: Math.round(r.y),
                            w: Math.round(r.width),
                            h: Math.round(r.height),
                            label,
                            href: href.slice(0, 320),
                            role,
                            haspopup,
                            expanded,
                            assetMatch,
                            nameMatch
                        });
                    }
                    rows.sort((a, b) => b.score - a.score || a.top - b.top || a.left - b.left);
                    return rows.slice(0, 24);
                }""",
                {
                    "assetId": current_asset,
                    "pageNames": [name for _, name in known_pages],
                },
            )
        except Exception as exc:
            geometry_rows = [{
                "error": f"{exc.__class__.__name__}: {exc}"[:500],
            }]

        if isinstance(geometry_rows, list):
            for row in geometry_rows[:6]:
                if not isinstance(row, dict):
                    continue
                x = row.get("x")
                y = row.get("y")
                if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                    continue
                try:
                    await self.page.mouse.click(float(x), float(y))
                    if await self._wait_for_form_ready(
                        timeout_ms=1200,
                        interval_ms=200,
                    ):
                        self._last_selector_diagnostic = {
                            "known_asset_selector": {
                                "current_asset_id": current_asset,
                                "geometry_match": row,
                                "geometry_candidates": geometry_rows[:12],
                            }
                        }
                        return True
                    if await self._wait_for_create_surface(
                        timeout_ms=4200,
                        interval_ms=250,
                    ):
                        self._last_selector_diagnostic = {
                            "known_asset_selector": {
                                "current_asset_id": current_asset,
                                "geometry_match": row,
                                "geometry_candidates": geometry_rows[:12],
                            }
                        }
                        return True
                    try:
                        await self.page.keyboard.press("Escape")
                        await self.page.wait_for_timeout(120)
                    except Exception:
                        pass
                except Exception as exc:
                    row["click_error"] = f"{exc.__class__.__name__}: {exc}"[:500]
                    try:
                        await self.page.keyboard.press("Escape")
                    except Exception:
                        pass

        self._last_selector_diagnostic = {
            "known_asset_selector": {
                "current_asset_id": current_asset,
                "known_pages": [
                    {"id": page_id, "name": page_name}
                    for page_id, page_name in known_pages[:12]
                ],
                "candidates": diagnostic_rows[-20:],
                "geometry_candidates": geometry_rows[:20]
                if isinstance(geometry_rows, list)
                else [],
            }
        }
        return False

    async def _try_open_top_left_portfolio_menu(
        self,
        *,
        skip_known_asset: bool = False,
    ) -> bool:
        if self.page is None:
            return False

        if await self._has_create_surface():
            return True

        # Meta can pin Business Suite to a Page via ?asset_id=... and then
        # redirect direct /reg/ navigation back to Home. In that state, use
        # the Page already known in ProfileContext as the selector anchor.
        if not skip_known_asset and await self._try_open_known_asset_selector():
            return True

        # Meta serves at least two Business Suite sidebar variants.
        # In one, "Meta Business Suite" is a narrow collapse/expand control;
        # in another, the same visible label is itself the portfolio selector.
        # Treat both as read-only candidates and trust only the observed result:
        # if clicking it exposes a Create action, keep it; otherwise Escape and
        # continue to the geometry/structured fallbacks below.
        try:
            role_buttons = self.page.locator('[role="button"]')
            count = min(await role_buttons.count(), 180)
            for index in range(count):
                item = role_buttons.nth(index)
                if not await item.is_visible():
                    continue
                text_value = _clean(await item.inner_text(timeout=1000))
                if text_value.casefold() != "meta business suite":
                    continue
                box = await item.bounding_box()
                if not box:
                    continue

                width = float(box.get("width") or 0)
                x = float(box.get("x") or 0)
                y = float(box.get("y") or 0)
                top_left_business_control = (
                    x <= 40
                    and y <= 140
                    and 0 < width <= 240
                )
                if not top_left_business_control:
                    continue

                try:
                    await item.click(timeout=3000)
                    if await self._wait_for_create_surface(
                        timeout_ms=2200,
                        interval_ms=200,
                    ):
                        return True
                finally:
                    # Clicking the wrong A/B variant is harmless navigation/UI
                    # state only. Close any menu/popover before trying another
                    # candidate path.
                    try:
                        await self.page.keyboard.press("Escape")
                        await self.page.wait_for_timeout(120)
                    except Exception:
                        pass
                break
        except Exception:
            # Read-only candidate probing must never make Add BM fail by itself.
            pass

        # Current Meta Business Suite places the business/page selector BELOW
        # the Meta Business Suite logo and ABOVE Home/Startseite. Do not walk
        # the complete SPA DOM here: the live Railway canary showed that a
        # querySelectorAll("*") geometry scan can destabilize Chromium. Probe
        # only a small grid inside that narrow sidebar band.
        try:
            probe = await self.page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el || el === document.body || el === document.documentElement) {
                            return false;
                        }
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const label = (el) => [
                        (el.getAttribute && el.getAttribute('aria-label')) || '',
                        (el.getAttribute && el.getAttribute('title')) || '',
                        el.innerText || el.textContent || ''
                    ].join(' ').replace(/\\s+/g, ' ').trim();

                    const xs = [16, 36, 60, 88, 120, 156, 192, 228, 264, 300, 328];
                    const ys = [58, 72, 86, 100, 114, 128, 142, 156, 170, 184, 198, 214, 230, 246, 264, 282, 300, 318];
                    const seen = new Set();
                    const rows = [];

                    for (const y of ys) {
                        for (const x of xs) {
                            const stack = document.elementsFromPoint(x, y) || [];
                            for (const el of stack.slice(0, 12)) {
                                if (seen.has(el) || !visible(el)) continue;
                                seen.add(el);

                                const r = el.getBoundingClientRect();
                                const text = label(el);
                                const role = (el.getAttribute && el.getAttribute('role')) || '';
                                const tabindex = (el.getAttribute && el.getAttribute('tabindex')) || '';
                                const haspopup = (el.getAttribute && el.getAttribute('aria-haspopup')) || '';
                                const tag = el.tagName || '';
                                const interactive = (
                                    role === 'button' ||
                                    role === 'menuitem' ||
                                    tag === 'BUTTON' ||
                                    tag === 'A' ||
                                    tabindex === '0' ||
                                    !!haspopup
                                );

                                if (r.x > 360 || r.y < 48 || r.y > 340) continue;
                                if (r.width < 18 || r.width > 340) continue;
                                if (r.height < 18 || r.height > 110) continue;
                                if (/^Meta Business Suite$/i.test(text)) continue;
                                if (/^(Home|Accueil|Startseite|Start|Главная|Головна)$/i.test(text)) continue;
                                if (!text && !interactive) continue;

                                rows.push({el, r, text, role, tabindex, haspopup, tag, interactive});
                            }
                        }
                    }

                    const score = (row) => {
                        const key = (row.text || '').toLowerCase();
                        let value = Math.round(row.r.y);
                        if (row.interactive) value -= 160;
                        if (row.haspopup) value -= 180;
                        if (/business|portfolio|account|asset|switch|select|page|profile|entreprise|portefeuille|compte|actif|changer|sélection|selection|profil/.test(key)) value -= 140;
                        if (!row.text) value += 35;
                        return value;
                    };

                    rows.sort((a,b) => {
                        const as = score(a);
                        const bs = score(b);
                        if (as !== bs) return as - bs;
                        const ai = a.r.width * a.r.height;
                        const bi = b.r.width * b.r.height;
                        return bi - ai || a.r.y - b.r.y;
                    });

                    const compact = rows.slice(0,16).map(row => ({
                        text:row.text,
                        role:row.role,
                        tag:row.tag,
                        haspopup:row.haspopup,
                        x:Math.round(row.r.x),
                        y:Math.round(row.r.y),
                        w:Math.round(row.r.width),
                        h:Math.round(row.r.height)
                    }));
                    const best = rows[0];

                    if (!best) {
                        return {clicked:false, candidates:compact};
                    }

                    best.el.click();
                    return {
                        clicked:true,
                        clickedCandidate:compact[0],
                        candidates:compact
                    };
                }"""
            )
            if isinstance(probe, dict):
                # Always preserve the compact band probe. If no candidate was
                # clickable this is the only useful evidence of what Meta
                # actually rendered between the logo and Home.
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "sidebar_probe": probe,
                }

                if probe.get("clicked"):
                    if await self._wait_for_create_surface():
                        return True

                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "sidebar_probe": probe,
                        "sidebar_open_without_create": {
                            "url": _clean(self.page.url),
                            "surface": await self._quick_surface_state(),
                        },
                    }
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(120)
        except Exception as exc:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "sidebar_probe_error": f"{exc.__class__.__name__}: {exc}",
            }
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                pass

        # Structured-menu fallbacks for other Business Suite variants.
        selectors = (
            'button[aria-haspopup="menu"]',
            '[role="button"][aria-haspopup="menu"]',
            'button[aria-expanded]',
            '[role="button"][aria-expanded]',
            '[role="button"]',
            'button',
            '[tabindex="0"]',
        )
        candidates: list[tuple[int, float, float, Any]] = []

        for selector in selectors:
            try:
                locator = self.page.locator(selector)
                count = min(await locator.count(), 60)
            except Exception:
                continue

            for index in range(count):
                item = locator.nth(index)
                try:
                    if not await item.is_visible():
                        continue
                    box = await item.bounding_box()
                    if not box:
                        continue

                    x = float(box.get("x") or 0)
                    y = float(box.get("y") or 0)
                    if x > 340 or y < 55 or y > 325:
                        continue

                    text_value = _clean(await item.inner_text(timeout=1000))
                    aria = _clean(await item.get_attribute("aria-label"))
                    title = _clean(await item.get_attribute("title"))
                    key = " ".join((text_value, aria, title)).lower()

                    # Explicitly reject the logo control discovered by canary.
                    if text_value.casefold() == "meta business suite":
                        continue

                    score = 0
                    if any(
                        token in key
                        for token in (
                            "business",
                            "portfolio",
                            "asset",
                            "entreprise",
                            "portefeuille",
                            "actif",
                        )
                    ):
                        score -= 100
                    if any(
                        token in key
                        for token in (
                            "switch",
                            "select",
                            "account",
                            "changer",
                            "sélection",
                            "selection",
                            "compte",
                        )
                    ):
                        score -= 50
                    score += int(y)
                    candidates.append((score, y, x, item))
                except Exception:
                    continue

        candidates.sort(key=lambda row: (row[0], row[1], row[2]))

        seen: set[tuple[int, int]] = set()
        for _, y, x, item in candidates[:4]:
            marker = (round(x), round(y))
            if marker in seen:
                continue
            seen.add(marker)
            try:
                await item.click(timeout=1200)
                if await self._wait_for_create_surface(
                    timeout_ms=1400,
                    interval_ms=200,
                ):
                    return True
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(120)
            except Exception:
                try:
                    await self.page.keyboard.press("Escape")
                except Exception:
                    pass

        return False

    async def _try_open_direct_create_url(self) -> bool:
        """
        Try Meta's current direct Business Portfolio creation route.

        This is navigation-only until Meta's own form is visible. It is useful
        for Page-pinned Business Suite sessions where /reg/ redirects back to
        latest/home?asset_id=<PageID> and no portfolio selector is rendered.
        """
        if self.page is None:
            return False

        requested_url = self.DIRECT_CREATE_URL
        final_url = ""
        try:
            final_url = await asyncio.wait_for(
                self._goto(requested_url),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "direct_create_route": {
                    "requested_url": requested_url,
                    "timeout": 10,
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False
        except BrowserBusinessError as exc:
            if exc.code != "FACEBOOK_NAVIGATION_FAILED":
                raise
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "direct_create_route": {
                    "requested_url": requested_url,
                    "navigation_error": exc.code,
                    "message": str(exc)[:500],
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False

        await self._assert_authenticated()

        if await self._wait_for_form_ready(timeout_ms=2500, interval_ms=200):
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "direct_create_route": {
                    "requested_url": requested_url,
                    "final_url": _clean(final_url or self.page.url),
                    "form_ready": True,
                },
            }
            return True

        create_surface = await self._wait_for_create_surface(
            timeout_ms=2500,
            interval_ms=200,
        )
        if create_surface and await self._click_named(self.CREATE_NAMES):
            await self._assert_authenticated()
            if await self._wait_for_form_ready(
                timeout_ms=6500,
                interval_ms=250,
            ):
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "direct_create_route": {
                        "requested_url": requested_url,
                        "final_url": _clean(self.page.url),
                        "create_surface": True,
                        "create_clicked": True,
                        "form_ready": True,
                    },
                }
                return True

        redirected_asset_id = ""
        try:
            query = parse_qs(urlsplit(_clean(self.page.url)).query)
            redirected_asset_id = _digits(
                (query.get("asset_id") or query.get("assetId") or [""])[0]
            )
        except Exception:
            redirected_asset_id = ""

        self._last_selector_diagnostic = {
            **self._last_selector_diagnostic,
            "direct_create_route": {
                "requested_url": requested_url,
                "final_url": _clean(final_url or self.page.url),
                "create_surface": bool(create_surface),
                "form_ready": False,
                "redirected_asset_id": redirected_asset_id,
            },
        }
        return False


    async def _try_open_overview_create_entry(self) -> bool:
        if self.page is None:
            return False

        requested_url = self.OVERVIEW_URL
        try:
            final_url = await asyncio.wait_for(
                self._goto(requested_url),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "overview_route": {
                    "requested_url": requested_url,
                    "timeout": 10,
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False
        except BrowserBusinessError as exc:
            if exc.code != "FACEBOOK_NAVIGATION_FAILED":
                raise
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "overview_route": {
                    "requested_url": requested_url,
                    "navigation_error": exc.code,
                    "message": str(exc)[:500],
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False

        await self._assert_authenticated()

        if await self._wait_for_form_ready(timeout_ms=2200, interval_ms=200):
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "overview_route": {
                    "requested_url": requested_url,
                    "final_url": _clean(final_url or self.page.url),
                    "form_ready": True,
                },
            }
            return True

        if await self._wait_for_create_surface(timeout_ms=2200, interval_ms=200):
            if await self._click_named(self.CREATE_NAMES):
                await self._assert_authenticated()
                if await self._wait_for_form_ready(timeout_ms=3500, interval_ms=200):
                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "overview_route": {
                            "requested_url": requested_url,
                            "final_url": _clean(self.page.url),
                            "create_clicked": True,
                            "form_ready": True,
                        },
                    }
                    return True

        menu_open = False
        try:
            menu_open = await asyncio.wait_for(
                self._try_open_top_left_portfolio_menu(),
                timeout=4.0,
            )
        except asyncio.TimeoutError:
            pass

        if menu_open and await self._click_named(self.CREATE_NAMES):
            await self._assert_authenticated()
            if await self._wait_for_form_ready(timeout_ms=3500, interval_ms=200):
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "overview_route": {
                        "requested_url": requested_url,
                        "final_url": _clean(self.page.url),
                        "menu_open": True,
                        "form_ready": True,
                    },
                }
                return True

        self._last_selector_diagnostic = {
            **self._last_selector_diagnostic,
            "overview_route": {
                "requested_url": requested_url,
                "final_url": _clean(final_url or self.page.url),
                "form_ready": False,
                "surface": await self._quick_surface_state(),
            },
        }
        return False


    async def _try_open_ads_manager_create_entry(self) -> bool:
        """
        Bounded fallback for Meta's 2026 Ads Manager Business Portfolio
        selector. This path is used only when Business Suite renders no usable
        account/business selector in a known Page context.
        """
        if self.page is None:
            return False

        probe_rows: list[dict[str, Any]] = []

        try:
            await asyncio.wait_for(
                self._goto(self.ADS_MANAGER_URL),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "ads_manager_probe": {
                    "navigation_timeout": 30,
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False
        except BrowserBusinessError as exc:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "ads_manager_probe": {
                    "navigation_error": exc.code,
                    "message": str(exc)[:500],
                    "url": _clean(self.page.url if self.page else ""),
                },
            }
            return False

        await self._assert_authenticated()

        if await self._form_ready():
            return True

        if await self._has_create_surface():
            if await self._click_named(self.CREATE_NAMES):
                await self._assert_authenticated()
                if await self._wait_for_form_ready(
                    timeout_ms=6500,
                    interval_ms=250,
                ):
                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "ads_manager_probe": {
                            "create_surface_direct": True,
                            "url": _clean(self.page.url),
                        },
                    }
                    return True

        # Current Ads Manager exposes its account/portfolio selector in the
        # upper-left account area. Probe only that bounded region and favor
        # controls that look like account selectors.
        selectors = (
            'button',
            '[role="button"]',
            '[role="menuitem"]',
            '[aria-haspopup]',
            '[aria-expanded]',
        )
        candidates: list[tuple[int, float, float, Any, str]] = []

        for selector in selectors:
            try:
                locator = self.page.locator(selector)
                count = min(await locator.count(), 100)
            except Exception:
                continue

            for index in range(count):
                item = locator.nth(index)
                try:
                    if not await item.is_visible() or not await item.is_enabled():
                        continue
                    box = await item.bounding_box()
                    if not box:
                        continue

                    x = float(box.get("x") or 0)
                    y = float(box.get("y") or 0)
                    w = float(box.get("width") or 0)
                    h = float(box.get("height") or 0)
                    if x > 620 or y > 260 or w <= 0 or h <= 0:
                        continue

                    text_value = _clean(await item.inner_text(timeout=700))
                    aria = _clean(await item.get_attribute("aria-label"))
                    title = _clean(await item.get_attribute("title"))
                    haspopup = _clean(await item.get_attribute("aria-haspopup"))
                    expanded = _clean(await item.get_attribute("aria-expanded"))
                    key = " ".join((text_value, aria, title)).casefold()

                    score = int(y)
                    if haspopup:
                        score -= 120
                    if expanded:
                        score -= 40
                    if any(
                        token in key
                        for token in (
                            "account",
                            "ad account",
                            "business",
                            "portfolio",
                            "switch",
                            "select",
                        )
                    ):
                        score -= 100
                    if re.search(r"\b\d{5,30}\b", key):
                        score -= 60

                    label = " | ".join(
                        value
                        for value in (text_value, aria, title)
                        if value
                    )[:300]
                    probe_rows.append(
                        {
                            "selector": selector,
                            "label": label,
                            "x": round(x),
                            "y": round(y),
                            "w": round(w),
                            "h": round(h),
                            "haspopup": haspopup,
                            "expanded": expanded,
                            "score": score,
                        }
                    )
                    candidates.append((score, y, x, item, label))
                except Exception:
                    continue

        candidates.sort(key=lambda row: (row[0], row[1], row[2]))
        seen: set[tuple[int, int]] = set()

        for _, y, x, item, label in candidates[:8]:
            marker = (round(x), round(y))
            if marker in seen:
                continue
            seen.add(marker)

            try:
                await item.click(timeout=2200)
                if await self._wait_for_create_surface(
                    timeout_ms=3500,
                    interval_ms=250,
                ):
                    if await self._click_named(self.CREATE_NAMES):
                        await self._assert_authenticated()
                        if await self._wait_for_form_ready(
                            timeout_ms=6500,
                            interval_ms=250,
                        ):
                            self._last_selector_diagnostic = {
                                **self._last_selector_diagnostic,
                                "ads_manager_probe": {
                                    "selector_opened": True,
                                    "matched_label": label,
                                    "url": _clean(self.page.url),
                                    "candidates": probe_rows[:30],
                                },
                            }
                            return True
                try:
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(120)
                except Exception:
                    pass
            except Exception:
                try:
                    await self.page.keyboard.press("Escape")
                except Exception:
                    pass

        self._last_selector_diagnostic = {
            **self._last_selector_diagnostic,
            "ads_manager_probe": {
                "selector_opened": False,
                "url": _clean(self.page.url if self.page else ""),
                "candidates": probe_rows[:30],
            },
        }
        return False

    async def _open_create_entry(
        self,
        *,
        open_form: bool,
        already_on_home: bool = False,
    ) -> bool:
        # Keep production on the lightweight Business Suite HOME SPA. The
        # heavier /overview and /reg surfaces have crashed Chromium on Railway
        # and are not required when the portfolio selector is available.
        # Preflight may already have authenticated HOME; do not navigate to the
        # same SPA twice because each Meta navigation may take tens of seconds.
        if not already_on_home:
            await self._goto(self.HOME_URL)

        if await self._form_ready():
            return True

        # Live profile-4 evidence showed Meta redirecting /reg/ back to
        # latest/home?asset_id=<PageID>. Once we are already pinned to a Page
        # that ReMask knows belongs to this profile, repeatedly navigating
        # ROOT -> /reg/ -> HOME only burns the whole BUSINESS timeout and lands
        # back in the same state. Handle that state once, with bounded selector
        # probes, and fail fast with diagnostics if the Create surface is not
        # reachable.
        current_asset = ""

        def read_current_asset() -> str:
            try:
                current_query = parse_qs(urlsplit(_clean(self.page.url)).query)
                return _digits(
                    (
                        current_query.get("asset_id")
                        or current_query.get("assetId")
                        or [""]
                    )[0]
                )
            except Exception:
                return ""

        current_asset = read_current_asset()

        # Meta Business Suite may add ?asset_id=<PageID> a moment after the
        # initial DOM becomes usable. If we classify the route too early we
        # fall into the generic menu scanner, which used to consume the whole
        # outer CREATE timeout. Give the SPA a short bounded chance to expose
        # its final Page context before choosing the routing branch.
        if open_form and already_on_home and not current_asset:
            for _ in range(8):
                await self.page.wait_for_timeout(200)
                current_asset = read_current_asset()
                if current_asset:
                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "late_asset_context": {
                            "asset_id": current_asset,
                            "url": _clean(self.page.url),
                        },
                    }
                    break

        known_page_ids = {
            _digits(row.get("id"))
            for row in (getattr(self.context, "pages", None) or [])
            if isinstance(row, dict) and _digits(row.get("id"))
        }
        pinned_known_asset = bool(
            open_form
            and current_asset
            and current_asset in known_page_ids
        )

        if pinned_known_asset:
            surface_state = await self._quick_surface_state()
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "asset_context_fast_path": {
                    "asset_id": current_asset,
                    "url": _clean(self.page.url),
                    "surface": surface_state,
                },
            }
            skip_home_selectors = bool(surface_state.get("blank"))

            targeted_open = False
            if not skip_home_selectors:
                try:
                    targeted_open = await asyncio.wait_for(
                        self._try_open_known_asset_selector(),
                        timeout=4.0,
                    )
                except asyncio.TimeoutError:
                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "asset_selector_timeout": 4,
                    }

            if targeted_open:
                if await self._click_named(self.CREATE_NAMES):
                    await self._assert_authenticated()
                    if await self._wait_for_form_ready(
                        timeout_ms=6500,
                        interval_ms=250,
                    ):
                        return True

            generic_open = False
            if not skip_home_selectors:
                try:
                    generic_open = await asyncio.wait_for(
                        self._try_open_top_left_portfolio_menu(
                            skip_known_asset=True,
                        ),
                        timeout=4.0,
                    )
                except asyncio.TimeoutError:
                    self._last_selector_diagnostic = {
                        **self._last_selector_diagnostic,
                        "asset_generic_selector_timeout": 4,
                    }

            if generic_open:
                if await self._click_named(self.CREATE_NAMES):
                    await self._assert_authenticated()
                    if await self._wait_for_form_ready(
                        timeout_ms=6500,
                        interval_ms=250,
                    ):
                        return True

            # Current Meta also exposes a direct /create route. It is distinct
            # from the legacy /reg/ route that was observed redirecting this
            # Page-pinned profile back to Home.
            direct_create_open = False
            try:
                direct_create_open = await asyncio.wait_for(
                    self._try_open_direct_create_url(),
                    timeout=12.0,
                )
            except asyncio.TimeoutError:
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "direct_create_outer_timeout": 12,
                }
            if direct_create_open:
                return True

            overview_open = False
            try:
                overview_open = await asyncio.wait_for(
                    self._try_open_overview_create_entry(),
                    timeout=14.0,
                )
            except asyncio.TimeoutError:
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "overview_outer_timeout": 14,
                }
            if overview_open:
                return True

            # Business Suite rendered no usable selector and the direct route
            # did not expose the form. Try Ads Manager as an independent UI
            # surface before declaring this profile unable to reach CREATE.
            ads_manager_open = False
            try:
                ads_manager_open = await asyncio.wait_for(
                    self._try_open_ads_manager_create_entry(),
                    timeout=40.0,
                )
            except asyncio.TimeoutError:
                self._last_selector_diagnostic = {
                    **self._last_selector_diagnostic,
                    "ads_manager_fallback_timeout": 40,
                }

            if ads_manager_open:
                return True

            # Do not navigate to ROOT or /reg/ from the same known Page
            # context: Meta already proved that /reg/ redirects back here.
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "asset_context_fast_fail": True,
            }
            return False

        menu_open = False
        try:
            menu_open = await asyncio.wait_for(
                self._try_open_top_left_portfolio_menu(),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "generic_portfolio_menu_timeout": 5,
                "url": _clean(self.page.url if self.page else ""),
            }

        if menu_open:
            if not open_form:
                return True

            if await self._click_named(self.CREATE_NAMES):
                await self._assert_authenticated()
                if await self._wait_for_form_ready():
                    return True

        # The SPA can finish its Page redirect while the bounded generic probe
        # is running. Re-enter once through the known-asset branch instead of
        # continuing through the long generic fallback chain.
        late_asset = read_current_asset()
        if (
            open_form
            and late_asset
            and late_asset in known_page_ids
            and not pinned_known_asset
        ):
            self._last_selector_diagnostic = {
                **self._last_selector_diagnostic,
                "late_asset_reclassify": {
                    "asset_id": late_asset,
                    "url": _clean(self.page.url),
                },
            }
            return await self._open_create_entry(
                open_form=True,
                already_on_home=True,
            )

        # Try Meta's current direct Business Portfolio creation route before
        # falling back to older Business Suite navigation surfaces.
        if await self._try_open_direct_create_url():
            return True

        # Accounts without an existing Business Portfolio can land on a
        # Page/personal-profile Business Suite shell whose selector sits higher
        # than the normal business selector. On an actual CREATE request, probe
        # the root Business Suite entry as a second UI surface before /reg/.
        # This is navigation-only and does not submit a mutation.
        if open_form:
            try:
                await self._goto(self.ROOT_URL)
            except BrowserBusinessError as exc:
                if exc.code != "FACEBOOK_NAVIGATION_FAILED":
                    raise
            else:
                if await self._form_ready():
                    return True
                if await self._try_open_top_left_portfolio_menu():
                    if await self._click_named(self.CREATE_NAMES):
                        await self._assert_authenticated()
                        if await self._wait_for_form_ready():
                            return True

        # Direct registration remains a normal fallback. The older live
        # canary reached the real Meta create form through /reg/ even when the
        # HOME selector was mounted/ambiguous. _goto() now tolerates Meta SPA
        # ERR_ABORTED redirects, so this path is safe to retry as navigation.
        try:
            await self._goto(self.CREATE_URL)
        except BrowserBusinessError as exc:
            if exc.code != "FACEBOOK_NAVIGATION_FAILED":
                raise

        if await self._form_ready():
            return True

        if await self._try_open_top_left_portfolio_menu():
            if not open_form:
                return True
            if await self._click_named(self.CREATE_NAMES):
                await self._assert_authenticated()
                if await self._wait_for_form_ready():
                    return True

        # /overview is much heavier and has crashed Railway Chromium. Keep it
        # diagnostic-only rather than part of normal Add BM execution.
        legacy_fallback = _clean(
            os.getenv("REMASK_BM_LEGACY_NAV_FALLBACK")
        ).lower() in {"1", "true", "yes", "on"}
        if legacy_fallback:
            try:
                await self._goto(self.OVERVIEW_URL)
            except BrowserBusinessError as exc:
                if exc.code != "FACEBOOK_NAVIGATION_FAILED":
                    raise
            else:
                if await self._form_ready():
                    return True
                if await self._try_open_top_left_portfolio_menu():
                    if not open_form:
                        return True
                    if await self._click_named(self.CREATE_NAMES):
                        await self._assert_authenticated()
                        if await self._wait_for_form_ready():
                            return True

        return False

    async def _fill_fan_page_category(self, category: str) -> bool:
        if self.page is None or not _clean(category):
            return False

        labels = (
            "Category",
            "Categories",
            "Категория",
            "Категорія",
            "Kategorie",
            "Catégorie",
            "বিভাগ",
            "Danh mục",
            "श्रेणी",
        )
        field = None

        for label in labels:
            pattern = re.compile(re.escape(label), re.IGNORECASE)
            for getter in (
                lambda: self.page.get_by_label(pattern),
                lambda: self.page.get_by_placeholder(pattern),
            ):
                try:
                    locator = getter()
                    for index in range(min(await locator.count(), 8)):
                        candidate = locator.nth(index)
                        if await candidate.is_visible() and await candidate.is_editable():
                            field = candidate
                            break
                except Exception:
                    continue
                if field is not None:
                    break
            if field is not None:
                break

        if field is None:
            try:
                inputs = self.page.locator(
                    'input:visible, [role="combobox"]:visible, [contenteditable="true"]:visible'
                )
                for index in range(min(await inputs.count(), 24)):
                    candidate = inputs.nth(index)
                    if not await candidate.is_visible():
                        continue
                    key = " ".join(
                        _clean(await candidate.get_attribute(attr))
                        for attr in ("name", "id", "placeholder", "aria-label", "role")
                    ).casefold()
                    if any(token.casefold() in key for token in labels):
                        field = candidate
                        break
            except Exception:
                field = None

        if field is None:
            return False

        try:
            await field.fill(_clean(category))
        except Exception:
            try:
                await field.click()
                await field.press("Control+A")
                await field.type(_clean(category), delay=15)
            except Exception:
                return False

        await self.page.wait_for_timeout(700)

        try:
            options = self.page.get_by_role("option")
            count = min(await options.count(), 20)
            exact = None
            fallback = None
            for index in range(count):
                option = options.nth(index)
                if not await option.is_visible():
                    continue
                if fallback is None:
                    fallback = option
                text = _clean(await option.inner_text())
                if _clean(category).casefold() in text.casefold():
                    exact = option
                    break
            chosen = exact or fallback
            if chosen is not None:
                await chosen.click()
                await self.page.wait_for_timeout(350)
                return True
        except Exception:
            pass

        try:
            await field.press("ArrowDown")
            await field.press("Enter")
            await self.page.wait_for_timeout(350)
            return True
        except Exception:
            return False

    async def _fan_page_snapshot(self) -> list[dict[str, Any]]:
        try:
            return await self.discover_managed_pages(fast=True)
        except BrowserBusinessError as exc:
            if exc.code == "FAN_PAGES_NOT_DISCOVERED":
                return []
            raise

    async def create_fan_page(
        self,
        *,
        page_name: str,
        category: str,
        bio: str = "",
        before_submit: CheckpointCallback | None = None,
    ) -> dict[str, Any]:
        """Create one Facebook Page through Meta's own profile-bound UI."""
        name = _clean(page_name)
        category_name = _clean(category)
        if not name:
            raise BrowserBusinessError(
                "FAN_PAGE_NAME_REQUIRED",
                "Fan Page name is required.",
                retryable=False,
            )
        if not category_name:
            raise BrowserBusinessError(
                "FAN_PAGE_CATEGORY_REQUIRED",
                "Fan Page category is required.",
                retryable=False,
            )

        before_pages = await self._fan_page_snapshot()
        before_ids = {
            _digits(row.get("id"))
            for row in before_pages
            if isinstance(row, dict) and _digits(row.get("id"))
        }

        existing = [
            row
            for row in before_pages
            if isinstance(row, dict)
            and _clean(row.get("name")).casefold() == name.casefold()
            and not _clean(row.get("business_id"))
            and _digits(row.get("id"))
        ]
        if len(existing) == 1:
            page_id = _digits(existing[0].get("id"))
            return {
                "page_id": page_id,
                "name": name,
                "category": category_name,
                "reused": True,
                "before_ids": sorted(before_ids),
                "after_ids": sorted(before_ids),
                "transport": "facebook_pages_inventory_exact_name",
            }

        opened = False
        form_diagnostics: list[dict[str, Any]] = []

        for target in self.FAN_PAGE_CREATE_URLS:
            try:
                await self._goto(target)
                name_filled = await self._fill_first(
                    labels=(
                        "Page name",
                        "Page Name",
                        "Название Страницы",
                        "Название страницы",
                        "Назва сторінки",
                        "Seitenname",
                        "Nom de la Page",
                        "Nom de la page",
                        "পেজের নাম",
                        "Tên Trang",
                        "Tên trang",
                        "पेज का नाम",
                    ),
                    value=name,
                    fill_timeout_ms=3500,
                )
                category_filled = await self._fill_fan_page_category(category_name)
                form_diagnostics.append(
                    {
                        "target": target,
                        "url": _clean(getattr(self.page, "url", "")),
                        "name_filled": name_filled,
                        "category_filled": category_filled,
                    }
                )
                if name_filled and category_filled:
                    opened = True
                    break
            except BrowserBusinessError:
                raise
            except Exception as exc:
                form_diagnostics.append(
                    {
                        "target": target,
                        "error": f"{exc.__class__.__name__}: {_clean(exc)}"[:500],
                    }
                )

        if not opened:
            diag = await self._diagnostic("fan_page_create_form_unavailable")
            diag["form_attempts"] = form_diagnostics[-6:]
            raise BrowserBusinessError(
                "FAN_PAGE_CREATE_UI_CHANGED",
                "Facebook Page creation form did not expose usable name/category fields.",
                retryable=True,
                diagnostic=diag,
            )

        clean_bio = _clean(bio)
        if clean_bio:
            await self._fill_first(
                labels=(
                    "Bio",
                    "Description",
                    "Биография",
                    "Описание",
                    "Біографія",
                    "Опис",
                    "Beschreibung",
                    "Giới thiệu",
                    "Tiểu sử",
                    "बायो",
                ),
                value=clean_bio[:255],
                fill_timeout_ms=2500,
            )

        await self.page.wait_for_timeout(500)

        async def mark_submit_intent() -> None:
            if before_submit is not None:
                await before_submit(
                    {
                        "phase": "PAGE_CREATE_CLICK_INTENT",
                        "page_name": name,
                        "category": category_name,
                        "before_ids": sorted(before_ids),
                        "current_url": _clean(getattr(self.page, "url", "")),
                    }
                )

        click_meta = await self._click_named_single_attempt(
            self.FAN_PAGE_CREATE_NAMES,
            roles=("button",),
            before_click=mark_submit_intent,
            click_timeout_ms=5000,
        )

        if not bool(click_meta.get("found")):
            diag = await self._diagnostic("fan_page_create_submit_missing")
            diag["click_meta"] = click_meta
            raise BrowserBusinessError(
                "FAN_PAGE_CREATE_UI_CHANGED",
                "Facebook Page creation form was filled but Create Page was not found.",
                retryable=True,
                diagnostic=diag,
            )

        if click_meta.get("error"):
            raise BrowserBusinessError(
                "FAN_PAGE_CREATE_RESULT_UNKNOWN",
                (
                    "Create Page click was attempted, but Playwright lost a "
                    "definitive final state. Reconciliation is required before "
                    "another CREATE. " + _clean(click_meta.get("error"))
                ),
                retryable=True,
                diagnostic={
                    "stage": "fan_page_final_click_unknown",
                    "click_meta": click_meta,
                    "before_ids": sorted(before_ids),
                    "page_name": name,
                },
            )

        try:
            await self.page.wait_for_timeout(1400)
        except Exception:
            pass

        try:
            body = (await self._body_text()).casefold()
        except Exception:
            body = ""
        reject_markers = (
            "couldn't create page",
            "could not create page",
            "unable to create page",
            "не удалось создать страницу",
            "не вдалося створити сторінку",
            "page creation failed",
        )
        if any(marker in body for marker in reject_markers):
            diag = await self._diagnostic("fan_page_create_rejected")
            raise BrowserBusinessError(
                "FAN_PAGE_CREATE_REJECTED",
                "Facebook explicitly rejected Page creation.",
                retryable=False,
                diagnostic=diag,
            )

        successful_inventory_reads = 0
        last_pages: list[dict[str, Any]] = []
        for attempt in range(4):
            try:
                after_pages = await self.discover_managed_pages(fast=True)
                successful_inventory_reads += 1
                last_pages = after_pages
            except BrowserBusinessError as exc:
                if exc.code != "FAN_PAGES_NOT_DISCOVERED" and attempt >= 3:
                    raise
                after_pages = []

            after_ids = {
                _digits(row.get("id"))
                for row in after_pages
                if isinstance(row, dict) and _digits(row.get("id"))
            }
            new_ids = sorted(after_ids - before_ids)
            exact_new = [
                row
                for row in after_pages
                if isinstance(row, dict)
                and _digits(row.get("id")) in new_ids
                and _clean(row.get("name")).casefold() == name.casefold()
            ]

            if len(exact_new) == 1:
                page_id = _digits(exact_new[0].get("id"))
                return {
                    "page_id": page_id,
                    "name": name,
                    "category": category_name,
                    "reused": False,
                    "before_ids": sorted(before_ids),
                    "after_ids": sorted(after_ids),
                    "transport": "facebook_pages_ui_inventory_diff",
                }

            if len(new_ids) == 1:
                return {
                    "page_id": new_ids[0],
                    "name": name,
                    "category": category_name,
                    "reused": False,
                    "before_ids": sorted(before_ids),
                    "after_ids": sorted(after_ids),
                    "transport": "facebook_pages_ui_inventory_unique_diff",
                }

            if attempt < 3:
                await asyncio.sleep(1.5 + (0.5 * attempt))

        raise BrowserBusinessError(
            "FAN_PAGE_CREATE_RESULT_UNKNOWN",
            (
                "Create Page was clicked, but the managed-Page inventory did "
                "not expose one definitive new Page ID. Reconciliation is "
                "required before another CREATE."
            ),
            retryable=True,
            diagnostic={
                "stage": "fan_page_inventory_unconfirmed",
                "page_name": name,
                "before_ids": sorted(before_ids),
                "successful_inventory_reads": successful_inventory_reads,
                "last_ids": sorted(
                    _digits(row.get("id"))
                    for row in last_pages
                    if isinstance(row, dict) and _digits(row.get("id"))
                ),
            },
        )

    async def discover_managed_pages(
        self,
        *,
        fast: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Discover Fan Pages from the authenticated browser session.

        This is read-only and is used when the PHP/profile cache has no Pages.
        It deliberately inspects Meta's rendered Pages surfaces instead of
        issuing a reconstructed LIST_PAGES mutation.
        """
        from .facebook_page_discovery import _extract_pages_from_browser_document

        surfaces = (
            "https://www.facebook.com/pages/?category=your_pages",
        ) if fast else (
            "https://www.facebook.com/pages/?category=your_pages",
            "https://www.facebook.com/pages/?category=your_pages&ref=bookmarks",
            "https://www.facebook.com/pages/",
        )
        merged: dict[str, dict[str, Any]] = {}
        diagnostics: list[str] = []

        for url in surfaces:
            try:
                await self._goto(url)
                await self.page.wait_for_timeout(1200)
                document = await self.page.content()
            except BrowserBusinessError:
                raise
            except Exception as exc:
                diagnostics.append(
                    f"{url}: {exc.__class__.__name__}: {exc}"
                )
                continue

            pages = _extract_pages_from_browser_document(document)

            # Current Facebook "Your Pages" surfaces may render Page cards as
            # normal anchors without a parseable Page JSON object. Collect
            # those visible links too; this is read-only DOM inspection.
            link_rows: list[dict[str, str]] = []
            try:
                link_rows = await self.page.locator(
                    'main a[href], [role="main"] a[href], a[href]'
                ).evaluate_all(
                    """els => els.slice(0, 2500).map(el => ({
                        href: el.href || '',
                        text: (
                            el.innerText
                            || el.getAttribute('aria-label')
                            || el.getAttribute('title')
                            || ''
                        ).replace(/\\s+/g, ' ').trim()
                    })).filter(row => row.href && row.text)"""
                )
            except Exception as exc:
                diagnostics.append(
                    f"{url}: anchor scan {exc.__class__.__name__}"
                )

            link_pages: list[dict[str, Any]] = []
            seen_link_ids: set[str] = set()
            link_patterns = (
                re.compile(r"[?&](?:page_id|id)=(\d{5,25})(?:&|$)", re.IGNORECASE),
                re.compile(r"/pages/(?:[^/?#]+/)?(\d{5,25})(?:[/?#]|$)", re.IGNORECASE),
            )
            for link_row in link_rows:
                if not isinstance(link_row, dict):
                    continue
                href = _clean(link_row.get("href"))
                name = _clean(link_row.get("text"))
                if not href or not name:
                    continue
                page_id = ""
                for pattern in link_patterns:
                    match = pattern.search(href)
                    if match:
                        page_id = _digits(match.group(1))
                        if page_id:
                            break
                if not page_id or page_id in seen_link_ids:
                    continue

                # Do not treat generic Facebook navigation/profile anchors as
                # Pages unless this is a Pages surface or the URL itself says
                # /pages/. All scanned URLs here are explicit Your Pages/Page
                # surfaces, so this condition remains intentionally narrow.
                if "/pages/" not in href.lower() and "category=your_pages" not in url.lower():
                    continue

                seen_link_ids.add(page_id)
                link_pages.append({
                    "id": page_id,
                    "name": name[:240],
                    "category": "",
                    "source": "browser_dom_link",
                })

            if link_pages:
                pages.extend(link_pages)

            diagnostics.append(
                f"{url}: bytes={len(document)} "
                f"json_pages={len(_extract_pages_from_browser_document(document))} "
                f"link_pages={len(link_pages)} merged_candidates={len(pages)}"
            )
            for row in pages:
                if not isinstance(row, dict):
                    continue
                page_id = _digits(row.get("id"))
                if not page_id:
                    continue
                current = merged.get(page_id)
                if current is None:
                    merged[page_id] = dict(row)
                    continue
                for key, value in row.items():
                    if key not in current or current.get(key) in ("", None, [], {}):
                        current[key] = value

            if merged:
                break

        if not merged:
            diag = await self._diagnostic("browser_pages_empty")
            diag["page_discovery"] = diagnostics[-8:]
            raise BrowserBusinessError(
                "FAN_PAGES_NOT_DISCOVERED",
                "Authenticated Facebook browser surfaces returned no parseable Fan Pages.",
                retryable=False,
                diagnostic=diag,
            )

        return sorted(
            merged.values(),
            key=lambda row: _clean(row.get("name")).casefold(),
        )

    async def preflight(self) -> BrowserPreflightResult:
        diagnostics: list[str] = []

        await self._goto(self.HOME_URL)
        diagnostics.append("home_authenticated")

        ready = await self._open_create_entry(
            open_form=False,
            already_on_home=True,
        )
        if ready:
            diagnostics.append("portfolio_create_action_visible")

        if not ready:
            diag = await self._diagnostic("create_surface_missing")
            if self._last_selector_diagnostic:
                diag = {
                    "selector_attempt": self._last_selector_diagnostic,
                    **diag,
                }
            raise BrowserBusinessError(
                "BUSINESS_CREATE_UI_UNAVAILABLE",
                "Meta Business portfolio create action is not available for this profile.",
                retryable=True,
                diagnostic=diag,
            )

        account_id = _digits(
            getattr(self.context, "cookies", {}).get("c_user")
            if isinstance(getattr(self.context, "cookies", {}), dict)
            else ""
        )

        return BrowserPreflightResult(
            ready=True,
            current_url=_clean(self.page.url),
            create_surface_ready=True,
            account_id=account_id,
            diagnostics=diagnostics,
        )

    async def preflight_create_form(self) -> dict[str, Any]:
        """
        Open Meta's real Create business portfolio form without filling or
        submitting it. This safe canary performs no irreversible mutation.
        """
        if not await self._open_create_entry(open_form=True):
            diag = await self._diagnostic("create_form_preflight_failed")
            raise BrowserBusinessError(
                "BUSINESS_CREATE_FORM_UNAVAILABLE",
                "Create business portfolio was visible, but Meta's creation form did not open.",
                retryable=False,
                diagnostic=diag,
            )

        await self._assert_authenticated()
        if not await self._form_ready():
            diag = await self._diagnostic("create_form_fields_missing")
            raise BrowserBusinessError(
                "CREATE_UI_CHANGED",
                "Meta opened Business creation but expected name/email fields were not found.",
                retryable=False,
                diagnostic=diag,
            )

        fields: list[dict[str, Any]] = []
        try:
            inputs = self.page.locator("input:visible")
            count = min(await inputs.count(), 20)
            for index in range(count):
                item = inputs.nth(index)
                fields.append({
                    "type": _clean(await item.get_attribute("type")) or "text",
                    "name": _clean(await item.get_attribute("name"))[:120],
                    "aria": _clean(await item.get_attribute("aria-label"))[:240],
                    "placeholder": _clean(await item.get_attribute("placeholder"))[:240],
                })
        except Exception:
            fields = []

        return {
            "ready": True,
            "current_url": _clean(self.page.url),
            "field_count": len(fields),
            "fields": fields,
        }

    async def preflight_fill_create_form(self) -> dict[str, Any]:
        """
        Fill the real Meta form with disposable canary values, then stop.
        Nothing is submitted and no Business is created.
        """
        canary_name = "ReMask Canary Business"
        canary_email = "remask-canary@example.com"
        await self._prepare_create_form(
            business_name=canary_name,
            user_email=canary_email,
            user_first_name="ReMask",
            user_last_name="Canary",
            profile_display_name="ReMask Canary",
        )

        values: list[str] = []
        try:
            inputs = self.page.locator("input:visible")
            count = min(await inputs.count(), 20)
            for index in range(count):
                values.append(_clean(await inputs.nth(index).input_value()))
        except Exception:
            values = []

        name_present = canary_name in values
        email_present = canary_email in values
        if not name_present or not email_present:
            raw_diag = await self._diagnostic("create_form_fill_mismatch")
            diag = {
                "name_present": name_present,
                "email_present": email_present,
                "filled_input_count": sum(1 for value in values if value),
                "visible_input_count": len(values),
                **raw_diag,
            }
            raise BrowserBusinessError(
                "CREATE_FORM_FILL_FAILED",
                "Meta form opened, but ReMask could not prove Business name and email were filled correctly.",
                retryable=False,
                diagnostic=diag,
            )

        return {
            "ready": True,
            "name_present": True,
            "email_present": True,
            "filled_input_count": sum(1 for value in values if value),
            "visible_input_count": len(values),
        }

    async def preflight_capture_create_request(self) -> dict[str, Any]:
        """
        Exercise the real final Create click while blocking every Meta POST
        before it leaves Chromium.

        This is a non-mutating canary: it proves what request Meta's frontend
        would send without allowing CREATE to reach Facebook. The route stays
        installed until the browser context is closed, preventing frontend
        retries after the first aborted request.
        """
        if self.page is None:
            await self.open()

        canary_name = "ReMask Canary Business"
        canary_email = "remask-canary@example.com"

        await self._prepare_create_form(
            business_name=canary_name,
            user_email=canary_email,
            user_first_name="ReMask",
            user_last_name="Canary",
            profile_display_name="ReMask Canary",
        )

        loop = asyncio.get_running_loop()
        captured: asyncio.Future[dict[str, Any]] = loop.create_future()
        blocked_posts: list[dict[str, Any]] = []

        def request_summary(request: Any) -> dict[str, Any]:
            raw = ""
            body_decodable = True
            try:
                raw_buffer = getattr(request, "post_data_buffer", None)
                if raw_buffer:
                    if isinstance(raw_buffer, bytes):
                        raw = raw_buffer.decode("utf-8")
                    else:
                        raw = str(raw_buffer)
                else:
                    raw = _clean(getattr(request, "post_data", ""))
            except (UnicodeDecodeError, UnicodeError):
                body_decodable = False
                raw = ""
            except Exception:
                body_decodable = False
                raw = ""

            parsed = parse_qs(raw, keep_blank_values=True) if raw else {}

            friendly = _clean(
                (parsed.get("fb_api_req_friendly_name") or [""])[0]
            )
            doc_id = _clean((parsed.get("doc_id") or [""])[0])

            variables_raw = _clean((parsed.get("variables") or [""])[0])
            variable_keys: list[str] = []
            input_keys: list[str] = []
            if variables_raw:
                try:
                    variables = json.loads(variables_raw)
                    if isinstance(variables, dict):
                        variable_keys = sorted(str(key) for key in variables)
                        raw_input = variables.get("input")
                        if isinstance(raw_input, dict):
                            input_keys = sorted(str(key) for key in raw_input)
                except (ValueError, json.JSONDecodeError):
                    pass

            return {
                "url": _clean(getattr(request, "url", "")),
                "method": _clean(getattr(request, "method", "")),
                "friendly_name": friendly,
                "doc_id": doc_id,
                "form_keys": sorted(
                    str(key)
                    for key in parsed
                    if str(key) not in {
                        "fb_dtsg",
                        "lsd",
                        "jazoest",
                    }
                ),
                "variable_keys": variable_keys,
                "input_keys": input_keys,
                "contains_canary_name": canary_name.casefold()
                in unquote_plus(raw).casefold(),
                "contains_canary_email": canary_email.casefold()
                in unquote_plus(raw).casefold(),
                "body_decodable": body_decodable,
            }

        async def block_meta_posts(route: Any, request: Any) -> None:
            try:
                method = _clean(request.method).upper()
                host = _clean(urlsplit(_clean(request.url)).hostname).lower()
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass
                return

            if method != "POST" or not (
                host == "facebook.com"
                or host.endswith(".facebook.com")
            ):
                try:
                    await route.continue_()
                except Exception:
                    pass
                return

            summary = request_summary(request)
            blocked_posts.append(summary)
            if len(blocked_posts) > 40:
                del blocked_posts[:-40]

            # During this canary no Meta POST is allowed to leave Chromium.
            # This guarantees the final Create click cannot mutate the account.
            try:
                await route.abort()
            except Exception:
                return

            if not captured.done() and (
                summary["contains_canary_name"]
                or summary["contains_canary_email"]
            ):
                captured.set_result(summary)

        await self.page.route("**/*", block_meta_posts)

        clicked = await self._click_named(self.CREATE_NAMES)
        if not clicked:
            clicked = await self._click_named(self.SUBMIT_NAMES)
        if not clicked:
            diag = await self._diagnostic("blocked_create_submit_missing")
            raise BrowserBusinessError(
                "CREATE_UI_CHANGED",
                "Safe canary could not find Meta's final Create action.",
                retryable=False,
                diagnostic=diag,
            )

        try:
            summary = await asyncio.wait_for(
                asyncio.shield(captured),
                timeout=min(12.0, float(self.timeout_seconds)),
            )
        except asyncio.TimeoutError as exc:
            diag = await self._diagnostic("blocked_create_request_missing")
            diag["blocked_post_count"] = len(blocked_posts)
            diag["blocked_posts"] = blocked_posts[-12:]
            raise BrowserBusinessError(
                "CREATE_REQUEST_NOT_OBSERVED",
                (
                    "Meta's final Create action was clicked, all Meta POSTs were "
                    "blocked, but no identifiable create request was observed."
                ),
                retryable=False,
                diagnostic=diag,
            ) from exc

        return {
            "ready": True,
            "blocked": True,
            "request": summary,
            "blocked_post_count": len(blocked_posts),
        }

    async def snapshot_businesses(self) -> dict[str, str]:
        await self._goto(self.HOME_URL)

        # The home document often contains only the currently selected
        # portfolio. Open the real top-left portfolio selector first so the
        # rendered DOM also contains the other portfolios available to this
        # Facebook profile. This makes CREATE reconciliation useful even when
        # Meta does not switch the current portfolio after creation.
        selector_opened = False
        try:
            selector_probe = await self.page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el || el === document.body || el === document.documentElement) {
                            return false;
                        }
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const label = (el) => [
                        (el.getAttribute && el.getAttribute('aria-label')) || '',
                        (el.getAttribute && el.getAttribute('title')) || '',
                        el.innerText || el.textContent || ''
                    ].join(' ').replace(/\\s+/g, ' ').trim();

                    const xs = [20, 52, 88, 124, 160, 196, 228];
                    const ys = [
                        58, 72, 86, 100, 114, 128, 142, 156,
                        170, 184, 198, 212, 226, 240, 254, 268
                    ];
                    const seen = new Set();
                    const rows = [];

                    for (const y of ys) {
                        for (const x of xs) {
                            const stack = document.elementsFromPoint(x, y) || [];
                            for (const el of stack.slice(0, 10)) {
                                if (seen.has(el) || !visible(el)) continue;
                                seen.add(el);
                                const r = el.getBoundingClientRect();
                                const text = label(el);
                                const role = (el.getAttribute && el.getAttribute('role')) || '';
                                const tabindex = (el.getAttribute && el.getAttribute('tabindex')) || '';
                                const tag = el.tagName || '';

                                if (r.x > 300 || r.y < 48 || r.y > 285) continue;
                                if (r.width < 70 || r.width > 300) continue;
                                if (r.height < 22 || r.height > 100) continue;
                                if (!text) continue;

                                rows.push({el, r, text, role, tabindex, tag});
                            }
                        }
                    }

                    const homeRows = rows.filter(row =>
                        /^(Home|Startseite|Start|Главная|Головна)$/i.test(row.text)
                    );
                    const homeY = homeRows.length
                        ? Math.min(...homeRows.map(row => row.r.y))
                        : 285;

                    const candidates = rows.filter(row =>
                        row.r.y < homeY - 2
                        && !/^Meta Business Suite$/i.test(row.text)
                        && !/^(Home|Startseite|Start|Главная|Головна)$/i.test(row.text)
                        && !/^(Create|Создать|Створити|Erstellen)$/i.test(row.text)
                    );

                    candidates.sort((a,b) => {
                        const ai = (
                            a.role === 'button' ||
                            a.tag === 'BUTTON' ||
                            a.tabindex === '0'
                        ) ? 1 : 0;
                        const bi = (
                            b.role === 'button' ||
                            b.tag === 'BUTTON' ||
                            b.tabindex === '0'
                        ) ? 1 : 0;
                        if (ai !== bi) return bi - ai;
                        // Portfolio selector normally sits directly above Home.
                        if (a.r.y !== b.r.y) return b.r.y - a.r.y;
                        return (b.r.width * b.r.height) - (a.r.width * a.r.height);
                    });

                    const best = candidates[0];
                    if (!best) {
                        return {
                            clicked:false,
                            candidates:candidates.slice(0,12).map(row => ({
                                text:row.text,
                                x:Math.round(row.r.x),
                                y:Math.round(row.r.y),
                                w:Math.round(row.r.width),
                                h:Math.round(row.r.height)
                            }))
                        };
                    }

                    best.el.click();
                    return {
                        clicked:true,
                        clickedCandidate:{
                            text:best.text,
                            x:Math.round(best.r.x),
                            y:Math.round(best.r.y),
                            w:Math.round(best.r.width),
                            h:Math.round(best.r.height)
                        }
                    };
                }"""
            )
            selector_opened = bool(
                isinstance(selector_probe, dict)
                and selector_probe.get("clicked")
            )
            if selector_opened:
                await self.page.wait_for_timeout(550)
        except Exception:
            selector_opened = False

        href_rows: list[dict[str, str]] = []
        try:
            href_rows = await self.page.locator("a[href]").evaluate_all(
                """els => els.slice(0, 3000).map(el => ({
                    href: el.href || "",
                    text: (el.innerText || el.textContent || "").trim()
                }))"""
            )
        except Exception:
            href_rows = []

        output: dict[str, str] = {}
        for row in href_rows:
            if not isinstance(row, dict):
                continue
            href = str(row.get("href") or "")
            text = _clean(row.get("text"))
            for business_id in _business_ids_from_text(href):
                if business_id not in output or (text and not output[business_id]):
                    output[business_id] = text

        try:
            content = await self.page.content()
        except Exception:
            content = ""

        for business_id in _business_ids_from_text(content):
            output.setdefault(business_id, "")

        if selector_opened:
            try:
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(120)
            except Exception:
                pass

        return output

    @staticmethod
    def _request_matches_ad_account_create(
        request: Any,
        *,
        business_id: str,
        account_name: str,
    ) -> bool:
        meta = _request_graphql_meta(request)
        if _clean(meta.get("method")).upper() != "POST":
            return False
        if "graphql" not in _clean(meta.get("url")).lower():
            return False

        doc_id = _clean(meta.get("doc_id"))
        if not doc_id.isdigit():
            return False

        decoded = _clean(meta.get("decoded_raw")).casefold()
        friendly = _clean(meta.get("friendly_name")).casefold()
        expected_name = _clean(account_name).casefold()
        business = _digits(business_id)

        operation_markers = (
            "adaccountcreate",
            "ad_account_create",
            "createadaccount",
            "create_ad_account",
            "businessadaccountcreate",
            "business_ad_account_create",
            "ad account create",
        )
        operation_match = any(
            marker in friendly or marker in decoded
            for marker in operation_markers
        )
        business_match = bool(business and business in decoded)
        name_match = bool(expected_name and expected_name in decoded)

        queryish = any(
            marker in friendly
            for marker in ("query", "search", "list", "lookup", "typeahead")
        )
        if queryish:
            return False

        # A known CREATE operation name + target Business is already strong
        # enough evidence. Meta does not guarantee that the account name stays
        # duplicated in the same encoded request envelope.
        if operation_match and business_match:
            return True

        variables = (
            meta.get("variables")
            if isinstance(meta.get("variables"), dict)
            else {}
        )

        def iter_dicts(value: Any):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from iter_dicts(child)
            elif isinstance(value, list):
                for child in value:
                    yield from iter_dicts(child)

        # Current Relay builds can split the target Business and the
        # immutable RK payload across sibling/nested variable objects, e.g.
        # {businessID: "...", adAccountData: {name,currency,timezone_id}}.
        # The older matcher required them in the same dict and therefore let a
        # real CREATE pass through unmatched. Build a conservative whole-tree
        # shape before the per-node checks.
        all_nodes = list(iter_dicts(variables))
        all_business_ids: set[str] = set()
        all_names: set[str] = set()
        all_keys: set[str] = set()
        has_existing_account_anywhere = False
        for node in all_nodes:
            all_keys.update(str(key) for key in node.keys())
            candidate_business = _digits(
                node.get("business_id")
                or node.get("businessId")
                or node.get("businessID")
                or node.get("business")
                or node.get("selectedBusinessID")
            )
            if candidate_business:
                all_business_ids.add(candidate_business)
            candidate_name = _clean(
                node.get("name")
                or node.get("account_name")
                or node.get("ad_account_name")
                or node.get("adAccountName")
            ).casefold()
            if candidate_name:
                all_names.add(candidate_name)
            for key in (
                "account_id",
                "ad_account_id",
                "adAccountId",
                "adaccount_id",
            ):
                if _clean(node.get(key)):
                    has_existing_account_anywhere = True

        cross_level_business_match = bool(
            business and business in all_business_ids
        )
        cross_level_name_match = bool(
            expected_name and expected_name in all_names
        )
        cross_level_currency = "currency" in all_keys
        cross_level_timezone = bool(
            {
                "timezone_id",
                "time_zone_id",
                "timezone",
                "time_zone",
                "timezoneId",
                "timeZoneId",
            }.intersection(all_keys)
        )
        if (
            cross_level_business_match
            and cross_level_currency
            and cross_level_timezone
            and not has_existing_account_anywhere
            and (
                cross_level_name_match
                or operation_match
                or "mutation" in friendly
            )
        ):
            return True

        for node in all_nodes:
            node_business = _digits(
                node.get("business_id")
                or node.get("businessId")
                or node.get("businessID")
                or node.get("business")
            )
            if node_business != business:
                continue

            node_name = _clean(
                node.get("name")
                or node.get("account_name")
                or node.get("ad_account_name")
            ).casefold()

            create_fields = {
                "currency",
                "timezone_id",
                "time_zone_id",
                "timezone",
                "time_zone",
                "end_advertiser",
                "media_agency",
                "partner",
            }
            present = create_fields.intersection(
                str(key) for key in node.keys()
            )

            existing_account_keys = {
                "account_id",
                "ad_account_id",
                "adAccountId",
                "adaccount_id",
            }
            has_existing_account = any(
                _clean(node.get(key))
                for key in existing_account_keys
                if key in node
            )

            # Existing behavior: exact name + multiple immutable creation
            # fields is enough for renamed Meta mutations.
            if (
                expected_name
                and node_name == expected_name
                and len(present) >= 2
                and not has_existing_account
            ):
                return True

            # Strong no-name fallback: a mutation targeting this Business with
            # both immutable account identity fields present and no existing
            # account ID. This tolerates Relay moving/omitting the name while
            # avoiding update/list/search traffic.
            immutable_pair = (
                "currency" in present
                and (
                    "timezone_id" in present
                    or "time_zone_id" in present
                    or "timezone" in present
                    or "time_zone" in present
                )
            )
            if (
                immutable_pair
                and not has_existing_account
                and node_business == business
            ):
                # Strong CREATE shape fallback. Meta operation names are not
                # stable and do not always include "Mutation"/"AdAccount".
                # A POST GraphQL request that targets this Business and carries
                # both immutable RK identity fields, with no existing account
                # id, is creation-shaped. Intermediate usage queries do not
                # carry this pair.
                return True

            # Weak single-field fallback still requires the exact requested
            # account name.
            if (
                name_match
                and node_name == expected_name
                and "mutation" in friendly
                and present
                and not has_existing_account
            ):
                return True

        return False

    @staticmethod
    def _request_is_ad_account_usage_step(request: Any) -> bool:
        """True for Meta's intermediate Add-RK usage/ownership step query.

        This request is not the CREATE mutation. Seeing it after clicking the
        visible "Create ad account" control means Meta advanced the wizard and
        ReMask must continue instead of marking CREATE as uncertain.
        """
        meta = _request_graphql_meta(request)
        friendly = _clean(meta.get("friendly_name")).casefold()
        if "query" not in friendly:
            return False
        return (
            "createadaccountusagestep" in friendly
            or "create_ad_account_usage_step" in friendly
            or "adaccountusagestep" in friendly
        )

    @staticmethod
    def _response_matches_ad_account_create(
        response: Any,
        *,
        business_id: str,
        account_name: str,
    ) -> bool:
        try:
            return FacebookBusinessBrowser._request_matches_ad_account_create(
                response.request,
                business_id=business_id,
                account_name=account_name,
            )
        except Exception:
            return False

    async def _wait_for_ad_account_settings_ready(
        self,
        *,
        business_id: str,
        timeout_seconds: float = 12.0,
    ) -> bool:
        """Wait for Meta Business Settings to finish client-side hydration."""
        business = _digits(business_id)
        if self.page is None:
            return False

        deadline = time.monotonic() + max(2.0, float(timeout_seconds))
        saw_nonempty_body = False

        while time.monotonic() < deadline:
            try:
                await self._assert_authenticated()
            except BrowserBusinessError:
                raise
            except Exception:
                pass

            body = (await self._body_text()).casefold()
            if body.strip():
                saw_nonempty_body = True

            markers = (
                "ad account",
                "advertising account",
                "реклам",
                "werbekonto",
                "compte publicitaire",
                "comptes publicitaires",
                "বিজ্ঞাপন অ্যাকাউন্ট",
                "tài khoản quảng cáo",
                "विज्ञापन खाता",
                "विज्ञापन खाते",
            )
            if any(marker in body for marker in markers):
                return True

            # Meta can render icon/button chrome before useful body text.
            # A visible Add/Create control on the expected business URL is
            # enough to treat the surface as hydrated.
            try:
                current = _clean(getattr(self.page, "url", ""))
                has_business = bool(business and business in current)
                visible_action = bool(
                    await self.page.evaluate(
                        """() => {
                            const visible = el => {
                                const r = el.getBoundingClientRect();
                                const s = getComputedStyle(el);
                                return r.width > 0 && r.height > 0
                                    && s.display !== 'none'
                                    && s.visibility !== 'hidden'
                                    && s.pointerEvents !== 'none';
                            };
                            const nodes = [...document.querySelectorAll(
                                'button,a,[role="button"],[role="menuitem"]'
                            )];
                            return nodes.some(el => {
                                if (!visible(el)) return false;
                                const t = (
                                    (el.getAttribute('aria-label') || '') + ' ' +
                                    (el.innerText || el.textContent || '')
                                ).replace(/\\s+/g, ' ').trim().toLowerCase();
                                return [
                                    'add','create','ajouter','créer',
                                    'добавить','создать','додати','створити',
                                    'hinzufügen','erstellen',
                                    'যোগ করুন','তৈরি করুন',
                                    'thêm','tạo',
                                    'जोड़ें','बनाएँ','बनाएं'
                                ].some(x => t.includes(x));
                            });
                        }"""
                    )
                )
                if has_business and visible_action:
                    return True
            except Exception:
                pass

            await self.page.wait_for_timeout(500)

        # A completely empty body after navigation is a hydration/load
        # failure, not proof that Meta changed the UI.
        return saw_nonempty_body and False

    async def _activate_ad_account_settings_section(
        self,
        *,
        business_id: str = "",
    ) -> bool:
        """Open the real Ad Accounts pane, preferring Meta's own section href."""
        if self.page is None:
            return False

        business = _digits(business_id)
        self._last_ad_account_section_diagnostic = {}

        # Meta often renders the visible localized label inside a nested span
        # while the actual SPA navigation lives on an ancestor <a> or role
        # control. Discover that real interactive node first. This avoids the
        # false-positive "click succeeded" state where clicking only the text
        # node leaves the generic Settings shell mounted.
        probe: dict[str, Any] | None = None
        try:
            raw_probe = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                    const names = [
                        'ad accounts',
                        'advertising accounts',
                        'рекламные аккаунты',
                        'рекламні акаунти',
                        'werbekonten',
                        'comptes publicitaires',
                        'বিজ্ঞাপন অ্যাকাউন্ট',
                        'বিজ্ঞাপন অ্যাকাউন্টসমূহ',
                        'tài khoản quảng cáo',
                        'विज्ञापन खाते',
                        'विज्ञापन खाता'
                    ];
                    const interactive = [
                        ...document.querySelectorAll(
                            'a[href],button,[role="link"],[role="menuitem"],'
                            + '[role="button"],[tabindex]'
                        )
                    ];
                    const rows = [];
                    for (const el of interactive) {
                        if (!visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        // The settings navigation is in Meta's left column.
                        // Keeping this bounded prevents a similarly named
                        // control in the content pane from winning.
                        if (r.x > 620 || r.y < 35 || r.y > 760) continue;

                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        const href = (
                            el.tagName === 'A'
                                ? (el.href || '')
                                : ((el.closest && el.closest('a[href]'))?.href || '')
                        );
                        const hrefKey = clean(href);
                        const hrefMatch = (
                            hrefKey.includes('/settings/ad_accounts')
                            || hrefKey.includes('/settings/ad-accounts')
                        );
                        const exactText = names.some(name => text === name);
                        const shortText = names.some(
                            name => text.includes(name)
                                && text.length <= Math.max(150, name.length + 90)
                        );
                        if (!hrefMatch && !exactText && !shortText) continue;

                        let score = Math.round(r.y);
                        if (hrefMatch) score -= 1000;
                        if (exactText) score -= 500;
                        if (el.tagName === 'A') score -= 180;
                        if ((el.getAttribute('role') || '') === 'link') score -= 120;
                        if (r.x < 360) score -= 100;

                        rows.push({
                            el,
                            href,
                            text,
                            score,
                            x: Math.round(r.x),
                            y: Math.round(r.y),
                            w: Math.round(r.width),
                            h: Math.round(r.height),
                            tag: el.tagName || '',
                            role: el.getAttribute('role') || ''
                        });
                    }
                    rows.sort((a,b) => a.score - b.score || a.y - b.y || a.x - b.x);
                    const best = rows[0];
                    if (!best) return {mode:'none', candidates:[]};

                    const compact = rows.slice(0,12).map(row => ({
                        href: row.href,
                        text: row.text,
                        x: row.x,
                        y: row.y,
                        w: row.w,
                        h: row.h,
                        tag: row.tag,
                        role: row.role,
                        score: row.score
                    }));

                    if (best.href && (
                        best.href.includes('/settings/ad_accounts')
                        || best.href.includes('/settings/ad-accounts')
                    )) {
                        return {
                            mode:'href',
                            href:best.href,
                            text:best.text,
                            x:best.x,
                            y:best.y,
                            tag:best.tag,
                            role:best.role,
                            candidates:compact
                        };
                    }

                    best.el.scrollIntoView({block:'center'});
                    best.el.click();
                    return {
                        mode:'click',
                        href:'',
                        text:best.text,
                        x:best.x,
                        y:best.y,
                        tag:best.tag,
                        role:best.role,
                        candidates:compact
                    };
                }"""
            )
            if isinstance(raw_probe, dict):
                probe = raw_probe
        except Exception as exc:
            self._last_ad_account_section_diagnostic = {
                "mode": "probe_error",
                "error": f"{exc.__class__.__name__}: {exc}"[:500],
            }

        if isinstance(probe, dict):
            mode = _clean(probe.get("mode")).lower()
            href = _clean(probe.get("href"))
            self._last_ad_account_section_diagnostic = {
                "mode": mode or "none",
                "href": href[:900],
                "text": _clean(probe.get("text"))[:240],
                "x": probe.get("x"),
                "y": probe.get("y"),
                "tag": _clean(probe.get("tag"))[:40],
                "role": _clean(probe.get("role"))[:80],
                "candidates": (
                    probe.get("candidates")[:8]
                    if isinstance(probe.get("candidates"), list)
                    else []
                ),
            }

            if mode == "href" and href:
                # Never follow a link that explicitly targets a different BM.
                href_business = ""
                try:
                    href_query = parse_qs(urlsplit(href).query)
                    href_business = _digits(
                        (href_query.get("business_id") or [""])[0]
                    )
                except Exception:
                    href_business = ""

                if business and href_business and href_business != business:
                    self._last_ad_account_section_diagnostic["href_rejected"] = (
                        "different_business"
                    )
                else:
                    try:
                        final_url = await self._goto(href)
                        await self._assert_authenticated()
                        await self.page.wait_for_timeout(700)
                        self._last_ad_account_section_diagnostic["final_url"] = (
                            _clean(final_url or self.page.url)[:900]
                        )
                        return True
                    except BrowserBusinessError as exc:
                        if exc.code in {
                            "SESSION_EXPIRED",
                            "CHECKPOINT_REQUIRED",
                            "TWO_FACTOR_REQUIRED",
                            "FACEBOOK_TEMPORARILY_BLOCKED",
                        }:
                            raise
                        self._last_ad_account_section_diagnostic[
                            "href_navigation_error"
                        ] = f"{exc.code}: {exc}"[:700]
                    except Exception as exc:
                        self._last_ad_account_section_diagnostic[
                            "href_navigation_error"
                        ] = f"{exc.__class__.__name__}: {exc}"[:700]

            if mode == "click":
                await self.page.wait_for_timeout(900)
                self._last_ad_account_section_diagnostic["final_url"] = (
                    _clean(getattr(self.page, "url", ""))[:900]
                )
                return True

        # Last fallback for variants where Playwright exposes the semantic
        # role cleanly but the bounded DOM probe above cannot see the item.
        clicked = await self._click_named(
            self.AD_ACCOUNT_SECTION_NAMES,
            roles=("link", "menuitem", "button"),
        )
        if clicked:
            await self.page.wait_for_timeout(900)
            self._last_ad_account_section_diagnostic = {
                **self._last_ad_account_section_diagnostic,
                "mode": "role_click",
                "final_url": _clean(getattr(self.page, "url", ""))[:900],
            }
            return True

        return False

    async def _click_ad_account_action_dom(
        self,
        *,
        allow_generic_add: bool = False,
    ) -> str:
        """Click Meta's current Ad Account Create/Add control by visible DOM text.

        Meta increasingly renders the Create-RK choice as a plain DIV/SPAN card
        rather than a semantic button/menuitem.  For CREATE we may safely click
        the visible text node (the browser click bubbles to React's parent
        handler), but only when the same node contains both a create verb and
        an ad-account marker in the right settings pane.
        """
        if self.page is None:
            return ""

        try:
            result = await self.page.evaluate(
                """(allowGenericAdd) => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                    const accountWords = [
                        'ad account','advertising account','реклам',
                        'werbekonto','compte publicitaire',
                        'বিজ্ঞাপন অ্যাকাউন্ট','tài khoản quảng cáo',
                        'विज्ञापन खाता','विज्ञापन खाते'
                    ];
                    const createWords = [
                        'create','new ad account',
                        'создать','створити','erstellen',
                        'créer','nouveau compte publicitaire',
                        'তৈরি করুন','নতুন বিজ্ঞাপন অ্যাকাউন্ট',
                        'tạo','tài khoản quảng cáo mới',
                        'बनाएँ','बनाएं','नया विज्ञापन खाता'
                    ];
                    const addWords = [
                        'add','ajouter','добавить','додати',
                        'hinzufügen','যোগ করুন','thêm','जोड़ें'
                    ];
                    const accountWords = [
                        'ad account','advertising account',
                        'compte publicitaire','comptes publicitaires',
                        'реклам','werbekonto','werbekonten',
                        'বিজ্ঞাপন অ্যাকাউন্ট','tài khoản quảng cáo',
                        'विज्ञापन खाता','विज्ञापन खाते'
                    ];
                    const hasLocalAccountContext = el => {
                        let cur = el;
                        for (
                            let depth = 0;
                            cur && depth < 7;
                            depth++, cur = cur.parentElement
                        ) {
                            if (!visible(cur)) continue;
                            const rr = cur.getBoundingClientRect();
                            if (rr.width > 1050 || rr.height > 520) continue;
                            const t = clean(
                                (cur.getAttribute('aria-label') || '') + ' ' +
                                (cur.getAttribute('title') || '') + ' ' +
                                (cur.innerText || cur.textContent || '')
                            ).toLowerCase();
                            if (accountWords.some(word => t.includes(word))) {
                                return true;
                            }
                        }
                        return false;
                    };
                    const metaAIRoot = el => {
                        const root = el.closest(
                            '[role="dialog"],[aria-modal="true"]'
                        );
                        if (!root) return false;
                        const headings = [...root.querySelectorAll(
                            'h1,h2,h3,[role="heading"]'
                        )].filter(visible).slice(0, 3).map(node =>
                            node.innerText || node.textContent || ''
                        ).join(' ');
                        const identity = clean(
                            (root.getAttribute('aria-label') || '') + ' ' +
                            (root.getAttribute('title') || '') + ' ' +
                            headings
                        );
                        return [
                            'meta ai',
                            'assistant business meta ai',
                            'meta ai business assistant',
                            'assistant meta ai'
                        ].some(word => identity.includes(word));
                    };
                    const nodes = [...document.querySelectorAll(
                        'button,a,span,div,h1,h2,h3,label,'
                        + '[role="button"],[role="link"],[role="menuitem"],'
                        + '[role="menuitemradio"],[role="option"],[tabindex]'
                    )];

                    const rows = [];
                    const seen = new Set();
                    for (const el of nodes) {
                        if (!visible(el) || metaAIRoot(el)) continue;
                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('placeholder') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        if (!text || text.length > 240) continue;

                        const semantic = el.closest(
                            'button,a,[role="button"],[role="link"],'
                            + '[role="menuitem"],[role="menuitemradio"],'
                            + '[role="option"],[tabindex]:not([tabindex="-1"])'
                        );
                        const clickable = semantic || el;
                        if (!visible(clickable)) continue;
                        if (
                            clickable.hasAttribute('disabled')
                            || clickable.getAttribute('aria-disabled') === 'true'
                        ) continue;

                        const r = clickable.getBoundingClientRect();
                        if (r.x < 300 || r.y < 30 || r.y > 795) continue;

                        const key = [
                            text,
                            Math.round(r.x),
                            Math.round(r.y),
                            Math.round(r.width),
                            Math.round(r.height)
                        ].join('|');
                        if (seen.has(key)) continue;
                        seen.add(key);
                        rows.push({el:clickable,text,r});
                    }

                    const direct = rows
                        .filter(row =>
                            accountWords.some(x => row.text.includes(x))
                            && createWords.some(x => row.text.includes(x))
                        )
                        .sort((a,b) => {
                            const semanticA = a.el.matches(
                                'button,a,[role="button"],[role="menuitem"],'
                                + '[role="menuitemradio"],[role="option"]'
                            ) ? 0 : 1;
                            const semanticB = b.el.matches(
                                'button,a,[role="button"],[role="menuitem"],'
                                + '[role="menuitemradio"],[role="option"]'
                            ) ? 0 : 1;
                            if (semanticA !== semanticB) {
                                return semanticA - semanticB;
                            }
                            if (a.text.length !== b.text.length) {
                                return a.text.length - b.text.length;
                            }
                            return a.r.y - b.r.y;
                        });
                    if (direct.length) {
                        direct[0].el.scrollIntoView({block: 'center'});
                        direct[0].el.click();
                        return 'create';
                    }

                    if (!allowGenericAdd) return '';

                    const generic = rows
                        .filter(row => {
                            const isInteractive = row.el.matches(
                                'button,a,[role="button"],[role="link"]'
                            );
                            if (!isInteractive || row.text.length > 96) {
                                return false;
                            }
                            if (accountWords.some(x => row.text.includes(x))) {
                                return false;
                            }
                            return addWords.some(
                                word => row.text === word
                                    || row.text.startsWith(word + ' ')
                            );
                        })
                        .sort((a,b) => {
                            const at = a.text.length - b.text.length;
                            if (at) return at;
                            return a.r.x - b.r.x;
                        });
                    if (!generic.length) return '';
                    generic[0].el.scrollIntoView({block: 'center'});
                    generic[0].el.click();
                    return 'add';
                }""",
                bool(allow_generic_add),
            )
        except Exception:
            return ""

        value = _clean(result).lower()
        return value if value in {"create", "add"} else ""

    async def _capture_ad_account_wizard_rect(self) -> dict[str, Any]:
        """Remember the full visual bounds of the active Add-RK wizard.

        Prefer a real dialog/modal root. When Meta renders the wizard as plain
        nested DIVs, prefer the smallest common ancestor that contains both the
        verified RK detail fields and a Next/Create action. This keeps the
        footer (where Meta places Suivant/Create) inside the anchor instead of
        capturing only the inner field card.
        """
        if self.page is None:
            return {}

        try:
            rect = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                    const name = [
                        'ad account name','advertising account name',
                        'nom du compte publicitaire','nom du compte',
                        'name des werbekontos','название рекламного аккаунта',
                        'назва рекламного акаунта','বিজ্ঞাপন অ্যাকাউন্টের নাম',
                        'tên tài khoản quảng cáo','विज्ञापन खाते का नाम',
                        'विज्ञापन खाता नाम'
                    ];
                    const currency = [
                        'currency','devise','währung','валюта','মুদ্রা',
                        'tiền tệ','मुद्रा'
                    ];
                    const timezone = [
                        'time zone','timezone','fuseau horaire','zeitzone',
                        'часовой пояс','часовий пояс','সময় অঞ্চল',
                        'múi giờ','समय क्षेत्र'
                    ];
                    const nextWords = [
                        'next','continue','suivant','continuer','weiter',
                        'fortfahren','далее','продолжить','далі','продовжити',
                        'পরবর্তী','চালিয়ে যান','tiếp','tiếp tục',
                        'अगला','आगे','जारी रखें'
                    ];
                    const createWords = [
                        'create','create account','create ad account',
                        'create advertising account','créer',
                        'créer le compte','créer le compte publicitaire',
                        'créer un compte publicitaire','создать',
                        'создать аккаунт','создать рекламный аккаунт',
                        'створити','створити обліковий запис',
                        'створити рекламний акаунт','erstellen',
                        'konto erstellen','werbekonto erstellen',
                        'তৈরি করুন','বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন',
                        'tạo','tạo tài khoản quảng cáo',
                        'बनाएँ','बनाएं','विज्ञापन खाता बनाएँ',
                        'विज्ञापन खाता बनाएं'
                    ];
                    const ai = [
                        'meta ai','assistant business meta ai',
                        'meta ai business assistant','assistant meta ai'
                    ];

                    const fieldShape = root => {
                        const t = clean(
                            (root.getAttribute('aria-label') || '') + ' ' +
                            (root.getAttribute('title') || '') + ' ' +
                            (root.innerText || root.textContent || '')
                        );
                        const headings = [...root.querySelectorAll(
                            'h1,h2,h3,[role="heading"]'
                        )].filter(visible).slice(0, 3).map(node =>
                            node.innerText || node.textContent || ''
                        ).join(' ');
                        const identity = clean(
                            (root.getAttribute('aria-label') || '') + ' ' +
                            (root.getAttribute('title') || '') + ' ' +
                            headings
                        );
                        if (!t || ai.some(word => identity.includes(word))) {
                            return {ok:false,text:t};
                        }
                        const hasName = name.some(word => t.includes(word));
                        const hasCurrency = currency.some(word => t.includes(word));
                        const hasTimezone = timezone.some(word => t.includes(word));
                        return {
                            ok:(
                                (hasName && (hasCurrency || hasTimezone))
                                || (hasCurrency && hasTimezone)
                            ),
                            text:t,
                            hasName,
                            hasCurrency,
                            hasTimezone
                        };
                    };

                    const roots = [];
                    const seen = new Set();
                    const addRoot = (root, priority, source, actionText='') => {
                        if (!root || !visible(root) || seen.has(root)) return;
                        const r = root.getBoundingClientRect();
                        if (
                            r.x < 250 || r.y < 20 || r.width < 220 || r.height < 120
                            || r.width > 1050 || r.height > 790
                        ) return;
                        const shape = fieldShape(root);
                        if (!shape.ok) return;
                        seen.add(root);
                        roots.push({
                            x:r.x,y:r.y,width:r.width,height:r.height,
                            area:r.width*r.height,
                            priority,
                            source,
                            action_text:actionText
                        });
                    };

                    // Best case: Meta gives us an actual modal/dialog. Always
                    // prefer the full dialog over an inner card containing only
                    // the immutable fields.
                    for (const root of document.querySelectorAll(
                        '[role="dialog"],[aria-modal="true"]'
                    )) {
                        addRoot(root, 0, 'dialog');
                    }

                    const actionNodes = [...document.querySelectorAll(
                        'button,a,[role="button"],[role="menuitem"],'
                        + '[tabindex]:not([tabindex="-1"])'
                    )].filter(el => {
                        if (!visible(el)) return false;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 40 || r.y > 795) return false;
                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        if (!text || text.length > 180) return false;
                        return nextWords.some(
                            word => text === word || text.startsWith(word + ' ')
                        ) || createWords.some(
                            word => text === word || text.startsWith(word + ' ')
                        );
                    });

                    // If no semantic dialog exists, bind the field area to the
                    // action footer by their smallest shared ancestor. This is
                    // the critical shape for Meta's current French RK wizard.
                    for (const action of actionNodes.slice(0, 20)) {
                        const actionText = clean(
                            (action.getAttribute('aria-label') || '') + ' ' +
                            (action.getAttribute('title') || '') + ' ' +
                            (action.innerText || action.textContent || '')
                        );
                        let cur = action;
                        for (
                            let depth = 0;
                            cur && depth < 12;
                            depth++, cur = cur.parentElement
                        ) {
                            addRoot(
                                cur,
                                1,
                                'fields_plus_action',
                                actionText
                            );
                        }
                    }

                    // Last-resort evidence: field-only container. Keep it so
                    // diagnostics still have an anchor even on a UI variant
                    // with no visible footer action yet.
                    const markerNodes = [...document.querySelectorAll(
                        'label,span,div,p,h1,h2,h3,input,select,[role="combobox"]'
                    )].filter(el => {
                        if (!visible(el)) return false;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 30 || r.y > 795) return false;
                        const t = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('placeholder') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        return name.some(word => t.includes(word))
                            || currency.some(word => t.includes(word))
                            || timezone.some(word => t.includes(word));
                    });

                    for (const node of markerNodes.slice(0, 30)) {
                        let cur = node;
                        for (
                            let depth = 0;
                            cur && depth < 9;
                            depth++, cur = cur.parentElement
                        ) {
                            addRoot(cur, 2, 'fields_only');
                        }
                    }

                    roots.sort((a,b) => {
                        if (a.priority !== b.priority) {
                            return a.priority - b.priority;
                        }
                        return a.area - b.area;
                    });
                    const best = roots[0];
                    if (!best) return {};
                    return {
                        x:Math.round(best.x),
                        y:Math.round(best.y),
                        width:Math.round(best.width),
                        height:Math.round(best.height),
                        source:best.source,
                        action_text:best.action_text || ''
                    };
                }"""
            )
        except Exception:
            return {}

        if not isinstance(rect, dict):
            return {}
        try:
            width = float(rect.get("width") or 0)
            height = float(rect.get("height") or 0)
            if width < 220 or height < 120:
                return {}
            return {
                "x": float(rect.get("x") or 0),
                "y": float(rect.get("y") or 0),
                "width": width,
                "height": height,
                "source": _clean(rect.get("source"))[:80],
                "action_text": _clean(rect.get("action_text"))[:180],
            }
        except (TypeError, ValueError):
            return {}

    async def _accept_ad_account_terms_if_present(self) -> dict[str, Any]:
        """Accept the RK confirmation terms only inside the verified wizard.

        Meta's current flow is Details -> Usage -> Confirm -> Done.  On Confirm
        the final Create control stays disabled until the terms checkbox is
        selected.  Never click page-global consent/checkbox controls.
        """
        if self.page is None or not self._ad_account_wizard_rect:
            return {"found": False, "clicked": False}

        try:
            result = await self.page.evaluate(
                """(anchor) => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                    const inside = el => {
                        const r = el.getBoundingClientRect();
                        const cx = r.x + r.width / 2;
                        const cy = r.y + r.height / 2;
                        const pad = 30;
                        return (
                            cx >= Number(anchor.x || 0) - pad
                            && cx <= Number(anchor.x || 0)
                                + Number(anchor.width || 0) + pad
                            && cy >= Number(anchor.y || 0) - pad
                            && cy <= Number(anchor.y || 0)
                                + Number(anchor.height || 0) + pad
                        );
                    };
                    const terms = [
                        'terms of service',
                        'advertising policies',
                        'advertising policy',
                        'meta terms',
                        'i agree',
                        'agree to',
                        'conditions d’utilisation',
                        "conditions d'utilisation",
                        'conditions de service',
                        'conditions générales',
                        'politiques publicitaires',
                        'politique publicitaire',
                        'j’accepte',
                        "j'accepte",
                        'accepte les conditions',
                        'nutzungsbedingungen',
                        'werberichtlinien',
                        'условия использования',
                        'рекламной политики',
                        'умови використання',
                        'рекламної політики',
                        'পরিষেবার শর্তাবলী',
                        'বিজ্ঞাপন নীতি',
                        'আমি সম্মত',
                        'điều khoản dịch vụ',
                        'chính sách quảng cáo',
                        'tôi đồng ý',
                        'सेवा की शर्तें',
                        'विज्ञापन नीतियाँ',
                        'विज्ञापन नीति',
                        'मैं सहमत हूँ',
                        'मैं सहमत हूं'
                    ];

                    const controls = [...document.querySelectorAll(
                        'input[type="checkbox"],[role="checkbox"],'
                        + '[aria-checked],[data-testid*="checkbox"]'
                    )].filter(el => visible(el) && inside(el));

                    const rows = [];
                    for (const control of controls) {
                        let root = control;
                        let context = '';
                        for (
                            let depth = 0;
                            root && depth < 7;
                            depth++, root = root.parentElement
                        ) {
                            if (!inside(root)) continue;
                            const t = clean(
                                (root.getAttribute('aria-label') || '') + ' ' +
                                (root.getAttribute('title') || '') + ' ' +
                                (root.innerText || root.textContent || '')
                            );
                            if (terms.some(word => t.includes(word))) {
                                context = t;
                                break;
                            }
                        }
                        if (!context) continue;

                        const checked = Boolean(
                            control.checked
                            || control.getAttribute('aria-checked') === 'true'
                            || control.getAttribute('data-state') === 'checked'
                        );
                        const r = control.getBoundingClientRect();
                        rows.push({
                            el: control,
                            checked,
                            text: context.slice(0, 240),
                            x: Math.round(r.x),
                            y: Math.round(r.y)
                        });
                    }

                    rows.sort((a,b) => a.y - b.y);
                    const best = rows[0];
                    if (!best) return {found:false,clicked:false};
                    if (best.checked) {
                        return {
                            found:true,
                            clicked:false,
                            already_checked:true,
                            text:best.text,
                            x:best.x,
                            y:best.y
                        };
                    }

                    best.el.setAttribute('data-remask-rk-terms', '1');
                    return {
                        found:true,
                        clicked:false,
                        already_checked:false,
                        text:best.text,
                        x:best.x,
                        y:best.y
                    };
                }""",
                self._ad_account_wizard_rect,
            )
        except Exception as exc:
            return {
                "found": False,
                "clicked": False,
                "error": f"{exc.__class__.__name__}: {exc}"[:500],
            }

        if not isinstance(result, dict) or not bool(result.get("found")):
            return {"found": False, "clicked": False}

        meta = {
            "found": True,
            "clicked": False,
            "already_checked": bool(result.get("already_checked")),
            "text": _clean(result.get("text"))[:240],
            "x": int(result.get("x") or 0),
            "y": int(result.get("y") or 0),
        }
        if meta["already_checked"]:
            return meta

        locator = self.page.locator('[data-remask-rk-terms="1"]').first
        try:
            await locator.click(timeout=2500)
            meta["clicked"] = True
            await self.page.wait_for_timeout(250)
            return meta
        except Exception as exc:
            meta["error"] = f"{exc.__class__.__name__}: {exc}"[:500]
            return meta
        finally:
            try:
                await self.page.locator(
                    '[data-remask-rk-terms="1"]'
                ).evaluate_all(
                    "(els) => els.forEach(el => "
                    "el.removeAttribute('data-remask-rk-terms'))"
                )
            except Exception:
                pass

    async def _click_ad_account_final_interactive(
        self,
        *,
        before_click: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Click exactly one real final CREATE control in the Add-RK surface."""
        if self.page is None:
            return {"found": False, "attempted": False, "clicked": False}

        create_words = {
            value.casefold()
            for value in (
                "Create ad account",
                "Create advertising account",
                "Create account",
                "Create",
                "Создать рекламный аккаунт",
                "Создать аккаунт",
                "Создать",
                "Створити рекламний акаунт",
                "Створити обліковий запис",
                "Створити",
                "Werbekonto erstellen",
                "Konto erstellen",
                "Erstellen",
                "Créer un compte publicitaire",
                "Créer le compte publicitaire",
                "Créer le compte",
                "Créer",
                "বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন",
                "তৈরি করুন",
                "Tạo tài khoản quảng cáo",
                "Tạo",
                "विज्ञापन खाता बनाएँ",
                "विज्ञापन खाता बनाएं",
                "बनाएँ",
                "बनाएं",
            )
        }
        candidates: list[tuple[int, Any, dict[str, Any]]] = []

        try:
            wizard_dialog_present = bool(
                await self.page.evaluate(
                    """() => {
                        const visible = el => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            return r.width > 0 && r.height > 0
                                && s.display !== 'none'
                                && s.visibility !== 'hidden';
                        };
                        const clean = text => (text || '')
                            .normalize('NFKC')
                            .replace(/\u00a0/g, ' ')
                            .replace(/\s+/g, ' ')
                            .trim()
                            .toLowerCase();
                        const wizard = [
                            'ad account name','advertising account name',
                            'nom du compte publicitaire','nom du compte',
                            'name des werbekontos',
                            'название рекламного аккаунта',
                            'назва рекламного акаунта',
                            'currency','devise','währung','валюта',
                            'time zone','timezone','fuseau horaire',
                            'zeitzone','часовой пояс','часовий пояс',
                            'my business','my business portfolio',
                            'for my business','mon entreprise',
                            'mon portefeuille business','pour mon entreprise',
                            'mein unternehmen','für mein unternehmen',
                            'мой бизнес','для моего бизнеса',
                            'мій бізнес','для мого бізнесу',
                            'আমার ব্যবসা','আমার ব্যবসার জন্য',
                            'doanh nghiệp của tôi',
                            'dành cho doanh nghiệp của tôi',
                            'मेरा व्यवसाय','मेरे व्यवसाय के लिए'
                        ];
                        const ai = [
                            'meta ai','assistant business meta ai',
                            'meta ai business assistant','assistant meta ai'
                        ];
                        return [...document.querySelectorAll(
                            '[role="dialog"],[aria-modal="true"]'
                        )].filter(visible).some(root => {
                            const t = clean(
                                (root.getAttribute('aria-label') || '') + ' ' +
                                (root.getAttribute('title') || '') + ' ' +
                                (root.innerText || root.textContent || '')
                            );
                            const headings = [...root.querySelectorAll(
                                'h1,h2,h3,[role="heading"]'
                            )].filter(visible).slice(0, 3).map(node =>
                                node.innerText || node.textContent || ''
                            ).join(' ');
                            const identity = clean(
                                (root.getAttribute('aria-label') || '') + ' ' +
                                (root.getAttribute('title') || '') + ' ' +
                                headings
                            );
                            return !ai.some(word => identity.includes(word))
                                && wizard.some(word => t.includes(word));
                        });
                    }"""
                )
            )
        except Exception:
            wizard_dialog_present = False

        try:
            locator = self.page.locator(
                'button,a,[role="button"],[role="menuitem"],'
                '[role="menuitemradio"],[tabindex]:not([tabindex="-1"])'
            )
            count = min(await locator.count(), 120)
        except Exception as exc:
            return {
                "found": False,
                "attempted": False,
                "clicked": False,
                "error": f"{exc.__class__.__name__}: {exc}"[:500],
            }

        for index in range(count):
            item = locator.nth(index)
            try:
                if not (await item.is_visible() and await item.is_enabled()):
                    continue
                box = await item.bounding_box()
                if not box:
                    continue
                x = float(box.get("x") or 0)
                y = float(box.get("y") or 0)
                if x < 280 or y < 40 or y > 795:
                    continue
                anchor = self._ad_account_wizard_rect
                in_anchor = False
                if anchor:
                    ax = float(anchor.get("x") or 0) - 36
                    ay = float(anchor.get("y") or 0) - 36
                    ar = (
                        float(anchor.get("x") or 0)
                        + float(anchor.get("width") or 0)
                        + 36
                    )
                    ab = (
                        float(anchor.get("y") or 0)
                        + float(anchor.get("height") or 0)
                        + 36
                    )
                    cx = x + float(box.get("width") or 0) / 2
                    cy = y + float(box.get("height") or 0) / 2
                    in_anchor = ax <= cx <= ar and ay <= cy <= ab
                aria_label = _clean(
                    await item.get_attribute("aria-label")
                )
                title = _clean(await item.get_attribute("title"))
                inner_text = _clean(await item.inner_text())
                labels = [
                    value
                    for value in (aria_label, title, inner_text)
                    if value
                ]
                normalized_labels = [
                    value
                    .replace("\u200b", "")
                    .replace("\u200c", "")
                    .replace("\u200d", "")
                    .replace("\ufeff", "")
                    .strip()
                    for value in labels
                ]
                matched_label = next(
                    (
                        value
                        for value in normalized_labels
                        if (
                            value.casefold() in create_words
                            or any(
                                value.casefold().startswith(word + " ")
                                for word in create_words
                                if len(word) >= 5
                            )
                        )
                    ),
                    "",
                )
                if not matched_label:
                    continue
                text = matched_label
                role = _clean(await item.get_attribute("role"))
                tag = _clean(
                    await item.evaluate("(el) => el.tagName || ''")
                ).upper()
                dialog_meta = await item.evaluate(
                    """(el) => {
                        const clean = text => (text || '')
                            .normalize('NFKC')
                            .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                            .replace(/\u00a0/g, ' ')
                            .replace(/\s+/g, ' ')
                            .trim()
                            .toLowerCase();
                        const ai = [
                            'meta ai','assistant business meta ai',
                            'meta ai business assistant','assistant meta ai'
                        ];
                        const account = [
                            'ad account','advertising account',
                            'compte publicitaire','werbekonto','реклам',
                            'বিজ্ঞাপন অ্যাকাউন্ট','tài khoản quảng cáo',
                            'विज्ञापन खाता'
                        ];
                        const name = [
                            'ad account name','advertising account name',
                            'nom du compte publicitaire','name des werbekontos',
                            'название рекламного аккаунта',
                            'назва рекламного акаунта'
                        ];
                        const currency = [
                            'currency','devise','währung','валюта','মুদ্রা',
                            'tiền tệ','मुद्रा'
                        ];
                        const timezone = [
                            'time zone','timezone','fuseau horaire','zeitzone',
                            'часовой пояс','часовий пояс','সময় অঞ্চল',
                            'múi giờ','समय क्षेत्र'
                        ];
                        const ownership = [
                            'my business','my business portfolio','for my business',
                            'mon entreprise','mon portefeuille business',
                            'pour mon entreprise','mein unternehmen',
                            'für mein unternehmen','мой бизнес','для моего бизнеса',
                            'мій бізнес','для мого бізнесу','আমার ব্যবসা',
                            'আমার ব্যবসার জন্য','doanh nghiệp của tôi',
                            'dành cho doanh nghiệp của tôi','मेरा व्यवसाय',
                            'मेरे व्यवसाय के लिए'
                        ];

                        const root = el.closest(
                            '[role="dialog"],[aria-modal="true"]'
                        );
                        let inDialog = false;
                        let inWizardDialog = false;
                        let inAI = false;
                        if (root) {
                            inDialog = true;
                            const t = clean(
                                (root.getAttribute('aria-label') || '') + ' ' +
                                (root.getAttribute('title') || '') + ' ' +
                                (root.innerText || root.textContent || '')
                            );
                            const headings = [...root.querySelectorAll(
                                'h1,h2,h3,[role="heading"]'
                            )].filter(visible).slice(0, 3).map(node =>
                                node.innerText || node.textContent || ''
                            ).join(' ');
                            const identity = clean(
                                (root.getAttribute('aria-label') || '') + ' ' +
                                (root.getAttribute('title') || '') + ' ' +
                                headings
                            );
                            inAI = ai.some(word => identity.includes(word));
                            const hasName = name.some(word => t.includes(word));
                            const hasCurrency = currency.some(word => t.includes(word));
                            const hasTimezone = timezone.some(word => t.includes(word));
                            const hasOwnership = ownership.some(word => t.includes(word));
                            const hasAccount = account.some(word => t.includes(word));
                            inWizardDialog = !inAI && (
                                (hasName && (hasCurrency || hasTimezone))
                                || (hasCurrency && hasTimezone)
                                || (hasOwnership && hasAccount)
                            );
                        }

                        let inWizardSurface = inWizardDialog;
                        if (!inWizardSurface && !inAI) {
                            let cur = el;
                            for (let depth = 0; cur && depth < 9; depth++, cur = cur.parentElement) {
                                const r = cur.getBoundingClientRect();
                                if (
                                    r.width < 180 || r.height < 80
                                    || r.width > 1050 || r.height > 780
                                ) {
                                    continue;
                                }
                                const t = clean(
                                    (cur.getAttribute('aria-label') || '') + ' ' +
                                    (cur.getAttribute('title') || '') + ' ' +
                                    (cur.innerText || cur.textContent || '')
                                );
                                if (!t) {
                                    continue;
                                }
                                const hasName = name.some(word => t.includes(word));
                                const hasCurrency = currency.some(word => t.includes(word));
                                const hasTimezone = timezone.some(word => t.includes(word));
                                const hasOwnership = ownership.some(word => t.includes(word));
                                const hasAccount = account.some(word => t.includes(word));
                                if (
                                    (hasName && (hasCurrency || hasTimezone))
                                    || (hasCurrency && hasTimezone)
                                    || (hasOwnership && hasAccount)
                                ) {
                                    inWizardSurface = true;
                                    break;
                                }
                            }
                        }

                        return {
                            in_dialog:inDialog,
                            in_wizard_dialog:inWizardDialog,
                            in_wizard_surface:inWizardSurface,
                            in_ai_dialog:inAI
                        };
                    }"""
                )
                in_dialog = bool(dialog_meta.get("in_dialog"))
                in_wizard_dialog = bool(
                    dialog_meta.get("in_wizard_dialog")
                )
                in_wizard_surface = bool(
                    dialog_meta.get("in_wizard_surface")
                )
                in_ai_dialog = bool(dialog_meta.get("in_ai_dialog"))
                if in_ai_dialog:
                    continue
                if (
                    wizard_dialog_present
                    and not in_wizard_dialog
                    and not in_anchor
                ):
                    continue
                if (
                    not wizard_dialog_present
                    and not in_wizard_surface
                    and not in_anchor
                ):
                    continue
                score = 0
                if in_anchor:
                    score -= 350
                if in_wizard_dialog:
                    score -= 500
                elif in_dialog:
                    score -= 100
                if tag == "BUTTON":
                    score -= 150
                if role == "button":
                    score -= 100
                score += int(y)
                candidates.append(
                    (
                        score,
                        item,
                        {
                            "found": True,
                            "attempted": False,
                            "clicked": False,
                            "text": text[:180],
                            "x": int(x),
                            "y": int(y),
                            "tag": tag[:40],
                            "role": role[:80],
                            "in_dialog": in_dialog,
                            "in_wizard_dialog": in_wizard_dialog,
                            "in_wizard_surface": in_wizard_surface,
                            "in_wizard_anchor": in_anchor,
                            "wizard_dialog_present": wizard_dialog_present,
                            "index": index,
                        },
                    )
                )
            except Exception:
                continue

        if not candidates and self._ad_account_wizard_rect:
            # Meta sometimes renders the final localized Create text in a plain
            # DIV/SPAN while the actual click handler lives on an ancestor.
            # The generic CREATE_ENTRY classifier can see that text, but the
            # interactive-only locator above cannot. Recover only when the
            # text and its actionable ancestor are inside the verified RK
            # wizard rectangle; never use this as a page-global Create click.
            try:
                anchored = await self.page.evaluate(
                    """(payload) => {
                        const anchor = payload.anchor || null;
                        if (!anchor) {
                            return {found:false};
                        }
                        const visible = el => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            return r.width > 0 && r.height > 0
                                && s.display !== 'none'
                                && s.visibility !== 'hidden'
                                && s.pointerEvents !== 'none';
                        };
                        const clean = text => (text || '')
                            .normalize('NFKC')
                            .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                            .replace(/\u00a0/g, ' ')
                            .replace(/\s+/g, ' ')
                            .trim()
                            .toLowerCase();
                        const createWords = [
                            'create','create account','create ad account',
                            'create advertising account',
                            'créer','créer le compte',
                            'créer le compte publicitaire',
                            'créer un compte publicitaire',
                            'создать','создать аккаунт',
                            'создать рекламный аккаунт',
                            'створити','створити обліковий запис',
                            'створити рекламний акаунт',
                            'erstellen','konto erstellen',
                            'werbekonto erstellen',
                            'তৈরি করুন','বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন',
                            'tạo','tạo tài khoản quảng cáo',
                            'बनाएँ','बनाएं','विज्ञापन खाता बनाएँ',
                            'विज्ञापन खाता बनाएं'
                        ];
                        const accountWords = [
                            'ad account','advertising account',
                            'compte publicitaire','werbekonto','реклам',
                            'বিজ্ঞাপন অ্যাকাউন্ট',
                            'tài khoản quảng cáo','विज्ञापन खाता'
                        ];
                        const ai = [
                            'meta ai','assistant business meta ai',
                            'meta ai business assistant','assistant meta ai'
                        ];
                        const insideAnchor = el => {
                            const r = el.getBoundingClientRect();
                            const cx = r.x + r.width / 2;
                            const cy = r.y + r.height / 2;
                            const pad = 24;
                            return (
                                cx >= Number(anchor.x || 0) - pad
                                && cx <= Number(anchor.x || 0)
                                    + Number(anchor.width || 0) + pad
                                && cy >= Number(anchor.y || 0) - pad
                                && cy <= Number(anchor.y || 0)
                                    + Number(anchor.height || 0) + pad
                            );
                        };

                        const rows = [];
                        for (const el of document.querySelectorAll(
                            'span,div,label,p,strong,a,button,[role],[tabindex]'
                        )) {
                            if (!visible(el) || !insideAnchor(el)) continue;
                            const text = clean(
                                (el.getAttribute('aria-label') || '') + ' ' +
                                (el.getAttribute('title') || '') + ' ' +
                                (el.innerText || el.textContent || '')
                            );
                            if (!text || text.length > 220) continue;
                            if (ai.some(word => text.includes(word))) continue;
                            const createMatch = createWords.some(
                                word => text === word
                                    || text.startsWith(word + ' ')
                            );
                            const accountMatch = accountWords.some(
                                word => text.includes(word)
                            );
                            if (!createMatch && !(accountMatch && text.includes('créer'))) {
                                continue;
                            }

                            const clickable = el.closest(
                                'button,a,[role="button"],[role="menuitem"],'
                                + '[tabindex]:not([tabindex="-1"])'
                            );
                            if (!clickable || !visible(clickable)) continue;
                            if (!insideAnchor(clickable)) continue;
                            if (
                                clickable.hasAttribute('disabled')
                                || clickable.getAttribute('aria-disabled') === 'true'
                            ) continue;

                            const r = clickable.getBoundingClientRect();
                            rows.push({
                                el: clickable,
                                text,
                                x:Math.round(r.x),
                                y:Math.round(r.y),
                                tag:clickable.tagName || '',
                                role:clickable.getAttribute('role') || '',
                                score:Math.round(r.y)
                                    - (clickable.tagName === 'BUTTON' ? 100 : 0)
                                    - (
                                        clickable.getAttribute('role') === 'button'
                                        ? 80 : 0
                                    )
                                    - (
                                        text.includes('compte publicitaire')
                                        ? 120 : 0
                                    )
                            });
                        }
                        rows.sort((a,b) => a.score - b.score);
                        const best = rows[0];
                        if (!best) return {found:false};
                        best.el.setAttribute(
                            'data-remask-rk-final-anchor',
                            '1'
                        );
                        return {
                            found:true,
                            text:best.text,
                            x:best.x,
                            y:best.y,
                            tag:best.tag,
                            role:best.role
                        };
                    }""",
                    {"anchor": self._ad_account_wizard_rect},
                )
            except Exception:
                anchored = {}

            if isinstance(anchored, dict) and bool(anchored.get("found")):
                locator = self.page.locator(
                    '[data-remask-rk-final-anchor="1"]'
                ).first
                meta = {
                    "found": True,
                    "attempted": False,
                    "clicked": False,
                    "text": _clean(anchored.get("text"))[:180],
                    "x": int(anchored.get("x") or 0),
                    "y": int(anchored.get("y") or 0),
                    "tag": _clean(anchored.get("tag"))[:40],
                    "role": _clean(anchored.get("role"))[:80],
                    "in_dialog": False,
                    "in_wizard_dialog": False,
                    "in_wizard_surface": False,
                    "in_wizard_anchor": True,
                    "wizard_dialog_present": wizard_dialog_present,
                    "anchor_recovered": True,
                }
                try:
                    if before_click is not None:
                        await before_click()
                    meta["attempted"] = True
                    await locator.click(timeout=2500)
                    meta["clicked"] = True
                    return meta
                except Exception as exc:
                    meta["error"] = (
                        f"{exc.__class__.__name__}: {exc}"
                    )[:500]
                    return meta
                finally:
                    try:
                        await self.page.locator(
                            '[data-remask-rk-final-anchor="1"]'
                        ).evaluate_all(
                            "(els) => els.forEach(el => "
                            "el.removeAttribute('data-remask-rk-final-anchor'))"
                        )
                    except Exception:
                        pass

        if not candidates:
            return {"found": False, "attempted": False, "clicked": False}

        candidates.sort(key=lambda row: row[0])
        _, item, meta = candidates[0]
        try:
            if before_click is not None:
                await before_click()
            meta["attempted"] = True
            await item.click(timeout=2500)
            meta["clicked"] = True
            return meta
        except Exception as exc:
            meta["error"] = f"{exc.__class__.__name__}: {exc}"[:500]
            return meta

    async def _click_ad_account_form_action_by_visible_text(
        self,
        action: str,
    ) -> dict[str, Any]:
        """Click Next/Create/Own-business even when Meta renders a plain DIV."""
        if self.page is None:
            return {"clicked": False, "action": _clean(action)}

        mode = _clean(action).lower()
        if mode not in {"next", "final", "own_business"}:
            return {"clicked": False, "action": mode}

        try:
            result = await self.page.evaluate(
                """(payload) => {
                    const mode = payload.mode;
                    const anchor = payload.anchor || null;
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();

                    const nextWords = [
                        'next','continue','suivant','continuer','weiter',
                        'fortfahren','далее','продолжить','далі','продовжити',
                        'পরবর্তী','চালিয়ে যান','tiếp','tiếp tục',
                        'अगला','आगे','जारी रखें'
                    ];
                    const createWords = [
                        'create','create account','create ad account',
                        'create advertising account',
                        'créer','créer le compte','créer le compte publicitaire',
                        'créer un compte publicitaire',
                        'создать','создать аккаунт','создать рекламный аккаунт',
                        'створити','створити обліковий запис',
                        'створити рекламний акаунт',
                        'erstellen','konto erstellen','werbekonto erstellen',
                        'তৈরি করুন','বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন',
                        'tạo','tạo tài khoản quảng cáo',
                        'बनाएँ','बनाएं','विज्ञापन खाता बनाएँ',
                        'विज्ञापन खाता बनाएं'
                    ];
                    const ownWords = [
                        'my business','my business portfolio','for my business',
                        'this ad account will be used for my business',
                        'ce compte publicitaire sera utilisé pour mon entreprise',
                        'ce compte publicitaire sera utilise pour mon entreprise',
                        'mon entreprise','mon portefeuille business',
                        'pour mon entreprise',
                        'mein unternehmen','für mein unternehmen',
                        'мой бизнес','для моего бизнеса',
                        'мій бізнес','для мого бізнесу',
                        'আমার ব্যবসা','আমার ব্যবসার জন্য',
                        'doanh nghiệp của tôi','dành cho doanh nghiệp của tôi',
                        'मेरा व्यवसाय','मेरे व्यवसाय के लिए'
                    ];

                    const wizardMarkers = [
                        'ad account name','advertising account name',
                        'nom du compte publicitaire','nom du compte',
                        'name des werbekontos','название рекламного аккаунта',
                        'назва рекламного акаунта','currency','devise',
                        'währung','валюта','time zone','timezone',
                        'fuseau horaire','zeitzone','часовой пояс',
                        'часовий пояс','my business','my business portfolio',
                        'for my business','mon entreprise',
                        'mon portefeuille business','pour mon entreprise',
                        'mein unternehmen','für mein unternehmen',
                        'мой бизнес','для моего бизнеса','мій бізнес',
                        'для мого бізнесу','আমার ব্যবসা',
                        'আমার ব্যবসার জন্য','doanh nghiệp của tôi',
                        'dành cho doanh nghiệp của tôi','मेरा व्यवसाय',
                        'मेरे व्यवसाय के लिए',
                        'create a new ad account','create new ad account',
                        'create ad account','créer un nouveau compte publicitaire',
                        'créer un compte publicitaire','nouveau compte publicitaire',
                        'создать новый рекламный аккаунт',
                        'создать рекламный аккаунт',
                        'створити новий рекламний акаунт',
                        'створити рекламний акаунт',
                        'neues werbekonto erstellen','werbekonto erstellen',
                        'নতুন বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন',
                        'বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন',
                        'tạo tài khoản quảng cáo mới',
                        'tạo tài khoản quảng cáo',
                        'नया विज्ञापन खाता बनाएँ',
                        'नया विज्ञापन खाता बनाएं',
                        'विज्ञापन खाता बनाएँ','विज्ञापन खाता बनाएं'
                    ];
                    const aiMarkers = [
                        'meta ai','assistant business meta ai',
                        'meta ai business assistant','assistant meta ai'
                    ];
                    const dialogRoots = [...document.querySelectorAll(
                        '[role="dialog"],[aria-modal="true"]'
                    )].filter(visible);
                    const wizardRoot = dialogRoots.find(root => {
                        const t = clean(
                            (root.getAttribute('aria-label') || '') + ' ' +
                            (root.getAttribute('title') || '') + ' ' +
                            (root.innerText || root.textContent || '')
                        );
                        return !aiMarkers.some(word => t.includes(word))
                            && wizardMarkers.some(word => t.includes(word));
                    }) || null;

                    const nodes = wizardRoot
                        ? [...wizardRoot.querySelectorAll(
                            'button,a,span,div,label,[role],[tabindex]'
                        )]
                        : [...document.querySelectorAll(
                            'button,a,span,div,label,[role],[tabindex]'
                        )];

                    const accountWords = [
                        'ad account','advertising account','compte publicitaire',
                        'werbekonto','реклам','বিজ্ঞাপন অ্যাকাউন্ট',
                        'tài khoản quảng cáo','विज्ञापन खाता'
                    ];
                    const nameMarkers = [
                        'ad account name','advertising account name',
                        'nom du compte publicitaire','name des werbekontos',
                        'название рекламного аккаунта','назва рекламного акаунта'
                    ];
                    const currencyMarkers = [
                        'currency','devise','währung','валюта','মুদ্রা',
                        'tiền tệ','मुद्रा'
                    ];
                    const timezoneMarkers = [
                        'time zone','timezone','fuseau horaire','zeitzone',
                        'часовой пояс','часовий пояс','সময় অঞ্চল',
                        'múi giờ','समय क्षेत्र'
                    ];

                    const belongsToWizardSurface = el => {
                        if (wizardRoot && wizardRoot.contains(el)) return true;
                        if (anchor) {
                            const r = el.getBoundingClientRect();
                            const cx = r.x + r.width / 2;
                            const cy = r.y + r.height / 2;
                            const pad = 36;
                            if (
                                cx >= Number(anchor.x || 0) - pad
                                && cx <= Number(anchor.x || 0)
                                    + Number(anchor.width || 0) + pad
                                && cy >= Number(anchor.y || 0) - pad
                                && cy <= Number(anchor.y || 0)
                                    + Number(anchor.height || 0) + pad
                            ) {
                                return true;
                            }
                        }
                        let cur = el;
                        for (let depth = 0; cur && depth < 9; depth++, cur = cur.parentElement) {
                            if (!visible(cur)) continue;
                            const r = cur.getBoundingClientRect();
                            if (r.width < 180 || r.height < 80) continue;
                            // Avoid accepting the whole application shell/body.
                            if (r.width > 1050 || r.height > 780) continue;
                            const t = clean(
                                (cur.getAttribute('aria-label') || '') + ' ' +
                                (cur.getAttribute('title') || '') + ' ' +
                                (cur.innerText || cur.textContent || '')
                            );
                            if (!t || aiMarkers.some(word => t.includes(word))) {
                                continue;
                            }
                            const hasAccount = accountWords.some(word => t.includes(word));
                            const hasName = nameMarkers.some(word => t.includes(word));
                            const hasCurrency = currencyMarkers.some(word => t.includes(word));
                            const hasTimezone = timezoneMarkers.some(word => t.includes(word));
                            const hasOwnership = ownWords.some(word => t.includes(word));

                            // First wizard screen: name + currency/timezone.
                            // Later wizard screen: explicit ownership + ad-account
                            // context. A normal Business Settings page does not
                            // satisfy either shape.
                            if (
                                (hasName && (hasCurrency || hasTimezone))
                                || (hasCurrency && hasTimezone)
                                || (hasOwnership && hasAccount)
                            ) {
                                return true;
                            }
                        }
                        return false;
                    };

                    const rows = [];
                    for (const el of nodes) {
                        if (!visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 40 || r.y > 795) continue;

                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        if (!text || text.length > 180) continue;

                        let match = false;
                        if (mode === 'next') {
                            match = nextWords.some(
                                word => text === word
                                    || text.startsWith(word + ' ')
                            );
                        } else if (mode === 'final') {
                            match = createWords.some(
                                word => text === word
                                    || text.startsWith(word + ' ')
                            );
                        } else {
                            match = ownWords.some(
                                word => text === word
                                    || text.startsWith(word + ' ')
                                    || text.includes(word)
                            );
                        }
                        if (!match) continue;

                        const clickableAncestor = el.closest(
                            'button,a,[role="button"],[role="radio"],'
                            + '[role="option"],[role="menuitem"],'
                            + '[role="menuitemradio"],'
                            + '[tabindex]:not([tabindex="-1"])'
                        );
                        const clickable = clickableAncestor || el;
                        if (!visible(clickable)) continue;
                        if (!belongsToWizardSurface(clickable)) continue;
                        if (
                            clickable.hasAttribute('disabled')
                            || clickable.getAttribute('aria-disabled') === 'true'
                        ) {
                            continue;
                        }

                        const cr = clickable.getBoundingClientRect();
                        const role = clickable.getAttribute('role') || '';
                        const tabindex = clickable.getAttribute('tabindex');
                        const interactive = (
                            clickable.tagName === 'BUTTON'
                            || clickable.tagName === 'A'
                            || ['button','radio','option','menuitem','menuitemradio']
                                .includes(role)
                            || (tabindex !== null && tabindex !== '-1')
                        );

                        // Final CREATE must be a real interactive control.
                        // Never treat a plain text DIV/SPAN as a successful
                        // submit click; that produces a false "clicked" state
                        // with no network mutation.
                        if (mode === 'final' && !interactive) {
                            continue;
                        }
                        let score = Math.round(cr.y);
                        if (!interactive) score += 500;
                        if (clickable !== el) score -= 80;
                        if (clickable.tagName === 'BUTTON') score -= 80;
                        if ((clickable.getAttribute('role') || '') === 'button') {
                            score -= 60;
                        }
                        if (mode === 'final' && text.includes('compte publicitaire')) {
                            score -= 120;
                        }
                        if (mode === 'own_business' && text === 'mon entreprise') {
                            score -= 120;
                        }

                        rows.push({
                            el: clickable,
                            text,
                            x: Math.round(cr.x),
                            y: Math.round(cr.y),
                            tag: clickable.tagName || '',
                            role: clickable.getAttribute('role') || '',
                            score
                        });
                    }

                    rows.sort((a,b) => a.score - b.score);
                    const best = rows[0];
                    if (!best) {
                        return {clicked:false, action:mode};
                    }

                    best.el.scrollIntoView({block:'center'});
                    best.el.click();
                    return {
                        clicked:true,
                        action:mode,
                        text:best.text,
                        x:best.x,
                        y:best.y,
                        tag:best.tag,
                        role:best.role
                    };
                }""",
                {
                    "mode": mode,
                    "anchor": self._ad_account_wizard_rect,
                },
            )
            if isinstance(result, dict):
                return {
                    "clicked": bool(result.get("clicked")),
                    "action": _clean(result.get("action")) or mode,
                    "text": _clean(result.get("text"))[:180],
                    "x": int(result.get("x") or 0),
                    "y": int(result.get("y") or 0),
                    "tag": _clean(result.get("tag"))[:40],
                    "role": _clean(result.get("role"))[:80],
                }
        except Exception as exc:
            return {
                "clicked": False,
                "action": mode,
                "error": f"{exc.__class__.__name__}: {exc}"[:300],
            }

        return {"clicked": False, "action": mode}

    async def _select_own_business_if_present(self) -> bool:
        """Select Meta's optional 'use this ad account for my business' choice."""
        names = (
            "My business",
            "My business portfolio",
            "For my business",
            "Mon entreprise",
            "Mon portefeuille business",
            "Pour mon entreprise",
            "Mein Unternehmen",
            "Für mein Unternehmen",
            "Мой бизнес",
            "Для моего бизнеса",
            "Мій бізнес",
            "Для мого бізнесу",
            "আমার ব্যবসা",
            "আমার ব্যবসার জন্য",
            "Doanh nghiệp của tôi",
            "Dành cho doanh nghiệp của tôi",
            "मेरा व्यवसाय",
            "मेरे व्यवसाय के लिए",
        )
        # Prefer the right-pane bounded semantic probe so a same-named
        # Business navigation item elsewhere in the SPA cannot be selected.
        fallback = await self._click_ad_account_form_action_by_visible_text(
            "own_business"
        )
        if fallback.get("clicked"):
            return True

        # Never fall back to a page-global "My business" click.
        # Meta Business Settings contains similarly named navigation/actions;
        # clicking one can close the RK wizard and leave us on the normal
        # Ad Accounts surface. The scoped semantic probe above is the only
        # safe ownership action.
        return False

    @staticmethod
    def _timezone_name_for_id(timezone_id: int) -> str:
        """Map common Meta timezone IDs to the names shown in the UI.

        Selection first matches the live option's numeric attributes.  These
        names are fallback tokens for Meta variants that expose only IANA text.
        """
        names = {
            1: "America/Los_Angeles",
            2: "America/Denver",
            3: "Pacific/Honolulu",
            4: "America/Anchorage",
            5: "America/Phoenix",
            6: "America/Chicago",
            7: "America/New_York",
            8: "Asia/Dubai",
            12: "Europe/Vienna",
            15: "Australia/Sydney",
            17: "Asia/Dhaka",
            18: "Europe/Brussels",
            25: "America/Sao_Paulo",
            28: "America/Vancouver",
            35: "America/Toronto",
            42: "Asia/Shanghai",
            47: "Europe/Berlin",
            53: "Africa/Cairo",
            55: "Europe/Madrid",
            56: "Europe/Helsinki",
            57: "Europe/Paris",
            58: "Europe/London",
            62: "Asia/Hong_Kong",
            66: "Asia/Jakarta",
            70: "Asia/Jerusalem",
            71: "Asia/Kolkata",
            74: "Europe/Rome",
            77: "Asia/Tokyo",
            79: "Asia/Seoul",
            86: "Africa/Casablanca",
            94: "America/Mexico_City",
            95: "Asia/Kuala_Lumpur",
            96: "Africa/Lagos",
            98: "Europe/Amsterdam",
            100: "Pacific/Auckland",
            104: "Asia/Manila",
            105: "Asia/Karachi",
            106: "Europe/Warsaw",
            110: "Europe/Lisbon",
            113: "Europe/Bucharest",
            116: "Europe/Moscow",
            126: "Asia/Riyadh",
            128: "Asia/Singapore",
            132: "Asia/Bangkok",
            134: "Europe/Istanbul",
            136: "Asia/Taipei",
            137: "Europe/Kyiv",
            140: "Asia/Ho_Chi_Minh",
            141: "Africa/Johannesburg",
            145: "Asia/Kathmandu",
            474: "UTC",
        }
        try:
            return names.get(int(timezone_id), "")
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _ad_account_choice_matches(
        text: str,
        *,
        tokens: tuple[str, ...] = (),
        numeric_id: str = "",
    ) -> bool:
        raw = _clean(text)
        folded = raw.casefold().replace("_", " ").replace("/", " ")
        numeric = _clean(numeric_id)
        if numeric:
            if raw == numeric:
                return True
            if re.search(
                rf"(?<!\\d){re.escape(numeric)}(?!\\d)",
                raw,
            ):
                return True
        for token in tokens:
            candidate = (
                _clean(token).casefold().replace("_", " ").replace("/", " ")
            )
            if not candidate:
                continue
            if len(candidate) <= 4:
                if re.search(
                    rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])",
                    folded,
                ):
                    return True
            elif candidate in folded:
                return True
        return False

    @classmethod
    def _ad_account_option_matches(
        cls,
        row: dict[str, Any],
        *,
        tokens: tuple[str, ...] = (),
        numeric_id: str = "",
    ) -> bool:
        """Match immutable RK choices without leaking numeric IDs into labels."""
        identity = _clean(row.get("identity"))
        label = _clean(row.get("label")) or _clean(row.get("text"))
        identity_match = bool(
            numeric_id
            and cls._ad_account_choice_matches(
                identity,
                numeric_id=numeric_id,
            )
        )
        label_match = cls._ad_account_choice_matches(
            label,
            tokens=tokens,
        )
        return identity_match or label_match

    async def _ad_account_visible_options(
        self,
    ) -> tuple[Any | None, list[dict[str, Any]]]:
        if self.page is None:
            return None, []
        selector = (
            '[role="option"]:visible,'
            '[role="menuitem"]:visible,'
            '[role="menuitemradio"]:visible,'
            '[role="listbox"] [tabindex]:visible,'
            '[role="menu"] [tabindex]:visible'
        )
        try:
            locator = self.page.locator(selector)
            count = min(await locator.count(), 100)
        except Exception:
            return None, []

        rows: list[dict[str, Any]] = []
        for index in range(count):
            item = locator.nth(index)
            try:
                if not await item.is_visible():
                    continue
                box = await item.bounding_box()
                if (
                    not isinstance(box, dict)
                    or float(box.get("x") or 0) < 260
                ):
                    continue
                identity_parts = []
                for attr in (
                    "value",
                    "data-value",
                    "data-id",
                    "data-key",
                    "id",
                ):
                    identity_parts.append(
                        _clean(await item.get_attribute(attr))
                    )
                label_parts = []
                for attr in ("aria-label", "title"):
                    label_parts.append(
                        _clean(await item.get_attribute(attr))
                    )
                try:
                    label_parts.append(_clean(await item.inner_text()))
                except Exception:
                    pass
                identity = " ".join(
                    part for part in identity_parts if part
                )
                label_text = " ".join(
                    part for part in label_parts if part
                )
                text = " ".join(
                    part for part in (identity, label_text) if part
                )
                if not text:
                    continue
                rows.append(
                    {
                        "index": index,
                        "text": text[:500],
                        "identity": identity[:300],
                        "label": label_text[:400],
                        "x": round(float(box.get("x") or 0)),
                        "y": round(float(box.get("y") or 0)),
                    }
                )
            except Exception:
                continue
        return locator, rows[:100]

    async def _ad_account_field_control_by_nearby_label(
        self,
        labels: tuple[str, ...],
    ) -> Any | None:
        """Find a dropdown control in the same compact field row as a label."""
        if self.page is None:
            return None

        folded_labels = [
            _clean(label).casefold()
            for label in labels
            if _clean(label)
        ]
        if not folded_labels:
            return None

        try:
            result = await self.page.evaluate(
                """(labels) => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();

                    for (const el of document.querySelectorAll(
                        '[data-remask-rk-field-probe]'
                    )) {
                        el.removeAttribute('data-remask-rk-field-probe');
                    }

                    const labelNodes = [...document.querySelectorAll(
                        'label,span,div,p,h1,h2,h3'
                    )].filter(el => {
                        if (!visible(el)) return false;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 35 || r.y > 795) return false;
                        const text = clean(el.innerText || el.textContent || '');
                        if (!text || text.length > 160) return false;
                        return labels.some(
                            label => text === label
                                || text.startsWith(label + ' ')
                                || text.includes(label)
                        );
                    });

                    const controlSelector = [
                        'select',
                        '[role="combobox"]',
                        'button[aria-haspopup]',
                        '[role="button"][aria-haspopup]',
                        'button[aria-expanded]',
                        '[role="button"][aria-expanded]',
                        '[role="button"]',
                        'button',
                        '[tabindex]:not([tabindex="-1"])'
                    ].join(',');

                    const candidates = [];
                    for (const labelNode of labelNodes) {
                        let root = labelNode;
                        for (let depth = 0; depth < 6 && root; depth++) {
                            const controls = [...root.querySelectorAll(
                                controlSelector
                            )].filter(visible);
                            if (controls.length) {
                                let acceptedAtThisDepth = 0;
                                for (const control of controls) {
                                    const r = control.getBoundingClientRect();
                                    if (r.x < 280 || r.y < 35 || r.y > 795) {
                                        continue;
                                    }
                                    const text = clean(
                                        (control.getAttribute('aria-label') || '')
                                        + ' '
                                        + (control.getAttribute('title') || '')
                                        + ' '
                                        + (control.innerText || control.textContent || '')
                                    );
                                    const lr = labelNode.getBoundingClientRect();
                                    // Plain Meta dropdowns can be role=button
                                    // without aria-haspopup. Only accept such
                                    // generic controls when they are plausibly
                                    // the value control in the same field row.
                                    const generic = !(
                                        control.tagName === 'SELECT'
                                        || control.getAttribute('role') === 'combobox'
                                        || control.hasAttribute('aria-haspopup')
                                        || control.hasAttribute('aria-expanded')
                                    );
                                    if (generic) {
                                        const sameRow = Math.abs(r.y - lr.y) <= 80;
                                        const toRight = r.x >= (lr.x - 20);
                                        const substantial = r.width >= 90 && r.height >= 24;
                                        if (!sameRow || !toRight || !substantial) {
                                            continue;
                                        }
                                    }
                                    candidates.push({
                                        el: control,
                                        x: Math.round(r.x),
                                        y: Math.round(r.y),
                                        w: Math.round(r.width),
                                        h: Math.round(r.height),
                                        label_x: Math.round(lr.x),
                                        label_y: Math.round(lr.y),
                                        text,
                                        tag: control.tagName || '',
                                        role: control.getAttribute('role') || '',
                                        generic
                                    });
                                    acceptedAtThisDepth += 1;
                                }
                                // Do not stop merely because an ancestor has
                                // some button (Meta often places help/final
                                // action controls there). Stop only when this
                                // ancestor produced a plausible field control.
                                if (acceptedAtThisDepth > 0) {
                                    break;
                                }
                            }
                            root = root.parentElement;
                        }
                    }

                    // React can render the label and value control as
                    // siblings whose nearest useful common ancestor is deeper
                    // than the bounded climb above. If no ancestry candidate
                    // survived, perform one geometry-only scan over the right
                    // form pane. This remains conservative: same visual row,
                    // substantial control, and never the final Create action.
                    if (!candidates.length) {
                        const allControls = [...document.querySelectorAll(
                            controlSelector
                        )].filter(visible);
                        const finalWords = [
                            'create','créer','создать','створити','erstellen',
                            'তৈরি করুন','tạo','बनाएँ','बनाएं'
                        ];
                        for (const labelNode of labelNodes) {
                            const lr = labelNode.getBoundingClientRect();
                            for (const control of allControls) {
                                const r = control.getBoundingClientRect();
                                if (r.x < 280 || r.y < 35 || r.y > 795) continue;
                                const text = clean(
                                    (control.getAttribute('aria-label') || '')
                                    + ' '
                                    + (control.getAttribute('title') || '')
                                    + ' '
                                    + (control.innerText || control.textContent || '')
                                );
                                if (
                                    finalWords.some(word => text === word)
                                    || text.includes('compte publicitaire')
                                    || text.includes('ad account')
                                ) {
                                    continue;
                                }
                                const sameRow = Math.abs(r.y - lr.y) <= 90;
                                const samePane = Math.abs(r.x - lr.x) <= 520;
                                const substantial = r.width >= 90 && r.height >= 24;
                                if (!sameRow || !samePane || !substantial) continue;
                                const generic = !(
                                    control.tagName === 'SELECT'
                                    || control.getAttribute('role') === 'combobox'
                                    || control.hasAttribute('aria-haspopup')
                                    || control.hasAttribute('aria-expanded')
                                );
                                candidates.push({
                                    el: control,
                                    x: Math.round(r.x),
                                    y: Math.round(r.y),
                                    w: Math.round(r.width),
                                    h: Math.round(r.height),
                                    label_x: Math.round(lr.x),
                                    label_y: Math.round(lr.y),
                                    text,
                                    tag: control.tagName || '',
                                    role: control.getAttribute('role') || '',
                                    generic,
                                    geometry_fallback: true
                                });
                            }
                        }
                    }

                    candidates.sort((a, b) => {
                        const ad = Math.abs(a.y - a.label_y);
                        const bd = Math.abs(b.y - b.label_y);
                        if (a.generic !== b.generic) {
                            return a.generic ? 1 : -1;
                        }
                        if (ad !== bd) return ad - bd;
                        if (a.tag === 'SELECT' && b.tag !== 'SELECT') return -1;
                        if (b.tag === 'SELECT' && a.tag !== 'SELECT') return 1;
                        // Value controls usually sit to the right of the label.
                        const ax = Math.abs(a.x - a.label_x);
                        const bx = Math.abs(b.x - b.label_x);
                        return bx - ax;
                    });

                    const best = candidates[0];
                    if (!best) return null;
                    best.el.setAttribute(
                        'data-remask-rk-field-probe',
                        '1'
                    );
                    return {
                        found:true,
                        x:best.x,
                        y:best.y,
                        text:best.text,
                        tag:best.tag,
                        role:best.role
                    };
                }""",
                folded_labels,
            )
        except Exception:
            return None

        if not isinstance(result, dict) or not result.get("found"):
            return None

        try:
            locator = self.page.locator(
                '[data-remask-rk-field-probe="1"]'
            )
            if await locator.count():
                item = locator.first
                if await item.is_visible():
                    return item
        except Exception:
            pass

        return None

    async def _select_ad_account_form_field(
        self,
        *,
        field_name: str,
        labels: tuple[str, ...],
        tokens: tuple[str, ...],
        numeric_id: str = "",
    ) -> dict[str, Any]:
        """Select one Meta Add-RK field without guessing an unrelated option."""
        if self.page is None:
            return {"field": field_name, "status": "no_page"}

        controls: list[Any] = []

        # Prefer native label wiring.
        for label in labels:
            pattern = re.compile(re.escape(label), re.IGNORECASE)
            try:
                locator = self.page.get_by_label(pattern)
                count = min(await locator.count(), 8)
            except Exception:
                count = 0
            for index in range(count):
                try:
                    item = locator.nth(index)
                    if await item.is_visible():
                        controls.append(item)
                except Exception:
                    continue

        # Meta often renders a plain text label next to a custom combobox.
        if not controls:
            for label in labels:
                pattern = re.compile(
                    rf"^\\s*{re.escape(label)}\\s*$",
                    re.IGNORECASE,
                )
                try:
                    label_nodes = self.page.get_by_text(pattern)
                    label_count = min(await label_nodes.count(), 8)
                except Exception:
                    label_count = 0
                for label_index in range(label_count):
                    try:
                        label_node = label_nodes.nth(label_index)
                        if not await label_node.is_visible():
                            continue
                    except Exception:
                        continue
                    for selector in (
                        'xpath=ancestor::*[.//select][1]//select',
                        'xpath=ancestor::*[.//*[@role="combobox"]][1]//*[@role="combobox"]',
                        # Never grab an arbitrary sibling button (help/info/
                        # close controls can live in the same Meta field row).
                        # A button fallback must expose dropdown semantics.
                        'xpath=ancestor::*[.//button[@aria-haspopup or @aria-expanded] or .//*[@role="button" and (@aria-haspopup or @aria-expanded)]][1]//*[self::button or @role="button"][@aria-haspopup or @aria-expanded]',
                    ):
                        try:
                            nearby = label_node.locator(selector)
                            count = min(await nearby.count(), 6)
                        except Exception:
                            count = 0
                        for index in range(count):
                            try:
                                item = nearby.nth(index)
                                if await item.is_visible():
                                    controls.append(item)
                            except Exception:
                                continue

        if not controls:
            nearby_control = (
                await self._ad_account_field_control_by_nearby_label(
                    labels
                )
            )
            if nearby_control is not None:
                controls.append(nearby_control)

        if not controls:
            return {
                "field": field_name,
                "status": "field_not_found",
            }

        control = controls[0]
        try:
            tag_name = _clean(
                await control.evaluate("(el) => el.tagName")
            ).upper()
        except Exception:
            tag_name = ""

        if tag_name == "SELECT":
            try:
                options = control.locator("option")
                count = min(await options.count(), 200)
            except Exception:
                count = 0
            preview: list[str] = []
            for index in range(count):
                option = options.nth(index)
                try:
                    parts = [
                        _clean(await option.get_attribute("value")),
                        _clean(await option.get_attribute("data-value")),
                        _clean(await option.get_attribute("data-id")),
                        _clean(await option.inner_text()),
                    ]
                    text = " ".join(part for part in parts if part)
                    if text:
                        preview.append(text[:300])
                    identity_text = " ".join(
                        part
                        for part in (
                            _clean(await option.get_attribute("value")),
                            _clean(await option.get_attribute("data-value")),
                            _clean(await option.get_attribute("data-id")),
                        )
                        if part
                    )
                    label_text = _clean(await option.inner_text())
                    if not self._ad_account_option_matches(
                        {
                            "identity": identity_text,
                            "label": label_text,
                            "text": text,
                        },
                        tokens=tokens,
                        numeric_id=numeric_id,
                    ):
                        continue
                    value = _clean(
                        await option.get_attribute("value")
                    )
                    if value:
                        await control.select_option(value=value)
                    else:
                        await control.select_option(index=index)
                    return {
                        "field": field_name,
                        "status": "selected_native",
                        "selected": text[:300],
                    }
                except Exception:
                    continue
            return {
                "field": field_name,
                "status": "native_option_not_found",
                "options": preview[:60],
            }

        current_parts = []
        for attr in (
            "aria-label",
            "title",
            "value",
            "data-value",
            "data-id",
        ):
            try:
                current_parts.append(
                    _clean(await control.get_attribute(attr))
                )
            except Exception:
                pass
        try:
            current_parts.append(_clean(await control.inner_text()))
        except Exception:
            pass
        current_text = " ".join(
            part for part in current_parts if part
        )
        current_identity = " ".join(
            part
            for part in current_parts[2:5]
            if part
        )
        current_label = " ".join(
            part
            for part in (
                current_parts[0] if len(current_parts) > 0 else "",
                current_parts[1] if len(current_parts) > 1 else "",
                current_parts[-1] if current_parts else "",
            )
            if part
        )
        if self._ad_account_option_matches(
            {
                "identity": current_identity,
                "label": current_label,
                "text": current_text,
            },
            tokens=tokens,
            numeric_id=numeric_id,
        ):
            return {
                "field": field_name,
                "status": "already_selected",
                "selected": current_text[:300],
            }

        try:
            await control.scroll_into_view_if_needed(timeout=1500)
            await control.click(timeout=2500)
        except Exception as exc:
            return {
                "field": field_name,
                "status": "open_failed",
                "error": f"{exc.__class__.__name__}: {exc}"[:400],
            }

        await self.page.wait_for_timeout(300)
        option_locator, rows = await self._ad_account_visible_options()
        if option_locator is None or not rows:
            return {
                "field": field_name,
                "status": "no_visible_options",
            }

        for row in rows:
            if not self._ad_account_option_matches(
                row,
                tokens=tokens,
                numeric_id=numeric_id,
            ):
                continue
            try:
                item = option_locator.nth(int(row["index"]))
                await item.scroll_into_view_if_needed(timeout=1500)
                await item.click(timeout=2500)
                return {
                    "field": field_name,
                    "status": "selected",
                    "selected": row.get("text", "")[:300],
                }
            except Exception as exc:
                return {
                    "field": field_name,
                    "status": "option_click_failed",
                    "selected": row.get("text", "")[:300],
                    "error": f"{exc.__class__.__name__}: {exc}"[:400],
                }

        try:
            await self.page.keyboard.press("Escape")
        except Exception:
            pass
        return {
            "field": field_name,
            "status": "option_not_found",
            "options": [
                row.get("text", "")[:300]
                for row in rows[:60]
            ],
        }

    async def _prepare_ad_account_form_fields(
        self,
        *,
        currency: str,
        timezone_id: int,
    ) -> dict[str, Any]:
        currency_labels = (
            "Currency",
            "Devise",
            "Währung",
            "Валюта",
            "মুদ্রা",
            "Tiền tệ",
            "मुद्रा",
        )
        timezone_labels = (
            "Time zone",
            "Timezone",
            "Fuseau horaire",
            "Zeitzone",
            "Часовой пояс",
            "Часовий пояс",
            "সময় অঞ্চল",
            "Múi giờ",
            "समय क्षेत्र",
        )

        requested_currency = _clean(currency).upper()
        timezone_name = self._timezone_name_for_id(
            int(timezone_id)
        )
        timezone_aliases: list[str] = []
        if timezone_name:
            timezone_aliases.extend(
                [
                    timezone_name,
                    timezone_name.replace("_", " "),
                    timezone_name.split("/")[-1].replace("_", " "),
                ]
            )
        if int(timezone_id) == 137:
            timezone_aliases.extend(
                [
                    "Europe/Kiev",
                    "Europe Kiev",
                    "Kiev",
                    "Kyiv",
                ]
            )
        timezone_tokens = tuple(
            dict.fromkeys(
                token
                for token in timezone_aliases
                if _clean(token)
            )
        )

        currency_result = await self._select_ad_account_form_field(
            field_name="currency",
            labels=currency_labels,
            tokens=(requested_currency,),
        )
        await self.page.wait_for_timeout(150)
        timezone_result = await self._select_ad_account_form_field(
            field_name="timezone",
            labels=timezone_labels,
            tokens=timezone_tokens,
            numeric_id=str(int(timezone_id)),
        )
        await self.page.wait_for_timeout(200)

        return {
            "currency": currency_result,
            "timezone": timezone_result,
            "timezone_name": timezone_name,
        }

    async def _reconcile_created_ad_account_from_ui(
        self,
        *,
        business_id: str,
        account_name: str,
    ) -> dict[str, Any]:
        """Read-only confirmation of a just-created RK from Business Settings.

        After Meta accepts the final Create click it commonly closes the wizard
        and returns to the Ad Accounts table. If our private GraphQL matcher
        missed the mutation, use the visible row/href/attributes to prove the
        account exactly once instead of forcing a duplicate retry.
        """
        if self.page is None:
            return {"confirmed": False}

        business = _digits(business_id)
        expected = _clean(account_name)
        if not business or not expected:
            return {"confirmed": False}

        try:
            rows = await self.page.evaluate(
                """(expectedName) => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const expected = clean(expectedName).toLowerCase();
                    const out = [];
                    const seen = new Set();

                    for (const el of document.querySelectorAll(
                        'a,button,[role="row"],[role="button"],div,span'
                    )) {
                        if (!visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 80 || r.y > 790) continue;

                        const own = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        if (!own || !own.toLowerCase().includes(expected)) {
                            continue;
                        }

                        let root = el;
                        let bestText = own;
                        let bestHref = '';
                        let attrs = '';
                        for (
                            let depth = 0;
                            root && depth < 7;
                            depth++, root = root.parentElement
                        ) {
                            if (!visible(root)) continue;
                            const rr = root.getBoundingClientRect();
                            if (
                                rr.x < 280 || rr.width > 1050
                                || rr.height > 320
                            ) continue;
                            const text = clean(
                                (root.getAttribute('aria-label') || '') + ' ' +
                                (root.getAttribute('title') || '') + ' ' +
                                (root.innerText || root.textContent || '')
                            );
                            if (text && text.toLowerCase().includes(expected)) {
                                bestText = text;
                            }
                            const link = root.matches?.('a[href]')
                                ? root
                                : root.querySelector?.('a[href]');
                            if (link && link.href) bestHref = link.href;

                            attrs += ' '
                                + (root.getAttribute('data-id') || '')
                                + ' ' + (root.getAttribute('data-key') || '')
                                + ' ' + (root.getAttribute('id') || '')
                                + ' ' + (root.getAttribute('href') || '');
                        }

                        const evidence = clean(
                            bestText + ' ' + bestHref + ' ' + attrs
                        );
                        const ids = [];
                        for (const match of evidence.matchAll(
                            /(?:act_)?(\d{5,30})/g
                        )) {
                            ids.push(match[1]);
                        }
                        const uniqueIds = [...new Set(ids)];
                        const key = bestText + '|' + bestHref + '|'
                            + uniqueIds.join(',');
                        if (seen.has(key)) continue;
                        seen.add(key);

                        out.push({
                            text: bestText.slice(0, 600),
                            href: bestHref.slice(0, 1000),
                            ids: uniqueIds.slice(0, 12),
                            x: Math.round(r.x),
                            y: Math.round(r.y)
                        });
                        if (out.length >= 20) break;
                    }
                    return out;
                }""",
                expected,
            )
        except Exception as exc:
            return {
                "confirmed": False,
                "error": f"{exc.__class__.__name__}: {exc}"[:500],
            }

        if not isinstance(rows, list):
            return {"confirmed": False}

        candidates: list[dict[str, Any]] = []
        for row in rows[:20]:
            if not isinstance(row, dict):
                continue
            text_value = _clean(row.get("text"))
            if expected.casefold() not in text_value.casefold():
                continue
            ids = []
            for raw_id in row.get("ids") or []:
                normalized = _normalize_ad_account_id(raw_id)
                if normalized and normalized not in ids:
                    ids.append(normalized)
            candidates.append(
                {
                    "text": text_value[:600],
                    "href": _clean(row.get("href"))[:1000],
                    "ids": ids,
                    "x": int(row.get("x") or 0),
                    "y": int(row.get("y") or 0),
                }
            )

        unique_ids = sorted(
            {
                account_id
                for row in candidates
                for account_id in row.get("ids") or []
            }
        )
        if len(unique_ids) == 1:
            return {
                "confirmed": True,
                "business_id": business,
                "ad_account_id": unique_ids[0],
                "source": "business_settings_ui",
                "candidates": candidates[:8],
            }

        return {
            "confirmed": False,
            "business_id": business,
            "source": "business_settings_ui",
            "candidates": candidates[:8],
            "unique_ids": unique_ids[:12],
        }

    async def _ad_account_submit_controls(
        self,
    ) -> list[dict[str, Any]]:
        if self.page is None:
            return []
        try:
            locator = self.page.locator(
                'button:visible,a:visible,[role="button"]:visible,'
                '[role="link"]:visible'
            )
            count = min(await locator.count(), 100)
        except Exception:
            return []

        markers = (
            "next",
            "continue",
            "create",
            "suivant",
            "continuer",
            "créer",
            "weiter",
            "fortfahren",
            "erstellen",
            "далее",
            "продолжить",
            "создать",
            "далі",
            "продовжити",
            "створити",
            "পরবর্তী",
            "চালিয়ে যান",
            "তৈরি করুন",
            "tiếp",
            "tiếp tục",
            "tạo",
            "अगला",
            "आगे",
            "जारी रखें",
            "बनाएँ",
            "बनाएं",
        )
        rows: list[dict[str, Any]] = []
        for index in range(count):
            item = locator.nth(index)
            try:
                box = await item.bounding_box()
                if (
                    not isinstance(box, dict)
                    or float(box.get("x") or 0) < 280
                ):
                    continue
                parts = [
                    _clean(await item.get_attribute("aria-label")),
                    _clean(await item.get_attribute("title")),
                ]
                try:
                    parts.append(_clean(await item.inner_text()))
                except Exception:
                    pass
                text = " ".join(part for part in parts if part)
                if not text:
                    continue
                folded = text.casefold()
                if not any(marker in folded for marker in markers):
                    continue
                disabled = False
                try:
                    disabled = (
                        not await item.is_enabled()
                        or _clean(
                            await item.get_attribute("aria-disabled")
                        ).lower()
                        == "true"
                    )
                except Exception:
                    pass
                rows.append(
                    {
                        "text": text[:250],
                        "disabled": disabled,
                        "x": round(float(box.get("x") or 0)),
                        "y": round(float(box.get("y") or 0)),
                    }
                )
            except Exception:
                continue
        return rows[:30]

    async def _ad_account_form_candidates(self) -> list[str]:
        """Compact visible inputs/selectors/buttons in the Add-RK dialog."""
        if self.page is None:
            return []
        try:
            rows = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const nodes = [
                        ...document.querySelectorAll(
                            'input,select,[role="combobox"],button,'
                            + '[role="button"],[role="radio"],[role="option"],'
                            + '[aria-disabled],[disabled]'
                        )
                    ];
                    const out = [];
                    for (const el of nodes) {
                        if (!visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 35 || r.y > 795) continue;
                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('placeholder') || '') + ' ' +
                            (el.getAttribute('name') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        const value = (
                            typeof el.value === 'string'
                                ? clean(el.value)
                                : ''
                        );
                        out.push(
                            (text || '<no-label>')
                            + (value ? ' value=' + value : '')
                            + ' [tag=' + (el.tagName || '')
                            + ' role=' + (el.getAttribute('role') || '')
                            + ' disabled='
                            + (
                                el.hasAttribute('disabled')
                                || el.getAttribute('aria-disabled') === 'true'
                            )
                            + ' x=' + Math.round(r.x)
                            + ' y=' + Math.round(r.y)
                            + ']'
                        );
                        if (out.length >= 40) break;
                    }
                    return out;
                }"""
            )
            if isinstance(rows, list):
                return [_clean(x)[:320] for x in rows if _clean(x)][:40]
        except Exception:
            pass
        return []

    async def _ad_account_label_control_probe(
        self,
    ) -> list[dict[str, Any]]:
        """Safe live map of currency/timezone labels to nearby controls."""
        if self.page is None:
            return []
        try:
            rows = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const targets = [
                        'Currency','Devise','Währung','Валюта','মুদ্রা',
                        'Tiền tệ','मुद्रा','Time zone','Timezone',
                        'Fuseau horaire','Zeitzone','Часовой пояс',
                        'Часовий пояс','সময় অঞ্চল','Múi giờ','समय क्षेत्र'
                    ].map(x => x.toLowerCase());
                    const labels = [...document.querySelectorAll(
                        'label,span,div,p,h1,h2,h3'
                    )].filter(el => {
                        if (!visible(el)) return false;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 35 || r.y > 795) return false;
                        const text = clean(el.innerText || el.textContent || '');
                        if (!text || text.length > 180) return false;
                        const low = text.toLowerCase();
                        return targets.some(
                            t => low === t || low.startsWith(t + ' ')
                                || low.includes(t)
                        );
                    });
                    const controls = [...document.querySelectorAll(
                        'input,select,button,[role="button"],[role="combobox"],'
                        + '[tabindex]:not([tabindex="-1"])'
                    )].filter(visible);

                    const out = [];
                    for (const label of labels.slice(0, 20)) {
                        const lr = label.getBoundingClientRect();
                        const nearby = [];
                        for (const control of controls) {
                            const r = control.getBoundingClientRect();
                            if (r.x < 280 || r.y < 35 || r.y > 795) continue;
                            const dy = Math.abs(r.y - lr.y);
                            const dx = Math.abs(r.x - lr.x);
                            if (dy > 140 || dx > 650) continue;
                            nearby.push({
                                text: clean(
                                    (control.getAttribute('aria-label') || '')
                                    + ' '
                                    + (control.getAttribute('title') || '')
                                    + ' '
                                    + (control.innerText || control.textContent || '')
                                ).slice(0, 180),
                                value: (
                                    typeof control.value === 'string'
                                        ? clean(control.value).slice(0, 120)
                                        : ''
                                ),
                                tag: control.tagName || '',
                                role: control.getAttribute('role') || '',
                                haspopup: control.getAttribute('aria-haspopup') || '',
                                expanded: control.getAttribute('aria-expanded') || '',
                                disabled: (
                                    control.hasAttribute('disabled')
                                    || control.getAttribute('aria-disabled') === 'true'
                                ),
                                x: Math.round(r.x),
                                y: Math.round(r.y),
                                w: Math.round(r.width),
                                h: Math.round(r.height),
                                dx: Math.round(dx),
                                dy: Math.round(dy)
                            });
                        }
                        nearby.sort((a,b) => a.dy - b.dy || a.dx - b.dx);
                        out.push({
                            label: clean(label.innerText || label.textContent || '').slice(0,180),
                            x: Math.round(lr.x),
                            y: Math.round(lr.y),
                            nearby: nearby.slice(0,12)
                        });
                    }
                    return out;
                }"""
            )
            if isinstance(rows, list):
                return [row for row in rows[:20] if isinstance(row, dict)]
        except Exception:
            pass
        return []

    async def find_ad_account_in_inventory(
        self,
        *,
        business_id: str,
        account_name: str,
        expected_ad_account_id: str = "",
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        """Read-only RK lookup from Meta's own Business Settings responses.

        This is intentionally independent from CREATE mutation names.  It
        reloads the Ad Accounts inventory and observes the GraphQL responses
        Meta uses to paint the table. It first accepts an exact-name match;
        under ReMask's 1 BM = 1 RK invariant it also accepts one unique
        structurally identified RK from a read-only query targeting the exact
        Business. Two explicit empty inventory observations are accepted as
        proof that a stale previous CREATE did not leave an RK behind.
        """
        business = _digits(business_id)
        expected = _clean(account_name)
        expected_id = _normalize_ad_account_id(expected_ad_account_id)
        if self.page is None or not business or not expected:
            return {
                "confirmed": False,
                "confirmed_empty": False,
                "reason": "invalid_input",
            }

        loop = asyncio.get_running_loop()
        found_future: asyncio.Future[dict[str, Any]] = loop.create_future()
        response_tasks: set[asyncio.Task[Any]] = set()
        diagnostics: list[dict[str, Any]] = []
        empty_observations = 0

        async def inspect_response(response: Any) -> None:
            nonlocal empty_observations
            if found_future.done():
                return
            try:
                url = _clean(getattr(response, "url", ""))
                if "graphql" not in url.casefold():
                    return
                raw = await response.text()
                payload = _decode_graphql_text(raw)

                request = getattr(response, "request", None)
                request_summary = (
                    self._safe_graphql_request_summary(request)
                    if request is not None
                    else {}
                )
                meta = (
                    _request_graphql_meta(request)
                    if request is not None
                    else {}
                )

                exact_name_ids = _extract_named_ad_account_ids(
                    payload,
                    expected,
                )
                inventory_ids = _extract_inventory_ad_account_ids(payload)
                inventory_observed = _has_ad_account_inventory_container(
                    payload
                )

                target_business_ids = {
                    candidate
                    for candidate, _ in _walk_business_ids(
                        meta.get("variables") or {}
                    )
                    if candidate
                }
                target_business_ids.update(
                    _business_ids_from_text(
                        _clean(meta.get("decoded_raw"))
                    )
                )
                targets_business = business in target_business_ids

                # Some current Meta Relay inventory queries inherit the
                # selected Business from the Business Settings route and no
                # longer repeat business_id inside GraphQL variables. The
                # exact ad-accounts route is still authoritative read-only
                # context, so accept it as a second way to bind the inventory
                # payload to the requested Business.
                page_url = _clean(getattr(self.page, "url", ""))
                page_targets_business = (
                    business in _business_ids_from_text(page_url)
                    and (
                        "/settings/ad_accounts" in page_url.casefold()
                        or "/settings/ad-accounts" in page_url.casefold()
                    )
                )
                exact_business_context = bool(
                    targets_business or page_targets_business
                )

                friendly = _clean(
                    meta.get("friendly_name")
                ).casefold()
                mutation_like = (
                    "mutation" in friendly
                    or "create" in friendly
                    or "update" in friendly
                    or "delete" in friendly
                )

                row = {
                    "exact_name_ids": exact_name_ids[:8],
                    "inventory_ids": inventory_ids[:12],
                    "inventory_observed": inventory_observed,
                    "targets_business": targets_business,
                    "page_targets_business": page_targets_business,
                    "exact_business_context": exact_business_context,
                    "friendly_name": _clean(
                        meta.get("friendly_name")
                    )[:180],
                    "expected_ad_account_id": expected_id,
                    "request": request_summary,
                }

                if exact_business_context and (
                    exact_name_ids
                    or (
                        not mutation_like
                        and (inventory_ids or inventory_observed)
                    )
                ):
                    diagnostics.append(row)

                if (
                    exact_business_context
                    and expected_id
                    and expected_id in set(exact_name_ids + inventory_ids)
                    and not found_future.done()
                ):
                    found_future.set_result(
                        {
                            "confirmed": True,
                            "confirmed_empty": False,
                            "business_id": business,
                            "ad_account_id": expected_id,
                            "account_name": expected,
                            "source": (
                                "business_settings_graphql_inventory_expected_id"
                            ),
                            "evidence": row,
                        }
                    )
                    return

                if (
                    exact_business_context
                    and not expected_id
                    and len(exact_name_ids) == 1
                    and not found_future.done()
                ):
                    found_future.set_result(
                        {
                            "confirmed": True,
                            "confirmed_empty": False,
                            "business_id": business,
                            "ad_account_id": exact_name_ids[0],
                            "account_name": expected,
                            "source": (
                                "business_settings_graphql_inventory_name"
                            ),
                            "evidence": row,
                        }
                    )
                    return

                if (
                    exact_business_context
                    and not expected_id
                    and not mutation_like
                    and len(inventory_ids) == 1
                    and not found_future.done()
                ):
                    found_future.set_result(
                        {
                            "confirmed": True,
                            "confirmed_empty": False,
                            "business_id": business,
                            "ad_account_id": inventory_ids[0],
                            "account_name": expected,
                            "source": (
                                "business_settings_graphql_inventory_unique"
                            ),
                            "evidence": row,
                        }
                    )
                    return

                if (
                    exact_business_context
                    and not mutation_like
                    and inventory_observed
                    and not inventory_ids
                ):
                    empty_observations += 1

            except Exception as exc:
                diagnostics.append(
                    {
                        "error": (
                            f"{exc.__class__.__name__}: {_clean(exc)}"
                        )[:500]
                    }
                )

        def on_response(response: Any) -> None:
            if found_future.done():
                return
            try:
                task = asyncio.create_task(inspect_response(response))
                response_tasks.add(task)
                task.add_done_callback(response_tasks.discard)
            except Exception:
                return

        self.page.on("response", on_response)
        attempts: list[dict[str, Any]] = []
        try:
            targets = [
                template.format(business_id=business)
                for template in self.SETTINGS_AD_ACCOUNTS_URLS
            ]
            current = _clean(getattr(self.page, "url", ""))
            if (
                "/settings/ad_accounts" in current
                or "/settings/ad-accounts" in current
            ):
                targets.insert(0, current)

            deadline = time.monotonic() + max(
                2.0,
                float(timeout_seconds),
            )
            seen_targets: set[str] = set()
            for target in targets:
                if target in seen_targets:
                    continue
                seen_targets.add(target)
                if time.monotonic() >= deadline:
                    break
                try:
                    empty_before = empty_observations
                    await self._goto(target)
                    attempts.append(
                        {
                            "url": _clean(self.page.url)[:700],
                            "result": "loaded",
                        }
                    )

                    # Response observers are scheduled as tasks. Give them one
                    # event-loop turn before deciding whether this navigation
                    # already produced an authoritative inventory observation.
                    await asyncio.sleep(0)
                    if found_future.done():
                        result = found_future.result()
                        result["attempts"] = attempts
                        return result

                    if empty_observations > empty_before:
                        # Do not burn the whole timeout on a page that already
                        # proved an explicit empty Ad Accounts collection.
                        continue

                    remaining = max(
                        0.2,
                        min(3.0, deadline - time.monotonic()),
                    )
                    try:
                        result = await asyncio.wait_for(
                            asyncio.shield(found_future),
                            timeout=remaining,
                        )
                        result["attempts"] = attempts
                        return result
                    except asyncio.TimeoutError:
                        pass
                except Exception as exc:
                    attempts.append(
                        {
                            "url": target[:700],
                            "result": "error",
                            "error": (
                                f"{exc.__class__.__name__}: {_clean(exc)}"
                            )[:500],
                        }
                    )

            if not found_future.done():
                # One final bounded hydration window for late Relay responses.
                try:
                    remaining = max(
                        0.1,
                        min(2.0, deadline - time.monotonic()),
                    )
                    if remaining > 0.1:
                        result = await asyncio.wait_for(
                            asyncio.shield(found_future),
                            timeout=remaining,
                        )
                        result["attempts"] = attempts
                        return result
                except asyncio.TimeoutError:
                    pass

            # Every counted observation is already constrained to:
            # exact requested Business ID + read-only GraphQL request +
            # structurally recognized ad-account inventory container + zero
            # RK IDs. One such snapshot is authoritative for a pre-submit
            # inventory check. Ambiguous post-submit paths still require
            # repeated/independent evidence in ad_account_handler.
            confirmed_empty = empty_observations >= 1
            return {
                "confirmed": False,
                "confirmed_empty": confirmed_empty,
                "business_id": business,
                "account_name": expected,
                "source": "business_settings_graphql_inventory",
                "empty_observations": empty_observations,
                "attempts": attempts,
                "diagnostics": diagnostics[-12:],
            }
        finally:
            try:
                self.page.remove_listener("response", on_response)
            except Exception:
                pass
            if not found_future.done():
                found_future.cancel()
            if response_tasks:
                await asyncio.gather(
                    *list(response_tasks),
                    return_exceptions=True,
                )

    async def verify_ad_account_inventory_empty(
        self,
        *,
        business_id: str,
    ) -> dict[str, Any]:
        """Read-only proof that Meta Business Settings currently shows zero RK."""
        business = _digits(business_id)
        if not business:
            return {
                "confirmed_empty": False,
                "reason": "invalid_business_id",
            }

        empty_markers = (
            "aucun compte publicitaire ajouté",
            "no ad accounts added",
            "no advertising accounts added",
            "нет добавленных рекламных аккаунтов",
            "рекламних акаунтів не додано",
            "keine werbekonten hinzugefügt",
            "কোনো বিজ্ঞাপন অ্যাকাউন্ট যোগ করা হয়নি",
            "কোনও বিজ্ঞাপন অ্যাকাউন্ট যোগ করা হয়নি",
            "chưa thêm tài khoản quảng cáo nào",
            "không có tài khoản quảng cáo nào được thêm",
            "कोई विज्ञापन खाता नहीं जोड़ा गया",
            "कोई विज्ञापन खाते नहीं जोड़े गए",
            "no ad accounts",
            "you haven't added any ad accounts",
            "you have not added any ad accounts",
            "aucun compte publicitaire",
            "no se han añadido cuentas publicitarias",
            "no hay cuentas publicitarias añadidas",
            "nenhuma conta de anúncios adicionada",
            "nenhuma conta publicitária adicionada",
            "belum ada akun iklan yang ditambahkan",
            "nie dodano kont reklamowych",
            "reklam hesabı eklenmedi",
        )

        attempts: list[dict[str, Any]] = []
        targets = [
            template.format(business_id=business)
            for template in self.SETTINGS_AD_ACCOUNTS_URLS
        ]
        current = _clean(getattr(self.page, "url", ""))
        if (
            business in _business_ids_from_text(current)
            and (
                "/settings/ad_accounts" in current.casefold()
                or "/settings/ad-accounts" in current.casefold()
            )
        ):
            targets.insert(0, current)

        seen_targets: set[str] = set()
        for target in targets:
            if target in seen_targets:
                continue
            seen_targets.add(target)
            try:
                await self._goto(target)
                await self.page.wait_for_timeout(1200)
                state = await self._ad_account_ui_state()
                body = _clean(await self._body_text()).casefold()
                signature = _clean(state.get("signature")).casefold()
                controls = " ".join(
                    _clean(x).casefold()
                    for x in (state.get("controls") or [])
                )
                dialogs = " ".join(
                    _clean(x).casefold()
                    for x in (state.get("dialogs") or [])
                )
                combined = " ".join((body, signature, controls, dialogs))

                matched_marker = next(
                    (
                        marker
                        for marker in empty_markers
                        if marker in combined
                    ),
                    "",
                )
                attempts.append(
                    {
                        "url": _clean(state.get("url") or target)[:700],
                        "state": _clean(state.get("state")).upper(),
                        "empty_marker": matched_marker,
                    }
                )
                if matched_marker:
                    return {
                        "confirmed_empty": True,
                        "business_id": business,
                        "source": "business_settings_ui",
                        "marker": matched_marker,
                        "attempts": attempts,
                    }
            except Exception as exc:
                attempts.append(
                    {
                        "url": target[:700],
                        "error": (
                            f"{exc.__class__.__name__}: {_clean(exc)}"
                        )[:500],
                    }
                )

        return {
            "confirmed_empty": False,
            "business_id": business,
            "source": "business_settings_ui",
            "attempts": attempts,
        }

    async def _ad_account_ui_state(self) -> dict[str, Any]:
        """Classify the current Meta Add-RK UI instead of assuming one DOM shape."""
        if self.page is None:
            return {"state": "NO_PAGE", "signature": ""}

        try:
            raw = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const lower = text => clean(text).toLowerCase();

                    const createWords = [
                        'create','créer','создать','створити','erstellen',
                        'তৈরি করুন','tạo','बनाएँ','बनाएं'
                    ];
                    const accountWords = [
                        'ad account','advertising account','compte publicitaire',
                        'реклам','werbekonto','বিজ্ঞাপন অ্যাকাউন্ট',
                        'tài khoản quảng cáo','विज्ञापन खाता','विज्ञापन खाते'
                    ];
                    const nameWords = [
                        'ad account name','advertising account name','account name',
                        'nom du compte publicitaire','nom du compte',
                        'название рекламного аккаунта','название аккаунта',
                        'назва рекламного акаунта','назва облікового запису',
                        'name des werbekontos','বিজ্ঞাপন অ্যাকাউন্টের নাম',
                        'tên tài khoản quảng cáo','विज्ञापन खाते का नाम',
                        'विज्ञापन खाता नाम'
                    ];
                    const formWords = [
                        'currency','devise','währung','валюта','валюта',
                        'time zone','timezone','fuseau horaire','zeitzone',
                        'часовой пояс','часовий пояс',
                        'মুদ্রা','সময় অঞ্চল',
                        'tiền tệ','múi giờ',
                        'मुद्रा','समय क्षेत्र'
                    ];
                    const errorWords = [
                        'not allowed','not eligible','cannot create',"can't create",
                        'unable to create','restricted','restriction',
                        'maximum number','reached the maximum','limit reached',
                        'permission','permissions','vérifier','verification',
                        'non autorisé','pas autorisé','impossible de créer',
                        'limite','restreint','restriction',
                        'অনুমতি নেই','যোগ্য নয়','তৈরি করা যাবে না',
                        'সীমা','সীমাবদ্ধ',
                        'không được phép','không đủ điều kiện',
                        'không thể tạo','giới hạn','bị hạn chế',
                        'अनुमति नहीं','पात्र नहीं','नहीं बना सकते',
                        'सीमा','प्रतिबंधित'
                    ];

                    const metaAIRoot = el => {
                        const root = el.closest(
                            '[role="dialog"],[aria-modal="true"]'
                        );
                        if (!root) return false;
                        const headings = [...root.querySelectorAll(
                            'h1,h2,h3,[role="heading"]'
                        )].filter(visible).slice(0, 3).map(node =>
                            node.innerText || node.textContent || ''
                        ).join(' ');
                        const identity = lower(
                            (root.getAttribute('aria-label') || '') + ' ' +
                            (root.getAttribute('title') || '') + ' ' +
                            headings
                        );
                        return [
                            'meta ai',
                            'assistant business meta ai',
                            'meta ai business assistant',
                            'assistant meta ai'
                        ].some(word => identity.includes(word));
                    };

                    const rightNodes = [...document.querySelectorAll(
                        'button,a,input,select,[role],[aria-label],[title],'
                        + '[tabindex],h1,h2,h3,label'
                    )].filter(el => {
                        if (!visible(el)) return false;
                        const r = el.getBoundingClientRect();
                        return r.x >= 280 && r.y >= 35 && r.y <= 795;
                    });

                    const controls = [];
                    const seen = new Set();
                    let createEntry = false;
                    let createTarget = null;
                    let nameInput = false;
                    let formEvidence = false;
                    let editableFormControl = false;
                    let addSurface = false;

                    for (const el of rightNodes) {
                        const r = el.getBoundingClientRect();
                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('placeholder') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.getAttribute('name') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        const low = text.toLowerCase();

                        const role = (
                            el.getAttribute('role') || ''
                        ).toLowerCase();
                        const isEditable = (
                            el.tagName === 'INPUT'
                            || el.tagName === 'TEXTAREA'
                            || el.tagName === 'SELECT'
                            || role === 'combobox'
                            || role === 'textbox'
                            || role === 'spinbutton'
                        );
                        if (
                            (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')
                            && nameWords.some(word => low.includes(word))
                        ) {
                            nameInput = true;
                        }
                        // Text-only column headings such as "Nom du compte"
                        // exist on the normal Ad Accounts table. They must not
                        // make the state-machine believe the creation form is
                        // already open. Require a real editable control here;
                        // dialog-level form detection below handles unlabeled
                        // Meta inputs.
                        if (
                            isEditable
                            && (
                                nameWords.some(word => low.includes(word))
                                || formWords.some(word => low.includes(word))
                            )
                        ) {
                            editableFormControl = true;
                            formEvidence = true;
                        }
                        if (
                            ['add','ajouter','добавить','додати','hinzufügen',
                             'যোগ করুন','thêm','जोड़ें'].some(
                                word => low === word || low.startsWith(word + ' ')
                            )
                        ) {
                            addSurface = true;
                        }

                        if (!text || text.length > 220) continue;
                        const row = text
                            + ' [tag=' + (el.tagName || '')
                            + ' role=' + (el.getAttribute('role') || '')
                            + ' x=' + Math.round(r.x)
                            + ' y=' + Math.round(r.y)
                            + ']';
                        if (seen.has(row)) continue;
                        seen.add(row);
                        controls.push(row);
                        if (controls.length >= 45) break;
                    }

                    // CREATE_ENTRY must be backed by a concrete click target.
                    // The old classifier could set CREATE_ENTRY from an entire
                    // dialog whose long text merely contained the localized
                    // Create Ad Account wording, while later clickers discarded
                    // that same container as too broad. Scan deep text nodes,
                    // choose the most specific visible target, and tag it.
                    for (const el of document.querySelectorAll(
                        'button,a,span,div,p,strong,label,[role],[tabindex]'
                    )) {
                        el.removeAttribute('data-remask-rk-create-state');
                        el.removeAttribute('data-remask-rk-create-leaf');
                    }
                    const createTargets = [];
                    for (const el of document.querySelectorAll(
                        'button,a,span,div,p,strong,label,[role],[tabindex]'
                    )) {
                        if (!visible(el) || metaAIRoot(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 35 || r.y > 795) continue;

                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        const low = text.toLowerCase();
                        if (!text || text.length > 1400) continue;
                        if (!createWords.some(word => low.includes(word))) continue;
                        if (!accountWords.some(word => low.includes(word))) continue;

                        const semantic = el.closest(
                            'button,a,[role="button"],[role="link"],'
                            + '[role="menuitem"],[role="menuitemradio"],'
                            + '[role="option"],[tabindex]:not([tabindex="-1"])'
                        );
                        const clickable = semantic || el;
                        if (!visible(clickable)) continue;
                        if (
                            clickable.hasAttribute('disabled')
                            || clickable.getAttribute('aria-disabled') === 'true'
                        ) continue;

                        const insideDialog = !!el.closest(
                            '[role="dialog"],[aria-modal="true"]'
                        );
                        const clickableRole = (
                            clickable.getAttribute('role') || ''
                        ).toLowerCase();
                        const clickableTag = (
                            clickable.tagName || ''
                        ).toUpperCase();
                        const clickableStyle = getComputedStyle(clickable);
                        const explicitlyInteractive = !!semantic
                            || clickableTag === 'BUTTON'
                            || clickableTag === 'A'
                            || [
                                'button','link','menuitem','menuitemradio',
                                'option','radio'
                            ].includes(clickableRole)
                            || clickable.hasAttribute('onclick')
                            || clickableStyle.cursor === 'pointer';

                        // Once Meta opens the modal, its title can itself be
                        // "Create ad account". A plain heading DIV/SPAN inside
                        // the modal is not the CREATE_ENTRY action.
                        if (insideDialog && !explicitlyInteractive) continue;

                        const cr = clickable.getBoundingClientRect();
                        if (
                            cr.x < 280 || cr.y < 35 || cr.y > 795
                            || cr.width < 24 || cr.height < 16
                        ) continue;

                        const role = (
                            clickable.getAttribute('role') || ''
                        ).toLowerCase();
                        const tag = (clickable.tagName || '').toUpperCase();
                        const exact = [
                            'create a new ad account',
                            'create new ad account',
                            'create ad account',
                            'créer un nouveau compte publicitaire',
                            'créer un compte publicitaire',
                            'nouveau compte publicitaire',
                            'создать новый рекламный аккаунт',
                            'создать рекламный аккаунт',
                            'створити новий рекламний акаунт',
                            'створити рекламний акаунт',
                            'neues werbekonto erstellen',
                            'werbekonto erstellen',
                            'নতুন বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন',
                            'বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন',
                            'tạo tài khoản quảng cáo mới',
                            'tạo tài khoản quảng cáo',
                            'नया विज्ञापन खाता बनाएँ',
                            'नया विज्ञापन खाता बनाएं',
                            'विज्ञापन खाता बनाएँ',
                            'विज्ञापन खाता बनाएं'
                        ].some(value => low === value);

                        const area = cr.width * cr.height;
                        let score = 0;
                        if (exact) score -= 3000;
                        if (semantic) score -= 900;
                        if (tag === 'BUTTON' || tag === 'A') score -= 500;
                        if (
                            role === 'button'
                            || role === 'menuitem'
                            || role === 'menuitemradio'
                            || role === 'option'
                        ) score -= 450;
                        score += Math.min(text.length, 1400) * 2;
                        score += Math.min(area / 120, 3500);
                        score += Math.round(cr.y / 8);

                        const sourceRect = el.getBoundingClientRect();
                        createTargets.push({
                            el:clickable,
                            source:el,
                            text,
                            x:Math.round(cr.x),
                            y:Math.round(cr.y),
                            w:Math.round(cr.width),
                            h:Math.round(cr.height),
                            source_x:Math.round(sourceRect.x),
                            source_y:Math.round(sourceRect.y),
                            source_w:Math.round(sourceRect.width),
                            source_h:Math.round(sourceRect.height),
                            source_tag:(el.tagName || '').toUpperCase(),
                            tag,
                            role,
                            // Prefer the deepest/smallest text node for the
                            // physical click. React often puts the actual
                            // handler on an ancestor, and a browser click on
                            // the leaf bubbles to it reliably.
                            score: score + Math.min(
                                (sourceRect.width * sourceRect.height) / 200,
                                900
                            )
                        });
                    }
                    createTargets.sort((a,b) => a.score - b.score);
                    if (createTargets.length) {
                        const best = createTargets[0];
                        best.el.setAttribute('data-remask-rk-create-state', '1');
                        if (best.source && best.source.isConnected) {
                            best.source.setAttribute(
                                'data-remask-rk-create-leaf',
                                '1'
                            );
                        }
                        createEntry = true;
                        createTarget = {
                            text:best.text.slice(0, 500),
                            x:best.x,
                            y:best.y,
                            w:best.w,
                            h:best.h,
                            source_x:best.source_x,
                            source_y:best.source_y,
                            source_w:best.source_w,
                            source_h:best.source_h,
                            source_tag:best.source_tag,
                            tag:best.tag,
                            role:best.role,
                            score:Math.round(best.score)
                        };
                    }

                    // Form can be open even when Meta omitted a useful label
                    // from the actual <input>. Dialog-level evidence is enough.
                    const dialogs = [...document.querySelectorAll(
                        '[role="dialog"],[aria-modal="true"]'
                    )].filter(visible);
                    const dialogTexts = dialogs
                        .map(el => clean(el.innerText || el.textContent || ''))
                        .filter(Boolean)
                        .slice(0, 6);
                    const dialogCombined = dialogTexts.join(' ').toLowerCase();
                    const visibleDialogFormControls = dialogs.flatMap(
                        dialog => [...dialog.querySelectorAll(
                            'input,textarea,select,[role="combobox"],'
                            + '[role="textbox"],[role="spinbutton"]'
                        )].filter(visible)
                    );
                    const dialogFormControlCount =
                        visibleDialogFormControls.length;
                    const dialogHasFormControl =
                        dialogFormControlCount > 0;
                    const dialogLooksLikeMetaAI = dialogs.some(dialog => {
                        const headings = [...dialog.querySelectorAll(
                            'h1,h2,h3,[role="heading"]'
                        )].filter(visible).slice(0, 3).map(node =>
                            node.innerText || node.textContent || ''
                        ).join(' ');
                        const identity = lower(
                            (dialog.getAttribute('aria-label') || '') + ' ' +
                            (dialog.getAttribute('title') || '') + ' ' +
                            headings
                        );
                        return [
                            'meta ai',
                            'assistant business meta ai',
                            'meta ai business assistant',
                            'assistant meta ai'
                        ].some(word => identity.includes(word));
                    });
                    // A generic "ad account" mention is not enough here.
                    // Meta AI opens its own dialog with a textbox and can
                    // mention the current settings surface, which previously
                    // made us classify the assistant as the Add-RK wizard.
                    // Require wizard-specific fields/ownership evidence from
                    // the same dialog and explicitly reject the AI assistant.
                    const dialogHasWizardMarker = (
                        nameWords.some(word => dialogCombined.includes(word))
                        || formWords.some(word => dialogCombined.includes(word))
                        || [
                            'my business','my business portfolio',
                            'for my business','mon entreprise',
                            'mon portefeuille business','pour mon entreprise',
                            'mein unternehmen','für mein unternehmen',
                            'мой бизнес','для моего бизнеса',
                            'мій бізнес','для мого бізнесу',
                            'আমার ব্যবসা','আমার ব্যবসার জন্য',
                            'doanh nghiệp của tôi',
                            'dành cho doanh nghiệp của tôi',
                            'मेरा व्यवसाय','मेरे व्यवसाय के लिए'
                        ].some(word => dialogCombined.includes(word))
                    );
                    const dialogHasCreateAccountMarker = (
                        createWords.some(word => dialogCombined.includes(word))
                        && accountWords.some(word => dialogCombined.includes(word))
                    );

                    if (
                        !dialogLooksLikeMetaAI
                        && dialogHasFormControl
                        && (
                            dialogHasWizardMarker
                            || dialogHasCreateAccountMarker
                        )
                    ) {
                        formEvidence = true;
                    }

                    const introDialog = (
                        !dialogLooksLikeMetaAI
                        && dialogHasCreateAccountMarker
                        && !formEvidence
                    );

                    // Only treat an error as blocking when it is surfaced in an
                    // alert/toast/dialog, not merely present in hidden app text.
                    const errorSurfaces = [...document.querySelectorAll(
                        '[role="alert"],[role="alertdialog"],[aria-live="assertive"],'
                        + '[aria-live="polite"],[role="dialog"]'
                    )].filter(visible);
                    const errors = [];
                    for (const el of errorSurfaces) {
                        const text = clean(el.innerText || el.textContent || '');
                        const low = text.toLowerCase();
                        if (!text || text.length > 1200) continue;
                        if (!errorWords.some(word => low.includes(word))) continue;
                        errors.push(text.slice(0, 500));
                        if (errors.length >= 6) break;
                    }

                    let state = 'UNKNOWN';
                    if (errors.length) state = 'BLOCKED';
                    else if (nameInput || formEvidence) state = 'FORM';
                    else if (introDialog) state = 'INTRO_DIALOG';
                    else if (createEntry) state = 'CREATE_ENTRY';
                    else if (addSurface) state = 'ADD_SURFACE';
                    else if (dialogs.length) state = 'DIALOG';

                    const signature = [
                        state,
                        location.pathname,
                        controls.slice(0, 18).join('||'),
                        dialogTexts.slice(0, 3).join('||')
                    ].join('::').slice(0, 6000);

                    return {
                        state,
                        signature,
                        url: location.href,
                        name_input: nameInput,
                        form_evidence: formEvidence,
                        editable_form_control: editableFormControl,
                        intro_dialog: introDialog,
                        dialog_form_control_count: dialogFormControlCount,
                        create_entry: createEntry,
                        create_target: createTarget,
                        add_surface: addSurface,
                        errors,
                        dialogs: dialogTexts.map(x => x.slice(0, 500)),
                        controls: controls.slice(0, 45)
                    };
                }"""
            )
        except Exception as exc:
            return {
                "state": "PROBE_ERROR",
                "signature": "",
                "error": f"{exc.__class__.__name__}: {exc}"[:500],
            }

        if not isinstance(raw, dict):
            return {"state": "UNKNOWN", "signature": ""}

        state = {
            "state": _clean(raw.get("state")).upper() or "UNKNOWN",
            "signature": _clean(raw.get("signature"))[:6000],
            "url": _clean(raw.get("url"))[:900],
            "name_input": bool(raw.get("name_input")),
            "form_evidence": bool(raw.get("form_evidence")),
            "editable_form_control": bool(
                raw.get("editable_form_control")
            ),
            "intro_dialog": bool(raw.get("intro_dialog")),
            "dialog_form_control_count": int(
                raw.get("dialog_form_control_count") or 0
            ),
            "create_entry": bool(raw.get("create_entry")),
            "create_target": (
                {
                    "text": _clean((raw.get("create_target") or {}).get("text"))[:500],
                    "x": int((raw.get("create_target") or {}).get("x") or 0),
                    "y": int((raw.get("create_target") or {}).get("y") or 0),
                    "w": int((raw.get("create_target") or {}).get("w") or 0),
                    "h": int((raw.get("create_target") or {}).get("h") or 0),
                    "source_x": int((raw.get("create_target") or {}).get("source_x") or 0),
                    "source_y": int((raw.get("create_target") or {}).get("source_y") or 0),
                    "source_w": int((raw.get("create_target") or {}).get("source_w") or 0),
                    "source_h": int((raw.get("create_target") or {}).get("source_h") or 0),
                    "source_tag": _clean((raw.get("create_target") or {}).get("source_tag"))[:40],
                    "tag": _clean((raw.get("create_target") or {}).get("tag"))[:40],
                    "role": _clean((raw.get("create_target") or {}).get("role"))[:80],
                    "score": int((raw.get("create_target") or {}).get("score") or 0),
                }
                if isinstance(raw.get("create_target"), dict)
                else {}
            ),
            "add_surface": bool(raw.get("add_surface")),
            "errors": [
                _clean(x)[:500]
                for x in (raw.get("errors") or [])
                if _clean(x)
            ][:6],
            "dialogs": [
                _clean(x)[:500]
                for x in (raw.get("dialogs") or [])
                if _clean(x)
            ][:6],
            "controls": [
                _clean(x)[:300]
                for x in (raw.get("controls") or [])
                if _clean(x)
            ][:45],
        }
        return state

    def _record_ad_account_ui_state(
        self,
        label: str,
        state: dict[str, Any],
    ) -> None:
        row = {
            "label": _clean(label)[:80],
            "state": _clean(state.get("state")).upper() or "UNKNOWN",
            "url": _clean(state.get("url"))[:700],
            "name_input": bool(state.get("name_input")),
            "form_evidence": bool(state.get("form_evidence")),
            "editable_form_control": bool(
                state.get("editable_form_control")
            ),
            "intro_dialog": bool(state.get("intro_dialog")),
            "dialog_form_control_count": int(
                state.get("dialog_form_control_count") or 0
            ),
            "create_entry": bool(state.get("create_entry")),
            "create_target": dict(state.get("create_target") or {}),
            "add_surface": bool(state.get("add_surface")),
            "errors": list(state.get("errors") or [])[:3],
            "dialogs": list(state.get("dialogs") or [])[:2],
            "controls": list(state.get("controls") or [])[:8],
        }
        self._ad_account_ui_trace.append(row)
        if len(self._ad_account_ui_trace) > 24:
            self._ad_account_ui_trace = self._ad_account_ui_trace[-24:]

    async def _wait_for_ad_account_ui_transition(
        self,
        *,
        previous_signature: str = "",
        timeout_seconds: float = 4.0,
        label: str = "transition",
        require_signature_change: bool = False,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.5, float(timeout_seconds))
        last: dict[str, Any] = {"state": "UNKNOWN", "signature": ""}
        while time.monotonic() < deadline:
            last = await self._ad_account_ui_state()
            current_signature = _clean(last.get("signature"))
            current_state = _clean(last.get("state")).upper()
            signature_changed = bool(
                previous_signature
                and current_signature
                and current_signature != previous_signature
            )
            terminal_state = current_state == "BLOCKED" or (
                not require_signature_change
                and current_state in {
                    "FORM",
                    "CREATE_ENTRY",
                    "INTRO_DIALOG",
                }
            )
            if terminal_state or signature_changed:
                self._record_ad_account_ui_state(label, last)
                return last
            await self.page.wait_for_timeout(200)

        self._record_ad_account_ui_state(label + "_timeout", last)
        return last

    async def _click_state_detected_ad_account_create_entry(
        self,
    ) -> dict[str, Any]:
        """Physically click the exact leaf that proved CREATE_ENTRY exists.

        The classifier keeps both the deepest matching text node and its
        semantic ancestor. Clicking the leaf is deliberate: Playwright sends a
        real pointer event at that text, which then bubbles to Meta's React
        handler even when the handler lives on a parent DIV. A broad parent
        locator is retained only as a fallback.
        """
        if self.page is None:
            return {"clicked": False}

        async def click_locator(locator: Any, target_kind: str) -> dict[str, Any]:
            if not await locator.count():
                return {"clicked": False, "reason": f"{target_kind}_missing"}
            item = locator.first
            if not await item.is_visible():
                return {"clicked": False, "reason": f"{target_kind}_not_visible"}
            if not await item.is_enabled():
                return {"clicked": False, "reason": f"{target_kind}_disabled"}

            meta = await item.evaluate(
                """el => {
                    const r = el.getBoundingClientRect();
                    const text = (
                        (el.getAttribute('aria-label') || '') + ' ' +
                        (el.getAttribute('title') || '') + ' ' +
                        (el.innerText || el.textContent || '')
                    ).normalize('NFKC')
                     .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                     .replace(/\u00a0/g, ' ')
                     .replace(/\s+/g, ' ')
                     .trim();
                    const top = document.elementFromPoint(
                        r.left + r.width / 2,
                        r.top + r.height / 2
                    );
                    return {
                        text:text.slice(0, 500),
                        x:Math.round(r.x),
                        y:Math.round(r.y),
                        w:Math.round(r.width),
                        h:Math.round(r.height),
                        tag:el.tagName || '',
                        role:el.getAttribute('role') || '',
                        center_top_tag:top ? (top.tagName || '') : '',
                        center_top_text:top ? (
                            top.innerText || top.textContent || ''
                        ).slice(0, 180) : ''
                    };
                }"""
            )
            try:
                await item.click(
                    timeout=1800,
                    position={
                        "x": max(1, int((meta or {}).get("w") or 2) // 2),
                        "y": max(1, int((meta or {}).get("h") or 2) // 2),
                    },
                )
                return {
                    "clicked": True,
                    "mode": "playwright_physical",
                    "target_kind": target_kind,
                    "target": meta if isinstance(meta, dict) else {},
                }
            except Exception as exc:
                # HTMLElement.click() on the leaf still bubbles through React's
                # ancestor chain and is safer than synthesizing a click on a
                # broad container.
                try:
                    dom = await item.evaluate(
                        """el => {
                            if (!el || !el.isConnected) return false;
                            el.click();
                            return true;
                        }"""
                    )
                except Exception:
                    dom = False
                if dom:
                    return {
                        "clicked": True,
                        "mode": "leaf_dom",
                        "target_kind": target_kind,
                        "target": meta if isinstance(meta, dict) else {},
                        "playwright_error": (
                            f"{exc.__class__.__name__}: {_clean(exc)}"[:300]
                        ),
                    }
                return {
                    "clicked": False,
                    "reason": "click_failed",
                    "target_kind": target_kind,
                    "error": f"{exc.__class__.__name__}: {_clean(exc)}"[:300],
                    "target": meta if isinstance(meta, dict) else {},
                }

        try:
            leaf_result = await click_locator(
                self.page.locator('[data-remask-rk-create-leaf="1"]'),
                "leaf",
            )
            if leaf_result.get("clicked"):
                return leaf_result

            parent_result = await click_locator(
                self.page.locator('[data-remask-rk-create-state="1"]'),
                "parent",
            )
            if parent_result.get("clicked"):
                parent_result["leaf_failure"] = leaf_result
                return parent_result

            return {
                "clicked": False,
                "reason": "no_clickable_create_target",
                "leaf": leaf_result,
                "parent": parent_result,
            }
        except Exception as exc:
            return {
                "clicked": False,
                "reason": "exception",
                "error": f"{exc.__class__.__name__}: {_clean(exc)}"[:300],
            }

    async def _click_ad_account_create_entry_by_visible_text(self) -> bool:
        """Click a visible Create-new-RK label even if Meta omitted ARIA roles.

        Meta frequently nests localized menu text in plain span/div nodes while
        the actual pointer handler lives on an ancestor.  Search by semantic
        text first, then climb to the nearest actionable ancestor instead of
        requiring a correct button/menuitem role.
        """
        if self.page is None:
            return False

        try:
            result = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();

                    const createWords = [
                        'create','créer','создать','створити',
                        'erstellen','তৈরি করুন','tạo','बनाएँ','बनाएं'
                    ];
                    const accountWords = [
                        'ad account','advertising account','compte publicitaire',
                        'реклам','werbekonto','বিজ্ঞাপন অ্যাকাউন্ট',
                        'tài khoản quảng cáo','विज्ञापन खाता','विज्ञापन खाते'
                    ];

                    const nodes = [...document.querySelectorAll(
                        'span,div,a,button,[role],[tabindex]'
                    )];
                    const candidates = [];
                    for (const el of nodes) {
                        if (!visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 40 || r.y > 790) continue;

                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        if (!text || text.length > 220) continue;
                        const hasCreate = createWords.some(word => text.includes(word));
                        const hasAccount = accountWords.some(word => text.includes(word));
                        if (!hasCreate || !hasAccount) continue;

                        const semantic = el.closest(
                            'button,a,[role="button"],[role="menuitem"],'
                            + '[role="menuitemradio"],[role="option"],'
                            + '[tabindex]:not([tabindex="-1"])'
                        );
                        const clickable = semantic || el;
                        if (!visible(clickable)) continue;

                        const insideDialog = !!el.closest(
                            '[role="dialog"],[aria-modal="true"]'
                        );
                        const style = getComputedStyle(clickable);
                        const role = (
                            clickable.getAttribute('role') || ''
                        ).toLowerCase();
                        const tag = (clickable.tagName || '').toUpperCase();
                        const explicitlyInteractive = !!semantic
                            || tag === 'BUTTON'
                            || tag === 'A'
                            || [
                                'button','menuitem','menuitemradio','option'
                            ].includes(role)
                            || clickable.hasAttribute('onclick')
                            || style.cursor === 'pointer';
                        if (insideDialog && !explicitlyInteractive) continue;

                        const cr = clickable.getBoundingClientRect();

                        let score = 0;
                        if (text === 'créer un compte publicitaire') score -= 500;
                        if (text === 'create ad account') score -= 500;
                        if (clickable !== el) score -= 100;
                        if (cr.x >= 300) score -= 50;
                        score += Math.round(cr.y / 10);

                        candidates.push({
                            el: clickable,
                            text,
                            x: Math.round(cr.x),
                            y: Math.round(cr.y),
                            tag: clickable.tagName || '',
                            role: clickable.getAttribute('role') || '',
                            score
                        });
                    }

                    candidates.sort((a,b) => a.score - b.score);
                    const best = candidates[0];
                    if (!best) return {clicked:false};

                    best.el.scrollIntoView({block:'center'});
                    best.el.click();
                    return {
                        clicked:true,
                        text:best.text,
                        x:best.x,
                        y:best.y,
                        tag:best.tag,
                        role:best.role
                    };
                }"""
            )
            return bool(isinstance(result, dict) and result.get("clicked"))
        except Exception:
            return False

    async def _expand_ad_account_details_if_present(
        self,
    ) -> dict[str, Any]:
        """Open Meta's read-only 'Show details' surface and return fresh UI state.

        Meta can replace the expected Add-RK wizard with an informational or
        restriction card after Create-entry is clicked. Expanding details is
        read-only and exposes the actual Meta reason so it can be classified
        instead of being reported as a generic UI change.
        """
        if self.page is None:
            return {"clicked": False, "state": {}}

        detail_names = (
            "Show details",
            "View details",
            "Afficher les détails",
            "Voir les détails",
            "Показать подробности",
            "Подробнее",
            "Показати деталі",
            "Докладніше",
            "Details anzeigen",
            "Weitere Details",
            "বিস্তারিত দেখুন",
            "Xem chi tiết",
            "विवरण देखें",
        )

        clicked = False
        clicked_text = ""
        try:
            for name in detail_names:
                locator = self.page.get_by_text(name, exact=True)
                count = await locator.count()
                for i in range(min(count, 4)):
                    item = locator.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        await item.click(timeout=1800)
                        clicked = True
                        clicked_text = name
                        break
                    except Exception:
                        continue
                if clicked:
                    break

            if not clicked:
                result = await self.page.evaluate(
                    """() => {
                        const visible = el => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            return r.width > 0 && r.height > 0
                                && s.display !== 'none'
                                && s.visibility !== 'hidden'
                                && s.pointerEvents !== 'none';
                        };
                        const clean = text => (text || '')
                            .normalize('NFKC')
                            .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                            .replace(/\u00a0/g, ' ')
                            .replace(/\s+/g, ' ')
                            .trim();
                        const names = [
                            'show details','view details',
                            'afficher les détails','voir les détails',
                            'показать подробности','подробнее',
                            'показати деталі','докладніше',
                            'details anzeigen','weitere details',
                            'বিস্তারিত দেখুন','xem chi tiết','विवरण देखें'
                        ];
                        const nodes = [...document.querySelectorAll(
                            'button,a,div,span,[role="button"],[role="link"],[tabindex]'
                        )];
                        for (const el of nodes) {
                            if (!visible(el)) continue;
                            const r = el.getBoundingClientRect();
                            if (r.x < 280 || r.y < 35 || r.y > 795) continue;
                            const text = clean(
                                (el.getAttribute('aria-label') || '') + ' ' +
                                (el.getAttribute('title') || '') + ' ' +
                                (el.innerText || el.textContent || '')
                            );
                            const low = text.toLowerCase();
                            if (!names.some(name => low === name)) continue;
                            const clickable = el.closest(
                                'button,a,[role="button"],[role="link"],'
                                + '[tabindex]:not([tabindex="-1"])'
                            ) || el;
                            if (!visible(clickable)) continue;
                            clickable.click();
                            return {clicked:true,text};
                        }
                        return {clicked:false,text:''};
                    }"""
                )
                if isinstance(result, dict):
                    clicked = bool(result.get("clicked"))
                    clicked_text = _clean(result.get("text"))[:120]
        except Exception:
            clicked = False

        if clicked:
            try:
                await self.page.wait_for_timeout(350)
            except Exception:
                pass

        state = await self._ad_account_ui_state()
        snapshot = await self._ad_account_right_pane_snapshot()
        return {
            "clicked": clicked,
            "text": clicked_text,
            "state": state,
            "snapshot": snapshot[:30],
        }

    async def _ad_account_right_pane_snapshot(self) -> list[str]:
        """Capture compact visible text/controls from Meta's right pane."""
        if self.page is None:
            return []
        try:
            rows = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();

                    const out = [];
                    const seen = new Set();
                    const nodes = [
                        ...document.querySelectorAll(
                            'button,a,[role="button"],[role="link"],'
                            + '[role="menuitem"],[role="menuitemradio"],'
                            + '[role="option"],[role="dialog"],h1,h2,h3,'
                            + '[aria-label],[title],[tabindex]'
                        )
                    ];
                    for (const el of nodes) {
                        if (!visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 35 || r.y > 795) continue;

                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        if (!text || text.length > 220) continue;
                        const row = text
                            + ' [tag=' + (el.tagName || '')
                            + ' role=' + (el.getAttribute('role') || '')
                            + ' x=' + Math.round(r.x)
                            + ' y=' + Math.round(r.y)
                            + ']';
                        if (seen.has(row)) continue;
                        seen.add(row);
                        out.push(row);
                        if (out.length >= 80) break;
                    }
                    return out;
                }"""
            )
            if isinstance(rows, list):
                return [_clean(x)[:300] for x in rows if _clean(x)][:80]
        except Exception:
            pass
        return []

    @staticmethod
    def _ad_account_ownership_step_present(state: dict[str, Any]) -> bool:
        """Detect Meta's pre-details ownership/usage wizard step."""
        if not isinstance(state, dict):
            return False
        parts: list[str] = []
        for key in ("controls", "dialogs"):
            values = state.get(key)
            if isinstance(values, list):
                parts.extend(_clean(x).casefold() for x in values if _clean(x))
        text = " ".join(parts)
        ownership_markers = (
            "my business",
            "my business portfolio",
            "for my business",
            "this ad account will be used for my business",
            "ce compte publicitaire sera utilisé pour mon entreprise",
            "ce compte publicitaire sera utilise pour mon entreprise",
            "mon entreprise",
            "mon portefeuille business",
            "pour mon entreprise",
            "mein unternehmen",
            "für mein unternehmen",
            "мой бизнес",
            "для моего бизнеса",
            "мій бізнес",
            "для мого бізнесу",
            "আমার ব্যবসা",
            "আমার ব্যবসার জন্য",
            "doanh nghiệp của tôi",
            "dành cho doanh nghiệp của tôi",
            "मेरा व्यवसाय",
            "मेरे व्यवसाय के लिए",
        )
        return any(marker in text for marker in ownership_markers)

    @staticmethod
    def _ad_account_create_form_confirmed(state: dict[str, Any]) -> bool:
        """Require evidence that Meta actually opened the Add-RK wizard.

        A successful click alone is not enough: Meta can expose matching text
        in the normal Ad Accounts surface without opening the creation wizard.
        """
        if not isinstance(state, dict):
            return False
        if _clean(state.get("state")).upper() != "FORM":
            return False
        if bool(state.get("name_input")):
            return True
        if FacebookBusinessBrowser._ad_account_ownership_step_present(state):
            return True

        # _ad_account_ui_state only sets form_evidence from a non-AI dialog
        # with real visible editable controls. Accept that structural proof
        # even when Meta omits/renames the visible field labels.
        if (
            bool(state.get("form_evidence"))
            and int(state.get("dialog_form_control_count") or 0) > 0
        ):
            return True

        dialogs = state.get("dialogs")
        if not isinstance(dialogs, list):
            dialogs = []
        dialog_text = " ".join(
            _clean(x).casefold()
            for x in dialogs
            if _clean(x)
        )
        # Do not accept the Meta AI assistant as the Add-RK wizard.
        # It is a real dialog with a textbox and can contain generic words
        # such as "compte publicitaire", but it has none of the immutable
        # Ad Account fields we need.
        ai_dialog_markers = (
            "meta ai",
            "assistant business meta ai",
            "meta ai business assistant",
            "assistant meta ai",
        )
        dialog_wizard_markers = (
            "ad account name",
            "advertising account name",
            "nom du compte publicitaire",
            "nom du compte",
            "name des werbekontos",
            "название рекламного аккаунта",
            "назва рекламного акаунта",
            "devise",
            "fuseau horaire",
            "currency",
            "time zone",
            "timezone",
            "währung",
            "zeitzone",
            "валюта",
            "часовой пояс",
            "часовий пояс",
            "my business",
            "my business portfolio",
            "for my business",
            "mon entreprise",
            "mon portefeuille business",
            "pour mon entreprise",
            "mein unternehmen",
            "für mein unternehmen",
            "мой бизнес",
            "для моего бизнеса",
            "мій бізнес",
            "для мого бізнесу",
            "আমার ব্যবসা",
            "আমার ব্যবসার জন্য",
            "doanh nghiệp của tôi",
            "dành cho doanh nghiệp của tôi",
            "मेरा व्यवसाय",
            "मेरे व्यवसाय के लिए",
        )
        if (
            dialog_text
            and any(
                marker in dialog_text
                for marker in dialog_wizard_markers
            )
        ):
            return True
        if any(marker in dialog_text for marker in ai_dialog_markers):
            return False
        # Unwrapped Meta wizard variants expose multiple form controls. Do not
        # accept a lone table/search input from the normal Ad Accounts page.
        controls = state.get("controls")
        if not isinstance(controls, list):
            controls = []
        folded = " ".join(_clean(x).casefold() for x in controls)
        form_markers = (
            "devise",
            "currency",
            "fuseau horaire",
            "time zone",
            "timezone",
            "nom du compte publicitaire",
            "ad account name",
            "werbekonto",
            "часовой пояс",
            "валюта",
            "часовий пояс",
        )
        marker_count = sum(1 for marker in form_markers if marker in folded)
        return bool(
            state.get("editable_form_control")
            and marker_count >= 2
        )

    async def _advance_ad_account_intro_dialog(
        self,
        *,
        timeout_seconds: float = 4.0,
    ) -> bool:
        """Advance Meta's intermediate Create-RK modal to the real form.

        Current Business Settings variants may use:
        Add -> Create ad account -> intro modal -> Next/Continue -> fields.
        Only controls inside the recognized non-AI modal are used here.
        """
        if self.page is None:
            return False

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        for attempt in range(2):
            if time.monotonic() >= deadline:
                break

            state = await self._ad_account_ui_state()
            if self._ad_account_create_form_confirmed(state):
                return True
            if _clean(state.get("state")).upper() != "INTRO_DIALOG":
                return False

            before_signature = _clean(state.get("signature"))
            action_meta = (
                await self._click_ad_account_form_action_by_visible_text(
                    "next"
                )
            )
            if not bool(action_meta.get("clicked")):
                action_meta = (
                    await self._click_ad_account_form_action_by_visible_text(
                        "final"
                    )
                )
            if not bool(action_meta.get("clicked")):
                return False

            remaining = max(1.0, deadline - time.monotonic())
            transition = await self._wait_for_ad_account_ui_transition(
                previous_signature=before_signature,
                timeout_seconds=min(3.5, remaining),
                label=f"after_intro_dialog_advance_{attempt}",
                require_signature_change=True,
            )
            if self._ad_account_create_form_confirmed(transition):
                return True
            if _clean(transition.get("state")).upper() == "BLOCKED":
                return False

        final_state = await self._ad_account_ui_state()
        return self._ad_account_create_form_confirmed(final_state)

    async def _wait_for_ad_account_create_entry(
        self,
        *,
        timeout_seconds: float = 6.0,
    ) -> bool:
        """Click Create-new-Ad-Account and verify that the wizard really opens."""
        if self.page is None:
            return False

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            before = await self._ad_account_ui_state()
            before_signature = _clean(before.get("signature"))
            before_state = _clean(before.get("state")).upper()

            if before_state == "INTRO_DIALOG":
                return await self._advance_ad_account_intro_dialog(
                    timeout_seconds=max(
                        1.0,
                        min(3.5, deadline - time.monotonic()),
                    )
                )

            tagged_click = {"clicked": False}
            if before_state == "CREATE_ENTRY":
                tagged_click = (
                    await self._click_state_detected_ad_account_create_entry()
                )
            clicked = bool(tagged_click.get("clicked"))

            if not clicked:
                clicked = await self._click_named(
                    self.AD_ACCOUNT_CREATE_ENTRY_NAMES,
                    roles=(
                        "button",
                        "link",
                        "menuitem",
                        "menuitemradio",
                        "option",
                    ),
                    click_timeout_ms=2500,
                )
            if not clicked:
                clicked = await self._click_ad_account_create_entry_by_visible_text()
            if not clicked:
                try:
                    clicked = (
                        await self._click_ad_account_action_dom(
                            allow_generic_add=False
                        )
                        == "create"
                    )
                except Exception:
                    clicked = False

            if clicked:
                transition = await self._wait_for_ad_account_ui_transition(
                    previous_signature=before_signature,
                    timeout_seconds=3.0,
                    label="after_create_entry_click",
                )
                if self._ad_account_create_form_confirmed(transition):
                    return True
                if _clean(
                    transition.get("state")
                ).upper() == "INTRO_DIALOG":
                    if await self._advance_ad_account_intro_dialog(
                        timeout_seconds=3.5,
                    ):
                        return True
                # Click landed on matching text/wrapper but did not open the
                # wizard. Keep probing instead of treating click success as
                # form success.

            current = await self._ad_account_ui_state()
            if self._ad_account_create_form_confirmed(current):
                return True

            await self.page.wait_for_timeout(250)

        return False

    async def _ad_account_add_button_candidates(self) -> list[dict[str, Any]]:
        """Find all visible Add controls in the right settings pane.

        Meta can render more than one localized Add control at once.  Do not
        let the generic role locator pick the first one blindly; tag each
        candidate so the caller can probe them one-by-one.
        """
        if self.page is None:
            return []

        try:
            rows = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const addWords = [
                        'add','ajouter','добавить','додати',
                        'hinzufügen','যোগ করুন','thêm','जोड़ें'
                    ];
                    const accountWords = [
                        'ad account','advertising account','compte publicitaire',
                        'реклам','werbekonto','বিজ্ঞাপন অ্যাকাউন্ট',
                        'tài khoản quảng cáo','विज्ञापन खाता','विज्ञापन खाते'
                    ];
                    const hasLocalAccountContext = el => {
                        let cur = el;
                        for (
                            let depth = 0;
                            cur && depth < 7;
                            depth++, cur = cur.parentElement
                        ) {
                            if (!visible(cur)) continue;
                            const rr = cur.getBoundingClientRect();
                            if (rr.width > 1050 || rr.height > 520) continue;
                            const t = clean(
                                (cur.getAttribute('aria-label') || '') + ' ' +
                                (cur.getAttribute('title') || '') + ' ' +
                                (cur.innerText || cur.textContent || '')
                            ).toLowerCase();
                            if (accountWords.some(word => t.includes(word))) {
                                return true;
                            }
                        }
                        return false;
                    };
                    for (const el of document.querySelectorAll(
                        '[data-remask-rk-add-probe]'
                    )) {
                        el.removeAttribute('data-remask-rk-add-probe');
                    }

                    const rows = [];
                    const seen = new Set();
                    for (const el of document.querySelectorAll(
                        'button,a,[role="button"],[role="link"],[aria-haspopup]'
                    )) {
                        if (!visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.x < 300) continue;

                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        const lower = text.toLowerCase();
                        if (!text || text.length > 120) continue;
                        if (!addWords.some(
                            word => lower === word
                                || lower.startsWith(word + ' ')
                        )) {
                            continue;
                        }

                        const localAccountContext = hasLocalAccountContext(el);
                        const key = [
                            text,
                            Math.round(r.x),
                            Math.round(r.y),
                            Math.round(r.width),
                            Math.round(r.height)
                        ].join('|');
                        if (seen.has(key)) continue;
                        seen.add(key);
                        rows.push({
                            el,
                            text,
                            x: Math.round(r.x),
                            y: Math.round(r.y),
                            w: Math.round(r.width),
                            h: Math.round(r.height),
                            tag: el.tagName || '',
                            role: el.getAttribute('role') || '',
                            haspopup: el.getAttribute('aria-haspopup') || '',
                            local_account_context: localAccountContext,
                            // A control inside the content body is a better
                            // first probe than a section-header Add.
                            toolbar_penalty: r.y < 180 ? 1 : 0
                        });
                    }

                    rows.sort((a, b) => {
                        if (a.toolbar_penalty !== b.toolbar_penalty) {
                            return a.toolbar_penalty - b.toolbar_penalty;
                        }
                        // For equivalent controls, probe the lower content
                        // action before the top toolbar duplicate.
                        if (a.y !== b.y) return b.y - a.y;
                        return a.x - b.x;
                    });

                    return rows.slice(0, 8).map((row, index) => {
                        const probeId = String(index);
                        row.el.setAttribute(
                            'data-remask-rk-add-probe',
                            probeId
                        );
                        return {
                            probe_id: probeId,
                            text: row.text,
                            x: row.x,
                            y: row.y,
                            w: row.w,
                            h: row.h,
                            tag: row.tag,
                            role: row.role,
                            haspopup: row.haspopup,
                            local_account_context: row.local_account_context,
                            toolbar_penalty: row.toolbar_penalty
                        };
                    });
                }"""
            )
        except Exception:
            return []

        if not isinstance(rows, list):
            return []

        output: list[dict[str, Any]] = []
        for row in rows[:8]:
            if not isinstance(row, dict):
                continue
            output.append(
                {
                    "probe_id": _clean(row.get("probe_id")),
                    "text": _clean(row.get("text"))[:120],
                    "x": int(row.get("x") or 0),
                    "y": int(row.get("y") or 0),
                    "w": int(row.get("w") or 0),
                    "h": int(row.get("h") or 0),
                    "tag": _clean(row.get("tag")),
                    "role": _clean(row.get("role")),
                    "haspopup": _clean(row.get("haspopup")),
                    "local_account_context": bool(
                        row.get("local_account_context")
                    ),
                    "toolbar_penalty": int(
                        row.get("toolbar_penalty") or 0
                    ),
                }
            )
        return output

    async def _ad_account_visible_create_candidates(
        self,
    ) -> list[dict[str, Any]]:
        """Visible Create-RK text candidates anywhere in the right pane."""
        if self.page is None:
            return []
        try:
            rows = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                    const createWords = [
                        'create','créer','создать','створити','erstellen',
                        'তৈরি করুন','tạo','बनाएँ','बनाएं'
                    ];
                    const accountWords = [
                        'ad account','advertising account','compte publicitaire',
                        'реклам','werbekonto','বিজ্ঞাপন অ্যাকাউন্ট',
                        'tài khoản quảng cáo','विज्ञापन खाता','विज्ञापन खाते'
                    ];
                    for (const el of document.querySelectorAll(
                        '[data-remask-rk-create-fresh]'
                    )) {
                        el.removeAttribute('data-remask-rk-create-fresh');
                    }

                    const out = [];
                    const seen = new Set();
                    const metaAIRoot = el => {
                        const root = el.closest(
                            '[role="dialog"],[aria-modal="true"]'
                        );
                        if (!root) return false;
                        const headings = [...root.querySelectorAll(
                            'h1,h2,h3,[role="heading"]'
                        )].filter(visible).slice(0, 3).map(node =>
                            node.innerText || node.textContent || ''
                        ).join(' ');
                        const identity = clean(
                            (root.getAttribute('aria-label') || '') + ' ' +
                            (root.getAttribute('title') || '') + ' ' +
                            headings
                        );
                        return [
                            'meta ai',
                            'assistant business meta ai',
                            'meta ai business assistant',
                            'assistant meta ai'
                        ].some(word => identity.includes(word));
                    };
                    for (const el of document.querySelectorAll(
                        'button,a,span,div,[role],[tabindex]'
                    )) {
                        if (!visible(el)) continue;
                        if (metaAIRoot(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.x < 280 || r.y < 35 || r.y > 795) continue;
                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        if (!text || text.length > 220) continue;
                        if (!createWords.some(w => text.includes(w))) continue;
                        if (!accountWords.some(w => text.includes(w))) continue;

                        const clickable = el.closest(
                            'button,a,[role="button"],[role="menuitem"],'
                            + '[role="menuitemradio"],[role="option"],'
                            + '[tabindex]:not([tabindex="-1"])'
                        ) || el;
                        if (!visible(clickable)) continue;
                        if (
                            clickable.hasAttribute('disabled')
                            || clickable.getAttribute('aria-disabled') === 'true'
                        ) continue;

                        const cr = clickable.getBoundingClientRect();
                        const key = [
                            text,
                            Math.round(cr.x),
                            Math.round(cr.y),
                            Math.round(cr.width),
                            Math.round(cr.height)
                        ].join('|');
                        if (seen.has(key)) continue;
                        seen.add(key);

                        const id = String(out.length);
                        clickable.setAttribute(
                            'data-remask-rk-create-fresh',
                            id
                        );
                        out.push({
                            probe_id:id,
                            text,
                            x:Math.round(cr.x),
                            y:Math.round(cr.y),
                            w:Math.round(cr.width),
                            h:Math.round(cr.height),
                            tag:clickable.tagName || '',
                            role:clickable.getAttribute('role') || ''
                        });
                        if (out.length >= 20) break;
                    }
                    return out;
                }"""
            )
        except Exception:
            return []

        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for row in rows[:20]:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "probe_id": _clean(row.get("probe_id")),
                    "text": _clean(row.get("text"))[:220],
                    "x": int(row.get("x") or 0),
                    "y": int(row.get("y") or 0),
                    "w": int(row.get("w") or 0),
                    "h": int(row.get("h") or 0),
                    "tag": _clean(row.get("tag"))[:40],
                    "role": _clean(row.get("role"))[:80],
                }
            )
        return out

    @staticmethod
    def _fresh_ad_account_create_candidates(
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        def same(a: dict[str, Any], b: dict[str, Any]) -> bool:
            if _clean(a.get("text")) != _clean(b.get("text")):
                return False
            return (
                abs(int(a.get("x") or 0) - int(b.get("x") or 0)) <= 12
                and abs(int(a.get("y") or 0) - int(b.get("y") or 0)) <= 12
            )

        return [
            row
            for row in after
            if isinstance(row, dict)
            and not any(
                same(row, old)
                for old in before
                if isinstance(old, dict)
            )
        ]

    async def _wait_for_fresh_ad_account_create_candidate(
        self,
        *,
        before: list[dict[str, Any]],
        timeout_seconds: float = 3.5,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Poll Meta's async Add menu until a new Create-RK entry appears."""
        if self.page is None:
            return [], {"state": "NO_PAGE", "signature": ""}

        deadline = time.monotonic() + max(0.8, float(timeout_seconds))
        last_state: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_state = await self._ad_account_ui_state()
            if self._ad_account_create_form_confirmed(last_state):
                return [], last_state

            after = await self._ad_account_visible_create_candidates()
            fresh = self._fresh_ad_account_create_candidates(
                before,
                after,
            )
            if fresh:
                return fresh, last_state

            await self.page.wait_for_timeout(200)

        return [], last_state

    async def _click_fresh_ad_account_create_candidate(
        self,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        if self.page is None:
            return {"clicked": False}
        probe_id = _clean(candidate.get("probe_id"))
        if not probe_id:
            return {"clicked": False}
        try:
            locator = self.page.locator(
                f'[data-remask-rk-create-fresh="{probe_id}"]'
            )
            if not await locator.count():
                return {"clicked": False, "reason": "candidate_disappeared"}
            item = locator.first
            if not (await item.is_visible() and await item.is_enabled()):
                return {"clicked": False, "reason": "candidate_not_clickable"}
            await item.scroll_into_view_if_needed(timeout=1500)
            await item.click(timeout=2500)
            return {
                "clicked": True,
                "text": _clean(candidate.get("text"))[:180],
                "x": int(candidate.get("x") or 0),
                "y": int(candidate.get("y") or 0),
                "tag": _clean(candidate.get("tag"))[:40],
                "role": _clean(candidate.get("role"))[:80],
            }
        except Exception as exc:
            return {
                "clicked": False,
                "error": f"{exc.__class__.__name__}: {_clean(exc)}"[:300],
            }

    async def _probe_ad_account_add_buttons(
        self,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Probe distinct right-pane Add buttons across React re-renders.

        Meta may replace the DOM after each popup open/close, so candidate
        locators must be rediscovered before every attempt.  We only persist a
        compact spatial signature of already-tried controls.
        """
        if self.page is None:
            return False, []

        attempts: list[dict[str, Any]] = []
        tried: list[tuple[str, int, int]] = []

        def already_tried(row: dict[str, Any]) -> bool:
            text = _clean(row.get("text")).lower()
            x = int(row.get("x") or 0)
            y = int(row.get("y") or 0)
            for old_text, old_x, old_y in tried:
                if text != old_text:
                    continue
                if abs(x - old_x) <= 48 and abs(y - old_y) <= 48:
                    return True
            return False

        for _ in range(8):
            # IMPORTANT: rescan on every loop. Opening or closing any Meta
            # popup can replace the button nodes and invalidate old locators.
            candidates = await self._ad_account_add_button_candidates()
            row = next(
                (
                    candidate
                    for candidate in candidates
                    if isinstance(candidate, dict)
                    and not already_tried(candidate)
                ),
                None,
            )
            if not isinstance(row, dict):
                break

            probe_id = _clean(row.get("probe_id"))
            if not probe_id:
                break

            signature = (
                _clean(row.get("text")).lower(),
                int(row.get("x") or 0),
                int(row.get("y") or 0),
            )
            tried.append(signature)

            attempt: dict[str, Any] = {
                key: value
                for key, value in row.items()
                if key != "probe_id"
            }
            attempt["probe_id"] = probe_id
            attempt["clicked"] = False
            attempt["create_entry_found"] = False

            try:
                # This locator was assigned by the *fresh* scan above and is
                # consumed immediately, before Meta has another chance to
                # replace the underlying React node.
                locator = self.page.locator(
                    f'[data-remask-rk-add-probe="{probe_id}"]'
                )
                if not await locator.count():
                    attempt["skip"] = "fresh_candidate_disappeared"
                    attempts.append(attempt)
                    continue

                item = locator.first
                if not await item.is_visible():
                    attempt["skip"] = "candidate_not_visible"
                    attempts.append(attempt)
                    continue
                if not await item.is_enabled():
                    attempt["skip"] = "candidate_disabled"
                    attempts.append(attempt)
                    continue

                before_state = await self._ad_account_ui_state()
                self._record_ad_account_ui_state(
                    "before_add_click",
                    before_state,
                )
                before_snapshot = set(
                    await self._ad_account_right_pane_snapshot()
                )
                before_create_candidates = (
                    await self._ad_account_visible_create_candidates()
                )

                # The Add controls are already proven visible/enabled by the
                # fresh scan above. Do not spend the remaining Playwright
                # action deadline on scroll_into_view_if_needed(): on Meta's
                # virtualized settings pane that call can consume the whole
                # deadline and even surface negative remaining time. Try one
                # normal click, then fall back to HTMLElement.click() on the
                # exact freshly-tagged node (opening Add is reversible).
                click_error = ""
                try:
                    await item.click(timeout=1200)
                    attempt["click_mode"] = "playwright"
                    attempt["clicked"] = True
                except Exception as click_exc:
                    click_error = (
                        f"{click_exc.__class__.__name__}:"
                        f"{_clean(click_exc)}"
                    )[:400]
                    dom_click = await self.page.evaluate(
                        """(probeId) => {
                            const el = document.querySelector(
                                '[data-remask-rk-add-probe="' + probeId + '"]'
                            );
                            if (!el || !el.isConnected) {
                                return {clicked:false, reason:'missing'};
                            }
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            const visible = r.width > 0 && r.height > 0
                                && s.display !== 'none'
                                && s.visibility !== 'hidden'
                                && s.pointerEvents !== 'none';
                            const disabled = el.hasAttribute('disabled')
                                || el.getAttribute('aria-disabled') === 'true';
                            if (!visible || disabled) {
                                return {
                                    clicked:false,
                                    reason:disabled ? 'disabled' : 'not_visible'
                                };
                            }
                            el.click();
                            return {
                                clicked:true,
                                x:Math.round(r.x),
                                y:Math.round(r.y)
                            };
                        }""",
                        probe_id,
                    )
                    if isinstance(dom_click, dict) and dom_click.get("clicked"):
                        attempt["click_mode"] = "dom"
                        attempt["playwright_click_error"] = click_error
                        attempt["clicked"] = True
                    else:
                        attempt["playwright_click_error"] = click_error
                        attempt["dom_click"] = dom_click
                        raise click_exc

                # IMPORTANT: Meta renders the Add menu through an async React
                # portal. Observe it immediately after the click. Waiting for
                # the coarse page state first can miss the transient/fresh
                # Create-RK entry entirely.
                (
                    fresh_create_candidates,
                    post_add_poll_state,
                ) = await self._wait_for_fresh_ad_account_create_candidate(
                    before=before_create_candidates,
                    timeout_seconds=3.5,
                )
                attempt["post_add_poll_state"] = _clean(
                    post_add_poll_state.get("state")
                ).upper()
                attempt["post_add_poll_signature"] = _clean(
                    post_add_poll_state.get("signature")
                )[:700]
                attempt["ui_state_after"] = _clean(
                    post_add_poll_state.get("state")
                ).upper()
                attempt["ui_errors"] = list(
                    post_add_poll_state.get("errors") or []
                )[:3]
                attempt["create_target"] = dict(
                    post_add_poll_state.get("create_target") or {}
                )
                attempt["create_surface_seen"] = bool(
                    attempt["ui_state_after"] == "CREATE_ENTRY"
                    or attempt["create_target"]
                )

                # If the state classifier has already identified and tagged a
                # concrete Create-RK target, consume THAT exact target before
                # any broad rescans.  Re-running the classifier first can
                # retag a parent/wrapper after Meta's React portal rerenders,
                # which is how a real CREATE_ENTRY used to be missed even
                # though the diagnostic had already proved it was visible.
                state_create_direct: dict[str, Any] = {"clicked": False}
                if (
                    attempt["ui_state_after"] == "CREATE_ENTRY"
                    and attempt["create_target"]
                ):
                    state_create_direct = (
                        await self._click_state_detected_ad_account_create_entry()
                    )
                    attempt["state_create_direct"] = state_create_direct
                    if state_create_direct.get("clicked"):
                        direct_transition = (
                            await self._wait_for_ad_account_ui_transition(
                                previous_signature=_clean(
                                    post_add_poll_state.get("signature")
                                ),
                                timeout_seconds=4.0,
                                label="after_state_create_entry_direct",
                                require_signature_change=True,
                            )
                        )
                        attempt["state_create_direct_state_after"] = _clean(
                            direct_transition.get("state")
                        ).upper()
                        if self._ad_account_create_form_confirmed(
                            direct_transition
                        ):
                            attempt["create_entry_found"] = True
                            attempts.append(attempt)
                            return True, attempts

                        if _clean(
                            direct_transition.get("state")
                        ).upper() == "INTRO_DIALOG":
                            intro_advanced = (
                                await self._advance_ad_account_intro_dialog(
                                    timeout_seconds=4.0,
                                )
                            )
                            attempt["intro_dialog_advanced"] = bool(
                                intro_advanced
                            )
                            if intro_advanced:
                                attempt["create_entry_found"] = True
                                attempts.append(attempt)
                                return True, attempts
                            direct_transition = (
                                await self._ad_account_ui_state()
                            )

                        # Continue recovery from the state produced by the
                        # exact Create-card click, not from the stale pre-click
                        # snapshot.
                        post_add_poll_state = direct_transition
                        attempt["ui_state_after"] = _clean(
                            direct_transition.get("state")
                        ).upper()
                        attempt["create_target"] = dict(
                            direct_transition.get("create_target") or {}
                        )

                after_snapshot = await self._ad_account_right_pane_snapshot()
                attempt["new_right_pane"] = [
                    snapshot_row
                    for snapshot_row in after_snapshot
                    if snapshot_row not in before_snapshot
                ][:20]

                # Meta may replace the expected wizard with a read-only
                # informational/restriction card after the exact Create-entry
                # click.  "Afficher les détails" is the current French
                # variant seen in production. Expand it before deciding this
                # is a UI mismatch so the real Meta reason becomes visible to
                # the state classifier.
                details_markers = (
                    "show details",
                    "view details",
                    "afficher les détails",
                    "voir les détails",
                    "показать подробности",
                    "подробнее",
                    "показати деталі",
                    "докладніше",
                    "details anzeigen",
                    "weitere details",
                    "বিস্তারিত দেখুন",
                    "xem chi tiết",
                    "विवरण देखें",
                )
                new_surface_text = " ".join(
                    _clean(row).casefold()
                    for row in attempt["new_right_pane"]
                )
                if (
                    state_create_direct.get("clicked")
                    and any(
                        marker in new_surface_text
                        for marker in details_markers
                    )
                ):
                    details_result = (
                        await self._expand_ad_account_details_if_present()
                    )
                    attempt["details_result"] = {
                        "clicked": bool(details_result.get("clicked")),
                        "text": _clean(details_result.get("text"))[:120],
                    }
                    details_state = (
                        details_result.get("state")
                        if isinstance(details_result.get("state"), dict)
                        else {}
                    )
                    attempt["details_state"] = _clean(
                        details_state.get("state")
                    ).upper()
                    attempt["details_errors"] = list(
                        details_state.get("errors") or []
                    )[:4]
                    attempt["details_dialogs"] = list(
                        details_state.get("dialogs") or []
                    )[:4]
                    attempt["details_snapshot"] = list(
                        details_result.get("snapshot") or []
                    )[:20]

                    if self._ad_account_create_form_confirmed(details_state):
                        attempt["create_entry_found"] = True
                        attempts.append(attempt)
                        return True, attempts

                    if _clean(details_state.get("state")).upper() == "BLOCKED":
                        attempt["blocked"] = True
                        attempts.append(attempt)
                        return False, attempts

                    if details_result.get("clicked"):
                        post_add_poll_state = details_state
                        attempt["ui_state_after"] = _clean(
                            details_state.get("state")
                        ).upper()
                        attempt["create_target"] = dict(
                            details_state.get("create_target") or {}
                        )

                if self._ad_account_create_form_confirmed(
                    post_add_poll_state
                ):
                    attempt["form_opened_during_poll"] = True
                    attempt["create_entry_found"] = True
                    attempts.append(attempt)
                    return True, attempts

                if attempt["ui_state_after"] == "BLOCKED":
                    attempt["blocked"] = True
                    attempts.append(attempt)
                    return False, attempts
                attempt["fresh_create_candidates"] = [
                    {
                        "text": _clean(candidate.get("text"))[:180],
                        "x": int(candidate.get("x") or 0),
                        "y": int(candidate.get("y") or 0),
                        "tag": _clean(candidate.get("tag"))[:40],
                        "role": _clean(candidate.get("role"))[:80],
                    }
                    for candidate in fresh_create_candidates[:8]
                ]

                fresh_create = {"clicked": False}
                if (
                    fresh_create_candidates
                    and not state_create_direct.get("clicked")
                ):
                    fresh_create = (
                        await self._click_fresh_ad_account_create_candidate(
                            fresh_create_candidates[0]
                        )
                    )
                attempt["fresh_create"] = fresh_create

                if fresh_create.get("clicked"):
                    fresh_transition = (
                        await self._wait_for_ad_account_ui_transition(
                            previous_signature=_clean(
                                post_add_poll_state.get("signature")
                            ),
                            timeout_seconds=4.0,
                            label="after_fresh_create_entry",
                            require_signature_change=True,
                        )
                    )
                    attempt["fresh_create_state_after"] = _clean(
                        fresh_transition.get("state")
                    ).upper()
                    if self._ad_account_create_form_confirmed(
                        fresh_transition
                    ):
                        attempt["create_entry_found"] = True
                        attempts.append(attempt)
                        return True, attempts

                # Critical recovery for Meta's current Add-RK card UI:
                # once the state machine proves CREATE_ENTRY, use the complete
                # verified Create-entry helper.  It tries semantic names,
                # visible plain DIV/SPAN text, and the bounded DOM fallback,
                # then *verifies the wizard opened* before returning success.
                state_create_verified = False
                if attempt["create_surface_seen"]:
                    state_create_verified = (
                        await self._wait_for_ad_account_create_entry(
                            timeout_seconds=2.5,
                        )
                    )
                attempt["state_create_verified"] = state_create_verified

                if state_create_verified:
                    attempt["create_entry_found"] = True
                    attempts.append(attempt)
                    return True, attempts

                attempt["post_click_candidates"] = (
                    await self._ad_account_popup_candidates()
                )

                popup_create = (
                    await self._click_ad_account_create_entry_in_popup()
                )
                attempt["popup_create"] = popup_create

                if popup_create.get("clicked"):
                    popup_transition = (
                        await self._wait_for_ad_account_ui_transition(
                                                        previous_signature=_clean(
                                post_add_poll_state.get("signature")
                            ),
timeout_seconds=4.0,
                            label="after_popup_create_entry",
                            require_signature_change=True,
                        )
                    )
                    attempt["popup_create_state_after"] = _clean(
                        popup_transition.get("state")
                    ).upper()
                    if self._ad_account_create_form_confirmed(
                        popup_transition
                    ):
                        attempt["create_entry_found"] = True
                        attempts.append(attempt)
                        return True, attempts

                # Never click Add again while Meta is still presenting the
                # Create-RK surface we just opened.  The old flow pressed
                # Escape here, reopened Add, and could turn one valid popup
                # into two stacked dialogs (exactly the popup_count=1 -> 2
                # failure seen in production).  Preserve the live surface and
                # let the caller run bounded Create-entry recovery on it.
                recovery_state = await self._ad_account_ui_state()
                recovery_name = _clean(
                    recovery_state.get("state")
                ).upper()
                attempt["post_recovery_state"] = recovery_name
                attempt["post_recovery_signature"] = _clean(
                    recovery_state.get("signature")
                )[:700]
                if (
                    (
                        attempt["create_surface_seen"]
                        or recovery_name == "INTRO_DIALOG"
                    )
                    and recovery_name in {
                        "CREATE_ENTRY",
                        "INTRO_DIALOG",
                        "DIALOG",
                    }
                ):
                    attempt["preserve_create_surface"] = True
                    attempts.append(attempt)
                    return False, attempts

                attempts.append(attempt)

                # Wrong Add control or unrelated popup. Close it, allow Meta to
                # settle, then RESCAN instead of reusing stale candidate nodes.
                try:
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(350)
                except Exception:
                    pass
            except Exception as exc:
                attempt["error"] = (
                    f"{exc.__class__.__name__}:{_clean(exc)}"
                )[:300]
                attempts.append(attempt)
                try:
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(250)
                except Exception:
                    pass

        return False, attempts

    @staticmethod
    def _summarize_ad_account_add_attempts(
        attempts: list[dict[str, Any]],
    ) -> list[str]:
        summary: list[str] = []
        for row in attempts[-8:]:
            if not isinstance(row, dict):
                continue
            popup = row.get("post_click_candidates")
            popup_head = ""
            if isinstance(popup, list) and popup:
                popup_head = _clean(popup[0])[:120]
            new_right_pane = row.get("new_right_pane")
            new_head = ""
            if isinstance(new_right_pane, list) and new_right_pane:
                new_head = _clean(new_right_pane[0])[:120]
            parts = [
                f"x={int(row.get('x') or 0)}",
                f"y={int(row.get('y') or 0)}",
                f"clicked={bool(row.get('clicked'))}",
                f"mode={_clean(row.get('click_mode')) or '-'}",
                f"state={_clean(row.get('ui_state_after')) or '-'}",
                f"create={bool(row.get('create_entry_found'))}",
            ]
            popup_create = row.get("popup_create")
            if isinstance(popup_create, dict):
                parts.append(
                    "popup_count="
                    f"{int(popup_create.get('popup_count') or 0)}"
                )
            if bool(row.get("create_surface_seen")):
                parts.append("create_surface=True")
            state_direct = row.get("state_create_direct")
            if isinstance(state_direct, dict):
                parts.append(
                    "state_direct="
                    f"{bool(state_direct.get('clicked'))}"
                )
                direct_kind = _clean(state_direct.get("target_kind"))
                direct_mode = _clean(state_direct.get("mode"))
                direct_target = state_direct.get("target")
                if direct_kind:
                    parts.append(f"target={direct_kind}")
                if direct_mode:
                    parts.append(f"click_mode={direct_mode}")
                if isinstance(direct_target, dict):
                    direct_text = _clean(direct_target.get("text"))[:90]
                    top_tag = _clean(
                        direct_target.get("center_top_tag")
                    )[:24]
                    top_text = _clean(
                        direct_target.get("center_top_text")
                    )[:70]
                    if direct_text:
                        parts.append(f"target_text={direct_text}")
                    if top_tag or top_text:
                        parts.append(
                            "center_top="
                            f"{top_tag}:{top_text}"[:100]
                        )
            if bool(row.get("preserve_create_surface")):
                parts.append("preserved=True")
            skip = _clean(row.get("skip"))
            error = _clean(row.get("error"))
            if skip:
                parts.append(f"skip={skip}")
            if error:
                parts.append(f"error={error[:100]}")
            ui_errors = row.get("ui_errors")
            if isinstance(ui_errors, list) and ui_errors:
                parts.append(
                    f"ui_error={_clean(ui_errors[0])[:100]}"
                )
            if popup_head:
                parts.append(f"popup={popup_head}")
            if new_head:
                parts.append(f"new={new_head}")
            summary.append(" ".join(parts))
        return summary

    async def _click_ad_account_create_entry_in_popup(
        self,
    ) -> dict[str, Any]:
        """Click Create RK only inside a currently visible Meta popup surface.

        This deliberately refuses matching text from the normal Ad Accounts
        content pane. It is used immediately after a concrete Add click.
        """
        if self.page is None:
            return {"clicked": False}

        try:
            result = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && s.pointerEvents !== 'none';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                    const createWords = [
                        'create','créer','создать','створити','erstellen',
                        'তৈরি করুন','tạo','बनाएँ','बनाएं'
                    ];
                    const accountWords = [
                        'ad account','advertising account','compte publicitaire',
                        'реклам','werbekonto','বিজ্ঞাপন অ্যাকাউন্ট',
                        'tài khoản quảng cáo','विज्ञापन खाता','विज्ञापन खाते'
                    ];

                    const popupRoots = [...document.querySelectorAll(
                        '[role="menu"],[role="listbox"],[role="dialog"],'
                        + '[aria-modal="true"]'
                    )].filter(visible);

                    const rows = [];
                    for (const root of popupRoots) {
                        const headings = [...root.querySelectorAll(
                            'h1,h2,h3,[role="heading"]'
                        )].filter(visible).slice(0, 3).map(node =>
                            node.innerText || node.textContent || ''
                        ).join(' ');
                        const rootIdentity = clean(
                            (root.getAttribute('aria-label') || '') + ' ' +
                            (root.getAttribute('title') || '') + ' ' +
                            headings
                        );
                        if ([
                            'meta ai',
                            'assistant business meta ai',
                            'meta ai business assistant',
                            'assistant meta ai'
                        ].some(word => rootIdentity.includes(word))) {
                            continue;
                        }
                        for (const el of root.querySelectorAll(
                            'button,a,span,div,[role="button"],'
                            + '[role="menuitem"],[role="menuitemradio"],'
                            + '[role="option"],[tabindex]'
                        )) {
                            if (!visible(el)) continue;
                            const text = clean(
                                (el.getAttribute('aria-label') || '') + ' ' +
                                (el.getAttribute('title') || '') + ' ' +
                                (el.innerText || el.textContent || '')
                            );
                            if (!text || text.length > 220) continue;
                            if (!createWords.some(w => text.includes(w))) continue;
                            if (!accountWords.some(w => text.includes(w))) continue;

                            const clickable = el.closest(
                                'button,a,[role="button"],[role="menuitem"],'
                                + '[role="menuitemradio"],[role="option"],'
                                + '[tabindex]:not([tabindex="-1"])'
                            ) || el;
                            if (!visible(clickable)) continue;
                            if (
                                clickable.hasAttribute('disabled')
                                || clickable.getAttribute('aria-disabled') === 'true'
                            ) continue;

                            const r = clickable.getBoundingClientRect();
                            rows.push({
                                el: clickable,
                                text,
                                x: Math.round(r.x),
                                y: Math.round(r.y),
                                tag: clickable.tagName || '',
                                role: clickable.getAttribute('role') || '',
                                score:
                                    (clickable === el ? 100 : 0)
                                    + Math.round(r.y)
                            });
                        }
                    }

                    rows.sort((a,b) => a.score - b.score);
                    const best = rows[0];
                    if (!best) {
                        return {
                            clicked:false,
                            popup_count:popupRoots.length
                        };
                    }

                    best.el.scrollIntoView({block:'center'});
                    best.el.click();
                    return {
                        clicked:true,
                        popup_count:popupRoots.length,
                        text:best.text,
                        x:best.x,
                        y:best.y,
                        tag:best.tag,
                        role:best.role
                    };
                }"""
            )
            if isinstance(result, dict):
                return {
                    "clicked": bool(result.get("clicked")),
                    "popup_count": int(result.get("popup_count") or 0),
                    "text": _clean(result.get("text"))[:180],
                    "x": int(result.get("x") or 0),
                    "y": int(result.get("y") or 0),
                    "tag": _clean(result.get("tag"))[:40],
                    "role": _clean(result.get("role"))[:80],
                }
        except Exception as exc:
            return {
                "clicked": False,
                "error": f"{exc.__class__.__name__}: {_clean(exc)}"[:300],
            }
        return {"clicked": False}

    async def _ad_account_popup_candidates(self) -> list[str]:
        """Return compact visible popup/menu text after clicking Add."""
        if self.page is None:
            return []
        try:
            rows = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const out = [];
                    const seen = new Set();
                    const selectors = [
                        '[role="menu"] [role="menuitem"]',
                        '[role="menu"] [role="menuitemradio"]',
                        '[role="listbox"] [role="option"]',
                        '[role="dialog"] button',
                        '[role="dialog"] a',
                        '[data-visualcompletion="ignore-dynamic"] [role="button"]',
                        '[data-visualcompletion="ignore-dynamic"] a'
                    ];
                    for (const selector of selectors) {
                        for (const el of document.querySelectorAll(selector)) {
                            if (!visible(el)) continue;
                            const r = el.getBoundingClientRect();
                            const insideSemanticPopup = Boolean(
                                el.closest(
                                    '[role="menu"],[role="listbox"],[role="dialog"],[aria-modal="true"]'
                                )
                            );
                            // Dynamic Meta wrappers also cover the whole SPA.
                            // For non-semantic popup fallbacks, ignore the left
                            // navigation entirely.
                            if (!insideSemanticPopup && r.x < 300) continue;
                            const text = clean(
                                (el.getAttribute('aria-label') || '') + ' ' +
                                (el.getAttribute('title') || '') + ' ' +
                                (el.innerText || el.textContent || '')
                            );
                            if (!text || text.length > 260) continue;
                            const row = text
                                + ' [tag=' + (el.tagName || '')
                                + ' role=' + (el.getAttribute('role') || '')
                                + ' x=' + Math.round(r.x)
                                + ' y=' + Math.round(r.y)
                                + ']';
                            if (seen.has(row)) continue;
                            seen.add(row);
                            out.push(row);
                            if (out.length >= 30) return out;
                        }
                    }
                    return out;
                }"""
            )
            if isinstance(rows, list):
                return [_clean(x)[:300] for x in rows if _clean(x)][:30]
        except Exception:
            pass
        return []

    async def _ad_account_action_candidates(self) -> list[str]:
        if self.page is None:
            return []
        try:
            rows = await self.page.evaluate(
                """() => {
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    };
                    const clean = text => (text || '')
                        .normalize('NFKC')
                        .replace(/\u00a0/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const markers = [
                        'add','create','ad account','advertising account',
                        'ajouter','créer','compte publicitaire',
                        'добавить','создать','реклам',
                        'додати','створити',
                        'hinzufügen','erstellen','werbekonto',
                        'যোগ করুন','তৈরি করুন','বিজ্ঞাপন অ্যাকাউন্ট',
                        'thêm','tạo','tài khoản quảng cáo',
                        'जोड़ें','बनाएँ','बनाएं','विज्ञापन खाता'
                    ];
                    const out = [];
                    const seen = new Set();
                    for (const el of document.querySelectorAll(
                        'button,a,[role="button"],[role="link"],[role="menuitem"],'
                        + '[role="menuitemradio"],[role="option"]'
                    )) {
                        if (!visible(el)) continue;
                        const text = clean(
                            (el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.innerText || el.textContent || '')
                        );
                        const lower = text.toLowerCase();
                        if (!text || text.length > 180) continue;
                        if (!markers.some(x => lower.includes(x))) continue;
                        const r = el.getBoundingClientRect();
                        const key = [
                            text,
                            el.tagName || '',
                            el.getAttribute('role') || '',
                            Math.round(r.x),
                            Math.round(r.y)
                        ].join('|');
                        if (seen.has(key)) continue;
                        seen.add(key);
                        out.push(
                            text
                            + ' [tag=' + (el.tagName || '')
                            + ' role=' + (el.getAttribute('role') || '')
                            + ' x=' + Math.round(r.x)
                            + ' y=' + Math.round(r.y)
                            + ']'
                        );
                        if (out.length >= 30) break;
                    }
                    return out;
                }"""
            )
            if isinstance(rows, list):
                return [_clean(value)[:180] for value in rows if _clean(value)][:30]
        except Exception:
            pass
        return []

    async def _wait_for_ad_account_create_action(
        self,
        *,
        timeout_seconds: float = 10.0,
    ) -> bool:
        """Wait for a real Add/Create control in the Ad Accounts content pane.

        The left settings navigation itself contains localized "Ad accounts"
        text, so body-text hydration markers are not enough. This probe waits
        for an actionable control in the main pane and deliberately ignores
        sidebar-only labels.
        """
        if self.page is None:
            return False

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            try:
                ready = bool(
                    await self.page.evaluate(
                        """() => {
                            const visible = el => {
                                const r = el.getBoundingClientRect();
                                const s = getComputedStyle(el);
                                return r.width > 0 && r.height > 0
                                    && s.display !== 'none'
                                    && s.visibility !== 'hidden'
                                    && s.pointerEvents !== 'none';
                            };
                            const clean = text => (text || '')
                                .replace(/\s+/g, ' ').trim().toLowerCase();
                            const accountWords = [
                                'ad account','advertising account','реклам',
                                'werbekonto','compte publicitaire',
                                'বিজ্ঞাপন অ্যাকাউন্ট','tài khoản quảng cáo',
                                'विज्ञापन खाता','विज्ञापन खाते'
                            ];
                            const actionWords = [
                                'add','create','ajouter','créer',
                                'добавить','создать','додати','створити',
                                'hinzufügen','erstellen',
                                'যোগ করুন','তৈরি করুন',
                                'thêm','tạo','जोड़ें','बनाएँ','बनाएं'
                            ];
                            const genericActions = new Set([
                                'add','create','ajouter','créer',
                                'добавить','создать','додати','створити',
                                'hinzufügen','erstellen',
                                'যোগ করুন','তৈরি করুন',
                                'thêm','tạo','जोड़ें','बनाएँ','बनाएं'
                            ]);
                            const localAccountContext = el => {
                                let cur = el;
                                for (
                                    let depth = 0;
                                    cur && depth < 7;
                                    depth++, cur = cur.parentElement
                                ) {
                                    if (!visible(cur)) continue;
                                    const rr = cur.getBoundingClientRect();
                                    if (rr.width > 1050 || rr.height > 520) continue;
                                    const t = clean(
                                        (cur.getAttribute('aria-label') || '') + ' ' +
                                        (cur.getAttribute('title') || '') + ' ' +
                                        (cur.innerText || cur.textContent || '')
                                    );
                                    if (accountWords.some(x => t.includes(x))) {
                                        return true;
                                    }
                                }
                                return false;
                            };
                            const nodes = [...document.querySelectorAll(
                                'button,a,[role="button"],[role="menuitem"],[aria-haspopup]'
                            )];
                            return nodes.some(el => {
                                if (!visible(el)) return false;
                                const r = el.getBoundingClientRect();
                                const text = clean(
                                    (el.getAttribute('aria-label') || '') + ' ' +
                                    (el.getAttribute('title') || '') + ' ' +
                                    (el.innerText || el.textContent || '')
                                );
                                if (!text) return false;
                                const hasAccount = accountWords.some(x => text.includes(x));
                                const hasAction = actionWords.some(x => text.includes(x));
                                if (hasAccount && hasAction) return true;
                                return r.x >= 300 && genericActions.has(text);
                            });
                        }"""
                    )
                )
                if ready:
                    return True
            except Exception:
                pass
            await self.page.wait_for_timeout(250)

        return False

    async def _open_ad_account_create_form(
        self,
        *,
        business_id: str,
        account_name: str,
    ) -> None:
        business = _digits(business_id)
        if not business:
            raise BrowserBusinessError(
                "INVALID_BUSINESS_ID",
                "Ad Account create requires a numeric Business ID.",
                retryable=False,
            )

        self._mark_ad_account_phase("OPENING_SETTINGS")
        opened = False
        navigation_errors: list[str] = []
        hydration_attempts = 0
        for template in self.SETTINGS_AD_ACCOUNTS_URLS:
            try:
                target_url = template.format(business_id=business)
                await self._goto(target_url)
                hydration_attempts += 1
                if await self._wait_for_ad_account_settings_ready(
                    business_id=business,
                    timeout_seconds=12.0,
                ):
                    opened = True
                    break

                # Meta occasionally returns the Business Settings shell first
                # (interactive DOM, 0 body text) and hydrates only after a
                # reload. Retry the same URL once before trying another route.
                try:
                    await self.page.reload(
                        wait_until="commit",
                        timeout=self.timeout_ms,
                    )
                    await self.page.wait_for_timeout(500)
                    hydration_attempts += 1
                    if await self._wait_for_ad_account_settings_ready(
                        business_id=business,
                        timeout_seconds=10.0,
                    ):
                        opened = True
                        break
                except Exception as reload_exc:
                    navigation_errors.append(
                        f"reload: {type(reload_exc).__name__}: {reload_exc}"
                    )
            except BrowserBusinessError as exc:
                navigation_errors.append(f"{exc.code}: {exc}")
                if exc.code in {
                    "SESSION_EXPIRED",
                    "CHECKPOINT_REQUIRED",
                    "TWO_FACTOR_REQUIRED",
                    "FACEBOOK_TEMPORARILY_BLOCKED",
                }:
                    raise

        if not opened:
            diag = await self._diagnostic("ad_account_settings_unavailable")
            diag["business_id"] = business
            diag["navigation_errors"] = navigation_errors[-6:]
            diag["hydration_attempts"] = hydration_attempts
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_UI_UNAVAILABLE",
                "Meta Business Settings Ad Accounts surface could not be opened.",
                retryable=True,
                diagnostic=diag,
            )

        # Meta's migrated settings URL can land on the generic settings shell
        # even though the URL already contains /ad_accounts. Resolve and open
        # Meta's own sidebar href first, then require a real right-pane action.
        self._mark_ad_account_phase("OPENING_AD_ACCOUNTS_SECTION")
        section_clicked = await self._activate_ad_account_settings_section(
            business_id=business,
        )
        action_surface_ready = await self._wait_for_ad_account_create_action(
            timeout_seconds=8.0 if section_clicked else 4.0,
        )
        section_reload_attempted = False
        section_route_attempts: list[str] = []

        # If Meta mounted only the generic Settings shell, reload the route that
        # the actual sidebar item resolved to. This is intentionally different
        # from re-clicking the same text node.
        if section_clicked and not action_surface_ready:
            current_url = _clean(self.page.url)
            if (
                "/settings/ad_accounts" in current_url
                or "/settings/ad-accounts" in current_url
            ):
                try:
                    await self.page.reload(
                        wait_until="commit",
                        timeout=self.timeout_ms,
                    )
                    section_reload_attempted = True
                    await self.page.wait_for_timeout(700)
                    await self._assert_authenticated()
                    await self._activate_ad_account_settings_section(
                        business_id=business,
                    )
                    action_surface_ready = (
                        await self._wait_for_ad_account_create_action(
                            timeout_seconds=8.0,
                        )
                    )
                except BrowserBusinessError as exc:
                    section_route_attempts.append(
                        f"reload:{exc.code}:{exc}"
                    )
                    if exc.code in {
                        "SESSION_EXPIRED",
                        "CHECKPOINT_REQUIRED",
                        "TWO_FACTOR_REQUIRED",
                        "FACEBOOK_TEMPORARILY_BLOCKED",
                    }:
                        raise
                except Exception as exc:
                    section_route_attempts.append(
                        f"reload:{exc.__class__.__name__}:{exc}"
                    )

        # Final bounded routing fallback: try every known Meta settings route,
        # but this time judge success only by the real right-pane Add/Create
        # control instead of sidebar text.
        if not action_surface_ready:
            for template in self.SETTINGS_AD_ACCOUNTS_URLS:
                target_url = template.format(business_id=business)
                try:
                    await self._goto(target_url)
                    await self.page.wait_for_timeout(450)
                    await self._assert_authenticated()
                    activated = await self._activate_ad_account_settings_section(
                        business_id=business,
                    )
                    ready = await self._wait_for_ad_account_create_action(
                        timeout_seconds=4.5 if activated else 2.5,
                    )
                    section_route_attempts.append(
                        f"{target_url}:activated={activated}:ready={ready}"
                    )
                    if ready:
                        action_surface_ready = True
                        section_clicked = section_clicked or activated
                        break
                except BrowserBusinessError as exc:
                    section_route_attempts.append(
                        f"{target_url}:{exc.code}:{exc}"
                    )
                    if exc.code in {
                        "SESSION_EXPIRED",
                        "CHECKPOINT_REQUIRED",
                        "TWO_FACTOR_REQUIRED",
                        "FACEBOOK_TEMPORARILY_BLOCKED",
                    }:
                        raise
                except Exception as exc:
                    section_route_attempts.append(
                        f"{target_url}:{exc.__class__.__name__}:{exc}"
                    )

        current_ui = await self._ad_account_ui_state()
        self._record_ad_account_ui_state(
            "ad_accounts_surface_ready",
            current_ui,
        )

        entry_clicked = self._ad_account_create_form_confirmed(current_ui)

        if not entry_clicked and _clean(
            current_ui.get("state")
        ).upper() == "BLOCKED":
            diag = await self._diagnostic(
                "ad_account_create_blocked_before_add"
            )
            diag["business_id"] = business
            diag["ui_state"] = current_ui
            diag["ui_trace"] = self._ad_account_ui_trace[-12:]
            raise BrowserBusinessError(
                "META_AD_ACCOUNT_CREATE_UNAVAILABLE",
                (
                    (current_ui.get("errors") or [
                        "Meta blocked Ad Account creation on this surface."
                    ])[0]
                ),
                retryable=False,
                diagnostic=diag,
            )

        if (
            not entry_clicked
            and _clean(current_ui.get("state")).upper() == "CREATE_ENTRY"
        ):
            # CREATE_ENTRY is produced only when _ad_account_ui_state tagged a
            # concrete target. Consume that exact target; never run a global
            # localized text click here because Business Suite can mount same-
            # named actions in background/assistant surfaces.
            before_direct = current_ui
            direct_meta = (
                await self._click_state_detected_ad_account_create_entry()
            )
            if direct_meta.get("clicked"):
                direct_transition = await self._wait_for_ad_account_ui_transition(
                    previous_signature=_clean(
                        before_direct.get("signature")
                    ),
                    timeout_seconds=3.0,
                    label="after_direct_create_tagged",
                    require_signature_change=True,
                )
                entry_clicked = self._ad_account_create_form_confirmed(
                    direct_transition
                )

        if (
            not entry_clicked
            and _clean(current_ui.get("state")).upper() == "CREATE_ENTRY"
        ):
            entry_clicked = (
                await self._wait_for_ad_account_create_entry(
                    timeout_seconds=2.5,
                )
            )

        if (
            not entry_clicked
            and _clean(current_ui.get("state")).upper() == "CREATE_ENTRY"
        ):
            before_dom = await self._ad_account_ui_state()
            dom_clicked = (
                await self._click_ad_account_action_dom(
                    allow_generic_add=False
                )
                == "create"
            )
            if dom_clicked:
                dom_transition = await self._wait_for_ad_account_ui_transition(
                    previous_signature=_clean(before_dom.get("signature")),
                    timeout_seconds=3.0,
                    label="after_direct_create_dom",
                    require_signature_change=True,
                )
                entry_clicked = self._ad_account_create_form_confirmed(
                    dom_transition
                )

        self._mark_ad_account_phase("ADD_PROBE")
        add_clicked = False
        post_add_candidates: list[str] = []
        add_attempts: list[dict[str, Any]] = []
        if not entry_clicked:
            entry_clicked, add_attempts = (
                await self._probe_ad_account_add_buttons()
            )
            add_clicked = any(
                bool(row.get("clicked"))
                for row in add_attempts
                if isinstance(row, dict)
            )
            if not entry_clicked and add_attempts:
                last_attempt = add_attempts[-1]
                post_add_candidates = list(
                    last_attempt.get("post_click_candidates") or []
                )[:30]

        create_surface_active = any(
            bool(row.get("preserve_create_surface"))
            for row in add_attempts
            if isinstance(row, dict)
        )

        if not entry_clicked and create_surface_active:
            # Add already worked and Meta is visibly on CREATE_ENTRY/DIALOG.
            # Stay on that state and retry only the Create-entry transition.
            # Do not reload the page or click Add again while this surface is
            # mounted.
            for recovery_index in range(2):
                entry_clicked = (
                    await self._wait_for_ad_account_create_entry(
                        timeout_seconds=3.5,
                    )
                )
                if entry_clicked:
                    break

                recovery_ui = await self._ad_account_ui_state()
                self._record_ad_account_ui_state(
                    f"preserved_create_surface_{recovery_index}",
                    recovery_ui,
                )
                recovery_state = _clean(
                    recovery_ui.get("state")
                ).upper()
                if recovery_state == "BLOCKED":
                    break
                if recovery_state not in {
                    "CREATE_ENTRY",
                    "INTRO_DIALOG",
                    "DIALOG",
                }:
                    create_surface_active = False
                    break

        if (
            not entry_clicked
            and add_clicked
            and not create_surface_active
            and not section_reload_attempted
        ):
            # Meta sometimes leaves the Ad Accounts pane in a stale React
            # state: the visible Add controls accept the click but no menu or
            # Create-RK entry is mounted. Previously this path failed
            # immediately because add_clicked=True skipped the only reload
            # fallback. Reload the exact Ad Accounts route once, reactivate the
            # section, and probe the fresh controls again.
            current_url = _clean(self.page.url)
            if (
                "/settings/ad_accounts" in current_url
                or "/settings/ad-accounts" in current_url
            ):
                try:
                    await self.page.reload(
                        wait_until="commit",
                        timeout=self.timeout_ms,
                    )
                    section_reload_attempted = True
                    section_route_attempts.append(
                        "post_add_stale_reload"
                    )
                    await self.page.wait_for_timeout(700)
                    await self._assert_authenticated()
                    activated = await self._activate_ad_account_settings_section(
                        business_id=business,
                    )
                    ready_after_reload = (
                        await self._wait_for_ad_account_create_action(
                            timeout_seconds=8.0 if activated else 4.0,
                        )
                    )
                    action_surface_ready = (
                        action_surface_ready or ready_after_reload
                    )
                    section_clicked = section_clicked or activated

                    refreshed_ui = await self._ad_account_ui_state()
                    self._record_ad_account_ui_state(
                        "after_stale_add_reload",
                        refreshed_ui,
                    )
                    if self._ad_account_create_form_confirmed(
                        refreshed_ui
                    ):
                        entry_clicked = True
                    else:
                        retry_entry, retry_attempts = (
                            await self._probe_ad_account_add_buttons()
                        )
                        add_attempts.extend(retry_attempts)
                        add_clicked = any(
                            bool(row.get("clicked"))
                            for row in add_attempts
                            if isinstance(row, dict)
                        )
                        entry_clicked = retry_entry
                        if not entry_clicked and retry_attempts:
                            post_add_candidates = list(
                                retry_attempts[-1].get(
                                    "post_click_candidates"
                                ) or []
                            )[:30]
                except BrowserBusinessError as exc:
                    section_route_attempts.append(
                        f"post_add_reload:{exc.code}:{exc}"
                    )
                    if exc.code in {
                        "SESSION_EXPIRED",
                        "CHECKPOINT_REQUIRED",
                        "TWO_FACTOR_REQUIRED",
                        "FACEBOOK_TEMPORARILY_BLOCKED",
                    }:
                        raise
                except Exception as exc:
                    section_route_attempts.append(
                        "post_add_reload:"
                        f"{exc.__class__.__name__}:{exc}"
                    )

        if not entry_clicked and not add_clicked:
            # No Add candidate was usable yet. Give Meta one late hydration
            # window, retry direct CREATE, then rescan all right-pane Add
            # controls once.
            await self.page.wait_for_timeout(1200)
            entry_clicked = await self._wait_for_ad_account_create_entry(
                timeout_seconds=2.0,
            )
            if not entry_clicked:
                late_entry, late_attempts = (
                    await self._probe_ad_account_add_buttons()
                )
                add_attempts.extend(late_attempts)
                add_clicked = any(
                    bool(row.get("clicked"))
                    for row in add_attempts
                    if isinstance(row, dict)
                )
                entry_clicked = late_entry
                if not entry_clicked and late_attempts:
                    post_add_candidates = list(
                        late_attempts[-1].get(
                            "post_click_candidates"
                        ) or []
                    )[:30]

        if not entry_clicked:
            final_ui = await self._ad_account_ui_state()
            self._record_ad_account_ui_state(
                "before_create_entry_failure",
                final_ui,
            )

            # One final read-only expansion for Meta's informational card.
            # This is safe because it cannot submit/create anything.
            if not self._ad_account_create_form_confirmed(final_ui):
                final_details = (
                    await self._expand_ad_account_details_if_present()
                )
                if final_details.get("clicked"):
                    expanded_state = (
                        final_details.get("state")
                        if isinstance(final_details.get("state"), dict)
                        else {}
                    )
                    if expanded_state:
                        final_ui = expanded_state
                        self._record_ad_account_ui_state(
                            "after_create_entry_details",
                            final_ui,
                        )

            if self._ad_account_create_form_confirmed(final_ui):
                entry_clicked = True
            elif _clean(final_ui.get("state")).upper() == "BLOCKED":
                diag = await self._diagnostic(
                    "ad_account_create_blocked_after_add"
                )
                diag["business_id"] = business
                diag["ui_state"] = final_ui
                diag["add_attempt_summary"] = (
                    self._summarize_ad_account_add_attempts(
                        add_attempts
                    )
                )
                diag["ui_trace"] = self._ad_account_ui_trace[-16:]
                raise BrowserBusinessError(
                    "META_AD_ACCOUNT_CREATE_UNAVAILABLE",
                    (
                        (final_ui.get("errors") or [
                            "Meta blocked Ad Account creation after Add."
                        ])[0]
                    ),
                    retryable=False,
                    diagnostic=diag,
                )

        if not entry_clicked:
            raw_diag = await self._diagnostic(
                "ad_account_create_entry_missing"
            )
            diag: dict[str, Any] = {
                "stage": "ad_account_create_entry_missing",
                "business_id": business,
                "add_attempt_summary": (
                    self._summarize_ad_account_add_attempts(add_attempts)
                ),
                "add_clicked": add_clicked,
                "action_surface_ready": action_surface_ready,
                "post_add_candidates": post_add_candidates[:8],
                "section_clicked": section_clicked,
                "section_reload_attempted": section_reload_attempted,
                "section_activation": self._last_ad_account_section_diagnostic,
                "action_candidates": (
                    await self._ad_account_action_candidates()
                ),
                "add_attempts": add_attempts[-8:],
                "section_route_attempts": section_route_attempts[-8:],
                "hydration_attempts": hydration_attempts,
                "ui_state": final_ui,
                "ui_trace": self._ad_account_ui_trace[-16:],
                "right_pane_snapshot": (
                    await self._ad_account_right_pane_snapshot()
                )[:30],
            }
            for key, value in raw_diag.items():
                if key not in diag:
                    diag[key] = value
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_UI_CHANGED",
                "Meta Ad Account create entry was not found.",
                retryable=True,
                diagnostic=diag,
            )

        # Meta can place an ownership/usage step before immutable RK
        # details. Walk only safe pre-submit transitions here: choose our own
        # business and use Next/Continue labels. Never click Create at this
        # stage.
        safe_next_names = (
            "Next",
            "Continue",
            "Далее",
            "Продолжить",
            "Далі",
            "Продовжити",
            "Weiter",
            "Suivant",
            "Continuer",
            "পরবর্তী",
            "চালিয়ে যান",
            "Tiếp",
            "Tiếp tục",
            "अगला",
            "आगे",
            "जारी रखें",
        )
        pre_details_trace: list[dict[str, Any]] = []
        for pre_step in range(4):
            current_pre = await self._ad_account_ui_state()
            self._record_ad_account_ui_state(
                f"pre_details_{pre_step}",
                current_pre,
            )
            if bool(current_pre.get("name_input")):
                break

            ownership_present = self._ad_account_ownership_step_present(
                current_pre
            )
            if not ownership_present:
                break

            selected = await self._select_own_business_if_present()
            before_signature = _clean(current_pre.get("signature"))
            next_clicked = False
            if selected:
                await self.page.wait_for_timeout(200)
                # Prefer the RK-bounded semantic probe. A global
                # role/name search can hit an unrelated "Continue" elsewhere
                # in Business Suite when multiple dialogs/panels are mounted.
                meta = (
                    await self._click_ad_account_form_action_by_visible_text(
                        "next"
                    )
                )
                next_clicked = bool(meta.get("clicked"))
                if not next_clicked:
                    next_clicked = await self._click_named(
                        safe_next_names,
                        click_timeout_ms=2500,
                    )

            pre_details_trace.append(
                {
                    "step": pre_step,
                    "ownership_present": ownership_present,
                    "selected": selected,
                    "next_clicked": next_clicked,
                }
            )
            if not selected:
                break

            transition = await self._wait_for_ad_account_ui_transition(
                previous_signature=before_signature,
                timeout_seconds=4.0,
                label=f"pre_details_{pre_step}_after",
                require_signature_change=True,
            )
            if bool(transition.get("name_input")):
                break
            if not next_clicked and not self._ad_account_ownership_step_present(
                transition
            ):
                break

        confirmed_form = await self._ad_account_ui_state()
        self._record_ad_account_ui_state(
            "before_form_fill",
            confirmed_form,
        )
        if not self._ad_account_create_form_confirmed(confirmed_form):
            raw_diag = await self._diagnostic(
                "ad_account_create_click_no_form"
            )
            raw_diag["business_id"] = business
            raw_diag["ui_state"] = confirmed_form
            raw_diag["ui_trace"] = self._ad_account_ui_trace[-16:]
            raw_diag["pre_details_trace"] = pre_details_trace[-8:]
            raw_diag["action_candidates"] = (
                await self._ad_account_action_candidates()
            )
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_UI_CHANGED",
                (
                    "Meta Create action was clicked, but the Add-RK wizard "
                    "did not open. ReMask will not guess an account-name field "
                    "on the normal Ad Accounts page."
                ),
                retryable=True,
                diagnostic=raw_diag,
            )

        self._mark_ad_account_phase("FORM_LOADING")
        await self.page.wait_for_timeout(350)

        name_labels = (
            "Ad account name",
            "Advertising account name",
            "Account name",
            "Название рекламного аккаунта",
            "Название аккаунта",
            "Назва рекламного акаунта",
            "Назва облікового запису",
            "Name des Werbekontos",
            "Nom du compte publicitaire",
            "Nom du compte",
            "বিজ্ঞাপন অ্যাকাউন্টের নাম",
            "Tên tài khoản quảng cáo",
            "विज्ञापन खाते का नाम",
            "विज्ञापन खाता नाम",
        )
        name_filled = False
        name_deadline = time.monotonic() + 6.0
        while time.monotonic() < name_deadline and not name_filled:
            name_filled = await self._fill_first(
                labels=name_labels,
                value=account_name,
                fill_timeout_ms=2500,
            )
            if not name_filled:
                await self.page.wait_for_timeout(250)

        if not name_filled:
            # Meta may render the Add-RK wizard directly in the right settings
            # pane without a semantic <form> or role=dialog wrapper. Collect
            # editable candidates from the whole right pane, but only use a
            # unique safe text control so we never guess a search/filter box.
            try:
                candidates = self.page.locator(
                    'input:visible,textarea:visible,'
                    '[role="textbox"]:visible,[contenteditable="true"]:visible'
                )
                count = min(await candidates.count(), 30)
            except Exception:
                count = 0

            safe_candidates: list[Any] = []
            for index in range(count):
                candidate = candidates.nth(index)
                try:
                    box = await candidate.bounding_box()
                    if (
                        not isinstance(box, dict)
                        or float(box.get("x") or 0) < 280
                        or float(box.get("y") or 0) < 35
                        or float(box.get("y") or 0) > 795
                    ):
                        continue

                    kind = _clean(
                        await candidate.get_attribute("type")
                    ).lower()
                    if kind in {
                        "hidden","checkbox","radio","submit","button","file"
                    }:
                        continue

                    meta_text = " ".join(
                        _clean(await candidate.get_attribute(attr))
                        for attr in (
                            "placeholder","aria-label","name","id","title"
                        )
                    ).casefold()
                    if any(
                        marker in meta_text
                        for marker in (
                            "search","recherche","chercher","поиск","пошук",
                            "suchen","অনুসন্ধান","tìm kiếm","खोज"
                        )
                    ):
                        continue

                    try:
                        if not await candidate.is_editable():
                            continue
                    except Exception:
                        # contenteditable nodes do not always expose the same
                        # editable semantics through every Playwright build.
                        if _clean(
                            await candidate.get_attribute("contenteditable")
                        ).lower() != "true":
                            continue

                    current = ""
                    try:
                        current = _clean(await candidate.input_value())
                    except Exception:
                        try:
                            current = _clean(await candidate.inner_text())
                        except Exception:
                            current = ""
                    if current and len(current) > 2:
                        continue

                    safe_candidates.append(candidate)
                except Exception:
                    continue

            if len(safe_candidates) == 1:
                candidate = safe_candidates[0]
                try:
                    try:
                        await candidate.fill(account_name, timeout=2500)
                    except Exception:
                        await candidate.click(timeout=1500)
                        await self.page.keyboard.press("Control+A")
                        await self.page.keyboard.type(account_name)
                    name_filled = True
                except Exception:
                    name_filled = False

        if name_filled:
            self._mark_ad_account_phase("FORM_NAME_FILLED")

        if not name_filled:
            diag = await self._diagnostic("ad_account_name_input_missing")
            diag["business_id"] = business
            diag["form_candidates"] = await self._ad_account_form_candidates()
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_UI_CHANGED",
                "Meta Ad Account form opened but the account-name field was not found.",
                retryable=True,
                diagnostic=diag,
            )

    async def create_ad_account(
        self,
        *,
        business_id: str,
        account_name: str,
        currency: str = "USD",
        timezone_id: int = 1,
        before_submit: CheckpointCallback | None = None,
    ) -> BrowserAdAccountResult:
        business = _digits(business_id)
        name = _clean(account_name)
        if not business:
            raise BrowserBusinessError(
                "INVALID_BUSINESS_ID",
                "Ad Account create requires a numeric Business ID.",
                retryable=False,
            )
        if not name:
            raise BrowserBusinessError(
                "INVALID_INPUT",
                "Ad Account name is required.",
                retryable=False,
            )

        self._ad_account_create_sent = False
        self._mark_ad_account_phase("OPENING_CREATE_FLOW")
        await self._open_ad_account_create_form(
            business_id=business,
            account_name=name,
        )
        self._mark_ad_account_phase("FORM_READY")

        async def checkpoint(patch: dict[str, Any]) -> None:
            if before_submit is None:
                return
            await before_submit(
                {
                    **patch,
                    "business_id": business,
                    "account_name": name,
                    "currency": _clean(currency).upper(),
                    "timezone_id": int(timezone_id),
                }
            )

        await checkpoint(
            {
                "phase": "CREATE_PREPARED",
                "activity": "AD_ACCOUNT_FORM_READY",
                "activity_at": int(time.time()),
            }
        )

        loop = asyncio.get_running_loop()
        gate_future: asyncio.Future[bool] = loop.create_future()
        response_future: asyncio.Future[Any] = loop.create_future()
        graphql_candidates: list[dict[str, Any]] = []
        network_candidates: list[dict[str, Any]] = []

        def observe_request(request: Any) -> None:
            try:
                row = self._safe_meta_network_request_summary(request)
                if not row:
                    return
                key = (
                    row.get("host"),
                    row.get("path"),
                    row.get("browser_method"),
                    row.get("effective_method"),
                    row.get("friendly_name"),
                    row.get("doc_id"),
                    tuple(row.get("param_keys") or []),
                )
                if any(
                    (
                        old.get("host"),
                        old.get("path"),
                        old.get("browser_method"),
                        old.get("effective_method"),
                        old.get("friendly_name"),
                        old.get("doc_id"),
                        tuple(old.get("param_keys") or []),
                    ) == key
                    for old in network_candidates
                ):
                    return
                network_candidates.append(row)
                if len(network_candidates) > 40:
                    del network_candidates[:-40]
            except Exception:
                return

        async def gate(route: Any, request: Any) -> None:
            request_meta = _request_graphql_meta(request)
            decoded = _clean(request_meta.get("decoded_raw")).casefold()
            friendly = _clean(request_meta.get("friendly_name")).casefold()
            if (
                _clean(request_meta.get("method")).upper() == "POST"
                and "graphql" in _clean(request_meta.get("url")).lower()
            ):
                summary = self._safe_graphql_request_summary(request)
                summary["business_seen"] = bool(business in decoded)
                summary["name_seen"] = bool(name.casefold() in decoded)
                summary["matched_create"] = bool(
                    self._request_matches_ad_account_create(
                        request,
                        business_id=business,
                        account_name=name,
                    )
                )
                key = (
                    summary.get("friendly_name"),
                    summary.get("doc_id"),
                    tuple(summary.get("input_keys") or []),
                    summary.get("matched_create"),
                )
                if not any(
                    (
                        row.get("friendly_name"),
                        row.get("doc_id"),
                        tuple(row.get("input_keys") or []),
                        row.get("matched_create"),
                    )
                    == key
                    for row in graphql_candidates
                ):
                    graphql_candidates.append(summary)
                    if len(graphql_candidates) > 24:
                        del graphql_candidates[:-24]

            if not self._request_matches_ad_account_create(
                request,
                business_id=business,
                account_name=name,
            ):
                await route.continue_()
                return

            if gate_future.done():
                await route.continue_()
                return

            try:
                await checkpoint(
                    {
                        "phase": "CREATE_SUBMITTED",
                        "activity": "AD_ACCOUNT_CREATE_SUBMITTED",
                        "activity_at": int(time.time()),
                        "network_gate": "before_meta_send",
                        "create_doc_id": _clean(
                            request_meta.get("doc_id")
                        ),
                        "create_friendly_name": _clean(
                            request_meta.get("friendly_name")
                        ),
                    }
                )
            except Exception as exc:
                try:
                    await route.abort()
                finally:
                    if not gate_future.done():
                        gate_future.set_exception(
                            BrowserBusinessError(
                                "CREATE_CHECKPOINT_FAILED_BEFORE_SEND",
                                (
                                    "ReMask intercepted Meta Add-RK CREATE but "
                                    "could not persist the submitted checkpoint, "
                                    "so the request was blocked before Meta."
                                ),
                                retryable=True,
                            )
                        )
                return

            patched_post_data, attribution_defaults = (
                _ad_account_required_attribution_post_data(
                    request,
                    currency=_clean(currency).upper(),
                    timezone_id=int(timezone_id),
                )
            )
            self._ad_account_create_sent = True
            self._mark_ad_account_phase("CREATE_SUBMITTED")

            if attribution_defaults:
                await checkpoint(
                    {
                        "phase": "CREATE_SUBMITTED",
                        "activity": "AD_ACCOUNT_REQUIRED_ATTRIBUTION_APPLIED",
                        "activity_at": int(time.time()),
                        "request_fields_applied": sorted(
                            attribution_defaults.keys()
                        ),
                    }
                )
                await route.continue_(post_data=patched_post_data)
            else:
                await route.continue_()
            if not gate_future.done():
                gate_future.set_result(True)

        def observe_response(response: Any) -> None:
            if response_future.done():
                return
            if self._response_matches_ad_account_create(
                response,
                business_id=business,
                account_name=name,
            ):
                response_future.set_result(response)

        await self.page.route("**/*graphql*", gate)
        self.page.on("request", observe_request)
        self.page.on("response", observe_response)

        next_names = (
            "Next",
            "Continue",
            "Suivant",
            "Continuer",
            "Weiter",
            "Fortfahren",
            "Далее",
            "Продолжить",
            "Далі",
            "Продовжити",
            "পরবর্তী",
            "চালিয়ে যান",
            "Tiếp",
            "Tiếp tục",
            "अगला",
            "आगे",
            "जारी रखें",
        )
        final_names = (
            "Create ad account",
            "Create account",
            "Create",
            "Создать рекламный аккаунт",
            "Создать аккаунт",
            "Создать",
            "Створити рекламний акаунт",
            "Створити обліковий запис",
            "Створити",
            "Werbekonto erstellen",
            "Konto erstellen",
            "Erstellen",
            "Créer un compte publicitaire",
            "Créer le compte publicitaire",
            "Créer le compte",
            "Créer",
            "বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন",
            "তৈরি করুন",
            "Tạo tài khoản quảng cáo",
            "Tạo",
            "विज्ञापन खाता बनाएँ",
            "विज्ञापन खाता बनाएं",
            "बनाएँ",
            "बनाएं",
        )

        self._mark_ad_account_phase("SUBMIT_UI")
        last_form_setup_signature = ""
        form_setup = await self._prepare_ad_account_form_fields(
            currency=currency,
            timezone_id=timezone_id,
        )
        self._ad_account_wizard_rect = (
            await self._capture_ad_account_wizard_rect()
        )
        initial_form_state = await self._ad_account_ui_state()
        last_form_setup_signature = _clean(
            initial_form_state.get("signature")
        )
        try:
            clicked_any = False
            own_business_selected = False
            final_click_attempted = False
            submit_attempts: list[dict[str, Any]] = []

            for step in range(12):
                if gate_future.done():
                    break

                before_state = await self._ad_account_ui_state()
                self._record_ad_account_ui_state(
                    f"submit_step_{step}_before",
                    before_state,
                )
                before_signature = _clean(before_state.get("signature"))

                if _clean(before_state.get("state")).upper() == "BLOCKED":
                    submit_attempts.append(
                        {
                            "step": step,
                            "action": "blocked",
                            "errors": list(before_state.get("errors") or [])[:3],
                        }
                    )
                    break

                # Ownership is a form choice, not merely a fallback
                # after Next. Select it before advancing whenever it appears,
                # otherwise Meta may carry a default/previous choice forward.
                if not own_business_selected:
                    own_selected_now = (
                        await self._select_own_business_if_present()
                    )
                    if own_selected_now:
                        own_business_selected = True
                        clicked_any = True
                        await self.page.wait_for_timeout(250)
                        ownership_state = await self._ad_account_ui_state()
                        self._record_ad_account_ui_state(
                            f"submit_step_{step}_ownership_selected",
                            ownership_state,
                        )
                        submit_attempts.append(
                            {
                                "step": step,
                                "action": "own_business_before_next",
                                "selected": True,
                                "state_after": _clean(
                                    ownership_state.get("state")
                                ),
                            }
                        )
                        ownership_signature = _clean(
                            ownership_state.get("signature")
                        )
                        if (
                            _clean(
                                ownership_state.get("state")
                            ).upper()
                            == "FORM"
                            and ownership_signature
                            and ownership_signature
                            != last_form_setup_signature
                        ):
                            form_setup = (
                                await self._prepare_ad_account_form_fields(
                                    currency=currency,
                                    timezone_id=timezone_id,
                                )
                            )
                            last_form_setup_signature = ownership_signature

                next_meta: dict[str, Any] = (
                    await self._click_ad_account_form_action_by_visible_text(
                        "next"
                    )
                )
                next_clicked = bool(next_meta.get("clicked"))

                if next_clicked:
                    clicked_any = True
                    transition = await self._wait_for_ad_account_ui_transition(
                        previous_signature=before_signature,
                        timeout_seconds=3.5,
                        label=f"submit_step_{step}_after_next",
                        require_signature_change=True,
                    )
                    submit_attempts.append(
                        {
                            "step": step,
                            "action": "next",
                            "fallback": next_meta,
                            "state_after": _clean(transition.get("state")),
                            "errors": list(transition.get("errors") or [])[:3],
                        }
                    )
                    if gate_future.done():
                        break

                    # Meta can show the ownership choice one or several
                    # transitions after the first Next. Keep probing until it
                    # is actually selected; do not burn the opportunity merely
                    # because it was absent on an earlier screen.
                    if not own_business_selected:
                        own_business_selected = (
                            await self._select_own_business_if_present()
                        )
                        if own_business_selected:
                            submit_attempts.append(
                                {
                                    "step": step,
                                    "action": "own_business_after_next",
                                    "selected": True,
                                }
                            )
                            await self.page.wait_for_timeout(250)

                    transition_signature = _clean(
                        transition.get("signature")
                    )
                    if (
                        _clean(transition.get("state")).upper() == "FORM"
                        and transition_signature
                        and transition_signature != last_form_setup_signature
                    ):
                        form_setup = await self._prepare_ad_account_form_fields(
                            currency=currency,
                            timezone_id=timezone_id,
                        )
                        last_form_setup_signature = transition_signature
                    continue

                if final_click_attempted:
                    submit_attempts.append(
                        {
                            "step": step,
                            "action": "final_blocked_duplicate",
                        }
                    )
                    break

                terms_meta = await self._accept_ad_account_terms_if_present()
                if bool(terms_meta.get("found")):
                    submit_attempts.append(
                        {
                            "step": step,
                            "action": "terms",
                            "terms": terms_meta,
                        }
                    )
                    if bool(terms_meta.get("clicked")):
                        clicked_any = True
                        await self.page.wait_for_timeout(250)

                async def persist_final_click_intent() -> None:
                    await checkpoint(
                        {
                            "phase": "CREATE_CLICK_INTENT",
                            "activity": "AD_ACCOUNT_CREATE_CLICK_INTENT",
                            "activity_at": int(time.time()),
                        }
                    )

                direct_final = await self._click_ad_account_final_interactive(
                    before_click=persist_final_click_intent,
                )
                final_clicked = bool(direct_final.get("clicked"))
                final_meta: dict[str, Any] = {
                    "interactive": direct_final,
                }

                if (
                    bool(direct_final.get("attempted"))
                    and not final_clicked
                ):
                    await checkpoint(
                        {
                            "phase": "CREATE_RESULT_UNKNOWN",
                            "resume_from": "RECONCILE_CREATE",
                            "activity": "AD_ACCOUNT_FINAL_CLICK_EXCEPTION",
                            "activity_at": int(time.time()),
                        }
                    )
                    raise BrowserBusinessError(
                        "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                        (
                            "Meta final Create click was attempted but Playwright "
                            "could not confirm the click result. Duplicate CREATE "
                            "is blocked; reconcile inventory before retry."
                        ),
                        retryable=True,
                        diagnostic={
                            "stage": "ad_account_final_click_exception",
                            "final_meta": final_meta,
                            "graphql_candidates": graphql_candidates[-12:],
                            "network_candidates": network_candidates[-24:],
                            "submit_attempts": submit_attempts[-12:],
                        },
                    )

                if final_clicked:
                    clicked_any = True
                    final_click_attempted = True
                    candidate_count_before_wait = len(graphql_candidates)

                    try:
                        await asyncio.wait_for(
                            asyncio.shield(gate_future),
                            timeout=6.0,
                        )
                    except asyncio.TimeoutError:
                        pass

                    if gate_future.done():
                        submit_attempts.append(
                            {
                                "step": step,
                                "action": "final",
                                "fallback": final_meta,
                                "gate": "matched",
                            }
                        )
                        break

                    transition = await self._wait_for_ad_account_ui_transition(
                        previous_signature=before_signature,
                        timeout_seconds=2.5,
                        label=f"submit_step_{step}_after_final",
                        require_signature_change=True,
                    )
                    new_network_candidates = graphql_candidates[
                        candidate_count_before_wait:
                    ]
                    usage_step_seen = any(
                        "createadaccountusagestep" in _clean(
                            row.get("friendly_name")
                        ).casefold()
                        or "adaccountusagestep" in _clean(
                            row.get("friendly_name")
                        ).casefold()
                        for row in new_network_candidates
                        if isinstance(row, dict)
                    )
                    submit_attempts.append(
                        {
                            "step": step,
                            "action": "final",
                            "fallback": final_meta,
                            "gate": (
                                "usage_step"
                                if usage_step_seen
                                else "not_matched"
                            ),
                            "state_after": _clean(transition.get("state")),
                            "errors": list(transition.get("errors") or [])[:3],
                            "new_graphql_candidates": new_network_candidates[-8:],
                        }
                    )

                    if usage_step_seen:
                        # The visible "Create ad account" control can advance
                        # Meta to a usage/ownership step before the real CREATE
                        # mutation. It is safe to continue: the network gate
                        # observed only the intermediate Query, not CREATE.
                        final_click_attempted = False
                        await checkpoint(
                            {
                                "phase": "CREATE_PREPARED",
                                "resume_from": "CREATE",
                                "activity": "AD_ACCOUNT_USAGE_STEP_OPENED",
                                "activity_at": int(time.time()),
                                "graphql_candidates": new_network_candidates[-8:],
                            }
                        )
                        await self.page.wait_for_timeout(300)
                        continue

                    # Meta closed the wizard after the final click. Before
                    # declaring uncertainty, inspect the resulting Ad Accounts
                    # table for the exact requested name and a unique account ID.
                    # This is read-only and cannot submit a duplicate CREATE.
                    await self.page.wait_for_timeout(900)
                    browser_inventory = (
                        await self.find_ad_account_in_inventory(
                            business_id=business,
                            account_name=name,
                            timeout_seconds=7.0,
                        )
                    )
                    if bool(browser_inventory.get("confirmed")):
                        reconciled_id = _clean(
                            browser_inventory.get("ad_account_id")
                        )
                        await checkpoint(
                            {
                                "phase": "CREATE_CONFIRMED",
                                "activity": (
                                    "AD_ACCOUNT_CREATE_CONFIRMED_BROWSER_INVENTORY"
                                ),
                                "activity_at": int(time.time()),
                                "ad_account_id": reconciled_id,
                                "create_friendly_name": "",
                                "create_response_path": (
                                    "business_settings_graphql_inventory"
                                ),
                            }
                        )
                        self._mark_ad_account_phase("CREATE_CONFIRMED")
                        return BrowserAdAccountResult(
                            business_id=business,
                            ad_account_id=reconciled_id,
                            response_friendly_name="",
                            response_doc_id="",
                            response_path=(
                                "business_settings_graphql_inventory"
                            ),
                        )

                    ui_reconcile = (
                        await self._reconcile_created_ad_account_from_ui(
                            business_id=business,
                            account_name=name,
                        )
                    )
                    if bool(ui_reconcile.get("confirmed")):
                        reconciled_id = _clean(
                            ui_reconcile.get("ad_account_id")
                        )
                        await checkpoint(
                            {
                                "phase": "CREATE_CONFIRMED",
                                "activity": "AD_ACCOUNT_CREATE_CONFIRMED_UI",
                                "activity_at": int(time.time()),
                                "ad_account_id": reconciled_id,
                                "create_friendly_name": "",
                                "create_response_path": (
                                    "business_settings_ui"
                                ),
                            }
                        )
                        self._mark_ad_account_phase("CREATE_CONFIRMED")
                        return BrowserAdAccountResult(
                            business_id=business,
                            ad_account_id=reconciled_id,
                            response_friendly_name="",
                            response_doc_id="",
                            response_path="business_settings_ui",
                        )

                    await checkpoint(
                        {
                            "phase": "CREATE_RESULT_UNKNOWN",
                            "resume_from": "RECONCILE_CREATE",
                            "activity": "AD_ACCOUNT_FINAL_CLICK_UNMATCHED",
                            "activity_at": int(time.time()),
                            "graphql_candidates": new_network_candidates[-8:],
                            "network_candidates": network_candidates[-24:],
                            "browser_inventory": browser_inventory,
                            "ui_reconcile": ui_reconcile,
                        }
                    )
                    try:
                        import logging
                        logging.getLogger("remask_worker").warning(
                            "[ad-account-final-unmatched] profile=%s business=%s "
                            "network_candidates=%s graphql_candidates=%s",
                            self.profile_id,
                            business,
                            json.dumps(
                                network_candidates[-24:],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            json.dumps(
                                new_network_candidates[-8:],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        )
                    except Exception:
                        pass
                    raise BrowserBusinessError(
                        "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                        (
                            "Meta final Create was clicked, but ReMask did not "
                            "match a definitive CREATE mutation. A second final "
                            "click is blocked; reconcile inventory before retry."
                        ),
                        retryable=True,
                        diagnostic={
                            "stage": "ad_account_final_click_unmatched",
                            "final_meta": final_meta,
                            "state_after": transition,
                            "graphql_candidates": graphql_candidates[-12:],
                            "network_candidates": network_candidates[-24:],
                            "browser_inventory": browser_inventory,
                            "ui_reconcile": ui_reconcile,
                            "submit_attempts": submit_attempts[-12:],
                        },
                    )

                submit_attempts.append(
                    {
                        "step": step,
                        "action": "none",
                        "state": _clean(before_state.get("state")),
                    }
                )
                break

            if gate_future.done() and gate_future.exception() is not None:
                raise gate_future.exception()

            if not gate_future.done():
                await checkpoint(
                    {
                        "phase": "CREATE_NOT_SUBMITTED",
                        "activity": "AD_ACCOUNT_CREATE_NOT_SUBMITTED",
                        "activity_at": int(time.time()),
                    }
                )
                raw_diag = await self._diagnostic(
                    "ad_account_create_submit_missing"
                )
                diag = {
                    "stage": "ad_account_create_submit_missing",
                    "clicked_any": clicked_any,
                    "final_click_attempted": final_click_attempted,
                    "submit_attempts": submit_attempts[-12:],
                    "wizard_rect": self._ad_account_wizard_rect,
                    "requested_currency": _clean(currency).upper(),
                    "requested_timezone_id": int(timezone_id),
                    "requested_timezone_name": self._timezone_name_for_id(
                        int(timezone_id)
                    ),
                    "form_setup": form_setup,
                    "label_control_probe": await self._ad_account_label_control_probe(),
                    "form_candidates": await self._ad_account_form_candidates(),
                    "submit_controls": await self._ad_account_submit_controls(),
                    "own_business_selected": own_business_selected,
                    "last_form_setup_signature": last_form_setup_signature[:1000],
                    "graphql_candidates": graphql_candidates[-12:],
                    "form_probe": await self._ad_account_form_candidates(),
                    "ui_state": await self._ad_account_ui_state(),
                    "ui_trace": self._ad_account_ui_trace[-16:],
                }
                for key, value in raw_diag.items():
                    if key not in diag:
                        diag[key] = value
                try:
                    import logging
                    logging.getLogger("remask_worker").warning(
                        "[ad-account-submit-missing] profile=%s business=%s "
                        "submit_attempts=%s wizard_rect=%s form_setup=%s "
                        "form_candidates=%s label_control_probe=%s submit_controls=%s",
                        self.profile_id,
                        business,
                        json.dumps(
                            submit_attempts[-12:],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            self._ad_account_wizard_rect,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            form_setup,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            diag.get("form_candidates") or [],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            diag.get("label_control_probe") or [],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            diag.get("submit_controls") or [],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                except Exception:
                    pass
                raise BrowserBusinessError(
                    "AD_ACCOUNT_CREATE_UI_CHANGED",
                    (
                        "Meta Ad Account form was opened, but ReMask could not "
                        "reach a safely identifiable CREATE request."
                    ),
                    retryable=True,
                    diagnostic=diag,
                )

            self._mark_ad_account_phase("WAITING_RESPONSE")
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(response_future),
                    timeout=min(15.0, float(self.timeout_seconds)),
                )
            except asyncio.TimeoutError as exc:
                await checkpoint(
                    {
                        "phase": "CREATE_RESULT_UNKNOWN",
                        "activity": "AD_ACCOUNT_RESPONSE_UNCONFIRMED",
                        "activity_at": int(time.time()),
                    }
                )
                raise BrowserBusinessError(
                    "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                    (
                        "Meta Add-RK CREATE passed the network gate but the "
                        "response was not observed. Reconcile inventory before retry."
                    ),
                    retryable=True,
                    diagnostic=await self._diagnostic(
                        "ad_account_create_response_missing"
                    ),
                ) from exc

            try:
                raw = await response.text()
                payload = _decode_graphql_text(raw)
            except Exception:
                payload = None

            request_meta = _request_graphql_meta(response.request)
            friendly = _clean(request_meta.get("friendly_name"))

            ad_account_id = ""
            response_path = ""
            if payload is not None:
                ad_account_id, response_path = (
                    _extract_created_ad_account_id(payload)
                )

            meta_errors = _graphql_error_details(payload)
            if not ad_account_id and meta_errors:
                await checkpoint(
                    {
                        "phase": "CREATE_REJECTED",
                        "activity": "AD_ACCOUNT_CREATE_REJECTED",
                        "activity_at": int(time.time()),
                        "meta_errors": meta_errors,
                        "create_friendly_name": friendly,
                    }
                )
                parts: list[str] = []
                for row in meta_errors[:3]:
                    code = _clean(row.get("code"))
                    subcode = _clean(row.get("subcode"))
                    message = _clean(row.get("message"))
                    prefix = "/".join(
                        value for value in (code, subcode) if value
                    )
                    if prefix and message:
                        parts.append(f"{prefix}: {message}")
                    elif message:
                        parts.append(message)
                    elif prefix:
                        parts.append(prefix)

                raise BrowserBusinessError(
                    "META_AD_ACCOUNT_CREATE_REJECTED",
                    (
                        " · ".join(parts)
                        or "Meta rejected Ad Account creation."
                    )[:2500],
                    retryable=_meta_error_retryable(meta_errors),
                    diagnostic={
                        "meta_errors": meta_errors,
                        "request": self._safe_graphql_request_summary(
                            response.request
                        ),
                    },
                )

            if not ad_account_id:
                await checkpoint(
                    {
                        "phase": "CREATE_RESULT_UNKNOWN",
                        "activity": "AD_ACCOUNT_ID_UNCONFIRMED",
                        "activity_at": int(time.time()),
                        "create_friendly_name": friendly,
                    }
                )
                raise BrowserBusinessError(
                    "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                    (
                        "Meta returned an Add-RK response but ReMask could not "
                        "prove a numeric Ad Account ID. Reconcile inventory before retry."
                    ),
                    retryable=True,
                    diagnostic={
                        "request": self._safe_graphql_request_summary(
                            response.request
                        ),
                    },
                )

            self._mark_ad_account_phase("CREATE_CONFIRMED")
            await checkpoint(
                {
                    "phase": "CREATE_CONFIRMED",
                    "activity": "AD_ACCOUNT_CREATE_CONFIRMED",
                    "activity_at": int(time.time()),
                    "ad_account_id": ad_account_id,
                    "create_friendly_name": friendly,
                    "create_response_path": response_path,
                }
            )
            return BrowserAdAccountResult(
                business_id=business,
                ad_account_id=ad_account_id,
                response_friendly_name=friendly,
                response_doc_id=_clean(request_meta.get("doc_id")),
                response_path=response_path,
            )

        finally:
            if not gate_future.done():
                gate_future.cancel()
            if not response_future.done():
                response_future.cancel()
            try:
                self.page.remove_listener("request", observe_request)
            except Exception:
                pass
            try:
                self.page.remove_listener("response", observe_response)
            except Exception:
                pass
            try:
                await self.page.unroute("**/*graphql*", gate)
            except Exception:
                pass

    async def capture_ad_account_create_request(
        self,
        *,
        business_id: str,
        account_name: str,
        currency: str = "USD",
        timezone_id: int = 1,
    ) -> dict[str, Any]:
        """Capture the exact current Add-RK GraphQL request without sending it.

        This mirrors the proven BM strategy: drive Meta's own Business Settings
        flow in the profile session, intercept the definitive CREATE request,
        abort it before Meta receives it, and return the exact doc_id, variables
        and request envelope for one controlled browser-native replay.
        """
        business = _digits(business_id)
        name = _clean(account_name)
        currency_code = _clean(currency).upper()
        try:
            timezone = int(timezone_id)
        except (TypeError, ValueError) as exc:
            raise BrowserBusinessError(
                "INVALID_INPUT",
                "Ad Account capture requires an integer timezone_id.",
                retryable=False,
            ) from exc

        if not business:
            raise BrowserBusinessError(
                "INVALID_BUSINESS_ID",
                "Ad Account capture requires a numeric Business ID.",
                retryable=False,
            )
        if not name:
            raise BrowserBusinessError(
                "INVALID_INPUT",
                "Ad Account capture requires an account name.",
                retryable=False,
            )
        if not currency_code:
            raise BrowserBusinessError(
                "INVALID_INPUT",
                "Ad Account capture requires a currency.",
                retryable=False,
            )
        if self.page is None:
            await self.open()

        self._ad_account_create_sent = False
        self._ad_account_final_capture_armed = False
        self._mark_ad_account_phase("CAPTURE_OPENING_CREATE_FLOW")
        await self._open_ad_account_create_form(
            business_id=business,
            account_name=name,
        )
        self._mark_ad_account_phase("CAPTURE_FORM_READY")

        # Put the wizard into the requested immutable state before capture.
        form_setup = await self._prepare_ad_account_form_fields(
            currency=currency_code,
            timezone_id=timezone,
        )
        self._ad_account_wizard_rect = (
            await self._capture_ad_account_wizard_rect()
        )

        loop = asyncio.get_running_loop()
        captured: asyncio.Future[dict[str, Any]] = loop.create_future()
        graphql_candidates: list[dict[str, Any]] = []
        capture_final_armed = False
        blocked_unclassified_create = False

        def plausible_final_create(request_meta: dict[str, Any]) -> bool:
            """Conservative safety gate for an unknown final CREATE mutation.

            During the final-click window it is safer to abort a strong unknown
            mutation than to let a renamed Meta CREATE reach production. This
            does NOT make the request replayable; only the definitive matcher
            can produce a captured request.
            """
            method = _clean(request_meta.get("method")).upper()
            url = _clean(request_meta.get("url")).lower()
            if method != "POST" or "graphql" not in url:
                return False

            friendly = _clean(request_meta.get("friendly_name")).casefold()
            decoded = _clean(request_meta.get("decoded_raw")).casefold()
            if any(
                marker in friendly
                for marker in (
                    "query","usage","search","list","lookup","typeahead",
                    "preview","validate",
                )
            ):
                return False

            variables = (
                request_meta.get("variables")
                if isinstance(request_meta.get("variables"), dict)
                else {}
            )
            variable_text = json.dumps(
                variables,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).casefold()

            business_match = bool(
                business
                and (
                    business in decoded
                    or business in variable_text
                )
            )
            if not business_match:
                return False

            immutable_hits = 0
            if name.casefold() in variable_text:
                immutable_hits += 1
            if currency_code.casefold() in variable_text:
                immutable_hits += 1
            if any(
                marker in variable_text
                for marker in (
                    "timezone_id","timezoneid","time_zone_id",
                    "timezone","time_zone",
                )
            ):
                immutable_hits += 1

            mutationish = any(
                marker in friendly or marker in decoded
                for marker in (
                    "create","submit","mutation","adaccount",
                    "ad_account",
                )
            )
            return mutationish and immutable_hits >= 2

        async def intercept(route: Any, request: Any) -> None:
            nonlocal blocked_unclassified_create
            request_meta = _request_graphql_meta(request)
            definitive_match = self._request_matches_ad_account_create(
                request,
                business_id=business,
                account_name=name,
            )
            plausible_unknown = bool(
                capture_final_armed
                and not definitive_match
                and plausible_final_create(request_meta)
            )

            if (
                _clean(request_meta.get("method")).upper() == "POST"
                and "graphql" in _clean(request_meta.get("url")).lower()
            ):
                summary = self._safe_graphql_request_summary(request)
                summary["matched_create"] = bool(definitive_match)
                summary["plausible_final_create"] = bool(plausible_unknown)
                summary["final_gate_armed"] = bool(capture_final_armed)
                graphql_candidates.append(summary)
                if len(graphql_candidates) > 24:
                    del graphql_candidates[:-24]

            if plausible_unknown:
                # Critical exactly-once invariant: a strong unknown mutation
                # observed after the final CTA is never allowed to escape the
                # capture pass. We abort it, but deliberately do not replay it
                # because the matcher could not prove its identity.
                blocked_unclassified_create = True
                await route.abort()
                return

            if not definitive_match:
                await route.continue_()
                return

            raw = _clean(getattr(request, "post_data", ""))
            parsed = parse_qs(raw, keep_blank_values=True) if raw else {}
            allowed = {
                "__aaid","__bid","__hs","__hblp","__hsdp","__rev","__s",
                "__hsi","__dyn","__csr","__comet_req","__spin_r","__spin_b",
                "__spin_t","__jssesw","__crn","__req","__ccg","dpr",
                "server_timestamps","fb_api_caller_class",
            }
            envelope = {
                key: _clean(values[0])
                for key, values in parsed.items()
                if (
                    key in allowed
                    and isinstance(values, list)
                    and values
                    and _clean(values[0])
                )
            }
            row = {
                "doc_id": _clean(request_meta.get("doc_id")),
                "friendly_name": _clean(
                    request_meta.get("friendly_name")
                ),
                "endpoint_url": _clean(getattr(request, "url", "")),
                "variables": (
                    request_meta.get("variables")
                    if isinstance(request_meta.get("variables"), dict)
                    else {}
                ),
                "request_envelope": envelope,
                "canary_name": name,
                "business_id": business,
                "currency": currency_code,
                "timezone_id": timezone,
                "source": "live_business_settings_capture",
            }

            # Critical invariant: the capture pass NEVER sends CREATE to Meta.
            await route.abort()
            if not captured.done():
                captured.set_result(row)

        await self.page.route("**/*graphql*", intercept)
        submit_attempts: list[dict[str, Any]] = []
        try:
            for step in range(12):
                if captured.done():
                    break

                before_state = await self._ad_account_ui_state()
                before_signature = _clean(before_state.get("signature"))

                if _clean(before_state.get("state")).upper() == "BLOCKED":
                    submit_attempts.append(
                        {
                            "step": step,
                            "action": "blocked",
                            "errors": list(
                                before_state.get("errors") or []
                            )[:3],
                        }
                    )
                    break

                selected = await self._select_own_business_if_present()
                if selected:
                    await self.page.wait_for_timeout(200)

                # Re-apply immutable fields after every wizard transition.
                form_setup = await self._prepare_ad_account_form_fields(
                    currency=currency_code,
                    timezone_id=timezone,
                )

                next_meta = (
                    await self._click_ad_account_form_action_by_visible_text(
                        "next"
                    )
                )
                if bool(next_meta.get("clicked")):
                    submit_attempts.append(
                        {
                            "step": step,
                            "action": "next",
                            "meta": next_meta,
                        }
                    )
                    await self._wait_for_ad_account_ui_transition(
                        previous_signature=before_signature,
                        timeout_seconds=3.5,
                        label=f"capture_step_{step}_after_next",
                        require_signature_change=True,
                    )
                    continue

                terms_meta = await self._accept_ad_account_terms_if_present()
                if bool(terms_meta.get("clicked")):
                    submit_attempts.append(
                        {
                            "step": step,
                            "action": "terms",
                            "meta": terms_meta,
                        }
                    )
                    await self.page.wait_for_timeout(200)

                capture_final_armed = True
                self._ad_account_final_capture_armed = True
                self._mark_ad_account_phase("CAPTURE_FINAL_ARMED")
                final_meta = await self._click_ad_account_final_interactive()
                final_clicked = bool(final_meta.get("clicked"))
                final_attempted = bool(
                    final_meta.get("attempted") or final_clicked
                )
                if not final_clicked:
                    fallback = (
                        await self._click_ad_account_form_action_by_visible_text(
                            "final"
                        )
                    )
                    final_clicked = bool(fallback.get("clicked"))
                    final_attempted = bool(
                        final_attempted
                        or fallback.get("attempted")
                        or final_clicked
                    )
                    if fallback:
                        final_meta["fallback"] = fallback

                if not final_attempted:
                    capture_final_armed = False
                    self._ad_account_final_capture_armed = False
                    self._mark_ad_account_phase("CAPTURE_FORM_READY")

                submit_attempts.append(
                    {
                        "step": step,
                        "action": "final" if final_clicked else "none",
                        "meta": final_meta,
                    }
                )

                if final_clicked:
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(captured),
                            timeout=3.0,
                        )
                    except asyncio.TimeoutError:
                        await self.page.wait_for_timeout(250)
                    if captured.done():
                        break
                    if blocked_unclassified_create:
                        # The safety gate already intercepted a strong unknown
                        # mutation. Do not click the irreversible CTA again in
                        # this browser pass; reconciliation decides whether a
                        # clean recapture is safe.
                        break

                if not final_clicked:
                    break

            if not captured.done():
                try:
                    row = await asyncio.wait_for(
                        asyncio.shield(captured),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError as exc:
                    diag = await self._diagnostic(
                        "ad_account_create_request_missing"
                    )
                    diag["business_id"] = business
                    diag["account_name"] = name
                    diag["form_setup"] = form_setup
                    diag["submit_attempts"] = submit_attempts[-12:]
                    diag["graphql_candidates"] = graphql_candidates[-12:]
                    diag["blocked_unclassified_create"] = bool(
                        blocked_unclassified_create
                    )
                    diag["ui_state"] = await self._ad_account_ui_state()
                    diag["ui_trace"] = self._ad_account_ui_trace[-16:]
                    raise BrowserBusinessError(
                        "AD_ACCOUNT_CREATE_REQUEST_NOT_OBSERVED",
                        (
                            "Meta Add-RK wizard was driven to the final action, "
                            "but ReMask did not observe a definitive private "
                            "CREATE request. The final capture gate blocked any "
                            "strong unknown CREATE candidate; no captured CREATE "
                            "was intentionally allowed to reach Meta."
                        ),
                        retryable=True,
                        diagnostic=diag,
                    ) from exc
            else:
                row = captured.result()
        finally:
            try:
                await self.page.unroute("**/*graphql*", intercept)
            except Exception:
                pass

        if (
            not _digits(row.get("doc_id"))
            or not isinstance(row.get("variables"), dict)
            or not row.get("variables")
        ):
            diag = await self._diagnostic(
                "ad_account_create_request_not_graphql"
            )
            diag["captured"] = {
                "doc_id": _clean(row.get("doc_id")),
                "friendly_name": _clean(row.get("friendly_name")),
                "variable_keys": sorted(
                    (row.get("variables") or {}).keys()
                ) if isinstance(row.get("variables"), dict) else [],
            }
            raise BrowserBusinessError(
                "AD_ACCOUNT_CREATE_MUTATION_NOT_CAPTURED",
                (
                    "Blocked Add-RK action did not expose a usable GraphQL "
                    "doc_id and variables. No CREATE reached Meta."
                ),
                retryable=True,
                diagnostic=diag,
            )

        self._mark_ad_account_phase("CAPTURE_CONFIRMED")
        return row

    async def _fill_first(
        self,
        *,
        labels: tuple[str, ...],
        value: str,
        input_type: str | None = None,
        fill_timeout_ms: int | None = None,
    ) -> bool:
        if self.page is None or not value:
            return False

        for label in labels:
            pattern = re.compile(re.escape(label), re.IGNORECASE)
            try:
                locator = self.page.get_by_label(pattern)
                if await locator.count() and await locator.first.is_visible():
                    if fill_timeout_ms is None:
                        await locator.first.fill(value)
                    else:
                        await locator.first.fill(
                            value,
                            timeout=max(
                                250,
                                min(int(fill_timeout_ms), 10000),
                            ),
                        )
                    return True
            except Exception:
                pass

            try:
                locator = self.page.get_by_placeholder(pattern)
                if await locator.count() and await locator.first.is_visible():
                    await locator.first.fill(value)
                    return True
            except Exception:
                pass

        # Meta's 2026 creation dialog renders several inputs without
        # name/aria/placeholder and does not consistently wire <label for=>.
        # Find a visible label-like text node and fill the nearest ancestor's
        # visible input through Playwright so React still receives input events.
        for label in labels:
            pattern = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
            try:
                text_nodes = self.page.get_by_text(pattern)
                count = min(await text_nodes.count(), 8)
            except Exception:
                count = 0

            for index in range(count):
                try:
                    text_node = text_nodes.nth(index)
                    if not await text_node.is_visible():
                        continue
                    nearby = text_node.locator(
                        'xpath=ancestor::*[.//input and not(self::body)][1]//input'
                    )
                    input_count = min(await nearby.count(), 6)
                    for input_index in range(input_count):
                        candidate = nearby.nth(input_index)
                        if not await candidate.is_visible() or not await candidate.is_editable():
                            continue
                        candidate_type = _clean(
                            await candidate.get_attribute("type")
                        ).lower()
                        if candidate_type in {"hidden", "checkbox", "radio", "submit", "button"}:
                            continue
                        if fill_timeout_ms is None:
                            await candidate.fill(value)
                        else:
                            await candidate.fill(
                                value,
                                timeout=max(
                                    250,
                                    min(int(fill_timeout_ms), 10000),
                                ),
                            )
                        return True
                except Exception:
                    continue

        selector = "input"
        if input_type:
            selector += f'[type="{input_type}"]'

        try:
            candidates = self.page.locator(selector)
            count = min(await candidates.count(), 20)
        except Exception:
            return False

        label_tokens = tuple(token.lower() for token in labels)
        for index in range(count):
            locator = candidates.nth(index)
            try:
                if not await locator.is_visible():
                    continue
                key = " ".join(
                    _clean(await locator.get_attribute(attr))
                    for attr in ("name", "id", "placeholder", "aria-label")
                ).lower()
                if key and any(token.lower() in key for token in label_tokens):
                    if fill_timeout_ms is None:
                        await locator.fill(value)
                    else:
                        await locator.fill(
                            value,
                            timeout=max(
                                250,
                                min(int(fill_timeout_ms), 10000),
                            ),
                        )
                    return True
            except Exception:
                continue

        return False

    def _mark_ad_account_phase(self, phase: str) -> None:
        self._ad_account_runtime_phase = _clean(phase).upper() or "UNKNOWN"
        self._ad_account_phase_started_at = time.monotonic()

    @property
    def ad_account_create_may_have_been_sent(self) -> bool:
        return bool(self._ad_account_create_sent)

    @property
    def ad_account_final_capture_armed(self) -> bool:
        return bool(self._ad_account_final_capture_armed)

    @property
    def ad_account_runtime_phase(self) -> str:
        return _clean(self._ad_account_runtime_phase).upper() or "UNKNOWN"

    async def ad_account_runtime_timeout_diagnostic(self) -> dict[str, Any]:
        diag = await self._diagnostic("ad_account_internal_timeout")
        diag["runtime_phase"] = self._ad_account_runtime_phase
        diag["phase_elapsed_seconds"] = round(
            max(0.0, time.monotonic() - self._ad_account_phase_started_at),
            2,
        )
        diag["create_may_have_been_sent"] = bool(
            self._ad_account_create_sent
        )
        try:
            diag["ui_state"] = await asyncio.wait_for(
                self._ad_account_ui_state(),
                timeout=2.0,
            )
        except Exception:
            diag["ui_state"] = {}
        diag["ui_trace"] = self._ad_account_ui_trace[-16:]
        try:
            diag["action_candidates"] = await asyncio.wait_for(
                self._ad_account_action_candidates(),
                timeout=2.0,
            )
        except Exception:
            diag["action_candidates"] = []
        return diag

    async def _click_named(
        self,
        names: tuple[str, ...],
        *,
        roles: tuple[str, ...] = ("button", "link", "menuitem"),
        before_click: Callable[[], Awaitable[None]] | None = None,
        click_timeout_ms: int | None = None,
    ) -> bool:
        if self.page is None:
            return False

        for name in names:
            pattern = re.compile(rf"^\s*{re.escape(name)}\s*$", re.IGNORECASE)
            for role in roles:
                try:
                    locator = self.page.get_by_role(role, name=pattern)
                    count = await locator.count()
                    for index in range(min(count, 6)):
                        item = locator.nth(index)
                        if await item.is_visible() and await item.is_enabled():
                            if before_click is not None:
                                await before_click()
                            if click_timeout_ms is None:
                                await item.click()
                            else:
                                await item.click(
                                    timeout=max(
                                        250,
                                        min(int(click_timeout_ms), 10000),
                                    )
                                )
                            return True
                except Exception:
                    continue

        return False

    async def _click_named_single_attempt(
        self,
        names: tuple[str, ...],
        *,
        roles: tuple[str, ...] = ("button", "link", "menuitem"),
        before_click: Callable[[], Awaitable[None]] | None = None,
        click_timeout_ms: int = 2500,
    ) -> dict[str, Any]:
        """Attempt at most one irreversible click.

        Unlike _click_named(), this never moves on to a second candidate after
        the chosen control's click has been attempted. That is required for
        operations such as final Ad Account CREATE where an exception may occur
        after Meta already received the click.
        """
        if self.page is None:
            return {"found": False, "attempted": False, "clicked": False}

        for name in names:
            pattern = re.compile(
                rf"^\s*{re.escape(name)}\s*$",
                re.IGNORECASE,
            )
            for role in roles:
                try:
                    locator = self.page.get_by_role(role, name=pattern)
                    count = await locator.count()
                except Exception:
                    continue

                for index in range(min(count, 6)):
                    try:
                        item = locator.nth(index)
                        if not (
                            await item.is_visible()
                            and await item.is_enabled()
                        ):
                            continue
                    except Exception:
                        continue

                    meta: dict[str, Any] = {
                        "found": True,
                        "attempted": False,
                        "clicked": False,
                        "name": name,
                        "role": role,
                        "index": index,
                    }
                    try:
                        if before_click is not None:
                            await before_click()
                        meta["attempted"] = True
                        await item.click(
                            timeout=max(
                                250,
                                min(int(click_timeout_ms), 10000),
                            )
                        )
                        meta["clicked"] = True
                        return meta
                    except Exception as exc:
                        # If before_click completed, the actual browser click
                        # may already have reached Meta before Playwright saw
                        # navigation/context teardown. Never try a second one.
                        meta["error"] = (
                            f"{exc.__class__.__name__}: {exc}"
                        )[:500]
                        return meta

        return {"found": False, "attempted": False, "clicked": False}

    async def _prepare_create_form(
        self,
        *,
        business_name: str,
        user_email: str,
        user_first_name: str,
        user_last_name: str,
        profile_display_name: str,
    ) -> None:
        already_on_home = False
        if self.page is not None:
            try:
                current = urlsplit(_clean(self.page.url))
                path = (current.path or "").rstrip("/")
                already_on_home = (
                    current.netloc.lower().endswith("business.facebook.com")
                    and path.startswith("/latest/home")
                )
            except Exception:
                already_on_home = False

        try:
            create_entry_ready = await asyncio.wait_for(
                self._open_create_entry(
                    open_form=True,
                    already_on_home=already_on_home,
                ),
                timeout=60.0,
            )
        except asyncio.TimeoutError as exc:
            diag = await self._diagnostic("create_form_timeout")
            if self._last_selector_diagnostic:
                diag = {
                    "selector_attempt": self._last_selector_diagnostic,
                    **diag,
                }
            raise BrowserBusinessError(
                "BUSINESS_CREATE_FORM_TIMEOUT",
                "Meta Business portfolio creation form did not become reachable within 60s.",
                retryable=True,
                diagnostic=diag,
            ) from exc

        if not create_entry_ready:
            diag = await self._diagnostic("create_form_unavailable")
            if self._last_selector_diagnostic:
                diag = {
                    "selector_attempt": self._last_selector_diagnostic,
                    **diag,
                }

            current_url = _clean(self.page.url if self.page else "")
            try:
                current_query = parse_qs(urlsplit(current_url).query)
            except Exception:
                current_query = {}

            redirected_asset_id = _digits(
                (
                    current_query.get("asset_id")
                    or current_query.get("assetId")
                    or [""]
                )[0]
            )
            if redirected_asset_id:
                diag["asset_context_redirect"] = True
                diag["redirected_asset_id"] = redirected_asset_id

            raise BrowserBusinessError(
                "BUSINESS_CREATE_UI_UNAVAILABLE",
                "Meta Business portfolio creation form could not be opened.",
                retryable=True,
                diagnostic=diag,
            )

        if not await self._form_ready():
            diag = await self._diagnostic("create_form_false_positive")
            if self._last_selector_diagnostic:
                diag = {
                    "selector_attempt": self._last_selector_diagnostic,
                    **diag,
                }
            raise BrowserBusinessError(
                "BUSINESS_CREATE_FORM_LOST",
                "Meta returned to Business Suite before the Business creation form became usable.",
                retryable=True,
                diagnostic=diag,
            )

        business_filled = await self._fill_first(
            labels=(
                "Business portfolio name",
                "Business name",
                "Business and account name",
                "Название бизнес-портфолио",
                "Название компании",
                "Назва бізнес-портфоліо",
                "Назва компанії",
                "Business-Portfolio-Name",
                "Name des Business-Portfolios",
                "Unternehmensname",
                "Nom du portefeuille business",
                "Nom du portefeuille professionnel",
                "Nom de l’entreprise",
                "Nom de l'entreprise",
            ),
            value=business_name,
        )

        display_name = _clean(profile_display_name)
        if not display_name:
            display_name = " ".join(
                value
                for value in (_clean(user_first_name), _clean(user_last_name))
                if value
            ).strip()

        if display_name:
            await self._fill_first(
                labels=(
                    "Your name",
                    "Name",
                    "Ваше имя",
                    "Ваше ім'я",
                    "Dein Name",
                    "Ihr Name",
                    "Votre nom",
                    "Nom complet",
                ),
                value=display_name,
            )

        if user_first_name:
            await self._fill_first(
                labels=("First name", "Имя", "Ім'я", "Vorname", "Prénom"),
                value=user_first_name,
            )
        if user_last_name:
            await self._fill_first(
                labels=("Last name", "Фамилия", "Прізвище", "Nachname", "Nom"),
                value=user_last_name,
            )

        email_filled = await self._fill_first(
            labels=(
                "Business email",
                "Business email address",
                "Email",
                "Рабочий электронный адрес",
                "Электронный адрес компании",
                "Робоча електронна адреса",
                "Електронна адреса компанії",
                "Geschäftliche E-Mail-Adresse",
                "Geschäftliche Email-Adresse",
                "Geschäftliche E-Mail",
                "Adresse e-mail professionnelle",
                "Adresse e-mail de l’entreprise",
                "Adresse e-mail de l'entreprise",
                "E-mail professionnel",
            ),
            value=user_email,
            input_type="email",
        )

        # Fallback for current registration form variants with unlabeled inputs.
        if not business_filled:
            try:
                text_inputs = self.page.locator(
                    'input:not([type]), input[type="text"]'
                )
                count = await text_inputs.count()
                for index in range(count):
                    candidate = text_inputs.nth(index)
                    if await candidate.is_visible():
                        current = _clean(await candidate.input_value())
                        if not current:
                            await candidate.fill(business_name)
                            business_filled = True
                            break
            except Exception:
                pass

        if not email_filled:
            try:
                email_inputs = self.page.locator('input[type="email"]')
                if await email_inputs.count() and await email_inputs.first.is_visible():
                    await email_inputs.first.fill(user_email)
                    email_filled = True
            except Exception:
                pass

        # Confirmed live 2026 Meta form fallback. The current dialog exposes
        # four visible text inputs with almost no DOM metadata:
        #   0 portfolio name, 1 first name, 2 last name, 3 business email.
        # Only use this positional mapping when the full four-field surface is
        # present; do not apply it to unrelated/shorter form variants.
        try:
            visible_inputs = self.page.locator(
                'input:visible:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])'
            )
            visible_count = await visible_inputs.count()
            if await self._form_ready() and visible_count >= 4:
                first_value = _clean(user_first_name)
                last_value = _clean(user_last_name)
                if not first_value or not last_value:
                    name_parts = [
                        part
                        for part in re.split(r"\s+", _clean(profile_display_name))
                        if part
                    ]
                    if not first_value and name_parts:
                        first_value = name_parts[0]
                    if not last_value and len(name_parts) > 1:
                        last_value = " ".join(name_parts[1:])

                current_business = _clean(
                    await visible_inputs.nth(0).input_value()
                )
                if current_business != business_name:
                    await visible_inputs.nth(0).fill(business_name)
                business_filled = True

                if first_value:
                    current = _clean(await visible_inputs.nth(1).input_value())
                    if current != first_value:
                        await visible_inputs.nth(1).fill(first_value)

                if last_value:
                    current = _clean(await visible_inputs.nth(2).input_value())
                    if current != last_value:
                        await visible_inputs.nth(2).fill(last_value)

                current_email = _clean(
                    await visible_inputs.nth(3).input_value()
                )
                if current_email != user_email:
                    await visible_inputs.nth(3).fill(user_email)
                email_filled = True
        except Exception:
            pass

        if not business_filled:
            diag = await self._diagnostic("business_name_field_missing")
            raise BrowserBusinessError(
                "CREATE_UI_CHANGED",
                "Meta Business creation form did not expose a Business name field.",
                retryable=False,
                diagnostic=diag,
            )

        if not email_filled:
            diag = await self._diagnostic("business_email_field_missing")
            raise BrowserBusinessError(
                "CREATE_UI_CHANGED",
                "Meta Business creation form did not expose a Business email field.",
                retryable=False,
                diagnostic=diag,
            )

    @staticmethod
    def _request_matches_create(request: Any, business_name: str) -> bool:
        meta = _request_graphql_meta(request)
        if meta["method"] != "POST":
            return False
        if "graphql" not in str(meta["url"]).lower():
            return False

        expected = _clean(business_name).casefold()
        if not expected:
            return False

        friendly = _clean(meta["friendly_name"]).casefold()
        decoded = _clean(meta["decoded_raw"]).casefold()
        input_data = meta["input"] if isinstance(meta["input"], dict) else {}

        operation_markers = (
            "businesscreation",
            "businesscreate",
            "createbusiness",
            "create_business",
            "business_creation",
        )
        operation_match = any(
            marker in friendly or marker in decoded
            for marker in operation_markers
        )

        candidate_names = []
        for key in (
            "business_name",
            "businessName",
            "name",
            "portfolio_name",
            "portfolioName",
        ):
            value = input_data.get(key)
            if isinstance(value, str) and value.strip():
                candidate_names.append(value.strip().casefold())

        name_match = (
            expected in candidate_names
            or expected in decoded
        )

        # The live canary has repeatedly observed
        # useBusinessCreationMutationMutation with input.business_name.
        # Prefer that structured evidence over fragile raw substring scanning.
        return operation_match and name_match

    @staticmethod
    def _response_matches_create(response: Any, business_name: str) -> bool:
        try:
            return FacebookBusinessBrowser._request_matches_create(
                response.request,
                business_name,
            )
        except Exception:
            return False

    async def _submit_create_and_observe(
        self,
        business_name: str,
        *,
        before_submit: CheckpointCallback | None = None,
    ) -> tuple[str, str, str]:
        if self.page is None:
            raise BrowserBusinessError(
                "BROWSER_NOT_OPEN",
                "Facebook browser page is not open.",
                retryable=False,
            )

        loop = asyncio.get_running_loop()
        gate_future: asyncio.Future[bool] = loop.create_future()

        async def gate(route: Any, request: Any) -> None:
            if not self._request_matches_create(request, business_name):
                await route.continue_()
                return

            if gate_future.done():
                await route.continue_()
                return

            try:
                if before_submit is not None:
                    await before_submit(
                        {
                            "phase": "CREATE_SUBMITTED",
                            "activity": "CREATE_SUBMITTED",
                            "activity_at": int(time.time()),
                            "submitted_at": int(time.time()),
                            "network_gate": "before_meta_send",
                        }
                    )
            except Exception as exc:
                try:
                    await route.abort()
                finally:
                    if not gate_future.done():
                        gate_future.set_exception(
                            BrowserBusinessError(
                                "CREATE_CHECKPOINT_FAILED_BEFORE_SEND",
                                (
                                    "ReMask intercepted Meta CREATE but could not "
                                    "persist the submitted checkpoint, so the "
                                    "request was blocked before reaching Meta."
                                ),
                                retryable=True,
                            )
                        )
                return

            await route.continue_()
            if not gate_future.done():
                gate_future.set_result(True)

        await self.page.route("**/api/graphql/**", gate)

        try:
            if not await self._form_ready():
                if before_submit is not None:
                    await before_submit(
                        {
                            "phase": "CREATE_NOT_SUBMITTED",
                            "activity": "CREATE_NOT_SUBMITTED",
                            "activity_at": int(time.time()),
                            "not_submitted_at": int(time.time()),
                        }
                    )
                diag = await self._diagnostic("create_form_missing_before_submit")
                raise BrowserBusinessError(
                    "BUSINESS_CREATE_FORM_LOST",
                    "Meta Business creation form disappeared before submit.",
                    retryable=True,
                    diagnostic=diag,
                )

            if before_submit is not None:
                await before_submit(
                    {
                        "phase": "CREATE_CLICK_INTENT",
                        "activity": "CREATE_CLICK_INTENT",
                        "activity_at": int(time.time()),
                        "click_intent_at": int(time.time()),
                    }
                )

            async with self.page.expect_response(
                lambda response: self._response_matches_create(
                    response,
                    business_name,
                ),
                timeout=self.timeout_ms,
            ) as response_info:
                clicked = await self._click_named(self.CREATE_NAMES)
                if not clicked:
                    clicked = await self._click_named(self.SUBMIT_NAMES)

                if not clicked:
                    if before_submit is not None:
                        await before_submit(
                            {
                                "phase": "CREATE_NOT_SUBMITTED",
                                "activity": "CREATE_NOT_SUBMITTED",
                                "activity_at": int(time.time()),
                                "not_submitted_at": int(time.time()),
                            }
                        )
                    diag = await self._diagnostic("create_submit_missing")
                    raise BrowserBusinessError(
                        "CREATE_UI_CHANGED",
                        "Meta Business creation submit button was not found.",
                        retryable=False,
                        diagnostic=diag,
                    )

            response_task = asyncio.ensure_future(response_info.value)
            gate_task = asyncio.ensure_future(gate_future)

            done, _ = await asyncio.wait(
                {response_task, gate_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )

            if gate_task in done and gate_task.exception() is not None:
                response_task.cancel()
                await asyncio.gather(response_task, return_exceptions=True)
                raise gate_task.exception()

            response = await response_task
            if not gate_future.done():
                await asyncio.wait_for(gate_future, timeout=2.0)

            raw = await response.text()
            payload = _decode_graphql_text(raw)

            business_id = ""
            response_path = ""
            if payload is not None:
                business_id, response_path = _extract_created_business_id(payload)

            friendly = ""
            try:
                request_meta = _request_graphql_meta(response.request)
                friendly = _clean(request_meta.get("friendly_name"))
            except Exception:
                friendly = ""

            meta_errors = _graphql_error_details(payload)
            if not business_id and meta_errors:
                retryable = _meta_error_retryable(meta_errors)
                if before_submit is not None:
                    await before_submit(
                        {
                            "phase": "CREATE_REJECTED",
                            "activity": "CREATE_REJECTED",
                            "activity_at": int(time.time()),
                            "meta_errors": meta_errors,
                            "response_friendly_name": friendly,
                        }
                    )

                parts = []
                for row in meta_errors[:3]:
                    code = _clean(row.get("code"))
                    subcode = _clean(row.get("subcode"))
                    message = _clean(row.get("message"))
                    prefix = "/".join(value for value in (code, subcode) if value)
                    if prefix and message:
                        parts.append(f"{prefix}: {message}")
                    elif message:
                        parts.append(message)
                    elif prefix:
                        parts.append(prefix)

                error_message = " · ".join(parts) or "Meta rejected Business creation."
                raise BrowserBusinessError(
                    "META_CREATE_REJECTED",
                    error_message[:2500],
                    retryable=retryable,
                    diagnostic={
                        "meta_errors": meta_errors,
                        "request": self._safe_graphql_request_summary(
                            response.request
                        ),
                    },
                )

            return business_id, friendly, response_path

        except BrowserBusinessError:
            raise
        except Exception:
            # If CREATE was actually sent, the network gate has already
            # persisted CREATE_SUBMITTED. A missing response is reconciled from
            # the Business portfolio inventory and is never blindly retried.
            return "", "", ""
        finally:
            if not gate_future.done():
                gate_future.cancel()
            try:
                await self.page.unroute("**/api/graphql/**", gate)
            except Exception:
                pass

    async def reconcile_created_business(
        self,
        *,
        before_ids: list[str] | set[str],
        business_name: str,
    ) -> BrowserCreateResult:
        before = {_digits(value) for value in before_ids}
        before.discard("")

        after_map = await self.snapshot_businesses()
        after = set(after_map)
        created = sorted(after - before)

        if len(created) == 1:
            return BrowserCreateResult(
                business_id=created[0],
                before_ids=sorted(before),
                after_ids=sorted(after),
                recovered=True,
            )

        if len(created) > 1:
            # Prefer a unique link label matching the requested Business name.
            expected = business_name.casefold()
            named = [
                business_id
                for business_id in created
                if _clean(after_map.get(business_id)).casefold() == expected
            ]
            if len(named) == 1:
                return BrowserCreateResult(
                    business_id=named[0],
                    before_ids=sorted(before),
                    after_ids=sorted(after),
                    recovered=True,
                )

            raise BrowserBusinessError(
                "CREATE_RECONCILIATION_AMBIGUOUS",
                (
                    "Multiple new Business portfolios appeared after CREATE; "
                    "ReMask will not guess which one belongs to this job."
                ),
                retryable=False,
                diagnostic={
                    "before_ids": sorted(before),
                    "after_ids": sorted(after),
                    "new_ids": created,
                },
            )

        raise BrowserBusinessError(
            "CREATE_RESULT_UNKNOWN",
            (
                "CREATE was submitted but no uniquely new Business portfolio "
                "could be confirmed. Retry is verification-only; ReMask will "
                "not submit CREATE again."
            ),
            retryable=True,
            diagnostic={
                "before_ids": sorted(before),
                "after_ids": sorted(after),
            },
        )

    async def create_business(
        self,
        *,
        business_name: str,
        user_email: str,
        user_first_name: str = "",
        user_last_name: str = "",
        profile_display_name: str = "",
        before_snapshot: dict[str, str] | None = None,
        before_submit: CheckpointCallback | None = None,
    ) -> BrowserCreateResult:
        name = _clean(business_name)
        email = _clean(user_email)

        if not name:
            raise BrowserBusinessError(
                "INVALID_INPUT",
                "Business name is required.",
                retryable=False,
            )
        if not email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise BrowserBusinessError(
                "BUSINESS_EMAIL_REQUIRED",
                "A valid Business email is required by the Meta creation form.",
                retryable=False,
            )

        before_map = before_snapshot
        if before_map is None:
            before_map = await self.snapshot_businesses()

        async def create_checkpoint(patch: dict[str, Any]) -> None:
            if before_submit is None:
                return
            await before_submit(
                {
                    **patch,
                    "business_name": name,
                    "business_ids_before": sorted(before_map),
                }
            )

        await create_checkpoint(
            {
                "activity": "CREATE_FORM_OPENING",
                "activity_at": int(time.time()),
            }
        )

        await self._prepare_create_form(
            business_name=name,
            user_email=email,
            user_first_name=_clean(user_first_name),
            user_last_name=_clean(user_last_name),
            profile_display_name=_clean(profile_display_name),
        )

        await create_checkpoint(
            {
                "activity": "CREATE_FORM_READY",
                "activity_at": int(time.time()),
            }
        )

        response_business_id, friendly, response_path = (
            await self._submit_create_and_observe(
                name,
                before_submit=create_checkpoint,
            )
        )

        await create_checkpoint(
            {
                "activity": (
                    "CREATE_RESPONSE_OBSERVED"
                    if response_business_id
                    else "CREATE_RESPONSE_UNCONFIRMED"
                ),
                "activity_at": int(time.time()),
                "create_response_business_id": response_business_id,
                "create_response_friendly_name": friendly,
                "create_response_path": response_path,
            }
        )

        before_ids = set(before_map)

        # The response belongs to the exact CREATE mutation that passed the
        # network gate. A numeric ID from a known CREATE response path is
        # authoritative Meta evidence; Business Suite inventory can lag behind
        # the mutation response and must not turn a success into a timeout.
        if response_business_id and response_path:
            return BrowserCreateResult(
                business_id=response_business_id,
                before_ids=sorted(before_ids),
                after_ids=sorted(before_ids | {response_business_id}),
                response_business_id=response_business_id,
                response_friendly_name=friendly,
                response_path=response_path,
            )

        await self.page.wait_for_timeout(1800)

        # If the response was missing or unparseable, fall back to UI
        # reconciliation before declaring the result unknown.
        await create_checkpoint(
            {
                "activity": "VERIFY_CREATE_INVENTORY",
                "activity_at": int(time.time()),
            }
        )
        after_map = await self.snapshot_businesses()
        after_ids = set(after_map)

        created = sorted(after_ids - before_ids)
        if len(created) == 1:
            return BrowserCreateResult(
                business_id=created[0],
                before_ids=sorted(before_ids),
                after_ids=sorted(after_ids),
                response_business_id=response_business_id,
                response_friendly_name=friendly,
                response_path=response_path,
            )

        await create_checkpoint(
            {
                "activity": "RECONCILE_CREATE",
                "activity_at": int(time.time()),
            }
        )
        return await self.reconcile_created_business(
            before_ids=sorted(before_ids),
            business_name=name,
        )

    async def verify_page_attached(self, *, business_id: str, page_id: str) -> bool:
        business = _digits(business_id)
        page = _digits(page_id)
        if not business or not page:
            return False

        await self._goto(self.SETTINGS_PAGES_URL.format(business_id=business))
        await self.page.wait_for_timeout(1200)

        try:
            content = await self.page.content()
        except Exception:
            content = ""

        # The selected business ID is in the URL, while a Page ID appearing in
        # the rendered settings document indicates that asset is present.
        if page in content:
            return True

        body = await self._body_text()
        return page in body

    @staticmethod
    def _safe_meta_network_request_summary(request: Any) -> dict[str, Any]:
        """Return non-secret transport metadata for Meta network diagnostics."""
        raw_url = _clean(getattr(request, "url", ""))
        try:
            parts = urlsplit(raw_url)
        except Exception:
            return {}

        host = _clean(parts.hostname).lower()
        if not (
            host.endswith("facebook.com")
            or host.endswith("fbcdn.net")
        ):
            return {}

        browser_method = _clean(
            getattr(request, "method", "")
        ).upper()
        if browser_method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return {}

        try:
            query = parse_qs(parts.query, keep_blank_values=True)
        except Exception:
            query = {}
        try:
            post_raw = _clean(getattr(request, "post_data", ""))
            post = (
                parse_qs(post_raw, keep_blank_values=True)
                if post_raw
                else {}
            )
        except Exception:
            post = {}

        merged_keys = sorted(
            {
                str(key)
                for key in list(query.keys()) + list(post.keys())
                if key
            }
        )[:40]

        friendly = _clean(
            (post.get("fb_api_req_friendly_name")
             or query.get("fb_api_req_friendly_name")
             or [""])[0]
        )
        doc_id = _clean(
            (post.get("doc_id") or query.get("doc_id") or [""])[0]
        )
        effective_method = browser_method
        transport_method = _clean(
            (post.get("method") or query.get("method") or [""])[0]
        ).upper()
        if browser_method == "GET" and transport_method == "POST":
            effective_method = "POST"

        return {
            "host": host[:120],
            "path": _clean(parts.path)[:240],
            "browser_method": browser_method,
            "effective_method": effective_method,
            "friendly_name": friendly[:180],
            "doc_id": doc_id[:80],
            "param_keys": merged_keys,
        }

    @staticmethod
    def _safe_graphql_request_summary(request: Any) -> dict[str, Any]:
        """Return non-secret request metadata for diagnostics/canaries."""
        meta = _request_graphql_meta(request)
        variables = (
            meta["variables"]
            if isinstance(meta.get("variables"), dict)
            else {}
        )
        raw_input = variables.get("input")
        input_keys = (
            sorted(str(key) for key in raw_input)
            if isinstance(raw_input, dict)
            else []
        )
        recursive_keys: set[str] = set()
        def collect_keys(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    recursive_keys.add(str(key))
                    collect_keys(child)
            elif isinstance(value, list):
                for child in value:
                    collect_keys(child)
        collect_keys(variables)
        return {
            "url": _clean(meta.get("url")),
            "method": _clean(meta.get("method")),
            "friendly_name": _clean(meta.get("friendly_name")),
            "doc_id": _clean(meta.get("doc_id")),
            "variable_keys": sorted(str(key) for key in variables),
            "input_keys": input_keys,
            "recursive_keys": sorted(recursive_keys)[:80],
            "body_decodable": bool(meta.get("body_decodable")),
        }

    @staticmethod
    def _request_matches_page_add(
        request: Any,
        *,
        business_id: str,
        page_id: str,
    ) -> bool:
        meta = _request_graphql_meta(request)
        if meta["method"] != "POST":
            return False
        if "graphql" not in str(meta["url"]).lower():
            return False

        business = _digits(business_id)
        page = _digits(page_id)
        if not business or not page:
            return False

        friendly = _clean(meta["friendly_name"]).casefold()
        decoded = _clean(meta["decoded_raw"])
        variables = (
            meta["variables"]
            if isinstance(meta.get("variables"), dict)
            else {}
        )

        try:
            variables_text = json.dumps(
                variables,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        except Exception:
            variables_text = ""

        evidence = decoded + "\n" + variables_text
        ids_match = business in evidence and page in evidence

        operation_match = (
            "mutation" in friendly
            and any(
                marker in friendly
                for marker in (
                    "addpage",
                    "pageadd",
                    "claimpage",
                    "pageclaim",
                    "businesspage",
                    "asset",
                )
            )
        ) or (
            "mutation" in decoded.casefold()
            and any(
                marker in decoded.casefold()
                for marker in (
                    "addpage",
                    "pageadd",
                    "claimpage",
                    "pageclaim",
                )
            )
        )

        return ids_match and operation_match

    @staticmethod
    def _response_matches_page_add(
        response: Any,
        *,
        business_id: str,
        page_id: str,
    ) -> bool:
        try:
            return FacebookBusinessBrowser._request_matches_page_add(
                response.request,
                business_id=business_id,
                page_id=page_id,
            )
        except Exception:
            return False

    async def preflight_page_add_form(
        self,
        *,
        business_id: str,
        page_id: str,
    ) -> dict[str, Any]:
        """
        Open and fill Meta's real Add existing Page dialog, then stop before
        any final Add/Confirm/Request approval action. Read-only/search
        requests are allowed; no Page mutation is submitted.
        """
        business = _digits(business_id)
        page = _digits(page_id)
        if not business or not page:
            raise BrowserBusinessError(
                "INVALID_PRIMARY_PAGE",
                "Business ID and Page ID must be numeric.",
                retryable=False,
            )

        if await self.verify_page_attached(
            business_id=business,
            page_id=page,
        ):
            return {
                "ready": True,
                "already_attached": True,
                "business_id": business,
                "page_id": page,
                "current_url": _clean(self.page.url if self.page else ""),
            }

        await self._goto(self.SETTINGS_PAGES_URL.format(business_id=business))

        if not await self._click_named(self.ADD_NAMES):
            diag = await self._diagnostic("page_preflight_add_button_missing")
            raise BrowserBusinessError(
                "PAGE_ADD_UI_CHANGED",
                "Meta Business Settings did not expose the Add Page action.",
                retryable=False,
                diagnostic=diag,
            )

        await self.page.wait_for_timeout(500)

        if not await self._click_named(self.ADD_EXISTING_PAGE_NAMES):
            body = (await self._body_text()).lower()
            if (
                "page id" not in body
                and "page url" not in body
                and "facebook page" not in body
            ):
                diag = await self._diagnostic(
                    "page_preflight_existing_action_missing"
                )
                raise BrowserBusinessError(
                    "PAGE_ADD_UI_CHANGED",
                    "Meta Business Settings did not expose Add existing Page.",
                    retryable=False,
                    diagnostic=diag,
                )

        page_filled = await self._fill_first(
            labels=(
                "Facebook Page URL or ID",
                "Page URL or ID",
                "Page ID",
                "Facebook Page",
                "URL или ID Страницы Facebook",
                "ID Страницы",
                "URL або ID сторінки Facebook",
                "ID сторінки",
                "Facebook-Seiten-URL oder -ID",
                "Seiten-URL oder -ID",
                "Seiten-ID",
                "Facebook-Seite",
            ),
            value=page,
        )
        if not page_filled:
            try:
                inputs = self.page.locator(
                    'input:not([type]), input[type="text"], input[type="search"]'
                )
                for index in range(min(await inputs.count(), 20)):
                    candidate = inputs.nth(index)
                    if await candidate.is_visible() and await candidate.is_editable():
                        await candidate.fill(page)
                        page_filled = True
                        break
            except Exception:
                pass

        if not page_filled:
            diag = await self._diagnostic("page_preflight_id_field_missing")
            raise BrowserBusinessError(
                "PAGE_ADD_UI_CHANGED",
                "Meta Page-add dialog did not expose a Page URL/ID field.",
                retryable=False,
                diagnostic=diag,
            )

        await self.page.wait_for_timeout(1200)

        # Selecting a search result is non-mutating. Stop before any
        # Add/Confirm/Request approval button is clicked.
        selected = False
        try:
            exact = self.page.get_by_text(
                re.compile(rf"^\s*{re.escape(page)}\s*$")
            )
            for index in range(min(await exact.count(), 5)):
                candidate = exact.nth(index)
                if not await candidate.is_visible():
                    continue
                target = candidate.locator(
                    'xpath=ancestor-or-self::*[@role="option" or @role="button" or self::button][1]'
                )
                if await target.count() and await target.first.is_visible():
                    await target.first.click()
                    selected = True
                    await self.page.wait_for_timeout(450)
                    break
        except Exception:
            pass

        if not selected:
            try:
                radios = self.page.get_by_role("radio")
                visible = []
                for index in range(min(await radios.count(), 8)):
                    item = radios.nth(index)
                    if await item.is_visible() and await item.is_enabled():
                        visible.append(item)
                if len(visible) == 1:
                    if not await visible[0].is_checked():
                        await visible[0].check()
                    selected = True
                    await self.page.wait_for_timeout(250)
            except Exception:
                pass

        body = await self._body_text()
        final_actions = []
        for label in (
            "Add Page",
            "Add Facebook Page",
            "Confirm",
            "Request approval",
            "Добавить Страницу",
            "Подтвердить",
            "Додати сторінку",
            "Підтвердити",
            "Seite hinzufügen",
            "Bestätigen",
        ):
            try:
                locator = self.page.get_by_role(
                    "button",
                    name=re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE),
                )
                if await locator.count() and await locator.first.is_visible():
                    final_actions.append(label)
            except Exception:
                continue

        return {
            "ready": True,
            "already_attached": False,
            "business_id": business,
            "page_id": page,
            "page_filled": True,
            "result_selected": selected,
            "final_actions": final_actions,
            "page_id_visible": page in body,
            "current_url": _clean(self.page.url),
        }

    async def add_existing_page(
        self,
        *,
        business_id: str,
        page_id: str,
        before_submit: CheckpointCallback | None = None,
    ) -> BrowserPageResult:
        business = _digits(business_id)
        page = _digits(page_id)
        if not business or not page:
            raise BrowserBusinessError(
                "INVALID_PRIMARY_PAGE",
                "Business ID and Page ID must be numeric.",
                retryable=False,
            )

        if await self.verify_page_attached(business_id=business, page_id=page):
            return BrowserPageResult(
                business_id=business,
                page_id=page,
                already_attached=True,
            )

        await self._goto(self.SETTINGS_PAGES_URL.format(business_id=business))

        if not await self._click_named(self.ADD_NAMES):
            diag = await self._diagnostic("page_add_button_missing")
            raise BrowserBusinessError(
                "PAGE_ADD_UI_CHANGED",
                "Meta Business Settings did not expose the Add Page action.",
                retryable=False,
                diagnostic=diag,
            )

        await self.page.wait_for_timeout(500)

        if not await self._click_named(self.ADD_EXISTING_PAGE_NAMES):
            # Some UI variants open directly into the existing-page dialog.
            body = (await self._body_text()).lower()
            if "page id" not in body and "page url" not in body and "facebook page" not in body:
                diag = await self._diagnostic("page_existing_action_missing")
                raise BrowserBusinessError(
                    "PAGE_ADD_UI_CHANGED",
                    "Meta Business Settings did not expose Add existing Page.",
                    retryable=False,
                    diagnostic=diag,
                )

        page_filled = await self._fill_first(
            labels=(
                "Facebook Page URL or ID",
                "Page URL or ID",
                "Page ID",
                "Facebook Page",
                "URL или ID Страницы Facebook",
                "ID Страницы",
                "URL або ID сторінки Facebook",
                "ID сторінки",
                "Facebook-Seiten-URL oder -ID",
                "Seiten-URL oder -ID",
                "Seiten-ID",
                "Facebook-Seite",
            ),
            value=page,
        )
        if not page_filled:
            try:
                inputs = self.page.locator(
                    'input:not([type]), input[type="text"], input[type="search"]'
                )
                count = await inputs.count()
                for index in range(count):
                    candidate = inputs.nth(index)
                    if await candidate.is_visible():
                        await candidate.fill(page)
                        page_filled = True
                        break
            except Exception:
                pass

        if not page_filled:
            diag = await self._diagnostic("page_id_field_missing")
            raise BrowserBusinessError(
                "PAGE_ADD_UI_CHANGED",
                "Meta Page-add dialog did not expose a Page URL/ID field.",
                retryable=False,
                diagnostic=diag,
            )

        loop = asyncio.get_running_loop()
        gate_future: asyncio.Future[bool] = loop.create_future()
        response_future: asyncio.Future[Any] = loop.create_future()

        def observe_response(response: Any) -> None:
            if response_future.done():
                return
            try:
                if self._response_matches_page_add(
                    response,
                    business_id=business,
                    page_id=page,
                ):
                    response_future.set_result(response)
            except Exception:
                return

        self.page.on("response", observe_response)

        async def gate(route: Any, request: Any) -> None:
            if not self._request_matches_page_add(
                request,
                business_id=business,
                page_id=page,
            ):
                await route.continue_()
                return

            if gate_future.done():
                await route.continue_()
                return

            try:
                if before_submit is not None:
                    await before_submit(
                        {
                            "phase": "PAGE_ADD_SUBMITTED",
                            "activity": "PAGE_ADD_SUBMITTED",
                            "activity_at": int(time.time()),
                            "business_id": business,
                            "primary_page_id": page,
                            "page_submitted_at": int(time.time()),
                            "network_gate": "before_meta_send",
                        }
                    )
            except Exception as exc:
                try:
                    await route.abort()
                finally:
                    if not gate_future.done():
                        gate_future.set_exception(
                            BrowserBusinessError(
                                "PAGE_CHECKPOINT_FAILED_BEFORE_SEND",
                                (
                                    "ReMask intercepted Meta Page-add but could "
                                    "not persist the submitted checkpoint, so "
                                    "the request was blocked before reaching Meta."
                                ),
                                retryable=True,
                                diagnostic=self._safe_graphql_request_summary(
                                    request
                                ),
                            )
                        )
                return

            await route.continue_()
            if not gate_future.done():
                gate_future.set_result(True)

        await self.page.route("**/api/graphql/**", gate)

        try:
            # Search results are often a selectable list before the review
            # step. Prefer an exact Page-ID result; otherwise select the only
            # visible option/radio if Meta rendered one.
            try:
                exact = self.page.get_by_text(
                    re.compile(rf"^\\s*{re.escape(page)}\\s*$")
                )
                count = min(await exact.count(), 5)
                for index in range(count):
                    candidate = exact.nth(index)
                    if not await candidate.is_visible():
                        continue
                    target = candidate.locator(
                        'xpath=ancestor-or-self::*[@role="option" or @role="button" or self::button][1]'
                    )
                    if await target.count() and await target.first.is_visible():
                        await target.first.click()
                        await self.page.wait_for_timeout(450)
                        break
            except Exception:
                pass

            try:
                radios = self.page.get_by_role("radio")
                visible_radios = []
                for index in range(min(await radios.count(), 8)):
                    item = radios.nth(index)
                    if await item.is_visible() and await item.is_enabled():
                        visible_radios.append(item)
                if len(visible_radios) == 1 and not await visible_radios[0].is_checked():
                    await visible_radios[0].check()
                    await self.page.wait_for_timeout(250)
            except Exception:
                pass

            sent = False
            clicked_any = False
            page_click_intent_written = False

            async def checkpoint_page_click_intent() -> None:
                nonlocal page_click_intent_written
                if page_click_intent_written or before_submit is None:
                    return
                await before_submit(
                    {
                        "phase": "PAGE_ADD_CLICK_INTENT",
                        "activity": "PAGE_ADD_CLICK_INTENT",
                        "activity_at": int(time.time()),
                        "business_id": business,
                        "primary_page_id": page,
                        "page_click_intent_at": int(time.time()),
                    }
                )
                page_click_intent_written = True

            for _ in range(7):
                if gate_future.done():
                    sent = gate_future.exception() is None
                    break

                # Meta can show a consent checkbox on the final review step.
                try:
                    checkboxes = self.page.get_by_role("checkbox")
                    for index in range(min(await checkboxes.count(), 12)):
                        checkbox = checkboxes.nth(index)
                        if not await checkbox.is_visible() or not await checkbox.is_enabled():
                            continue
                        if await checkbox.is_checked():
                            continue
                        label = " ".join(
                            [
                                _clean(await checkbox.get_attribute("aria-label")),
                                _clean(
                                    await checkbox.evaluate(
                                        "(e) => (e.parentElement && e.parentElement.innerText) || ''"
                                    )
                                )[:500],
                            ]
                        ).lower()
                        if any(
                            marker in label
                            for marker in (
                                "agree",
                                "terms",
                                "confirm",
                                "understand",
                                "соглас",
                                "подтверж",
                                "погодж",
                                "підтвер",
                                "zustimm",
                                "bestät",
                            )
                        ):
                            await checkbox.check()
                            await self.page.wait_for_timeout(200)
                except Exception:
                    pass

                final_clicked = await self._click_named(
                    (
                        "Add Page",
                        "Add Facebook Page",
                        "Add Page and Instagram",
                        "Confirm",
                        "Request approval",
                        "Добавить Страницу",
                        "Добавить страницу",
                        "Подтвердить",
                        "Додати сторінку",
                        "Підтвердити",
                        "Seite hinzufügen",
                        "Bestätigen",
                    ),
                    before_click=checkpoint_page_click_intent,
                )
                if final_clicked:
                    clicked_any = True
                    await self.page.wait_for_timeout(700)
                    if gate_future.done():
                        sent = gate_future.exception() is None
                        break
                    continue

                next_clicked = await self._click_named(
                    (
                        "Next",
                        "Continue",
                        "Review",
                        "Select",
                        "Далее",
                        "Продолжить",
                        "Проверить",
                        "Выбрать",
                        "Далі",
                        "Продовжити",
                        "Перевірити",
                        "Вибрати",
                        "Weiter",
                        "Fortfahren",
                        "Überprüfen",
                        "Auswählen",
                    )
                )
                if next_clicked:
                    clicked_any = True
                    await self.page.wait_for_timeout(700)
                    continue

                break

            if gate_future.done() and gate_future.exception() is not None:
                raise gate_future.exception()

            if gate_future.done() and gate_future.exception() is None:
                response = None
                try:
                    response = await asyncio.wait_for(
                        asyncio.shield(response_future),
                        timeout=min(8.0, float(self.timeout_seconds)),
                    )
                except asyncio.TimeoutError:
                    if before_submit is not None:
                        await before_submit(
                            {
                                "activity": "PAGE_ADD_RESPONSE_UNCONFIRMED",
                                "activity_at": int(time.time()),
                                "business_id": business,
                                "primary_page_id": page,
                            }
                        )
                except Exception:
                    response = None

                if response is not None:
                    try:
                        raw = await response.text()
                        payload = _decode_graphql_text(raw)
                    except Exception:
                        payload = None

                    meta_errors = _graphql_error_details(payload)
                    if meta_errors:
                        retryable = _meta_error_retryable(meta_errors)
                        if before_submit is not None:
                            await before_submit(
                                {
                                    "phase": "PAGE_ADD_REJECTED",
                                    "activity": "PAGE_ADD_REJECTED",
                                    "activity_at": int(time.time()),
                                    "business_id": business,
                                    "primary_page_id": page,
                                    "meta_errors": meta_errors,
                                }
                            )

                        parts = []
                        for row in meta_errors[:3]:
                            code = _clean(row.get("code"))
                            subcode = _clean(row.get("subcode"))
                            message = _clean(row.get("message"))
                            prefix = "/".join(
                                value for value in (code, subcode) if value
                            )
                            if prefix and message:
                                parts.append(f"{prefix}: {message}")
                            elif message:
                                parts.append(message)
                            elif prefix:
                                parts.append(prefix)

                        error_message = (
                            " · ".join(parts)
                            or "Meta rejected Page attachment."
                        )
                        raise BrowserBusinessError(
                            "META_PAGE_ADD_REJECTED",
                            error_message[:2500],
                            retryable=retryable,
                            diagnostic={
                                "meta_errors": meta_errors,
                                "request": self._safe_graphql_request_summary(
                                    response.request
                                ),
                            },
                        )

                    if before_submit is not None:
                        await before_submit(
                            {
                                "activity": "PAGE_ADD_RESPONSE_OBSERVED",
                                "activity_at": int(time.time()),
                                "business_id": business,
                                "primary_page_id": page,
                                "response_friendly_name": _clean(
                                    _request_graphql_meta(
                                        response.request
                                    ).get("friendly_name")
                                ),
                            }
                        )

            if not gate_future.done():
                # A non-GraphQL Meta variant may still have completed the
                # operation. If a FINAL action was clicked, preserve
                # PAGE_ADD_CLICK_INTENT until verification so retry cannot
                # blindly submit a second ownership request. Only mark
                # NOT_SUBMITTED when no final mutation-capable click happened.
                if before_submit is not None and not page_click_intent_written:
                    await before_submit(
                        {
                            "phase": "PAGE_ADD_NOT_SUBMITTED",
                            "business_id": business,
                            "primary_page_id": page,
                            "page_not_submitted_at": int(time.time()),
                        }
                    )

                if not clicked_any:
                    diag = await self._diagnostic("page_add_submit_missing")
                    raise BrowserBusinessError(
                        "PAGE_ADD_UI_CHANGED",
                        "Meta Page-add review/submit action was not found.",
                        retryable=False,
                        diagnostic=diag,
                    )

        except BrowserBusinessError:
            raise
        finally:
            if not gate_future.done():
                gate_future.cancel()
            if not response_future.done():
                response_future.cancel()
            try:
                self.page.remove_listener("response", observe_response)
            except Exception:
                pass
            try:
                await self.page.unroute("**/api/graphql/**", gate)
            except Exception:
                pass

        await self.page.wait_for_timeout(1500)

        if not await self.verify_page_attached(business_id=business, page_id=page):
            diag = await self._diagnostic("page_attach_unconfirmed")
            raise BrowserBusinessError(
                "PAGE_ATTACH_RESULT_UNKNOWN",
                (
                    "Meta Page add was submitted but the selected Page could "
                    "not be confirmed in Business Settings. Retry is "
                    "verification-only; ReMask will not blindly submit the "
                    "Page-add action again."
                ),
                retryable=True,
                diagnostic=diag,
            )

        return BrowserPageResult(
            business_id=business,
            page_id=page,
            already_attached=False,
        )


__all__ = [
    "BrowserAdAccountResult",
    "BrowserBusinessError",
    "BrowserCreateResult",
    "BrowserPageResult",
    "BrowserPreflightResult",
    "FacebookBusinessBrowser",
]
