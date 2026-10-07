from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fb_worker import AuthenticationError, RemoteRequestError

from .facebook_docids import (
    classify_cache_failure,
    record_result,
    upsert_candidate,
)
from .provisioning.models import ProvisioningError


OPERATION = "PAGE_ACCESS_REQUEST"
VARIABLES_MODE = "private_page_access_request_v1"

_SAFE_ENVELOPE_KEYS = {
    "__aaid", "__bid", "__hs", "__hblp", "__hsdp", "__rev", "__s",
    "__hsi", "__dyn", "__csr", "__comet_req", "__spin_r", "__spin_b",
    "__spin_t", "__jssesw", "__crn", "__req", "__ccg", "dpr",
    "server_timestamps", "fb_api_caller_class",
}
_FORBIDDEN_AUTH_KEYS = {
    "fb_dtsg", "access_token", "authorization", "cookie", "cookies",
    "jazoest", "lsd", "__user", "xs", "c_user",
}
_ALLOWED_HOSTS = {
    "business.facebook.com",
    "www.facebook.com",
    "adsmanager.facebook.com",
}
_STORE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class PageAccessContract:
    doc_id: str
    friendly_name: str
    endpoint_url: str
    variables: dict[str, Any]
    request_envelope: dict[str, str]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_endpoint(value: Any) -> str:
    endpoint = _clean(value) or "https://business.facebook.com/api/graphql/"
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or not parsed.path.startswith("/api/graphql")
    ):
        raise ProvisioningError(
            "PRIVATE_PAGE_ACCESS_CONTRACT_INVALID",
            "Captured Page-access GraphQL endpoint is unsupported",
            retryable=False,
        )
    return endpoint


