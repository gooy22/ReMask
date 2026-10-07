from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fb_worker import AuthenticationError, RemoteRequestError

from .action_result import ActionResult
from .facebook_business_browser import FacebookBusinessBrowser
from .page_access_inspection import inspect_browser_pages
from .payment_inspection import inspect_payment_methods
from .provisioning.models import ProvisioningError
from .provisioning.state import ProvisioningStateStore


class PrivateLaunchStep(str, Enum):
    CAMPAIGN = "CAMPAIGN"
    ADSET = "ADSET"
    CREATIVE = "CREATIVE"
    AD = "AD"


STEP_ORDER = [
    PrivateLaunchStep.CAMPAIGN,
    PrivateLaunchStep.ADSET,
    PrivateLaunchStep.CREATIVE,
    PrivateLaunchStep.AD,
]


@dataclass(slots=True, frozen=True)
class MutationContract:
    step: PrivateLaunchStep
    doc_id: str
    friendly_name: str
    endpoint_url: str
    variables: dict[str, Any]
    result_id_paths: tuple[str, ...]
    request_envelope: dict[str, Any]


class PrivateLaunchContractStore:
    """Durable server-side contracts captured from Meta's current private web flow."""

    ALLOWED_HOSTS = {"business.facebook.com", "www.facebook.com"}
    SAFE_ENVELOPE_KEYS = {
        "__aaid", "__bid", "__hs", "__hblp", "__hsdp", "__rev", "__s",
        "__hsi", "__dyn", "__csr", "__comet_req", "__spin_r", "__spin_b",
        "__spin_t", "__jssesw", "__crn", "__req", "__ccg", "dpr",
        "server_timestamps", "fb_api_caller_class",
    }
    REQUIRED_PLACEHOLDERS = {
        PrivateLaunchStep.CAMPAIGN: {"{{ad_account_id}}"},
        PrivateLaunchStep.ADSET: {"{{campaign_id}}"},
        PrivateLaunchStep.CREATIVE: {"{{ad_account_id}}", "{{page_id}}"},
        PrivateLaunchStep.AD: {"{{adset_id}}", "{{creative_id}}"},
    }

    def __init__(
        self,
        raw: str | None = None,
        *,
        path: str | Path | None = None,
    ) -> None:
        self.path = Path(
            path
            or os.getenv("REMASK_PRIVATE_LAUNCH_CONTRACTS_PATH")
            or "/var/lib/remask/private-launch-contracts.json"
        )
        if raw is not None:
            parsed = self._decode(raw)
        else:
            persisted: dict[str, Any] = {}
            try:
                if self.path.is_file():
                    persisted = self._decode(self.path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise ProvisioningError(
                    "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                    f"Private Launch contract registry cannot be read: {exc.__class__.__name__}",
                    retryable=False,
                ) from exc
            env_parsed = self._decode(
                os.getenv("REMASK_PRIVATE_LAUNCH_CONTRACTS_JSON", "{}")
            )
            parsed = {**persisted, **env_parsed}
        self._raw = parsed

    @staticmethod
    def _decode(raw: str | None) -> dict[str, Any]:
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                "Private Launch contract JSON is invalid",
                retryable=False,
            ) from exc
        if not isinstance(parsed, dict):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                "Private Launch contract JSON must be an object",
                retryable=False,
            )
        return parsed

    @classmethod
    def _endpoint(cls, value: Any) -> str:
        endpoint = str(value or "https://business.facebook.com/api/graphql/").strip()
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in cls.ALLOWED_HOSTS
            or parsed.path.rstrip("/") != "/api/graphql"
        ):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                "Private Launch endpoint must be business.facebook.com/api/graphql/",
                retryable=False,
            )
        return endpoint

    @classmethod
    def _validate_placeholders(
        cls,
        step: PrivateLaunchStep,
        variables: dict[str, Any],
    ) -> None:
        encoded = json.dumps(
            variables,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        missing = sorted(
            placeholder
            for placeholder in cls.REQUIRED_PLACEHOLDERS[step]
            if placeholder not in encoded
        )
        if missing:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                f"{step.value} contract is missing required placeholders: {', '.join(missing)}",
                retryable=False,
            )

    @classmethod
    def _clean_envelope(cls, value: Any) -> dict[str, str]:
        if value in (None, {}):
            return {}
        if not isinstance(value, dict):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                "request_envelope must be an object",
                retryable=False,
            )
        unknown = sorted(
            str(key)
            for key in value
            if str(key) not in cls.SAFE_ENVELOPE_KEYS
        )
        if unknown:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                "Private Launch request_envelope contains unsupported fields: "
                + ", ".join(unknown[:10]),
                retryable=False,
            )
        return {
            str(key): str(raw_value)[:20000]
            for key, raw_value in value.items()
            if str(raw_value or "").strip()
        }

    def get(self, step: PrivateLaunchStep) -> MutationContract:
        row = self._raw.get(step.value)
        if not isinstance(row, dict):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_REQUIRED",
                f"No captured private mutation contract for {step.value}",
                retryable=False,
            )
        doc_id = str(row.get("doc_id") or "").strip()
        friendly_name = str(row.get("friendly_name") or "").strip()
        variables = row.get("variables")
        result_paths = row.get("result_id_paths")
        if (
            not re.fullmatch(r"\d{5,40}", doc_id)
            or not friendly_name
            or not isinstance(variables, dict)
            or not isinstance(result_paths, list)
            or not result_paths
            or not all(isinstance(path, str) and path.strip() for path in result_paths)
        ):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                f"Captured contract for {step.value} is incomplete",
                retryable=False,
            )
        self._validate_placeholders(step, variables)
        return MutationContract(
            step=step,
            doc_id=doc_id,
            friendly_name=friendly_name[:180],
            endpoint_url=self._endpoint(row.get("endpoint_url")),
            variables=variables,
            result_id_paths=tuple(path.strip() for path in result_paths),
            request_envelope=self._clean_envelope(row.get("request_envelope")),
        )

    def status(self) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        for step in STEP_ORDER:
            try:
                contract = self.get(step)
                output[step.value] = {
                    "configured": True,
                    "friendly_name": contract.friendly_name,
                    "endpoint_host": urlsplit(contract.endpoint_url).hostname or "",
                    "result_paths": len(contract.result_id_paths),
                }
            except ProvisioningError as exc:
                output[step.value] = {
                    "configured": False,
                    "code": exc.code,
                    "message": str(exc),
                }
        return output

    def register(
        self,
        step: PrivateLaunchStep,
        row: dict[str, Any],
    ) -> MutationContract:
        if not isinstance(row, dict):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                "Captured contract must be an object",
                retryable=False,
            )
        previous = self._raw.get(step.value)
        self._raw[step.value] = dict(row)
        try:
            contract = self.get(step)
        except Exception:
            if previous is None:
                self._raw.pop(step.value, None)
            else:
                self._raw[step.value] = previous
            raise

        persisted = dict(self._raw)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(
                    persisted,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except OSError as exc:
            if previous is None:
                self._raw.pop(step.value, None)
            else:
                self._raw[step.value] = previous
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_STORE_FAILED",
                f"Private Launch contract registry write failed: {exc.__class__.__name__}",
                retryable=False,
            ) from exc
        return contract


class PrivateLaunchStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS private_launch_steps(
                    launch_key TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    business_id TEXT NOT NULL,
                    ad_account_id TEXT NOT NULL,
                    step TEXT NOT NULL,
                    status TEXT NOT NULL,
                    entity_id TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    submitted INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(launch_key,step)
                )
                """
            )

    async def step(self, launch_key: str, step: PrivateLaunchStep) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._step_sync, launch_key, step.value)

    def _step_sync(self, launch_key: str, step: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM private_launch_steps WHERE launch_key=? AND step=?",
                (launch_key, step),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        raw = data.pop("result_json", None)
        if raw:
            try:
                data["result"] = json.loads(str(raw))
            except (json.JSONDecodeError, TypeError, ValueError):
                data["result"] = {}
        else:
            data["result"] = {}
        return data

    async def save(
        self,
        *,
        launch_key: str,
        profile_id: str,
        business_id: str,
        ad_account_id: str,
        step: PrivateLaunchStep,
        status: str,
        entity_id: str = "",
        result: dict[str, Any] | None = None,
        error_code: str = "",
        submitted: bool | None = False,
    ) -> None:
        await asyncio.to_thread(
            self._save_sync,
            launch_key,
            profile_id,
            business_id,
            ad_account_id,
            step.value,
            status,
            entity_id,
            result or {},
            error_code,
            submitted,
        )

    def _save_sync(
        self,
        launch_key: str,
        profile_id: str,
        business_id: str,
        ad_account_id: str,
        step: str,
        status: str,
        entity_id: str,
        result: dict[str, Any],
        error_code: str,
        submitted: bool | None,
    ) -> None:
        now = int(time.time())
        submitted_db = None if submitted is None else (1 if submitted else 0)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO private_launch_steps(
                    launch_key,profile_id,business_id,ad_account_id,step,status,
                    entity_id,result_json,error_code,submitted,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(launch_key,step) DO UPDATE SET
                    status=excluded.status,
                    entity_id=excluded.entity_id,
                    result_json=excluded.result_json,
                    error_code=excluded.error_code,
                    submitted=excluded.submitted,
                    updated_at=excluded.updated_at
                """,
                (
                    launch_key,
                    profile_id,
                    business_id,
                    ad_account_id,
                    step,
                    status,
                    entity_id or None,
                    encoded,
                    error_code or None,
                    submitted_db,
                    now,
                    now,
                ),
            )


