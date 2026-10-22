from __future__ import annotations

import asyncio
import json
import hashlib
import copy
import logging
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
from .facebook_docids import (
    DocIdCandidate,
    classify_cache_failure,
    list_candidates,
    record_result,
    registry_view,
    upsert_candidate,
)
from .private_launch_read import inspect_pages
from .private_ads_document import _strict_json
from .static_payment_read import inspect_methods
from .private_auth import private_auth_error
from .provisioning.models import ProvisioningError
from .provisioning.meta_transport import MetaTransportRouter
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

AUTO_RESULT_ID_PATH = "__AUTO_UNIQUE_CREATED_ID__"

LAUNCH_DOCID_OPERATION = {
    PrivateLaunchStep.CAMPAIGN: "LAUNCH_CAMPAIGN",
    PrivateLaunchStep.ADSET: "LAUNCH_ADSET",
    PrivateLaunchStep.CREATIVE: "LAUNCH_CREATIVE",
    PrivateLaunchStep.AD: "LAUNCH_AD",
}

log = logging.getLogger("remask.private_launch")


@dataclass(slots=True, frozen=True)
class MutationContract:
    step: PrivateLaunchStep
    doc_id: str
    friendly_name: str
    endpoint_url: str
    variables: dict[str, Any]
    result_id_paths: tuple[str, ...]
    request_envelope: dict[str, Any]
    verification: dict[str, Any] | None = None


def _contract_variables_mode(step: PrivateLaunchStep) -> str:
    return f"private_launch_{step.value.lower()}_v1"


def _contract_candidate(
    step: PrivateLaunchStep,
    contract: MutationContract,
    *,
    source: str = "private_launch_contract",
) -> DocIdCandidate:
    return upsert_candidate(
        LAUNCH_DOCID_OPERATION[step],
        doc_id=contract.doc_id,
        friendly_name=contract.friendly_name,
        endpoint_url=contract.endpoint_url,
        variables_mode=_contract_variables_mode(step),
        source=source or "private_launch_contract",
        priority=8_000,
    )


def _candidate_is_active(
    step: PrivateLaunchStep,
    candidate: DocIdCandidate,
) -> bool:
    for row in list_candidates(
        LAUNCH_DOCID_OPERATION[step],
        confirmed_only=False,
    ):
        if (
            row.doc_id == candidate.doc_id
            and row.friendly_name == candidate.friendly_name
            and row.variables_mode == candidate.variables_mode
            and row.endpoint_url == candidate.endpoint_url
        ):
            return True
    return False


def _record_contract_result(
    step: PrivateLaunchStep,
    candidate: DocIdCandidate,
    *,
    success: bool,
    profile_id: str,
    reason: str = "",
    response_path: str = "",
    failure_kind: str = "",
) -> None:
    try:
        record_result(
            LAUNCH_DOCID_OPERATION[step],
            candidate,
            success=success,
            reason=reason,
            response_path=response_path,
            profile_id=profile_id,
            failure_kind=failure_kind,
        )
    except Exception as exc:
        log.warning(
            "private Launch doc_id result registry failed step=%s type=%s",
            step.value,
            exc.__class__.__name__,
        )


_CAPTURE_PLACEHOLDER_KEY = re.compile(
    r"^(?:actor_id|client_mutation_id|business_id|ad_account_id|page_id|campaign_id|adset_id|creative_id|"
    r"payload(?:\.[A-Za-z0-9_-]+)+)$"
)