def _reject_auth_material(value: Any, path: str = "contract") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _clean(key).lower()
            if normalized in _FORBIDDEN_AUTH_KEYS:
                raise ProvisioningError(
                    "PRIVATE_PAGE_ACCESS_CONTRACT_INVALID",
                    f"Captured Page-access contract contains auth material at {path}.{key}",
                    retryable=False,
                )
            _reject_auth_material(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_auth_material(child, f"{path}[{index}]")


def _template_value(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _template_value(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_template_value(child, replacements) for child in value]

    if isinstance(value, (str, int)):
        text = str(value)
        exact = replacements.get(text)
        if exact:
            return exact

        if isinstance(value, str):
            rendered = value
            for raw, placeholder in sorted(
                replacements.items(),
                key=lambda row: len(row[0]),
                reverse=True,
            ):
                if raw.isdigit() and len(raw) >= 5:
                    rendered = re.sub(
                        rf"(?<!\d){re.escape(raw)}(?!\d)",
                        placeholder,
                        rendered,
                    )
            return rendered

    return value


def _render_value(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _render_value(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_render_value(child, replacements) for child in value]
    if isinstance(value, str):
        rendered = value
        for placeholder, replacement in replacements.items():
            rendered = rendered.replace(placeholder, replacement)
        return rendered
    return value


def _value_contains_id(value: Any, expected: str) -> bool:
    if isinstance(value, dict):
        return any(_value_contains_id(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_value_contains_id(child, expected) for child in value)
    text = str(value or "")
    return bool(
        re.search(
            rf"(?<!\d){re.escape(expected)}(?!\d)",
            text,
        )
    )


def _recursive_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(_recursive_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_recursive_keys(child))
    return keys


def page_access_request_match(
    meta: dict[str, Any],
    *,
    business_id: str,
    page_id: str,
) -> bool:
    variables = (
        meta.get("variables")
        if isinstance(meta.get("variables"), dict)
        else {}
    )
    if not variables:
        return False

    business = _clean(business_id)
    page = _clean(page_id)
    if not business.isdigit() or not page.isdigit():
        return False
    if not _value_contains_id(variables, business):
        return False
    if not _value_contains_id(variables, page):
        return False

    doc_id = _clean(meta.get("doc_id"))
    if not doc_id.isdigit():
        return False

    friendly = _clean(meta.get("friendly_name")).lower()
    keys = _recursive_keys(variables)
    semantic_tokens = (
        "access", "permission", "partner", "request",
        "task", "role", "share", "assign",
    )
    semantic = any(token in friendly for token in semantic_tokens)
    if not semantic:
        semantic = any(
            any(token in key for token in semantic_tokens)
            for key in keys
        )
    return semantic


class PageAccessContractStore:
    def __init__(self, path: str | Path | None = None) -> None:
        default = (
            Path(os.getenv("REMASK_DATA_DIR") or "/var/lib/remask")
            / "private-page-access-contract.json"
        )
        self.path = Path(
            path
            or os.getenv("REMASK_PRIVATE_PAGE_ACCESS_CONTRACT_PATH")
            or default
        )

    def _load(self) -> dict[str, Any]:
        try:
            if not self.path.is_file():
                return {}
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        tmp = self.path.with_suffix(
            self.path.suffix + f".tmp.{os.getpid()}"
        )
        tmp.write_text(encoded, encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self) -> PageAccessContract | None:
        with _STORE_LOCK:
            row = self._load()
        if not row:
            return None

        doc_id = _clean(row.get("doc_id"))
        friendly_name = _clean(row.get("friendly_name"))
        endpoint_url = _safe_endpoint(row.get("endpoint_url"))
        variables = row.get("variables")
        envelope = row.get("request_envelope")
        if (
            not doc_id.isdigit()
            or not isinstance(variables, dict)
            or not variables
            or not isinstance(envelope, dict)
        ):
            return None

        _reject_auth_material(variables, "variables")
        _reject_auth_material(envelope, "request_envelope")
        safe_envelope = {
            str(key): _clean(value)
            for key, value in envelope.items()
            if key in _SAFE_ENVELOPE_KEYS and _clean(value)
        }
        encoded = json.dumps(
            variables,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if "{{business_id}}" not in encoded or "{{page_id}}" not in encoded:
            return None

        return PageAccessContract(
            doc_id=doc_id,
            friendly_name=friendly_name,
            endpoint_url=endpoint_url,
            variables=variables,
            request_envelope=safe_envelope,
        )

    def register_capture(
        self,
        captured: dict[str, Any],
        *,
        business_id: str,
        page_id: str,
    ) -> PageAccessContract:
        if not isinstance(captured, dict):
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_CAPTURE_INVALID",
                "Captured Page-access mutation is invalid",
                retryable=False,
            )

        business = _clean(business_id)
        page = _clean(page_id)
        variables = captured.get("variables")
        if (
            not business.isdigit()
            or not page.isdigit()
            or not isinstance(variables, dict)
            or not variables
        ):
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_CAPTURE_INVALID",
                "Captured Page-access mutation is missing exact target IDs",
                retryable=False,
            )

        templated = _template_value(
            variables,
            {
                business: "{{business_id}}",
                page: "{{page_id}}",
            },
        )
        encoded = json.dumps(
            templated,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if "{{business_id}}" not in encoded or "{{page_id}}" not in encoded:
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_CAPTURE_INVALID",
                "Captured Page-access mutation did not bind both target IDs",
                retryable=False,
            )

        _reject_auth_material(templated, "variables")

        envelope = (
            captured.get("request_envelope")
            if isinstance(captured.get("request_envelope"), dict)
            else {}
        )
        _reject_auth_material(envelope, "request_envelope")
        safe_envelope = {
            str(key): _clean(value)
            for key, value in envelope.items()
            if key in _SAFE_ENVELOPE_KEYS and _clean(value)
        }

        row = {
            "doc_id": _clean(captured.get("doc_id")),
            "friendly_name": _clean(captured.get("friendly_name")),
            "endpoint_url": _safe_endpoint(captured.get("endpoint_url")),
            "variables": templated,
            "request_envelope": safe_envelope,
            "source": "browser_graphql_capture",
        }
        if not row["doc_id"].isdigit():
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_CAPTURE_INVALID",
                "Captured Page-access mutation has no usable doc_id",
                retryable=False,
            )

        with _STORE_LOCK:
            self._save(row)

        contract = self.get()
        if contract is None:
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_CONTRACT_INVALID",
                "Captured Page-access contract could not be persisted",
                retryable=False,
            )

        upsert_candidate(
            OPERATION,
            doc_id=contract.doc_id,
            friendly_name=contract.friendly_name,
            endpoint_url=contract.endpoint_url,
            variables_mode=VARIABLES_MODE,
            source="browser_graphql_capture",
            priority=8_500,
        )
        return contract

    def render(
        self,
        contract: PageAccessContract,
        *,
        business_id: str,
        page_id: str,
    ) -> dict[str, Any]:
        business = _clean(business_id)
        page = _clean(page_id)
        if not business.isdigit() or not page.isdigit():
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_TARGET_INVALID",
                "Page-access target IDs are invalid",
                retryable=False,
            )
        variables = _render_value(
            contract.variables,
            {
                "{{business_id}}": business,
                "{{page_id}}": page,
            },
        )
        if not isinstance(variables, dict):
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_CONTRACT_INVALID",
                "Rendered Page-access variables are invalid",
                retryable=False,
            )
        return variables

    async def execute(
        self,
        web: Any,
        *,
        business_id: str,
        page_id: str,
        profile_id: str,
    ) -> dict[str, Any]:
        contract = self.get()
        if contract is None:
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_CONTRACT_MISSING",
                "No captured private Page-access contract is available",
                retryable=True,
            )

        candidate = upsert_candidate(
            OPERATION,
            doc_id=contract.doc_id,
            friendly_name=contract.friendly_name,
            endpoint_url=contract.endpoint_url,
            variables_mode=VARIABLES_MODE,
            source="private_page_access_contract",
            priority=8_500,
        )
        variables = self.render(
            contract,
            business_id=business_id,
            page_id=page_id,
        )

        try:
            payload = await web.graphql(
                contract.doc_id,
                variables,
                friendly_name=contract.friendly_name,
                endpoint_url=contract.endpoint_url,
                request_envelope=contract.request_envelope,
            )
        except AuthenticationError as exc:
            record_result(
                OPERATION,
                candidate,
                success=False,
                reason="AUTHENTICATION",
                profile_id=profile_id,
                failure_kind="account",
            )
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_AUTH_FAILED",
                "Private Page-access request could not authenticate",
                retryable=True,
            ) from exc
        except RemoteRequestError as exc:
            kind = classify_cache_failure(
                exception=exc,
                payload=(
                    exc.meta_payload
                    if isinstance(exc.meta_payload, dict)
                    else None
                ),
                message=str(exc),
                http_status=exc.http_status,
            )
            record_result(
                OPERATION,
                candidate,
                success=False,
                reason=str(exc)[:800],
                profile_id=profile_id,
                failure_kind=kind,
            )
            if kind == "stale_schema":
                raise ProvisioningError(
                    "PRIVATE_PAGE_ACCESS_CONTRACT_STALE",
                    "Captured Page-access contract is stale",
                    retryable=True,
                ) from exc
            if exc.http_status and 400 <= int(exc.http_status) < 500:
                raise ProvisioningError(
                    "PRIVATE_PAGE_ACCESS_META_REJECTED",
                    "Meta explicitly rejected the private Page-access request",
                    retryable=False,
                ) from exc
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_RESULT_UNKNOWN",
                "Private Page-access request transport failed after submit",
                retryable=True,
            ) from exc

        if not isinstance(payload, dict):
            raise ProvisioningError(
                "PRIVATE_PAGE_ACCESS_RESULT_UNKNOWN",
                "Meta returned an unexpected Page-access response",
                retryable=True,
            )

        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            kind = classify_cache_failure(
                payload=payload,
                message=json.dumps(
                    errors,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )[:1600],
            )
            record_result(
                OPERATION,
                candidate,
                success=False,
                reason="GRAPHQL_ERRORS",
                profile_id=profile_id,
                failure_kind=kind,
            )
            code = (
                "PRIVATE_PAGE_ACCESS_CONTRACT_STALE"
                if kind == "stale_schema"
                else "PRIVATE_PAGE_ACCESS_META_REJECTED"
            )
            raise ProvisioningError(
                code,
                "Meta rejected the private Page-access mutation",
                retryable=(kind == "stale_schema"),
            )

        record_result(
            OPERATION,
            candidate,
            success=True,
            reason="GRAPHQL_OK",
            profile_id=profile_id,
        )
        return payload


__all__ = [
    "OPERATION",
    "PageAccessContract",
    "PageAccessContractStore",
    "page_access_request_match",
]
