from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .facebook_docids import (
    DocIdCandidate,
    classify_cache_failure,
    list_candidates,
    record_result,
    upsert_candidate,
)
from .graphql_mutation_capture import SAFE_ENVELOPE_KEYS
from .provisioning.models import ProvisioningError


PAGE_SHARE_OPERATION = "PAGE_SHARE_REQUEST"
VARIABLES_MODE = "private_page_share_request_v1"
_ALLOWED_HOSTS = {
    "business.facebook.com",
    "www.facebook.com",
    "adsmanager.facebook.com",
}
_FORBIDDEN_AUTH_KEYS = {
    "fb_dtsg",
    "access_token",
    "authorization",
    "cookie",
    "cookies",
    "jazoest",
    "lsd",
    "__user",
    "xs",
    "c_user",
}
_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class PrivatePageShareContract:
    doc_id: str
    friendly_name: str
    endpoint_url: str
    variables: dict[str, Any]
    request_envelope: dict[str, str]
    source: str = "browser_graphql_capture"
    observed_at: str = ""


def _clean_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("act_"):
        text = text[4:]
    return text if text.isdigit() else ""


def _endpoint(value: Any) -> str:
    endpoint = str(
        value or "https://business.facebook.com/api/graphql/"
    ).strip()
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in _ALLOWED_HOSTS
        or not parsed.path.startswith("/api/graphql")
    ):
        raise ProvisioningError(
            "PRIVATE_PAGE_SHARE_CONTRACT_INVALID",
            "Captured Page-share endpoint is not an allowed Facebook GraphQL endpoint",
            retryable=False,
        )
    return endpoint