def _capture_template(
    value: Any,
    replacements: dict[str, str],
) -> Any:
    """Replace exact captured values with reusable Launch placeholders."""
    if isinstance(value, dict):
        return {
            str(key): _capture_template(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_capture_template(child, replacements) for child in value]

    text = str(value) if isinstance(value, (str, int)) else None
    if text is None:
        return value

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
    ALLOWED_CONTRACT_KEYS = {
        "doc_id", "friendly_name", "endpoint_url", "variables",
        "result_id_paths", "request_envelope", "source", "observed_at", "verification",
    }
    FORBIDDEN_AUTH_KEYS = {
        "fb_dtsg", "access_token", "authorization", "cookie", "cookies",
        "jazoest", "lsd", "__user", "xs", "c_user",
    }

    def __init__(
        self,
        raw: str | None = None,
        *,
        path: str | Path | None = None,
    ) -> None:
        default_registry = (
            Path(os.getenv("REMASK_DATA_DIR") or "/var/lib/remask")
            / "private-launch-contracts.json"
        )
        self.path = Path(
            path
            or os.getenv("REMASK_PRIVATE_LAUNCH_CONTRACTS_PATH")
            or default_registry
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
    def _reject_auth_material(cls, value: Any, path: str = "contract") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key or "").strip().lower()
                if normalized in cls.FORBIDDEN_AUTH_KEYS:
                    raise ProvisioningError(
                        "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                        f"Private Launch contract contains forbidden auth field at {path}.{key}",
                        retryable=False,
                    )
                cls._reject_auth_material(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                cls._reject_auth_material(child, f"{path}[{index}]")

    @classmethod
    def _endpoint(cls, value: Any) -> str:
        endpoint = str(value or "https://business.facebook.com/api/graphql/").strip()
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in cls.ALLOWED_HOSTS
            or parsed.path.rstrip("/") != "/api/graphql"
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or parsed.query or parsed.fragment
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
        unknown_keys = sorted(
            str(key) for key in row
            if str(key) not in self.ALLOWED_CONTRACT_KEYS
        )
        if unknown_keys:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR",
                "Private Launch contract contains unsupported fields: "
                + ", ".join(unknown_keys[:10]),
                retryable=False,
            )
        self._reject_auth_material(row)

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
            verification=row.get("verification"),
        )

    def status(self) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        for step in STEP_ORDER:
            try:
                contract = self.get(step)
                candidate = _contract_candidate(
                    step,
                    contract,
                    source="private_launch_contract",
                )
                active = _candidate_is_active(step, candidate)
                from .private_launch_verify import validate_contract
                verification_code = ""
                try:
                    validate_contract(contract.verification, step.value)
                except ProvisioningError as exc:
                    verification_code = exc.code
                stats = {}
                try:
                    registry = registry_view(
                        LAUNCH_DOCID_OPERATION[step]
                    )
                    for row in (
                        registry.get("operations", {})
                        .get(LAUNCH_DOCID_OPERATION[step], [])
                    ):
                        if (
                            str(row.get("doc_id") or "") == candidate.doc_id
                            and str(row.get("friendly_name") or "")
                            == candidate.friendly_name
                            and str(row.get("variables_mode") or "")
                            == candidate.variables_mode
                        ):
                            stats = (
                                row.get("stats")
                                if isinstance(row.get("stats"), dict)
                                else {}
                            )
                            break
                except Exception:
                    stats = {}
                output[step.value] = {
                    "configured": True,
                    "active": active and not verification_code,
                    "verification_configured": not bool(verification_code),
                    "verification_code": verification_code,
                    "friendly_name": contract.friendly_name,
                    "endpoint_host": urlsplit(contract.endpoint_url).hostname or "",
                    "result_paths": len(contract.result_id_paths),
                    "registry_operation": LAUNCH_DOCID_OPERATION[step],
                    "success_count": int(stats.get("success_count") or 0),
                    "stale_failure_count": int(
                        stats.get("stale_failure_count") or 0
                    ),
                    "disabled": bool(stats.get("disabled")),
                }
            except ProvisioningError as exc:
                output[step.value] = {
                    "configured": False,
                    "code": exc.code,
                    "message": str(exc),
                }
        return output

    def register_capture(
        self,
        step: PrivateLaunchStep,
        captured: dict[str, Any],
        *,
        placeholder_values: dict[str, Any],
        result_id_paths: list[str] | tuple[str, ...] | None = None,
    ) -> MutationContract:
        """Normalize one browser-captured mutation into a reusable contract."""
        if not isinstance(captured, dict):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CAPTURE_INVALID",
                "Captured mutation must be an object",
                retryable=False,
            )
        variables = captured.get("variables")
        if not isinstance(variables, dict) or not variables:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CAPTURE_INVALID",
                "Captured mutation does not contain GraphQL variables",
                retryable=False,
            )
        if not isinstance(placeholder_values, dict):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CAPTURE_INVALID",
                "Capture placeholder values must be an object",
                retryable=False,
            )

        replacements: dict[str, str] = {}
        for raw_key, raw_value in placeholder_values.items():
            key = str(raw_key or "").strip()
            if not _CAPTURE_PLACEHOLDER_KEY.fullmatch(key):
                raise ProvisioningError(
                    "PRIVATE_LAUNCH_CAPTURE_INVALID",
                    f"Unsupported capture placeholder: {key}",
                    retryable=False,
                )
            value = str(raw_value or "").strip()
            if not value:
                continue
            placeholder = "{{" + key + "}}"
            current = replacements.get(value)
            if current and current != placeholder:
                raise ProvisioningError(
                    "PRIVATE_LAUNCH_CAPTURE_AMBIGUOUS",
                    "Two Launch placeholders map to the same captured scalar value",
                    retryable=False,
                )
            replacements[value] = placeholder

        templated = _capture_template(variables, replacements)

        paths = [
            str(path).strip()
            for path in (result_id_paths or [])
            if str(path or "").strip()
        ]
        if not paths:
            try:
                paths = list(self.get(step).result_id_paths)
            except ProvisioningError:
                paths = [AUTO_RESULT_ID_PATH]

        row = {
            "doc_id": str(captured.get("doc_id") or "").strip(),
            "friendly_name": str(captured.get("friendly_name") or "").strip(),
            "endpoint_url": str(
                captured.get("endpoint_url")
                or "https://business.facebook.com/api/graphql/"
            ).strip(),
            "variables": templated,
            "result_id_paths": paths,
            "request_envelope": (
                captured.get("request_envelope")
                if isinstance(captured.get("request_envelope"), dict)
                else {}
            ),
            "source": str(
                captured.get("source") or "browser_graphql_capture"
            ).strip(),
            "observed_at": str(captured.get("observed_at") or "").strip(),
        }
        return self.register(step, row)

    def promote_result_path(
        self,
        step: PrivateLaunchStep,
        response_path: str,
    ) -> MutationContract:
        clean_path = str(response_path or "").strip()
        if not clean_path or clean_path == AUTO_RESULT_ID_PATH:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_RESULT_PATH_INVALID",
                "A concrete response ID path is required",
                retryable=False,
            )
        row = self._raw.get(step.value)
        if not isinstance(row, dict):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_CONTRACT_REQUIRED",
                f"No captured private mutation contract for {step.value}",
                retryable=False,
            )
        updated = dict(row)
        existing = [
            str(path).strip()
            for path in (updated.get("result_id_paths") or [])
            if str(path or "").strip()
            and str(path).strip() != AUTO_RESULT_ID_PATH
        ]
        if clean_path not in existing:
            existing.insert(0, clean_path)
        updated["result_id_paths"] = existing
        return self.register(step, updated)

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

        try:
            _contract_candidate(
                step,
                contract,
                source="private_launch_contract",
            )
        except Exception as exc:
            log.warning(
                "private Launch contract candidate mirror failed step=%s type=%s",
                step.value,
                exc.__class__.__name__,
            )
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
            con.execute("""CREATE TABLE IF NOT EXISTS private_launch_plans(
                launch_key TEXT PRIMARY KEY, profile_id TEXT NOT NULL, business_id TEXT NOT NULL,
                ad_account_id TEXT NOT NULL, fingerprint TEXT NOT NULL, contract_digest TEXT NOT NULL,
                created_at INTEGER NOT NULL)""")
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

    async def bind_plan(self, launch_key, values, fingerprint, contract_digest):
        await asyncio.to_thread(self._bind_plan_sync, launch_key, values, fingerprint, contract_digest)

    def _bind_plan_sync(self, launch_key, values, fingerprint, contract_digest):
        expected = (values["profile_id"], values["business_id"], values["ad_account_id"], fingerprint, contract_digest)
        with self._connect() as con:
            con.execute("""INSERT INTO private_launch_plans VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(launch_key) DO NOTHING""", (launch_key, *expected, int(time.time())))
            row = con.execute("SELECT * FROM private_launch_plans WHERE launch_key=?", (launch_key,)).fetchone()
            actual = tuple(row[key] for key in ("profile_id", "business_id", "ad_account_id", "fingerprint", "contract_digest"))
            if actual != expected:
                raise ProvisioningError("PRIVATE_LAUNCH_PLAN_CHANGED",
                    "This Launch key already has another target, configuration or contract revision", retryable=False)

    async def step(self, launch_key: str, step: PrivateLaunchStep) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._step_sync, launch_key, step.value)

    async def all_steps(self, launch_key):
        def read():
            with self._connect() as con:
                names = [row[0] for row in con.execute("SELECT step FROM private_launch_steps WHERE launch_key=?", (launch_key,))]
            return [self._step_sync(launch_key, name) for name in names]
        return await asyncio.to_thread(read)

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
        corrupt = False
        if raw:
            try:
                decoded = _strict_json(str(raw))
                if not isinstance(decoded, dict):
                    corrupt = True
                    data["result"] = {}
                else:
                    data["result"] = decoded
            except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
                corrupt = True
                data["result"] = {}
        else:
            data["result"] = {}
            corrupt = data.get("status") == "SUCCESS"
        if corrupt:
            # Never expose a persisted SUCCESS as authoritative when its
            # independent result evidence cannot be decoded. This is a read
            # projection only; we do not rewrite or silently replay the claim.
            data["status"] = "RECONCILE_REQUIRED"
            data["entity_id"] = None
            data["error_code"] = "PRIVATE_LAUNCH_JOURNAL_CORRUPT"
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

    async def finish_read(self, prior: dict[str, Any], result: ActionResult) -> bool:
        """A late read must not overwrite another worker's confirmed result."""
        return await asyncio.to_thread(self._finish_read_sync, prior, result.as_dict())

    def _finish_read_sync(self, prior: dict[str, Any], result: dict[str, Any]) -> bool:
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with self._connect() as con:
            cursor = con.execute("""UPDATE private_launch_steps
                SET status=?,entity_id=?,result_json=?,error_code=?,submitted=1,updated_at=?
                WHERE launch_key=? AND step=? AND profile_id=? AND business_id=? AND ad_account_id=?
                    AND status=? AND entity_id IS ? AND result_json=?""",
                (result["status"], result.get("entity_id") or None, encode(result), result["code"], int(time.time()),
                 prior["launch_key"], prior["step"], prior["profile_id"], prior["business_id"],
                 prior["ad_account_id"], prior["status"], prior["entity_id"], encode(prior["result"])))
            return cursor.rowcount == 1

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
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with self._connect() as con:
            # INSERT is the durable ownership claim. Two worker processes can
            # both pass the earlier read/preflight; only one may cross CREATE.
            conflict = "DO NOTHING" if status == "SUBMITTING" else """DO UPDATE SET
                    status=excluded.status,
                    entity_id=excluded.entity_id,
                    result_json=excluded.result_json,
                    error_code=excluded.error_code,
                    submitted=excluded.submitted,
                    updated_at=excluded.updated_at
                WHERE private_launch_steps.profile_id=excluded.profile_id
                    AND private_launch_steps.business_id=excluded.business_id
                    AND private_launch_steps.ad_account_id=excluded.ad_account_id"""
            cursor = con.execute(
                """
                INSERT INTO private_launch_steps(
                    launch_key,profile_id,business_id,ad_account_id,step,status,
                    entity_id,result_json,error_code,submitted,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(launch_key,step) """ + conflict,
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
            if cursor.rowcount != 1:
                if status == "SUBMITTING":
                    raise ProvisioningError("PRIVATE_LAUNCH_RECONCILE_REQUIRED",
                        f"{step} already has a durable submit claim; concurrent CREATE is blocked", retryable=False)
                raise ProvisioningError("PRIVATE_LAUNCH_SAVED_OPERATION_MISMATCH",
                    "A journal step is already owned by another profile, business or account", retryable=False)


def _lookup(value: Any, path: str) -> Any:
    current = value
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _resumable_read(prior: dict[str, Any], step: PrivateLaunchStep) -> bool:
    """Only an exact returned ID with a temporary READ failure may resume."""
    entity_id = str(prior.get("entity_id") or "")
    if (prior.get("status") != "RECONCILE_REQUIRED"
            or not re.fullmatch(r"\d{5,30}", entity_id)
            or prior.get("submitted") != 1):
        return False
    code = prior.get("error_code")
    if code == f"PRIVATE_LAUNCH_{step.value}_READ_PENDING":
        # A crashed worker may leave READ_PENDING. Allow the first query's
        # 40-second timeout to finish before a different worker can retry it.
        return time.time() - int(prior.get("updated_at") or 0) >= 90
    return (code == f"PRIVATE_LAUNCH_{step.value}_READ_UNCONFIRMED"
            and prior.get("result", {}).get("evidence", {}).get("verification", {}).get("code")
                in {"READ_UNAVAILABLE", "READ_INCOMPLETE", "SESSION_EXPIRED", "BUSINESS_LOGIN_GATE",
                    "META_AUTH_TOKEN_UNAVAILABLE"})


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


def _normalize_entity_id(value: Any) -> str:
    candidate = str(value or "").removeprefix("act_").strip()
    return candidate if re.fullmatch(r"\d{5,40}", candidate) else ""


def _complete_response(payload: Any) -> bool:
    return (isinstance(payload, dict) and isinstance(payload.get("data"), dict)
            and not payload.get("errors") and not payload.get("error")
            and payload.get("hasNext") is not True
            and not (isinstance(payload.get("extensions"), dict)
                     and payload["extensions"].get("is_final") is False))


def _auto_entity_id(
    payload: Any,
    step: PrivateLaunchStep,
    *,
    excluded_ids: set[str],
) -> tuple[str, str]:
    """Return one unambiguous new entity ID and its response path."""
    if not _complete_response(payload):
        return "", ""
    aliases = {
        PrivateLaunchStep.CAMPAIGN: {
            "campaign_id", "campaignid",
        },
        PrivateLaunchStep.ADSET: {
            "adset_id", "ad_set_id", "adsetid", "ad_setid",
        },
        PrivateLaunchStep.CREATIVE: {
            "creative_id", "creativeid", "adcreative_id", "adcreativeid",
        },
        PrivateLaunchStep.AD: {
            "ad_id", "adid",
        },
    }[step]
    specific: list[tuple[str, str]] = []
    generic: list[tuple[str, str]] = []

    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key)
                child_path = f"{path}.{key}" if path else key
                normalized_key = re.sub(r"[^a-z0-9_]", "", key.casefold())
                entity_id = _normalize_entity_id(child)
                if entity_id and entity_id not in excluded_ids:
                    if normalized_key in aliases:
                        specific.append((entity_id, child_path))
                    elif normalized_key == "id":
                        generic.append((entity_id, child_path))
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_path = f"{path}.{index}" if path else str(index)
                walk(child, child_path)

    walk(payload["data"], "data")

    def unique(rows: list[tuple[str, str]]) -> tuple[str, str]:
        by_id: dict[str, list[str]] = {}
        for entity_id, path in rows:
            by_id.setdefault(entity_id, []).append(path)
        if len(by_id) != 1:
            return "", ""
        entity_id = next(iter(by_id))
        paths = by_id[entity_id]
        return entity_id, paths[0] if paths else ""

    entity_id, response_path = unique(specific)
    if entity_id:
        return entity_id, response_path
    return unique(generic)