def _lookup(value: Any, path: str) -> Any:
    current = value
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}")


def _render(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {str(k): _render(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, context) for v in value]
    if not isinstance(value, str):
        return value

    match = _PLACEHOLDER.fullmatch(value.strip())
    if match:
        resolved = _lookup(context, match.group(1))
        if resolved is None:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_INPUT_MISSING",
                f"Missing contract value: {match.group(1)}",
                retryable=False,
            )
        return resolved

    def replace(match: re.Match[str]) -> str:
        resolved = _lookup(context, match.group(1))
        if resolved is None:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_INPUT_MISSING",
                f"Missing contract value: {match.group(1)}",
                retryable=False,
            )
        if isinstance(resolved, (dict, list)):
            return json.dumps(resolved, ensure_ascii=False, separators=(",", ":"))
        return str(resolved)

    return _PLACEHOLDER.sub(replace, value)


def _extract_entity_id(payload: Any, paths: tuple[str, ...]) -> tuple[str, str]:
    for path in paths:
        value = _lookup(payload, path)
        candidate = str(value or "").removeprefix("act_").strip()
        if re.fullmatch(r"\d{5,40}", candidate):
            return candidate, path
    return "", ""


def _deep_merge(base: Any, override: Any) -> Any:
    """Merge a per-RK Launch override without mutating the saved base payload."""
    if not isinstance(base, dict):
        return override
    if not isinstance(override, dict):
        return dict(base)
    out = dict(base)
    for key, value in override.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _effective_launch_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvisioningError(
            "PRIVATE_LAUNCH_INVALID_INPUT",
            "launch must be an object",
            retryable=False,
        )
    if "base" not in value and "override" not in value:
        return dict(value)
    base = value.get("base") or {}
    override = value.get("override") or {}
    if not isinstance(base, dict) or not isinstance(override, dict):
        raise ProvisioningError(
            "PRIVATE_LAUNCH_INVALID_INPUT",
            "launch.base and launch.override must be objects",
            retryable=False,
        )
    return _deep_merge(base, override)


def _response_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"response_type": type(payload).__name__}
    errors = payload.get("errors")
    return {
        "response_keys": sorted(str(key) for key in payload.keys())[:40],
        "has_errors": isinstance(errors, list) and bool(errors),
        "error_count": len(errors) if isinstance(errors, list) else 0,
    }