def _reject_auth(value: Any, path: str = "contract") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key or "").strip().lower()
            if normalized in _FORBIDDEN_AUTH_KEYS:
                raise ProvisioningError(
                    "PRIVATE_PAGE_SHARE_CONTRACT_INVALID",
                    f"Captured Page-share contract contains auth material at {path}.{key}",
                    retryable=False,
                )
            _reject_auth(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_auth(child, f"{path}[{index}]")


def _template_value(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _template_value(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_template_value(child, replacements) for child in value]

    text = str(value) if isinstance(value, (str, int)) else None
    if text is None:
        return value

    if text in replacements:
        return replacements[text]

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


def _render(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _render(child, values)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_render(child, values) for child in value]
    if not isinstance(value, str):
        return value

    rendered = value
    for key, raw in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(raw))
    return rendered


def _clean_envelope(value: Any) -> dict[str, str]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ProvisioningError(
            "PRIVATE_PAGE_SHARE_CONTRACT_INVALID",
            "Captured Page-share request envelope must be an object",
            retryable=False,
        )
    unknown = sorted(
        str(key)
        for key in value
        if str(key) not in SAFE_ENVELOPE_KEYS
    )
    if unknown:
        raise ProvisioningError(
            "PRIVATE_PAGE_SHARE_CONTRACT_INVALID",
            "Captured Page-share request envelope contains unsupported fields: "
            + ", ".join(unknown[:10]),
            retryable=False,
        )
    return {
        str(key): str(raw)[:20000]
        for key, raw in value.items()
        if str(raw or "").strip()
    }


class PrivatePageShareContractStore:
    def __init__(self, *, path: str | Path | None = None) -> None:
        data_root = (
            os.getenv("REMASK_DATA_DIR")
            or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
            or "/var/lib/remask"
        )
        self.path = Path(
            path
            or os.getenv("REMASK_PRIVATE_PAGE_SHARE_CONTRACT_PATH")
            or (Path(data_root) / "private-page-share-contract.json")
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_CONTRACT_INVALID",
                f"Page-share contract store cannot be read: {exc.__class__.__name__}",
                retryable=False,
            ) from exc
        return raw if isinstance(raw, dict) else {}

    def _save(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            row,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        tmp = self.path.with_suffix(
            self.path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}"
        )
        tmp.write_text(encoded, encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _candidate(contract: PrivatePageShareContract) -> DocIdCandidate:
        return upsert_candidate(
            PAGE_SHARE_OPERATION,
            doc_id=contract.doc_id,
            friendly_name=contract.friendly_name,
            endpoint_url=contract.endpoint_url,
            variables_mode=VARIABLES_MODE,
            source=contract.source or "browser_graphql_capture",
            priority=9_000,
            observed_at=contract.observed_at,
        )

    def get(self) -> PrivatePageShareContract:
        row = self._load()
        if not row:
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_CONTRACT_REQUIRED",
                "No captured private Page-share mutation contract is available",
                retryable=True,
            )
        _reject_auth(row)
        doc_id = str(row.get("doc_id") or "").strip()
        friendly_name = str(row.get("friendly_name") or "").strip()
        variables = row.get("variables")
        if (
            not (doc_id.isdigit() and 5 <= len(doc_id) <= 40)
            or not friendly_name
            or not isinstance(variables, dict)
        ):
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_CONTRACT_INVALID",
                "Captured private Page-share contract is incomplete",
                retryable=False,
            )
        encoded = json.dumps(variables, ensure_ascii=False)
        for required in ("{{business_id}}", "{{page_id}}"):
            if required not in encoded:
                raise ProvisioningError(
                    "PRIVATE_PAGE_SHARE_CONTRACT_INVALID",
                    f"Captured Page-share contract is missing {required}",
                    retryable=False,
                )
        contract = PrivatePageShareContract(
            doc_id=doc_id,
            friendly_name=friendly_name,
            endpoint_url=_endpoint(row.get("endpoint_url")),
            variables=variables,
            request_envelope=_clean_envelope(row.get("request_envelope")),
            source=str(row.get("source") or "browser_graphql_capture").strip(),
            observed_at=str(row.get("observed_at") or "").strip(),
        )
        candidate = self._candidate(contract)
        active = any(
            row.doc_id == candidate.doc_id
            and row.friendly_name == candidate.friendly_name
            and row.variables_mode == candidate.variables_mode
            and row.endpoint_url == candidate.endpoint_url
            for row in list_candidates(PAGE_SHARE_OPERATION)
        )
        if not active:
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_CONTRACT_STALE",
                "The captured Page-share mutation contract is disabled",
                retryable=True,
            )
        return contract

    def register_capture(
        self,
        captured: dict[str, Any],
        *,
        business_id: str,
        page_id: str,
        actor_id: str = "",
    ) -> PrivatePageShareContract:
        if not isinstance(captured, dict):
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_CAPTURE_INVALID",
                "Captured Page-share mutation is not an object",
                retryable=False,
            )
        business = _clean_id(business_id)
        page = _clean_id(page_id)
        actor = _clean_id(actor_id)
        if not business or not page:
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_CAPTURE_INVALID",
                "Exact Business and Page IDs are required to template Page-share capture",
                retryable=False,
            )
        variables = captured.get("variables")
        if not isinstance(variables, dict) or not variables:
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_CAPTURE_INVALID",
                "Captured Page-share mutation has no GraphQL variables",
                retryable=False,
            )
        replacements = {
            business: "{{business_id}}",
            page: "{{page_id}}",
        }
        if actor and actor not in replacements:
            replacements[actor] = "{{actor_id}}"
        templated = _template_value(variables, replacements)
        encoded = json.dumps(
            templated,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if "{{business_id}}" not in encoded or "{{page_id}}" not in encoded:
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_CAPTURE_INVALID",
                "Captured mutation does not bind both exact Business and Page IDs",
                retryable=False,
            )
        for raw in (business, page):
            if re.search(rf"(?<!\d){re.escape(raw)}(?!\d)", encoded):
                raise ProvisioningError(
                    "PRIVATE_PAGE_SHARE_CAPTURE_INVALID",
                    "Target-specific IDs remain in the templated Page-share contract",
                    retryable=False,
                )

        contract = PrivatePageShareContract(
            doc_id=str(captured.get("doc_id") or "").strip(),
            friendly_name=str(captured.get("friendly_name") or "").strip(),
            endpoint_url=_endpoint(captured.get("endpoint_url")),
            variables=templated,
            request_envelope=_clean_envelope(captured.get("request_envelope")),
            source=str(
                captured.get("source") or "browser_graphql_capture"
            ).strip(),
            observed_at=str(
                captured.get("observed_at") or int(time.time())
            ).strip(),
        )
        if not (
            contract.doc_id.isdigit()
            and 5 <= len(contract.doc_id) <= 40
            and contract.friendly_name
        ):
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_CAPTURE_INVALID",
                "Captured Page-share mutation is missing doc_id/friendly_name",
                retryable=False,
            )
        _reject_auth({
            "variables": contract.variables,
            "request_envelope": contract.request_envelope,
        })

        with _LOCK:
            previous = self._load()
            row = {
                "doc_id": contract.doc_id,
                "friendly_name": contract.friendly_name,
                "endpoint_url": contract.endpoint_url,
                "variables": contract.variables,
                "request_envelope": contract.request_envelope,
                "source": contract.source,
                "observed_at": contract.observed_at,
            }
            try:
                self._save(row)
                self._candidate(contract)
            except Exception:
                if previous:
                    try:
                        self._save(previous)
                    except Exception:
                        pass
                raise
        return contract

    @staticmethod
    def render(
        contract: PrivatePageShareContract,
        *,
        business_id: str,
        page_id: str,
        actor_id: str = "",
    ) -> dict[str, Any]:
        business = _clean_id(business_id)
        page = _clean_id(page_id)
        actor = _clean_id(actor_id)
        if not business or not page:
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_INVALID_INPUT",
                "Exact Business and Page IDs are required",
                retryable=False,
            )
        rendered = _render(
            contract.variables,
            {
                "business_id": business,
                "page_id": page,
                "actor_id": actor,
            },
        )
        encoded = json.dumps(rendered, ensure_ascii=False)
        if "{{" in encoded or "}}" in encoded:
            raise ProvisioningError(
                "PRIVATE_PAGE_SHARE_CONTRACT_INVALID",
                "Captured Page-share contract still contains unresolved placeholders",
                retryable=False,
            )
        return rendered


