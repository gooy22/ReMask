"""Durable, session-free Add-RK contracts observed in Meta's own wizard.

A doc_id alone is never a contract. Only an intercepted CREATE with its exact
variables can enter this store. Profile/business values are typed arguments;
cookies, bootstrap fields and opaque identity values cannot enter a template.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_LOCK = threading.Lock()
_AUTH = {"fbdtsg", "lsd", "jazoest", "cookie", "cookies", "authorization",
         "accesstoken", "xs", "cuser", "user", "requestenvelope"}
_FIELDS = {"businessid": "business", "endadvertiserid": "business",
           "accountname": "name", "adaccountname": "name", "name": "name",
           "currency": "currency", "currencycode": "currency",
           "timezoneid": "timezone", "timezone": "timezone", "actorid": "actor", "userid": "actor",
           "clientmutationid": "mutation"}


def _compact(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


class AdAccountContractStore:
    def __init__(self, path: str | Path | None = None, *, max_age: int = 86400):
        root = os.getenv("REMASK_DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "/var/lib/remask"
        self.path = Path(path or os.getenv("REMASK_AD_ACCOUNT_CONTRACT_STORE") or Path(root) / "facebook-web" / "ad-account-contract.json")
        self.max_age = max_age

    def _read(self) -> dict[str, Any]:
        try:
            row = json.loads(self.path.read_text(encoding="utf-8"))
            return row if isinstance(row, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, target = tempfile.mkstemp(dir=self.path.parent, prefix=".rk-contract-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(row, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(target, self.path)
        finally:
            if os.path.exists(target):
                os.unlink(target)

    def diagnostic(self) -> dict[str, Any]:
        """Expose cache availability, never its schema or captured values."""
        with _LOCK:
            row = self._read()
        status = row.get("status")
        try:
            age = int(time.time() - int(row.get("observed_at") or 0))
        except (ValueError, TypeError):
            age = -1
        reason = "missing" if not row else (
            "invalid" if row.get("version") != 1 or status not in {"captured", "verified", "stale"}
            else "stale" if status == "stale"
            else "expired" if not 0 <= age <= self.max_age
            else "available")
        doc = str(row.get("doc_id") or "")
        return {"state": reason, "age_seconds": age if row else None,
                "doc_id": doc if doc.isdigit() and 5 <= len(doc) <= 40 else ""}

    def register_capture(self, capture: dict[str, Any]) -> bool:
        """Return False for an unsupported shape, retaining direct fresh replay."""
        from .facebook_ad_account_create import _validate_rewritten_capture_variables, _replace_capture_values
        if capture.get("source") not in {"live_business_settings_capture", "live_private_web_modules"}:
            return False
        if capture.get("source") == "live_private_web_modules":
            proof = capture.get("module_sha256")
            if (capture.get("schema_source") != "web_module" or not isinstance(proof, list)
                    or not proof or not all(isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item) for item in proof)):
                return False
        endpoint = str(capture.get("endpoint_url") or "https://business.facebook.com/api/graphql/")
        try:
            parts = urlsplit(endpoint)
            endpoint_port = parts.port
        except ValueError:
            return False
        doc_id = str(capture.get("doc_id") or "")
        friendly = str(capture.get("friendly_name") or "")
        if (not doc_id.isdigit() or not 5 <= len(doc_id) <= 40
                or parts.scheme != "https" or parts.hostname != "business.facebook.com"
                or parts.path not in {"/api/graphql", "/api/graphql/"}
                or parts.query or parts.fragment or parts.username or parts.password or endpoint_port
                or not re.fullmatch(r"[A-Za-z0-9_]+", friendly)
                or not all(word in friendly.lower() for word in ("create", "adaccount", "mutation"))):
            return False
        variables = capture.get("variables")
        if not isinstance(variables, dict) or len(json.dumps(variables)) > 100000:
            return False
        try:
            variables = _replace_capture_values(
                variables, canary_name=str(capture["canary_name"]),
                business_id=str(capture["business_id"]), account_name=str(capture["canary_name"]),
                currency=str(capture["currency"]), timezone_id=int(capture["timezone_id"]),
                include_attribution_defaults=capture.get("schema_source") != "web_module",
            )
            validation = _validate_rewritten_capture_variables(
                variables, business_id=str(capture["business_id"]),
                account_name=str(capture["canary_name"]),
                currency=str(capture["currency"]), timezone_id=int(capture["timezone_id"]),
            )
            if not validation.get("ok"):
                return False
            arguments: set[str] = set()

            def template(value: Any) -> Any:
                if isinstance(value, dict):
                    output = {}
                    for key, child in value.items():
                        compact = _compact(key)
                        if compact in _AUTH or str(key).startswith("__") or str(key).startswith("$rk_"):
                            raise ValueError("session material")
                        argument = _FIELDS.get(compact)
                        if argument and isinstance(child, (str, int)) and not isinstance(child, bool):
                            # Unrelated nested names must not be replaced.
                            if argument == "name" and str(child) != str(capture["canary_name"]):
                                raise ValueError("ambiguous nested name")
                            arguments.add(argument)
                            output[key] = {"$rk_arg": argument, "$rk_type": "int" if isinstance(child, int) else "str"}
                        else:
                            output[key] = template(child)
                    return output
                if isinstance(value, list):
                    return [template(child) for child in value]
                # Unknown IDs/session URLs cannot silently follow another profile.
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    if re.search(r"(?<!\d)\d{5,}(?!\d)|https?://", str(value)):
                        raise ValueError("opaque identity")
                return value

            shaped = template(variables)
            if not {"business", "name", "currency", "timezone"}.issubset(arguments):
                return False
            row = {"version": 1, "status": "captured", "observed_at": int(time.time()),
                   "doc_id": doc_id, "friendly_name": friendly, "endpoint_url": endpoint,
                   "variables_template": shaped, "schema_source": capture.get("schema_source", "live_ui")}
            with _LOCK:
                self._write(row)
            return True
        except (KeyError, TypeError, ValueError):
            return False

    def get(self, *, business_id: str, account_name: str, currency: str,
            timezone_id: int, actor_id: str) -> dict[str, Any] | None:
        with _LOCK:
            row = self._read()
        try:
            age = time.time() - int(row.get("observed_at") or 0)
        except (TypeError, ValueError):
            return None
        if not str(row.get("doc_id") or "").isdigit() or not isinstance(row.get("variables_template"), dict):
            return None
        if row.get("version") != 1 or row.get("status") not in {"captured", "verified"} or not 0 <= age <= self.max_age:
            return None
        if not str(actor_id).isdigit() or not str(business_id).isdigit():
            return None
        values = {"business": business_id, "name": account_name, "currency": currency,
                  "timezone": timezone_id, "actor": actor_id, "mutation": uuid.uuid4().hex[:16]}

        def render(value: Any) -> Any:
            if isinstance(value, dict):
                if set(value) == {"$rk_arg", "$rk_type"}:
                    if value["$rk_type"] not in {"int", "str"}:
                        raise ValueError("unknown scalar type")
                    item = values[value["$rk_arg"]]
                    return int(item) if value["$rk_type"] == "int" else str(item)
                return {key: render(child) for key, child in value.items()}
            if isinstance(value, list):
                return [render(child) for child in value]
            return value
        try:
            variables = render(copy.deepcopy(row["variables_template"]))
        except (KeyError, TypeError, ValueError):
            return None
        if not all(isinstance(row.get(key), str) and row.get(key) for key in ("doc_id", "friendly_name", "endpoint_url")):
            return None
        return {"doc_id": row["doc_id"], "friendly_name": row["friendly_name"],
                "endpoint_url": row["endpoint_url"], "variables": variables,
                "canary_name": account_name, "source": "reusable_business_settings_contract",
                "contract_status": row["status"], "schema_source": row.get("schema_source", "live_ui"), "request_envelope": {}}

    def invalidate(self, doc_id: str) -> None:
        with _LOCK:
            row = self._read()
            if str(row.get("doc_id")) == str(doc_id):
                row["status"] = "stale"
                self._write(row)

    def confirm(self, doc_id: str) -> None:
        with _LOCK:
            row = self._read()
            if str(row.get("doc_id")) == str(doc_id) and row.get("status") != "stale":
                row["status"] = "verified"
                row["verified_at"] = int(time.time())
                self._write(row)
