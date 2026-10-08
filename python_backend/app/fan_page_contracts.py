"""Observed Page CREATE schemas; no guessed doc IDs or profile credentials."""
from __future__ import annotations

import hashlib
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
         "accesstoken", "xs", "cuser", "requestenvelope"}


def compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def valid_page_create(meta: dict[str, Any], *, name: str, actor_id: str) -> bool:
    """Require the observed final mutation to address this actor and Page."""
    try:
        url = urlsplit(str(meta.get("url") or meta.get("endpoint_url") or ""))
        if (url.scheme != "https" or url.hostname not in {"www.facebook.com", "facebook.com", "business.facebook.com"}
                or url.path not in {"/api/graphql", "/api/graphql/"}
                or url.query or url.fragment or url.username or url.password or url.port):
            return False
    except ValueError:
        return False
    friendly = str(meta.get("friendly_name") or "")
    if not re.fullmatch(r"[A-Za-z0-9_]+", friendly) or not all(
        word in friendly.lower() for word in ("page", "create", "mutation")
    ) or any(word in friendly.lower() for word in ("draft", "preview", "suggest", "delete", "update")):
        return False
    doc = str(meta.get("doc_id") or "")
    if not doc.isdigit() or not 5 <= len(doc) <= 40 or not actor_id.isdigit():
        return False
    variables = meta.get("variables")
    input_data = variables.get("input") if isinstance(variables, dict) else None
    if not isinstance(input_data, dict):
        return False
    def session_free(value):
        if isinstance(value, dict):
            return all(compact(key) not in _AUTH and not str(key).startswith(("__", "$fp_"))
                       and (compact(key) != "actorid" or str(child) == actor_id)
                       and session_free(child) for key, child in value.items())
        if isinstance(value, list):
            return all(session_free(child) for child in value)
        return True
    if not session_free(variables):
        return False
    names = [str(value).strip() for key, value in input_data.items() if compact(key) in {"name", "pagename"}]
    actors = [str(value) for key, value in input_data.items() if compact(key) == "actorid"]
    return bool(names and all(value == name for value in names) and actors and set(actors) == {actor_id})


class FanPageContractStore:
    def __init__(self, path: str | Path | None = None, *, max_age: int = 86400):
        root = os.getenv("REMASK_DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "/var/lib/remask"
        self.path = Path(path or os.getenv("REMASK_FAN_PAGE_CONTRACT_STORE") or Path(root) / "facebook-web" / "fan-page-contracts.json")
        self.max_age = max_age

    @staticmethod
    def _key(category: str) -> str:
        return hashlib.sha256(category.strip().casefold().encode()).hexdigest()

    def _read(self) -> dict[str, Any]:
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
            return rows if isinstance(rows, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, rows: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, target = tempfile.mkstemp(dir=self.path.parent, prefix=".fp-contract-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(rows, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(target, self.path)
        finally:
            if os.path.exists(target):
                os.unlink(target)

    def register_capture(self, capture: dict[str, Any], *, name: str, category: str,
                         bio: str, actor_id: str) -> bool:
        if capture.get("source") != "live_page_create_capture" or not category.strip():
            return False
        if not valid_page_create(capture, name=name, actor_id=actor_id):
            return False
        arguments = set()
        categories = []

        def template(value: Any, *, category_scope: bool = False) -> Any:
            if isinstance(value, dict):
                result = {}
                for key, child in value.items():
                    normalized = compact(key)
                    if normalized in _AUTH or str(key).startswith(("__", "$fp_")):
                        raise ValueError("session material")
                    argument = {"name": "name", "pagename": "name", "actorid": "actor",
                                "bio": "bio", "description": "bio", "clientmutationid": "mutation"}.get(normalized)
                    if argument:
                        if not isinstance(child, (str, int)) or isinstance(child, bool):
                            raise ValueError("invalid scalar")
                        if argument == "name" and str(child).strip() != name:
                            raise ValueError("unrelated name")
                        if argument == "actor" and str(child) != actor_id:
                            raise ValueError("unrelated actor")
                        if argument == "bio" and str(child) != bio:
                            raise ValueError("unrelated description")
                        arguments.add(argument)
                        result[key] = {"$fp_arg": argument, "$fp_type": "int" if isinstance(child, int) else "str"}
                    else:
                        result[key] = template(child, category_scope=category_scope or normalized in {
                            "category", "categories", "categoryid", "categoryids", "categorylist"})
                return result
            if isinstance(value, list):
                return [template(child, category_scope=category_scope) for child in value]
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                if category_scope and str(value).isdigit():
                    categories.append(str(value))
                elif re.search(r"(?<!\d)\d{5,}(?!\d)|https?://", str(value)):
                    raise ValueError("opaque identity")
            return value

        try:
            if len(json.dumps(capture["variables"])) > 100000:
                return False
            shaped = template(capture["variables"])
            if not {"name", "actor"}.issubset(arguments) or not categories or (bio and "bio" not in arguments):
                return False
            row = {"version": 1, "status": "captured", "observed_at": int(time.time()),
                   "doc_id": capture["doc_id"], "friendly_name": capture["friendly_name"],
                   "endpoint_url": capture["endpoint_url"], "category": category.strip(),
                   "category_ids": sorted(set(categories)), "variables_template": shaped}
            with _LOCK:
                rows = self._read()
                rows[self._key(category)] = row
                self._write(rows)
            return True
        except (KeyError, TypeError, ValueError):
            return False

    def get(self, *, name: str, category: str, bio: str, actor_id: str) -> dict[str, Any] | None:
        with _LOCK:
            row = self._read().get(self._key(category), {})
        if not isinstance(row, dict):
            return None
        try:
            age = time.time() - int(row.get("observed_at") or 0)
            if row.get("version") != 1 or row.get("status") not in {"captured", "verified"} or not 0 <= age <= self.max_age:
                return None
            values = {"name": name, "actor": actor_id, "bio": bio, "mutation": uuid.uuid4().hex[:16]}
            seen = set()
            def render(value):
                if isinstance(value, dict):
                    if set(value) == {"$fp_arg", "$fp_type"}:
                        argument = value["$fp_arg"]
                        seen.add(argument)
                        if value["$fp_type"] == "int":
                            return int(values[argument])
                        if value["$fp_type"] == "str":
                            return str(values[argument])
                        raise ValueError("unknown scalar")
                    return {key: render(child) for key, child in value.items()}
                if isinstance(value, list):
                    return [render(child) for child in value]
                return value
            capture = {"doc_id": row["doc_id"], "friendly_name": row["friendly_name"],
                       "endpoint_url": row["endpoint_url"], "variables": render(row["variables_template"]),
                       "source": "reusable_page_create_contract", "contract_status": row["status"]}
            if bio and "bio" not in seen:
                return None
            return capture if valid_page_create(capture, name=name, actor_id=actor_id) else None
        except (KeyError, ValueError, TypeError):
            return None

    def set_status(self, *, category: str, doc_id: str, status: str) -> None:
        if status not in {"stale", "verified"}:
            raise ValueError("unknown contract status")
        with _LOCK:
            rows = self._read()
            row = rows.get(self._key(category))
            if isinstance(row, dict) and str(row.get("doc_id")) == doc_id:
                row["status"] = status
                if status == "verified":
                    row["verified_at"] = int(time.time())
                self._write(rows)