async def execute_private_page_share(
    web: Any,
    contract: PrivatePageShareContract,
    *,
    business_id: str,
    page_id: str,
    actor_id: str = "",
    profile_id: str = "",
) -> dict[str, Any]:
    candidate = PrivatePageShareContractStore._candidate(contract)
    variables = PrivatePageShareContractStore.render(
        contract,
        business_id=business_id,
        page_id=page_id,
        actor_id=actor_id,
    )
    try:
        payload = await web.graphql(
            contract.doc_id,
            variables,
            friendly_name=contract.friendly_name,
            endpoint_url=contract.endpoint_url,
            request_envelope=contract.request_envelope,
        )
    except Exception as exc:
        kind = classify_cache_failure(
            exception=exc,
            message=str(exc),
            http_status=int(getattr(exc, "http_status", 0) or 0) or None,
            payload=(
                getattr(exc, "meta_payload", None)
                if isinstance(getattr(exc, "meta_payload", None), dict)
                else None
            ),
        )
        record_result(
            PAGE_SHARE_OPERATION,
            candidate,
            success=False,
            reason=str(exc)[:1000],
            profile_id=profile_id,
            failure_kind=kind,
        )
        raise

    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, list) and errors:
        kind = classify_cache_failure(
            payload=payload,
            message=json.dumps(errors, ensure_ascii=False)[:2000],
        )
        record_result(
            PAGE_SHARE_OPERATION,
            candidate,
            success=False,
            reason="GraphQL response contains errors",
            profile_id=profile_id,
            failure_kind=kind,
        )
        raise ProvisioningError(
            "PRIVATE_PAGE_SHARE_REJECTED",
            "Meta rejected the private Page-share mutation",
            retryable=(kind in {"network", "other"}),
        )

    record_result(
        PAGE_SHARE_OPERATION,
        candidate,
        success=True,
        reason="request_accepted",
        profile_id=profile_id,
    )
    return payload if isinstance(payload, dict) else {}


__all__ = [
    "PAGE_SHARE_OPERATION",
    "PrivatePageShareContract",
    "PrivatePageShareContractStore",
    "execute_private_page_share",
]