def _extract_entity_id(payload: Any, paths: tuple[str, ...]) -> tuple[str, str]:
    if not _complete_response(payload):
        return "", ""
    for path in paths:
        if not path.startswith("data."):
            continue
        value = _lookup(payload, path)
        candidate = str(value or "").removeprefix("act_").strip()
        if re.fullmatch(r"\d{5,40}", candidate):
            return candidate, path
    return "", ""


def _deep_merge(base: Any, override: Any, path: tuple[str, ...] = ()) -> Any:
    """Merge an RK override, replacing each selected targeting value atomically.

    An override may select an entirely new nested exclusion, geo object or
    logical targeting group. Merging within that field retains stale audience
    conditions from another form/account even though the draft matches the
    resulting (incorrect) merged intent. Other independent targeting fields
    and ordinary per-RK overrides still inherit the shared configuration.
    """
    if not isinstance(base, dict):
        return override
    if not isinstance(override, dict):
        return dict(base)
    out = dict(base)
    for key, value in override.items():
        # Replace the targeting object as a unit when a per-RK override
        # explicitly supplies it. Merging nested groups can resurrect stale
        # placements, GEO, audience conditions or exclusions from the base.
        if ((*path, key) == ("adset", "targeting")
                or not isinstance(out.get(key), dict)
                or not isinstance(value, dict)):
            out[key] = value
        else:
            out[key] = _deep_merge(out[key], value, (*path, key))
    return out


def _effective_launch_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvisioningError(
            "PRIVATE_LAUNCH_INVALID_INPUT",
            "launch must be an object",
            retryable=False,
        )
    if "base" not in value and "override" not in value:
        effective = copy.deepcopy(value)
    else:
        base = value.get("base") or {}
        override = value.get("override") or {}
        if not isinstance(base, dict) or not isinstance(override, dict):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_INVALID_INPUT",
                "launch.base and launch.override must be objects",
                retryable=False,
            )
        effective = copy.deepcopy(_deep_merge(base, override))
        # Explicit per-RK status overrides take precedence over a shared
        # delivery choice, just like per-RK budgets and creative settings.
        if isinstance(effective.get("delivery"), dict):
            for section in ("campaign", "adset", "ad"):
                if (isinstance(override.get(section), dict) and "status" in override[section]
                        and section not in (override.get("delivery") or {})):
                    effective["delivery"][section] = override[section]["status"]
    delivery = effective.pop("delivery", {})
    if not isinstance(delivery, dict) or set(delivery) - {"campaign", "adset", "ad"}:
        raise ProvisioningError("PRIVATE_LAUNCH_INVALID_INPUT", "Invalid advertising delivery settings", retryable=False)
    desired = {}
    for section in ("campaign", "adset", "ad"):
        row = effective.get(section)
        if not isinstance(row, dict):
            raise ProvisioningError("PRIVATE_LAUNCH_INVALID_INPUT", f"launch.{section} must be an object", retryable=False)
        requested = delivery.get(section, row.get("status", "PAUSED"))
        if not isinstance(requested, str) or requested not in {"PAUSED", "ACTIVE"}:
            raise ProvisioningError("PRIVATE_LAUNCH_STATUS_INVALID", "Advertising supports PAUSED or ACTIVE delivery", retryable=False)
        desired[section] = requested
        effective[section] = {**row, "status": "PAUSED"}
    if any(status == "ACTIVE" for status in desired.values()):
        effective["delivery"] = desired
    creative = effective.get("creative")
    if not isinstance(creative, dict):
        raise ProvisioningError("PRIVATE_LAUNCH_INVALID_INPUT", "launch.creative must be an object", retryable=False)
    from .private_launch_media import local_references, creative_with_media
    local = local_references(creative)
    if "format" in creative or local or creative.get("carousel_cards"):
        creative = dict(creative)
        effective["creative"] = creative
        format_name = creative.get("format", "SINGLE")
        if not isinstance(format_name, str) or format_name not in {"SINGLE", "CAROUSEL", "INSTAGRAM_POST"}:
            raise ProvisioningError("PRIVATE_LAUNCH_CREATIVE_FORMAT_INVALID", "Unsupported advertising format", retryable=False)
        creative["format"] = format_name
        if creative.get("carousel_cards") and format_name != "CAROUSEL":
            raise ProvisioningError("PRIVATE_LAUNCH_CREATIVE_FORMAT_INVALID", "Carousel cards require CAROUSEL format", retryable=False)
        if format_name == "CAROUSEL":
            planned = [{"image_hash": "planned-local-media"} for _ in local]
            creative_with_media(creative, planned)
            return effective
        media_keys = ("source_instagram_media_id",) if format_name == "INSTAGRAM_POST" else (
            "existing_image_hash", "existing_video_id", "existing_creative_id")
        if format_name == "INSTAGRAM_POST" and local:
            raise ProvisioningError("PRIVATE_LAUNCH_CREATIVE_FORMAT_INVALID", "Instagram posts cannot use local files", retryable=False)
        if not local and not any(creative.get(key) for key in media_keys):
            raise ProvisioningError("PRIVATE_LAUNCH_MEDIA_REQUIRED", "Select media for this ad account", retryable=False)
        if format_name == "SINGLE" and local and len(local) != 1:
            raise ProvisioningError("PRIVATE_LAUNCH_MEDIA_INVALID", "Single creative requires one media item", retryable=False)
    return effective


def _require_paused_contract(contract: MutationContract) -> None:
    """The outgoing template must carry the initial state, not only the UI."""
    if contract.step == PrivateLaunchStep.CREATIVE:
        return
    expected = "{{payload." + contract.step.value.lower() + ".status}}"
    bindings = set(_PLACEHOLDER.findall(json.dumps(contract.variables)))
    if "payload" in bindings or "payload." + contract.step.value.lower() in bindings:
        return  # The complete section is normalized to PAUSED before rendering.
    statuses = []
    pending = [contract.variables]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            statuses.extend(child for key, child in value.items() if key == "status")
            pending.extend(child for child in value.values() if isinstance(child, (dict, list)))
        elif isinstance(value, list):
            pending.extend(value)
    if not statuses or any(value not in ("PAUSED", expected) for value in statuses):
        raise ProvisioningError("PRIVATE_LAUNCH_PAUSED_CONTRACT_REQUIRED",
            f"{contract.step.value} request template must explicitly transmit PAUSED status", retryable=False)


def _require_payload_coverage(contracts, payload):
    """Reject unsupported options before CREATE instead of silently dropping them."""
    bindings = set()
    for contract in contracts.values():
        bindings.update(_PLACEHOLDER.findall(json.dumps(contract.variables)))
    missing = []
    def visit(value, path):
        if any(path == bound or path.startswith(bound + ".") for bound in bindings):
            return
        # PAUSED is enforced independently, and format selects the local
        # creative adapter rather than being an outgoing Meta field.
        if path in {"payload.campaign.status", "payload.adset.status", "payload.ad.status", "payload.creative.format"}:
            return
        if path == "payload.creative.page_id" and "page_id" in bindings:
            return
        if isinstance(value, dict) and value:
            for key, child in value.items():
                visit(child, path + "." + key)
        elif value is not None:
            missing.append(path.removeprefix("payload."))
    for section in ("campaign", "adset", "creative", "ad"):
        visit(payload[section], "payload." + section)
    if missing:
        raise ProvisioningError("PRIVATE_LAUNCH_OPTIONS_UNSUPPORTED",
            "The pinned advertising sender does not transmit these options: " + ", ".join(sorted(missing)[:20]),
            retryable=False)