class PrivateLaunchService:
    def __init__(
        self,
        provisioning_state: ProvisioningStateStore,
        *,
        contracts: PrivateLaunchContractStore | None = None,
    ) -> None:
        self.provisioning_state = provisioning_state
        self.contracts = contracts or PrivateLaunchContractStore()
        self.state = PrivateLaunchStateStore(provisioning_state.path)
        self._preflight_cache: dict[
            tuple[str, str, str, str],
            tuple[float, int, dict[str, Any]],
        ] = {}

    @staticmethod
    def _id(value: Any, label: str) -> str:
        clean = str(value or "").removeprefix("act_").strip()
        if not re.fullmatch(r"\d{5,30}", clean):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_INVALID_TARGET",
                f"{label} is missing or invalid",
                retryable=False,
            )
        return clean

    @staticmethod
    def _inventory_row(context: Any, business_id: str, ad_account_id: str) -> dict[str, Any] | None:
        for row in (getattr(context, "ad_accounts", None) or []):
            if not isinstance(row, dict):
                continue
            account = str(
                row.get("ad_account_id")
                or row.get("account_id")
                or row.get("id")
                or ""
            ).removeprefix("act_").strip()
            business = str(row.get("business_id") or "").strip()
            if account == ad_account_id and business == business_id:
                return row
        return None

    async def _live_preflight(
        self,
        *,
        context: Any,
        profile_id: str,
        business_id: str,
        ad_account_id: str,
        page_id: str,
    ) -> dict[str, Any]:
        row = self._inventory_row(context, business_id, ad_account_id)
        if row is None:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_RK_NOT_IN_CONFIRMED_INVENTORY",
                "Exact RK/BM relation is absent from the last confirmed Workspace inventory",
                retryable=False,
            )

        try:
            max_age = max(
                60,
                min(
                    7200,
                    int(os.getenv("REMASK_LAUNCH_INVENTORY_MAX_AGE_SECONDS") or "1800"),
                ),
            )
        except (TypeError, ValueError):
            max_age = 1800
        inventory_at = int(getattr(context, "inventory_updated_at", 0) or 0)
        if inventory_at <= 0 or int(time.time()) - inventory_at > max_age:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_INVENTORY_STALE",
                "Launch requires a fresh confirmed Workspace inventory",
                retryable=False,
            )

        status = row.get("account_status")
        if status is None:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_RK_STATUS_UNVERIFIED",
                "Launch requires a live account_status for the exact RK",
                retryable=False,
            )
        try:
            active = int(status) == 1
        except (TypeError, ValueError):
            active = False
        if not active:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_RK_NOT_ACTIVE",
                "Selected RK is not ACTIVE in the confirmed live inventory",
                retryable=False,
            )

        account_name = str(row.get("name") or row.get("account_name") or ad_account_id).strip()
        progress: dict[str, Any] = {}
        async with FacebookBusinessBrowser(context, v8_old_space_mb=256) as browser:
            pages = await asyncio.wait_for(
                inspect_browser_pages(
                    browser,
                    ad_account_id,
                    business_id,
                    timeout=45,
                    progress=progress,
                    open_identity=True,
                ),
                timeout=60,
            )
        page_ids = {
            str(value.get("id") or "").strip()
            for value in (pages.get("data") or [])
            if isinstance(value, dict)
        }
        if (
            pages.get("checked_live") is not True
            or pages.get("account_scope_verified") is not True
            or pages.get("ad_account_page_access_verified") is not True
            or page_id not in page_ids
        ):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_PAGE_ACCESS_UNVERIFIED",
                "Launch requires the selected Page to be live-proven for the exact RK",
                retryable=False,
            )

        # inspect_browser_pages intentionally keeps a write barrier installed
        # until its browser context closes. Billing therefore gets a fresh
        # context so read-only payment hydration cannot inherit that route gate.
        async with FacebookBusinessBrowser(context, v8_old_space_mb=256) as browser:
            funding = await asyncio.wait_for(
                inspect_payment_methods(
                    browser,
                    ad_account_id,
                    business_id=business_id,
                    asset={
                        "id": ad_account_id,
                        "business_id": business_id,
                        "name": account_name,
                    },
                    fresh_billing_context=True,
                ),
                timeout=65,
            )
        if (
            funding.get("account_scope_verified") is not True
            or funding.get("checked_live") is not True
            or funding.get("verification_status") != "LINKED"
            or funding.get("card_linked") is not True
            or not (funding.get("payment_methods") or [])
        ):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_PAYMENT_UNVERIFIED",
                "Launch requires a live linked payment method on the exact RK",
                retryable=False,
            )

        return {
            "profile_id": profile_id,
            "business_id": business_id,
            "ad_account_id": ad_account_id,
            "page_id": page_id,
            "account_status": 1,
            "inventory_updated_at": inventory_at,
            "page_access_checked_live": True,
            "payment_checked_live": True,
            "payment_method_count": len(funding.get("payment_methods") or []),
        }

    async def _review_preflight(
        self,
        *,
        context: Any,
        profile_id: str,
        business_id: str,
        ad_account_id: str,
        page_id: str,
    ) -> dict[str, Any]:
        try:
            ttl = max(
                10.0,
                min(
                    120.0,
                    float(os.getenv("REMASK_PRIVATE_LAUNCH_REVIEW_TTL_SECONDS") or "60"),
                ),
            )
        except (TypeError, ValueError):
            ttl = 60.0
        inventory_at = int(getattr(context, "inventory_updated_at", 0) or 0)
        key = (profile_id, business_id, ad_account_id, page_id)
        cached = self._preflight_cache.get(key)
        now = time.monotonic()
        if cached is not None:
            cached_at, cached_inventory_at, proof = cached
            if (
                inventory_at > 0
                and cached_inventory_at == inventory_at
                and now - cached_at <= ttl
            ):
                return {**proof, "review_cache_reused": True}

        proof = await self._live_preflight(
            context=context,
            profile_id=profile_id,
            business_id=business_id,
            ad_account_id=ad_account_id,
            page_id=page_id,
        )
        self._preflight_cache[key] = (now, inventory_at, dict(proof))
        return proof

    async def review(
        self,
        *,
        profile_id: str,
        context: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_INVALID_INPUT",
                "Private Launch payload must be an object",
                retryable=False,
            )
        business_id = self._id(payload.get("business_id"), "business_id")
        ad_account_id = self._id(payload.get("ad_account_id"), "ad_account_id")
        page_id = self._id(payload.get("page_id"), "page_id")
        launch_payload = _effective_launch_payload(payload.get("launch"))

        # Validate the complete mutation chain before the first irreversible
        # request. A missing downstream contract must never leave an orphaned
        # Campaign or Ad Set behind.
        contracts = {step: self.contracts.get(step) for step in STEP_ORDER}
        preflight = await self._review_preflight(
            context=context,
            profile_id=profile_id,
            business_id=business_id,
            ad_account_id=ad_account_id,
            page_id=page_id,
        )
        return {
            "ready": True,
            "profile_id": profile_id,
            "business_id": business_id,
            "ad_account_id": ad_account_id,
            "page_id": page_id,
            "preflight": preflight,
            "launch": launch_payload,
            "contracts": {
                step.value: {
                    "friendly_name": contract.friendly_name,
                    "doc_id_configured": True,
                }
                for step, contract in contracts.items()
            },
            "_contracts": contracts,
        }

    async def run(
        self,
        *,
        item_id: str,
        profile_id: str,
        context: Any,
        session: Any,
        payload: dict[str, Any],
        task_idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        del item_id
        review = await self.review(
            profile_id=profile_id,
            context=context,
            payload=payload,
        )
        business_id = str(review["business_id"])
        ad_account_id = str(review["ad_account_id"])
        page_id = str(review["page_id"])
        launch_payload = review["launch"]
        contracts = review.pop("_contracts")
        preflight = review["preflight"]

        launch_key = str(
            payload.get("launch_key")
            or task_idempotency_key
            or f"{profile_id}:{business_id}:{ad_account_id}"
        ).strip()
        if not launch_key or len(launch_key) > 200:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_INVALID_INPUT",
                "launch_key is missing or too long",
                retryable=False,
            )

        await self.state.init()

        values: dict[str, Any] = {
            "profile_id": profile_id,
            "business_id": business_id,
            "ad_account_id": ad_account_id,
            "page_id": page_id,
            "payload": launch_payload,
        }
        completed: list[dict[str, Any]] = []

        for step in STEP_ORDER:
            prior = await self.state.step(launch_key, step)
            if prior and str(prior.get("status") or "").upper() == "SUCCESS":
                entity_id = str(prior.get("entity_id") or "").strip()
                values[f"{step.value.lower()}_id"] = entity_id
                completed.append({
                    "step": step.value,
                    "status": "SUCCESS",
                    "skipped": True,
                    "entity_id": entity_id,
                })
                continue
            if prior and str(prior.get("status") or "").upper() in {
                "RECONCILE_REQUIRED",
                "BLOCKED",
            }:
                previous_status = str(prior.get("status") or "").upper()
                code = (
                    "PRIVATE_LAUNCH_RECONCILE_REQUIRED"
                    if previous_status == "RECONCILE_REQUIRED"
                    else "PRIVATE_LAUNCH_BLOCKED_REPLAY"
                )
                raise ProvisioningError(
                    code,
                    f"{step.value} has a durable {previous_status} result; automatic resubmit is blocked",
                    retryable=False,
                )

            contract = contracts[step]
            variables = _render(
                contract.variables,
                {
                    **values,
                    "target": {
                        "profile_id": profile_id,
                        "business_id": business_id,
                        "ad_account_id": ad_account_id,
                        "page_id": page_id,
                    },
                },
            )

            web = await session.facebook_web()
            try:
                response = await web.graphql(
                    contract.doc_id,
                    variables,
                    friendly_name=contract.friendly_name,
                    endpoint_url=contract.endpoint_url,
                    request_envelope=contract.request_envelope,
                )
            except AuthenticationError:
                result = ActionResult.reconcile(
                    step.value,
                    code=f"PRIVATE_LAUNCH_{step.value}_AUTH_RESULT_UNKNOWN",
                    message="Facebook authentication changed around the private mutation boundary",
                    submitted=None,
                    evidence={"friendly_name": contract.friendly_name},
                )
                await self.state.save(
                    launch_key=launch_key,
                    profile_id=profile_id,
                    business_id=business_id,
                    ad_account_id=ad_account_id,
                    step=step,
                    status=result.status.value,
                    result=result.as_dict(),
                    error_code=result.code,
                    submitted=result.submitted,
                )
                raise ProvisioningError(result.code, result.message, retryable=False)
            except RemoteRequestError as exc:
                http_status = int(getattr(exc, "http_status", 0) or 0)
                if 400 <= http_status < 500 and http_status != 429:
                    result = ActionResult.blocked(
                        step.value,
                        code=f"PRIVATE_LAUNCH_{step.value}_REJECTED",
                        message=f"Meta rejected {step.value} private mutation",
                        evidence={
                            "http_status": http_status,
                            "friendly_name": contract.friendly_name,
                        },
                    )
                    await self.state.save(
                        launch_key=launch_key,
                        profile_id=profile_id,
                        business_id=business_id,
                        ad_account_id=ad_account_id,
                        step=step,
                        status=result.status.value,
                        result=result.as_dict(),
                        error_code=result.code,
                        submitted=True,
                    )
                    raise ProvisioningError(result.code, result.message, retryable=False)

                result = ActionResult.reconcile(
                    step.value,
                    code=f"PRIVATE_LAUNCH_{step.value}_RESULT_UNKNOWN",
                    message=f"{step.value} private mutation result is uncertain",
                    submitted=None,
                    evidence={
                        "http_status": http_status,
                        "friendly_name": contract.friendly_name,
                    },
                )
                await self.state.save(
                    launch_key=launch_key,
                    profile_id=profile_id,
                    business_id=business_id,
                    ad_account_id=ad_account_id,
                    step=step,
                    status=result.status.value,
                    result=result.as_dict(),
                    error_code=result.code,
                    submitted=result.submitted,
                )
                raise ProvisioningError(result.code, result.message, retryable=False)

            entity_id, response_path = _extract_entity_id(
                response,
                contract.result_id_paths,
            )
            if not entity_id:
                result = ActionResult.reconcile(
                    step.value,
                    code=f"PRIVATE_LAUNCH_{step.value}_RESULT_UNKNOWN",
                    message=f"{step.value} response did not contain a configured exact entity ID",
                    submitted=True,
                    evidence={
                        **_response_summary(response),
                        "friendly_name": contract.friendly_name,
                    },
                )
                await self.state.save(
                    launch_key=launch_key,
                    profile_id=profile_id,
                    business_id=business_id,
                    ad_account_id=ad_account_id,
                    step=step,
                    status=result.status.value,
                    result=result.as_dict(),
                    error_code=result.code,
                    submitted=True,
                )
                raise ProvisioningError(result.code, result.message, retryable=False)

            result = ActionResult.success(
                step.value,
                entity_id=entity_id,
                code=f"PRIVATE_LAUNCH_{step.value}_CONFIRMED",
                evidence={
                    **_response_summary(response),
                    "response_path": response_path,
                    "friendly_name": contract.friendly_name,
                },
            )
            await self.state.save(
                launch_key=launch_key,
                profile_id=profile_id,
                business_id=business_id,
                ad_account_id=ad_account_id,
                step=step,
                status=result.status.value,
                entity_id=entity_id,
                result=result.as_dict(),
                submitted=True,
            )
            values[f"{step.value.lower()}_id"] = entity_id
            completed.append({
                "step": step.value,
                "status": "SUCCESS",
                "skipped": False,
                "entity_id": entity_id,
            })

        return {
            "profile_id": profile_id,
            "business_id": business_id,
            "ad_account_id": ad_account_id,
            "page_id": page_id,
            "launch_key": launch_key,
            "status": "SUCCESS",
            "preflight": preflight,
            "steps": completed,
            "entities": {
                "campaign_id": values.get("campaign_id", ""),
                "adset_id": values.get("adset_id", ""),
                "creative_id": values.get("creative_id", ""),
                "ad_id": values.get("ad_id", ""),
            },
        }