def _launch_fingerprint(payload: dict[str, Any], page_id: str) -> str:
    canonical = json.dumps({"launch": _effective_launch_payload(payload.get("launch")), "page_id": page_id,
                            "payment": _payment_selector(payload)}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _payment_selector(payload: dict[str, Any]) -> dict[str, str] | None:
    raw = (
        payload.get("selected_payment_method")
        if "selected_payment_method" in payload
        else payload.get("payment_method")
    )
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise ProvisioningError(
            "PRIVATE_LAUNCH_PAYMENT_SELECTION_INVALID",
            "selected payment method must be a masked object",
            retryable=False,
        )
    allowed = {"type", "brand", "last4"}
    unknown = sorted(str(key) for key in raw if str(key) not in allowed)
    if unknown:
        raise ProvisioningError(
            "PRIVATE_LAUNCH_PAYMENT_SELECTION_INVALID",
            "selected payment method contains unsupported fields: "
            + ", ".join(unknown[:10]),
            retryable=False,
        )
    brand = str(raw.get("brand") or raw.get("type") or "").strip()
    last4 = str(raw.get("last4") or "").strip()
    if brand and (
        len(brand) > 40
        or not re.fullmatch(r"[A-Za-z][A-Za-z .+-]{0,39}", brand)
    ):
        raise ProvisioningError(
            "PRIVATE_LAUNCH_PAYMENT_SELECTION_INVALID",
            "selected payment method brand is invalid",
            retryable=False,
        )
    if not re.fullmatch(r"\d{4}", last4):
        raise ProvisioningError(
            "PRIVATE_LAUNCH_PAYMENT_SELECTION_INVALID",
            "selected payment method requires masked last4",
            retryable=False,
        )
    return {"type": brand, "last4": last4}


def _safe_payment_methods(methods: Any) -> list[dict[str, str]]:
    safe_methods: list[dict[str, str]] = []
    for row in methods if isinstance(methods, list) else []:
        if not isinstance(row, dict):
            continue
        brand = str(row.get("type") or row.get("brand") or "").strip()
        last4 = str(row.get("last4") or "").strip()
        if not re.fullmatch(r"\d{4}", last4):
            continue
        if brand and (
            len(brand) > 40
            or not re.fullmatch(r"[A-Za-z][A-Za-z .+-]{0,39}", brand)
        ):
            continue
        # The private billing reader already deduplicates by credential ID.
        # Distinct instruments can share brand/last4: keep that ambiguity
        # instead of turning two cards into one auto-selected payment source.
        safe_methods.append({
            "type": brand,
            "last4": last4,
            "linkage_status": str(
                row.get("linkage_status") or "OBSERVED"
            )[:32],
        })
    return safe_methods


def _select_payment_method(
    methods: Any,
    selector: dict[str, str] | None,
) -> tuple[dict[str, str], str]:
    safe_methods = _safe_payment_methods(methods)

    if not safe_methods:
        raise ProvisioningError(
            "PRIVATE_LAUNCH_PAYMENT_UNVERIFIED",
            "Launch requires a live linked payment method on the exact RK",
            retryable=False,
        )

    if selector is None:
        if len(safe_methods) != 1:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_PAYMENT_SELECTION_REQUIRED",
                "Multiple live payment methods are linked; select one masked card for this RK",
                retryable=False,
            )
        return safe_methods[0], "single_live_method"

    wanted_brand = str(selector.get("type") or "").casefold()
    wanted_last4 = str(selector.get("last4") or "")
    matches = [
        row
        for row in safe_methods
        if row["last4"] == wanted_last4
        and (
            not wanted_brand
            or str(row.get("type") or "").casefold() == wanted_brand
        )
    ]
    if not matches:
        raise ProvisioningError(
            "PRIVATE_LAUNCH_PAYMENT_SELECTION_UNAVAILABLE",
            "Selected masked payment method is not live-linked to the exact RK",
            retryable=False,
        )
    if len(matches) != 1:
        raise ProvisioningError(
            "PRIVATE_LAUNCH_PAYMENT_SELECTION_AMBIGUOUS",
            "Selected masked payment method is not unique for the exact RK",
            retryable=False,
        )
    return matches[0], "explicit_masked_selection"


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
        if contracts is None:
            from .static_launch_contracts import StaticLaunchContractStore
            contracts = StaticLaunchContractStore()
        self.contracts = contracts
        from .private_launch_extensions import LaunchExtensionCatalog, LaunchOperationRunner
        self.extensions = getattr(contracts, "extensions", None) or LaunchExtensionCatalog()
        from .private_advertising_reads import AdvertisingReadCatalog, AdvertisingResourceStore
        self.reads = getattr(contracts, "reads", None) or AdvertisingReadCatalog()
        self.resources = AdvertisingResourceStore(self.reads)
        self.state = PrivateLaunchStateStore(provisioning_state.path)
        self.operations = LaunchOperationRunner(self.state)
        self.ads_manager = None
        if getattr(contracts, "protocol", None) == "private_ads_manager_versioned":
            from .private_ads_launch import AdsManagerLaunch
            self.ads_manager = AdsManagerLaunch(self)
            from .private_ads_reads import AdsManagerReadCatalog
            from .private_ads_resources import AdsManagerResourceCatalog
            self.reads = AdsManagerResourceCatalog(AdsManagerReadCatalog(self.ads_manager.catalog))
            self.resources = AdvertisingResourceStore(self.reads)
        self._preflight_cache: dict[
            tuple[str, str, str, str, str, str, str],
            tuple[float, int, dict[str, Any]],
        ] = {}

    def plan(self, *, profile_id, payload):
        """Build an advertising plan without resolving a profile or contacting Meta."""
        if self.ads_manager is not None:
            return self.ads_manager.plan(profile_id=profile_id, payload=payload)
        from .private_launch_media import local_references
        if not isinstance(payload, dict) or not str(profile_id or "").strip():
            raise ProvisioningError("PRIVATE_LAUNCH_INVALID_INPUT", "Advertising target is missing", retryable=False)
        target = {"profile_id": str(profile_id),
                  **{key: self._id(payload.get(key), key) for key in ("business_id", "ad_account_id", "page_id")}}
        launch = _effective_launch_payload(payload.get("launch"))
        creative = launch["creative"]
        references = local_references(creative)
        delivery = launch.get("delivery", {"campaign": "PAUSED", "adset": "PAUSED", "ad": "PAUSED"})
        required = ["PUBLISH_FAMILY"] if "PUBLISH_FAMILY" in self.extensions.operations else [step.value for step in STEP_ORDER]
        steps = []
        if references:
            steps.append({"stage": "MEDIA", "label": "Подготовка медиа", "items": len(references)})
        steps.extend({"stage": step.value, "label": label} for step, label in zip(STEP_ORDER,
                     ("Кампания", "Группа объявлений", "Креатив", "Объявление")))
        for section in ("ad", "adset", "campaign"):
            if delivery.get(section) == "ACTIVE":
                name = "ACTIVATE_" + section.upper()
                required.append(name)
                steps.append({"stage": name, "label": "Активация рекламы"})
        statuses = {**self.contracts.status(), **self.extensions.status()}
        missing = [name for name in required if not statuses.get(name, {}).get("active")]
        format_name = creative.get("format", "SINGLE")
        if format_name not in self.extensions.formats:
            missing.append("CREATIVE_FORMAT_" + format_name)
        selections = self._asset_selections(launch, bool(references))
        missing.extend("READ_"+name.upper() for name in selections if name not in self.reads.rows)
        return {"configuration_valid": True, "ready": False, "checked_live": False,
                "mutation_posts": 0, "target": target, "creative_format": format_name,
                "delivery": delivery, "steps": steps, "local_media": references,
                "missing_contracts": missing, "media_resolution_required": bool(references),
                "asset_selections": selections,
                "launch": launch, "launch_fingerprint": _launch_fingerprint(payload, target["page_id"])}

    @staticmethod
    def _asset_selections(launch, local_media=False):
        selected = {}
        def add(resource, identity):
            if identity in (None, ""):
                return
            if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{2,128}",identity):
                raise ProvisioningError("PRIVATE_LAUNCH_ASSET_INVALID", "Select an exact advertising resource", retryable=False)
            if identity not in selected.setdefault(resource,[]):
                selected[resource].append(identity)
        creative = launch["creative"]
        if not local_media:
            add("images", creative.get("existing_image_hash"))
            add("videos", creative.get("existing_video_id"))
            add("creatives", creative.get("existing_creative_id"))
            for card in creative.get("carousel_cards",[]) if isinstance(creative.get("carousel_cards",[]),list) else []:
                if isinstance(card,dict):
                    add("images",card.get("image_hash")); add("videos",card.get("video_id"))
        add("instagram_media",creative.get("source_instagram_media_id"))
        add("instagram_accounts",creative.get("instagram_actor_id"))
        promoted = launch["adset"].get("promoted_object") or {}
        if isinstance(promoted,dict):
            add("pixels",promoted.get("pixel_id"))
            add("conversion_goals",promoted.get("custom_conversion_id"))
        targeting = launch["adset"].get("targeting") or {}
        if isinstance(targeting,dict):
            # Account-owned selections also occur inside logical groups and
            # exclusions. Collect their IDs without flattening or rewriting
            # targeting; every ID needs the same exact-account inventory read.
            pending, seen = [targeting], set()
            while pending:
                node = pending.pop()
                if not isinstance(node, (dict, list)) or id(node) in seen:
                    continue
                seen.add(id(node))
                if isinstance(node, list):
                    pending.extend(node)
                    continue
                for field in ("custom_audiences", "excluded_custom_audiences"):
                    items = node.get(field) or []
                    if not isinstance(items, list):
                        raise ProvisioningError("PRIVATE_LAUNCH_ASSET_INVALID", "Audience selection must be a list", retryable=False)
                    for item in items:
                        add("custom_audiences", item.get("id") if isinstance(item, dict) else item)
                pending.extend(node.values())
        return selected

    async def _verify_assets(self, web, selections, values):
        checks = {}
        for resource, wanted in selections.items():
            result = await asyncio.wait_for(self.reads.read(web,resource,{"query":"","limit":500,**values}),timeout=65)
            if (not isinstance(result, dict) or result.get("checked_live") is not True
                    or result.get("account_scope_verified") is not True
                    or str(result.get("account_id")) != str(values["ad_account_id"])
                    or type(result.get("checked_at")) is not int
                    or not time.time()-120 <= result["checked_at"] <= time.time()+5
                    or not isinstance(result.get("source"), str) or not result["source"]
                    or not isinstance(result.get("data"), list)
                    or result.get("query_required") is True
                    or result.get("truncated") is True
                    or result.get("inventory_complete") is False):
                raise ProvisioningError("PRIVATE_LAUNCH_ASSET_ACCESS_UNCONFIRMED",
                    "Selected advertising resources require a fresh exact-account read",retryable=False)
            identity_field = "hash" if resource == "images" else "id"
            found = {str(row.get(identity_field) or ""): row for row in result["data"] if isinstance(row, dict)}
            if len(found) != len(result["data"]) or not set(wanted) <= set(found):
                raise ProvisioningError("PRIVATE_LAUNCH_ASSET_ACCESS_UNCONFIRMED",
                    "Selected advertising resources are not confirmed for this exact account",retryable=False)
            checks[resource] = {"verified":True,"selected":wanted,"checked_live":True,
                "source":result.get("source"),"checked_at":result.get("checked_at"),
                "selected_details":[{"id":item,"label":str(found[item].get("name") or found[item].get("title") or item)}
                    for item in wanted]}
        return checks

    async def _media_inputs(self, launch):
        from .private_launch_media import LaunchMediaResolver, local_references, creative_with_media
        creative = launch["creative"]
        format_name = creative.get("format", "SINGLE")
        if format_name not in self.extensions.formats:
            raise ProvisioningError("PRIVATE_LAUNCH_CREATIVE_FORMAT_CONTRACT_REQUIRED",
                "The pinned creative sender does not declare this format", retryable=False)
        references = local_references(creative)
        media = await LaunchMediaResolver().resolve(references) if references else []
        planned = []
        for item in media:
            self.extensions.get("IMAGE_UPLOAD" if item.media_type == "image" else "VIDEO_UPLOAD")
            planned.append({"image_hash" if item.media_type == "image" else "video_id": "<pending-media>"})
        preview = copy.deepcopy(launch)
        preview["creative"] = creative_with_media(creative, planned)
        return media, preview

    def _validate_operations(self, values, media):
        from .private_launch_extensions import OPERATIONS
        from .private_launch_verify import validate_contract
        family = self.extensions.operations.get("PUBLISH_FAMILY")
        contracts = {} if family else {step: self.contracts.get(step) for step in STEP_ORDER}
        for contract in contracts.values():
            _require_paused_contract(contract)
            validate_contract(contract.verification, contract.step.value)
            _render(contract.variables, values)
            _render(contract.verification["variables"], {**values, "entity_id": "<pending>"})
            if not _candidate_is_active(contract.step, _contract_candidate(contract.step, contract)):
                raise ProvisioningError(f"PRIVATE_LAUNCH_{contract.step.value}_CONTRACT_STALE",
                    "The advertising contract is disabled and must be reviewed before publication", retryable=False)
        if contracts:
            _require_payload_coverage(contracts, values["payload"])
        required = {}
        if family:
            required[family.name] = family
            _render(family.row["variables"], values)
            if family.row.get("async_publication"):
                _render(family.row["async_publication"]["status_query"]["variables"],
                        {**values, "operation_id": "<pending>"})
            for read in family.row["verification"].values():
                _render(read["variables"], {**values, "entity_id": "<pending>"})
        for item in media:
            name = "IMAGE_UPLOAD" if item.media_type == "image" else "VIDEO_UPLOAD"
            contract = self.extensions.get(name)
            required[name] = contract
            _render(contract.row["variables"], {**values, "media": item.public()})
            _render(contract.row["verification"]["variables"], {**values, "media": item.public(), "entity_id": "<pending>"})
        for section, status in values.get("delivery", {}).items():
            if status != "ACTIVE":
                continue
            name = "ACTIVATE_" + section.upper()
            contract = self.extensions.get(name)
            required[name] = contract
            context = {**values, "entity_id": "<pending>", "target_status": status}
            _render(contract.row["variables"], context)
            _render(contract.row["verification"]["variables"], context)
        # Pin the complete chain to one immutable Launch, including format,
        # uploads, publication strategy and post-create delivery changes.
        serialized = {step.value: {"doc_id": c.doc_id, "friendly_name": c.friendly_name,
            "endpoint_url": c.endpoint_url, "variables": c.variables,
            "result_id_paths": c.result_id_paths, "request_envelope": c.request_envelope,
            "verification": c.verification} for step, c in contracts.items()}
        serialized.update({name: contract.digest for name, contract in required.items() if name in OPERATIONS})
        serialized["creative_format"] = values["payload"]["creative"].get("format", "SINGLE")
        serialized["actor_id"] = values.get("actor_id", "")
        for resource in self._asset_selections(values["payload"],bool(media)):
            if resource in self.reads.rows:
                serialized["read_"+resource] = self.reads.rows[resource]
        if "pages" in self.reads.rows:
            serialized["read_pages"] = self.reads.rows["pages"]
        digest = hashlib.sha256(json.dumps(serialized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return contracts, family, digest

    async def _upload_media(self, web, media, launch, values, launch_key, fingerprint):
        from .private_launch_media import creative_with_media
        references = []
        for item in media:
            name = "IMAGE_UPLOAD" if item.media_type == "image" else "VIDEO_UPLOAD"
            output = await self.operations.run(web=web, contract=self.extensions.get(name),
                values=values, launch_key=launch_key, launch_fingerprint=fingerprint, media=item)
            references.append({"image_hash" if item.media_type == "image" else "video_id": output["entity_id"]})
        result = copy.deepcopy(launch)
        result["creative"] = creative_with_media(launch["creative"], references)
        return result

    async def _activate(self, web, values, launch_key, fingerprint):
        completed = []
        # Children first: a paused campaign remains the delivery barrier until
        # its fully verified ad and ad set have the requested statuses.
        for section in ("ad", "adset", "campaign"):
            status = values.get("delivery", {}).get(section, "PAUSED")
            if status != "ACTIVE":
                continue
            name = "ACTIVATE_" + section.upper()
            await self.operations.run(web=web, contract=self.extensions.get(name),
                values={**values, "entity_id": values[section + "_id"], "target_status": status},
                launch_key=launch_key, launch_fingerprint=fingerprint)
            completed.append({"step": name, "status": "SUCCESS", "entity_id": values[section + "_id"]})
        return completed

    async def run_delivery(self, *, item_id, profile_id, context, session, payload, task_idempotency_key=None):
        """Change delivery of this worker's existing objects; never create new ones."""
        if self.ads_manager is not None:
            return await self.ads_manager.run_delivery(profile_id=profile_id, context=context, session=session,
                payload=payload, task_idempotency_key=task_idempotency_key)
        del item_id
        from .private_launch_extensions import OperationKey
        from .private_launch_verify import proof, _complete
        if not isinstance(payload, dict) or not isinstance(payload.get("status"), str) or payload["status"] not in {"ACTIVE", "PAUSED"}:
            raise ProvisioningError("PRIVATE_LAUNCH_STATUS_INVALID", "Choose ACTIVE or PAUSED delivery", retryable=False)
        payload = copy.deepcopy(payload)
        source_key = str(payload.get("source_launch_key") or "").strip()
        key = str(payload.get("delivery_key") or task_idempotency_key or "").strip()
        if not source_key or not key or max(len(source_key),len(key)) > 200 or source_key == key:
            raise ProvisioningError("PRIVATE_LAUNCH_INVALID_INPUT", "Delivery requires separate source and request keys", retryable=False)
        values = {"profile_id": str(profile_id), **{name:self._id(payload.get(name),name)
                  for name in ("business_id","ad_account_id","page_id")}, "target_status": payload["status"],
                  "actor_id":str((getattr(context,"cookies",None) or {}).get("c_user") or ""),
                  "client_mutation_id":"<pending>"}
        target = tuple(values[name] for name in ("profile_id","business_id","ad_account_id"))
        await self.state.init()
        source = {row["step"]:row for row in await self.state.all_steps(source_key)}
        family = source.get("PUBLISH_FAMILY")
        if family:
            source_rows = [family]
            ids = family.get("result",{}).get("evidence",{}).get("outputs",{})
            reads = self.extensions.get("PUBLISH_FAMILY").row["verification"]
        else:
            source_rows = [source[step.value] for step in STEP_ORDER if step.value in source]
            ids = {step.value.lower()+"_id":source[step.value]["entity_id"] for step in STEP_ORDER
                   if step.value in source and source[step.value].get("entity_id")}
            reads = {step.value:self.contracts.get(step).verification for step in STEP_ORDER
                     if ids.get(step.value.lower()+"_id")}
        if (not source_rows or any(tuple(str(row[name]) for name in ("profile_id","business_id","ad_account_id")) != target
                                  for row in source_rows)):
            raise ProvisioningError("PRIVATE_LAUNCH_KEY_TARGET_MISMATCH", "Source advertising job belongs to another target", retryable=False)
        for name, identity in ids.items():
            if name not in {step.value.lower()+"_id" for step in STEP_ORDER} or not re.fullmatch(r"\d{5,40}",str(identity)):
                raise ProvisioningError("PRIVATE_LAUNCH_RECONCILE_REQUIRED", "Source job has no exact advertising references", retryable=False)
        if not ids or (payload["status"] == "ACTIVE" and len(ids) != 4):
            raise ProvisioningError("PRIVATE_LAUNCH_RECONCILE_REQUIRED", "Activation requires the complete published advertising chain", retryable=False)
        values.update(ids)
        order = ("ad","adset","campaign") if payload["status"] == "ACTIVE" else ("campaign","adset","ad")
        contracts = {section:self.extensions.get("ACTIVATE_"+section.upper()) for section in order if ids.get(section+"_id")}
        for section, contract in contracts.items():
            _render(contract.row["variables"], {**values,"entity_id":ids[section+"_id"]})
            _render(contract.row["verification"]["variables"], {**values,"entity_id":ids[section+"_id"]})
        fingerprint = hashlib.sha256(json.dumps({"source":source_key,"target":target,"page_id":values["page_id"],
            "ids":ids,"status":payload["status"]},sort_keys=True).encode()).hexdigest()
        digest = hashlib.sha256(json.dumps({"actor_id":values["actor_id"],
            "contracts":{section:contract.digest for section,contract in contracts.items()},
            "source_reads": reads},sort_keys=True).encode()).hexdigest()
        await self.state.bind_plan(key,values,fingerprint,digest)
        transport = MetaTransportRouter(session,private_only=True)
        web = await transport.facebook_web()
        web.private_only = True
        current = {}
        # Fresh typed READs establish exact scope and parents even if a source
        # task ended with an interrupted verification. IDs supplied by a client
        # are never accepted as a delivery target.
        for step in STEP_ORDER:
            identity = ids.get(step.value.lower()+"_id")
            if not identity:
                continue
            read = reads[step.value]
            response = await web.graphql(read["doc_id"],_render(read["variables"],{**values,"entity_id":identity}),
                friendly_name=read["friendly_name"],endpoint_url=PrivateLaunchContractStore._endpoint(read.get("endpoint_url")),
                business_context_id=values["business_id"])
            status = _lookup(response,read["status_path"]) if read.get("status_path") else "PAUSED"
            if not _complete(response) or proof(response,read,{**values,"entity_id":identity,"expected_status":status}).get("verified") is not True:
                raise ProvisioningError("PRIVATE_LAUNCH_DELIVERY_SCOPE_UNCONFIRMED",
                    "Independent read did not confirm the source job's exact advertising objects",retryable=False)
            current[step.value.lower()] = status
        if payload["status"] == "ACTIVE":
            preflight = await self._review_preflight(context=context,transport=transport,payment_selector=_payment_selector(payload),**{
                name:values[name] for name in ("profile_id","business_id","ad_account_id","page_id")})
            if preflight.get("payment_selection_required") is True:
                raise ProvisioningError("PRIVATE_LAUNCH_PAYMENT_SELECTION_REQUIRED", "Choose the linked payment method before activation", retryable=False)
        completed = []
        for section, contract in contracts.items():
            if current.get(section) == payload["status"]:
                completed.append({"step":contract.name,"status":"SUCCESS","skipped":True,"entity_id":ids[section+"_id"]})
                continue
            await self.operations.run(web=web,contract=contract,values={**values,"entity_id":ids[section+"_id"]},
                launch_key=key,launch_fingerprint=fingerprint)
            completed.append({"step":contract.name,"status":"SUCCESS","entity_id":ids[section+"_id"]})
        return {"status":"SUCCESS","source_launch_key":source_key,"delivery_key":key,"delivery_status":payload["status"],
                "profile_id":str(profile_id),"business_id":values["business_id"],"ad_account_id":values["ad_account_id"],
                "entities":ids,"steps":completed,"browser_started":False}

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
        transport: MetaTransportRouter | None = None,
        payment_selector: dict[str, str] | None = None,
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

        if transport is None:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_HTTP_SESSION_REQUIRED",
                "Launch review requires the selected profile HTTP session",
                retryable=False,
            )
        web = await transport.facebook_web()
        web.private_only = True
        try:
            await web.bootstrap()
            # Both reads share the profile connection pool, cookies and proxy.
            # Queries may overlap; create operations remain ordered separately.
            page_read = (self.reads.pages(web, {"profile_id": profile_id, "business_id": business_id,
                         "ad_account_id": ad_account_id, "page_id": page_id, "query": "", "limit": 500,
                         "actor_id":str((getattr(context,"cookies",None) or {}).get("c_user") or "")})
                         if "pages" in self.reads.rows else
                         inspect_pages(web, account=ad_account_id, business_id=business_id))
            pages, funding = await asyncio.wait_for(asyncio.gather(
                page_read,
                inspect_methods(web, account=ad_account_id, business_id=business_id),
            ), timeout=65)
        except AuthenticationError as exc:
            raise private_auth_error(exc) from exc
        except (RemoteRequestError, asyncio.TimeoutError) as exc:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_HTTP_PREFLIGHT_FAILED",
                "Meta HTTP readiness reads did not complete; no advertising CREATE was sent",
                retryable=True,
            ) from exc
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

        safe_payment_methods = _safe_payment_methods(
            funding.get("payment_methods")
        )
        payment_selection_required = (
            payment_selector is None
            and len(safe_payment_methods) > 1
        )
        if payment_selection_required:
            selected_payment_method: dict[str, str] = {}
            payment_selection_source = "selection_required"
        else:
            try:
                selected_payment_method, payment_selection_source = _select_payment_method(
                    safe_payment_methods, payment_selector)
            except ProvisioningError as exc:
                if exc.code != "PRIVATE_LAUNCH_PAYMENT_SELECTION_UNAVAILABLE":
                    raise
                # A stale selected mask must prompt a new explicit choice,
                # never silently switch cards or leave the chooser stuck.
                payment_selection_required = True
                selected_payment_method = {}
                payment_selection_source = "selection_unavailable"
            selected = [method for method in funding["payment_methods"] if selected_payment_method
                if method.get("last4") == selected_payment_method["last4"]
                and str(method.get("type") or "").casefold() == selected_payment_method["type"].casefold()]
            if any(method.get("is_expired") is True for method in selected):
                raise ProvisioningError("PRIVATE_LAUNCH_PAYMENT_EXPIRED",
                    "The selected linked card is expired", retryable=False)
            if any(method.get("needs_verification") is True or method.get("verification_tasks") for method in selected):
                raise ProvisioningError("PRIVATE_LAUNCH_PAYMENT_CONFIRMATION_REQUIRED",
                    "Meta requires confirmation of the selected linked card before advertising Launch", retryable=False)

        return {
            "profile_id": profile_id,
            "business_id": business_id,
            "ad_account_id": ad_account_id,
            "page_id": page_id,
            "account_status": 1,
            "inventory_updated_at": inventory_at,
            "transport": "private_http_h2",
            "browser_started": False,
            "page_access_checked_live": True,
            "payment_checked_live": True,
            "payment_method_count": len(funding.get("payment_methods") or []),
            "page_read_source": pages.get("source", ""),
            "payment_read_source": funding.get("source", ""),
            "selected_payment_method": selected_payment_method,
            "payment_selection_source": payment_selection_source,
            "payment_selection_required": payment_selection_required,
            "payment_methods": (
                safe_payment_methods
                if payment_selection_required
                else []
            ),
        }

    async def _review_preflight(
        self,
        *,
        context: Any,
        profile_id: str,
        business_id: str,
        ad_account_id: str,
        page_id: str,
        transport: MetaTransportRouter | None = None,
        payment_selector: dict[str, str] | None = None,
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
        selector_type = str((payment_selector or {}).get("type") or "").casefold()
        selector_last4 = str((payment_selector or {}).get("last4") or "")
        key = (
            profile_id,
            business_id,
            ad_account_id,
            page_id,
            selector_type,
            selector_last4,
            hashlib.sha256(json.dumps({
                "cookies": getattr(context, "cookies", {}),
                "proxy": getattr(context, "proxy", None),
                "user_agent": getattr(context, "user_agent", ""),
            }, sort_keys=True).encode()).hexdigest(),
        )
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
            transport=transport,
            payment_selector=payment_selector,
        )
        self._preflight_cache[key] = (now, inventory_at, dict(proof))
        return proof

    async def review(
        self,
        *,
        profile_id: str,
        context: Any,
        payload: dict[str, Any],
        transport: MetaTransportRouter | None = None,
    ) -> dict[str, Any]:
        if self.ads_manager is not None:
            return await self.ads_manager.review(profile_id=profile_id, context=context,
                                                  payload=payload, transport=transport)
        if transport is None:
            from .session import ProfileSession
            async with ProfileSession(context) as session:
                return await self.review(
                    profile_id=profile_id, context=context, payload=payload,
                    transport=MetaTransportRouter(session, private_only=True),
                )
        transport = MetaTransportRouter(transport.session, context=context, private_only=True)
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
        payment_selector = _payment_selector(payload)
        if launch_payload["creative"].get("page_id") not in (None,"",page_id):
            raise ProvisioningError("PRIVATE_LAUNCH_PAGE_MISMATCH", "Creative Page differs from the selected account binding",retryable=False)

        # Resolve local media and validate every required operation before
        # submitting an upload, family publication or individual CREATE.
        media, preview = await self._media_inputs(launch_payload)
        validation_values = {
            "profile_id": profile_id, "business_id": business_id,
            "ad_account_id": ad_account_id, "page_id": page_id,
            "payload": preview,
            "delivery": launch_payload.get("delivery", {}),
            "campaign_id": "<pending>", "adset_id": "<pending>", "creative_id": "<pending>", "ad_id": "<pending>",
            "actor_id": str((getattr(context,"cookies",None) or {}).get("c_user") or ""),
            "client_mutation_id": "<pending>",
        }
        validation_values["payload"].pop("delivery", None)
        contracts, family, digest = self._validate_operations(validation_values, media)
        selections = self._asset_selections(launch_payload,bool(media))
        for resource in selections:
            row = self.reads.rows.get(resource)
            if row is None:
                raise ProvisioningError("PRIVATE_LAUNCH_RESOURCE_CONTRACT_REQUIRED",
                    "A pinned resource query is required before publishing these selected assets",retryable=False)
            _render(row["variables"],{"query":"","limit":500,"cursor":"",**validation_values})
        preflight = await self._review_preflight(
            context=context,
            profile_id=profile_id,
            business_id=business_id,
            ad_account_id=ad_account_id,
            page_id=page_id,
            transport=transport,
            payment_selector=payment_selector,
        )
        if selections:
            preflight["asset_checks"] = await self._verify_assets(await transport.facebook_web(),selections,validation_values)
        return {
            "ready": preflight.get("payment_selection_required") is not True,
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
            "_family": family,
            "_media": media,
            "_contract_digest": digest,
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
        if self.ads_manager is not None:
            return await self.ads_manager.run(item_id=item_id, profile_id=profile_id,
                context=context, session=session, payload=payload,
                task_idempotency_key=task_idempotency_key)
        del item_id
        if not isinstance(payload, dict):
            raise ProvisioningError(
                "PRIVATE_LAUNCH_INVALID_INPUT",
                "Private Launch payload must be an object",
                retryable=False,
            )

        payload = copy.deepcopy(payload)

        # Durable submit uncertainty must outrank fresh Page/payment/contract
        # checks. Once a step may have crossed CREATE, a rerun must surface the
        # saved no-replay state before opening browsers or evaluating a newer
        # configuration.
        business_id = self._id(payload.get("business_id"), "business_id")
        ad_account_id = self._id(payload.get("ad_account_id"), "ad_account_id")
        page_id = self._id(payload.get("page_id"), "page_id")
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
        fingerprint = _launch_fingerprint(payload, page_id)
        # Unknown media/family submissions outrank fresh live preflight just
        # like the existing four CREATE stages. They can never be replayed.
        for saved in await self.state.all_steps(launch_key):
            if saved["step"] in {step.value for step in STEP_ORDER}:
                continue
            evidence = saved.get("result", {}).get("evidence", {})
            if (str(saved["profile_id"]), str(saved["business_id"]), str(saved["ad_account_id"])) != (
                    str(profile_id), business_id, ad_account_id):
                raise ProvisioningError("PRIVATE_LAUNCH_KEY_TARGET_MISMATCH",
                    "Saved advertising operation belongs to another target", retryable=False)
            if evidence.get("launch_fingerprint") != fingerprint:
                raise ProvisioningError("PRIVATE_LAUNCH_KEY_PAYLOAD_MISMATCH",
                    "Advertising operation belongs to another Launch configuration", retryable=False)
            from .private_launch_publication import RETRY_CODES, pending_operation
            family_contract = self.extensions.operations.get("PUBLISH_FAMILY")
            resumable_publication = (saved["step"] == "PUBLISH_FAMILY" and family_contract is not None
                and family_contract.row.get("async_publication") and pending_operation(evidence)
                and evidence.get("contract_digest") == family_contract.digest
                and evidence.get("publication", {}).get("code") in RETRY_CODES
                and saved["status"] == "RECONCILE_REQUIRED")
            if saved["status"] in {"SUBMITTING", "RECONCILE_REQUIRED", "BLOCKED"} and not evidence.get("outputs") and not resumable_publication:
                raise ProvisioningError("PRIVATE_LAUNCH_RECONCILE_REQUIRED",
                    "A saved media or publication request has an unknown result; resubmission is blocked", retryable=False)
        meta_transport = MetaTransportRouter(session, private_only=True)
        prior_by_step: dict[PrivateLaunchStep, dict[str, Any] | None] = {}
        current_target = (profile_id, business_id, ad_account_id)
        for step in STEP_ORDER:
            prior = await self.state.step(launch_key, step)
            prior_by_step[step] = prior
            if not prior:
                continue
            prior_target = (
                str(prior.get("profile_id") or "").strip(),
                str(prior.get("business_id") or "").strip(),
                str(prior.get("ad_account_id") or "").removeprefix("act_").strip(),
            )
            if prior_target != current_target:
                raise ProvisioningError(
                    "PRIVATE_LAUNCH_KEY_TARGET_MISMATCH",
                    (
                        "Private Launch key is already bound to a different "
                        "profile/BM/RK target"
                    ),
                    retryable=False,
                )
            previous_status = str(prior.get("status") or "").upper()
            if previous_status in {"SUBMITTING", "RECONCILE_REQUIRED", "BLOCKED"} and not _resumable_read(prior, step):
                code = (
                    "PRIVATE_LAUNCH_BLOCKED_REPLAY"
                    if previous_status == "BLOCKED"
                    else "PRIVATE_LAUNCH_RECONCILE_REQUIRED"
                )
                raise ProvisioningError(
                    code,
                    f"{step.value} has a durable {previous_status} result; automatic resubmit is blocked",
                    retryable=False,
                )
        fingerprint = _launch_fingerprint(payload, page_id)
        for prior in prior_by_step.values():
            if prior and prior.get("status") == "RECONCILE_REQUIRED" and (
                    prior.get("result", {}).get("evidence", {}).get("launch_fingerprint") != fingerprint):
                raise ProvisioningError("PRIVATE_LAUNCH_KEY_PAYLOAD_MISMATCH",
                    "Created object verification is bound to the saved Launch configuration", retryable=False)
        for prior in prior_by_step.values():
            if prior and str(prior.get("status") or "").upper() == "SUCCESS" and (
                    prior.get("result", {}).get("evidence", {}).get("launch_fingerprint") != fingerprint
                    or prior.get("result", {}).get("evidence", {}).get("verification", {}).get("verified") is not True):
                raise ProvisioningError("PRIVATE_LAUNCH_KEY_PAYLOAD_MISMATCH",
                    "Saved Launch is bound to another configuration or lacks an immutable configuration proof; resubmission is blocked",
                    retryable=False)

        review = await self.review(
            profile_id=profile_id,
            context=context,
            payload=payload,
            transport=meta_transport,
        )
        if review.get("ready") is not True:
            raise ProvisioningError(
                "PRIVATE_LAUNCH_PAYMENT_SELECTION_REQUIRED",
                "Select one live masked payment method for the exact RK before Launch",
                retryable=False,
            )
        business_id = str(review["business_id"])
        ad_account_id = str(review["ad_account_id"])
        page_id = str(review["page_id"])
        launch_payload = review["launch"]
        contracts = review.pop("_contracts")
        family = review.pop("_family")
        media = review.pop("_media")
        contract_digest = review.pop("_contract_digest")
        preflight = review["preflight"]

        values: dict[str, Any] = {
            "profile_id": profile_id,
            "business_id": business_id,
            "ad_account_id": ad_account_id,
            "page_id": page_id,
            "payload": launch_payload,
            "delivery": launch_payload.get("delivery", {}),
            "actor_id": str((getattr(context,"cookies",None) or {}).get("c_user") or ""),
        }
        completed: list[dict[str, Any]] = []

        from .private_launch_extensions import OperationKey
        prior_family = await self.state.step(launch_key, OperationKey("PUBLISH_FAMILY"))
        if (family and any(prior_by_step.values())) or (not family and prior_family):
            raise ProvisioningError("PRIVATE_LAUNCH_PLAN_CHANGED",
                "An existing Launch cannot switch between family and individual publication", retryable=False)
        await self.state.bind_plan(launch_key, values, fingerprint, contract_digest)
        web = await meta_transport.facebook_web()
        launch_payload = await self._upload_media(web, media, launch_payload, values, launch_key, fingerprint)
        values["payload"] = {key: value for key, value in launch_payload.items() if key != "delivery"}
        if family:
            outputs = await self.operations.run(web=web, contract=family, values=values,
                                               launch_key=launch_key, launch_fingerprint=fingerprint)
            values.update(outputs)
            completed.extend({"step": step.value, "status": "SUCCESS", "entity_id": outputs[step.value.lower() + "_id"],
                              "publication": "family"} for step in STEP_ORDER)

        for step in ([] if family else STEP_ORDER):
            values["client_mutation_id"] = hashlib.sha256((launch_key + ":" + step.value).encode()).hexdigest()[:32]
            prior = prior_by_step.get(step)
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
            contract = contracts[step]
            if prior and _resumable_read(prior, step):
                web = await meta_transport.facebook_web()
                result = await self._confirm_created_entity(web, contract, values, prior)
                values[f"{step.value.lower()}_id"] = result.entity_id
                completed.append({"step": step.value, "status": "SUCCESS", "skipped": True,
                    "read_resumed": True, "entity_id": result.entity_id})
                continue
            candidate = _contract_candidate(
                step,
                contract,
                source="private_launch_contract",
            )
            if not _candidate_is_active(step, candidate):
                raise ProvisioningError(
                    f"PRIVATE_LAUNCH_{step.value}_CONTRACT_STALE",
                    (
                        f"{step.value} private mutation contract is disabled "
                        "by the shared doc_id lifecycle"
                    ),
                    retryable=False,
                )
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

            web = await meta_transport.facebook_web()
            try:
                await web.bootstrap()
            except AuthenticationError as exc:
                _record_contract_result(
                    step,
                    candidate,
                    success=False,
                    profile_id=profile_id,
                    reason="AUTH_PRECHECK",
                    failure_kind="account",
                )
                raise ProvisioningError(
                    f"PRIVATE_LAUNCH_{step.value}_AUTH_PRECHECK",
                    "Facebook session is not ready before the private mutation",
                    retryable=False,
                ) from exc
            except RemoteRequestError as exc:
                _record_contract_result(
                    step,
                    candidate,
                    success=False,
                    profile_id=profile_id,
                    reason="PRE_SUBMIT_TRANSPORT",
                    failure_kind=classify_cache_failure(
                        exception=exc,
                        message=str(exc),
                        http_status=int(
                            getattr(exc, "http_status", 0) or 0
                        ) or None,
                    ),
                )
                raise ProvisioningError(
                    f"PRIVATE_LAUNCH_{step.value}_PRE_SUBMIT_TRANSPORT",
                    "Private Launch transport failed before the submit checkpoint",
                    retryable=True,
                ) from exc

            intent = ActionResult.reconcile(
                step.value,
                code=f"PRIVATE_LAUNCH_{step.value}_SUBMITTING",
                message=f"{step.value} mutation crossed the durable submit checkpoint",
                submitted=None,
                evidence={
                    "launch_fingerprint": fingerprint,
                    "friendly_name": contract.friendly_name,
                    "variable_keys": sorted(str(key) for key in variables.keys())[:40],
                },
            )
            await self.state.save(
                launch_key=launch_key,
                profile_id=profile_id,
                business_id=business_id,
                ad_account_id=ad_account_id,
                step=step,
                status="SUBMITTING",
                result=intent.as_dict(),
                error_code=intent.code,
                submitted=None,
            )

            try:
                response = await web.graphql(
                    contract.doc_id,
                    variables,
                    friendly_name=contract.friendly_name,
                    endpoint_url=contract.endpoint_url,
                    request_envelope=contract.request_envelope,
                    business_context_id=business_id,
                )
            except AuthenticationError:
                _record_contract_result(
                    step,
                    candidate,
                    success=False,
                    profile_id=profile_id,
                    reason="AUTH_RESULT_UNKNOWN",
                    failure_kind="account",
                )
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
                failure_kind = classify_cache_failure(
                    exception=exc,
                    payload=(
                        getattr(exc, "payload", None)
                        if isinstance(getattr(exc, "payload", None), dict)
                        else None
                    ),
                    message=str(exc),
                    http_status=http_status or None,
                )
                _record_contract_result(
                    step,
                    candidate,
                    success=False,
                    profile_id=profile_id,
                    reason=(
                        f"HTTP_{http_status}"
                        if http_status
                        else exc.__class__.__name__
                    ),
                    failure_kind=failure_kind,
                )
                if 400 <= http_status < 500 and http_status != 429:
                    result = ActionResult.blocked(
                        step.value,
                        code=f"PRIVATE_LAUNCH_{step.value}_REJECTED",
                        message=f"Meta rejected {step.value} private mutation",
                        submitted=True,
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
                tuple(
                    path
                    for path in contract.result_id_paths
                    if path != AUTO_RESULT_ID_PATH
                ),
            )
            if (
                not entity_id
                and AUTO_RESULT_ID_PATH in contract.result_id_paths
            ):
                excluded_ids = {
                    value
                    for value in (
                        _normalize_entity_id(business_id),
                        _normalize_entity_id(ad_account_id),
                        _normalize_entity_id(page_id),
                        _normalize_entity_id(values.get("campaign_id")),
                        _normalize_entity_id(values.get("adset_id")),
                        _normalize_entity_id(values.get("creative_id")),
                        _normalize_entity_id(values.get("ad_id")),
                    )
                    if value
                }
                entity_id, response_path = _auto_entity_id(
                    response,
                    step,
                    excluded_ids=excluded_ids,
                )
            if not entity_id:
                _record_contract_result(
                    step,
                    candidate,
                    success=False,
                    profile_id=profile_id,
                    reason="RESULT_PATH_MISSING",
                    failure_kind=classify_cache_failure(
                        payload=(
                            response
                            if isinstance(response, dict)
                            else None
                        ),
                        message="configured result id path missing",
                    ),
                )
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

            pending_read = ActionResult.reconcile(step.value,
                code=f"PRIVATE_LAUNCH_{step.value}_READ_PENDING",
                message="CREATE returned an ID; independent Meta READ is pending", submitted=True,
                evidence={"launch_fingerprint": fingerprint, "response_path": response_path})
            pending_read.entity_id = entity_id
            await self.state.save(launch_key=launch_key, profile_id=profile_id, business_id=business_id,
                ad_account_id=ad_account_id, step=step, status=pending_read.status.value,
                entity_id=entity_id, result=pending_read.as_dict(), error_code=pending_read.code, submitted=True)
            prior_read = await self.state.step(launch_key, step)
            result = await self._confirm_created_entity(web, contract, values, prior_read,
                response_summary=_response_summary(response))
            if AUTO_RESULT_ID_PATH in contract.result_id_paths and response_path:
                try:
                    self.contracts.promote_result_path(step, response_path)
                except Exception as exc:
                    log.warning("private Launch verified result path promotion failed step=%s type=%s", step.value, type(exc).__name__)

            _record_contract_result(
                step,
                candidate,
                success=True,
                profile_id=profile_id,
                response_path=response_path,
            )
            values[f"{step.value.lower()}_id"] = entity_id
            completed.append({
                "step": step.value,
                "status": "SUCCESS",
                "skipped": False,
                "entity_id": entity_id,
            })

        completed.extend(await self._activate(web, values, launch_key, fingerprint))
        return {
            "profile_id": profile_id,
            "business_id": business_id,
            "ad_account_id": ad_account_id,
            "page_id": page_id,
            "launch_key": launch_key,
            "status": "SUCCESS",
            "delivery": launch_payload.get("delivery", {"campaign": "PAUSED", "adset": "PAUSED", "ad": "PAUSED"}),
            "preflight": preflight,
            "steps": completed,
            "entities": {
                "campaign_id": values.get("campaign_id", ""),
                "adset_id": values.get("adset_id", ""),
                "creative_id": values.get("creative_id", ""),
                "ad_id": values.get("ad_id", ""),
            },
        }

    async def _confirm_created_entity(self, web, contract, values, prior, *, response_summary=None):
        entity_id = str(prior["entity_id"])
        evidence = {**prior["result"].get("evidence", {}), **(response_summary or {}),
            "friendly_name": contract.friendly_name}
        try:
            verification = await asyncio.wait_for(self._verify_entity(web, contract,
                {**values, "entity_id": entity_id}), timeout=40)
        except AuthenticationError as exc:
            verification = {"verified": False, "code": private_auth_error(exc).code}
        except Exception:
            verification = {"verified": False, "code": "READ_UNAVAILABLE"}
        evidence["verification"] = verification
        if verification.get("verified") is True:
            result = ActionResult.success(contract.step.value, entity_id=entity_id,
                code=f"PRIVATE_LAUNCH_{contract.step.value}_CONFIRMED", evidence=evidence)
        else:
            result = ActionResult.reconcile(contract.step.value,
                code=f"PRIVATE_LAUNCH_{contract.step.value}_READ_UNCONFIRMED",
                message="Independent Meta READ did not confirm the exact paused object; the created ID is retained",
                submitted=True, evidence=evidence)
            result.entity_id = entity_id
        if not await self.state.finish_read(prior, result):
            latest = await self.state.step(prior["launch_key"], contract.step)
            saved = (latest or {}).get("result", {})
            if (latest and latest.get("status") == "SUCCESS" and latest.get("entity_id") == entity_id
                    and saved.get("evidence", {}).get("launch_fingerprint") == evidence.get("launch_fingerprint")
                    and saved.get("evidence", {}).get("verification", {}).get("verified") is True):
                return ActionResult.success(contract.step.value, entity_id=entity_id,
                    code=saved.get("code", ""), evidence=saved.get("evidence", {}))
            raise ProvisioningError("PRIVATE_LAUNCH_RECONCILE_REQUIRED",
                "Created object state changed while its independent READ was in progress", retryable=False)
        if result.verified is not True:
            retry_read = _resumable_read({**prior, "status": result.status.value,
                "error_code": result.code, "result": result.as_dict()}, contract.step)
            raise ProvisioningError(result.code, result.message, retryable=retry_read)
        return result

    @staticmethod
    async def _verify_entity(web, contract, values):
        from .private_launch_verify import verify
        return await verify(web, contract.verification, contract.step.value, values)
