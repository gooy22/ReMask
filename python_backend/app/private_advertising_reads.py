"""Pinned advertising resource reads and a durable, explicitly refreshed catalog."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata

from .private_launch_extensions import fail, source_evidence
from .private_ads_document import _strict_json

RESOURCE_FIELDS = {
    "pages": {"id", "name"},
    "pixels": {"id", "name"},
    "custom_audiences": {"id", "name", "subtype", "approximate_count_lower_bound", "approximate_count_upper_bound"},
    "images": {"id", "hash", "name", "url", "width", "height"},
    "videos": {"id", "title", "description", "picture", "thumbnails"},
    "creatives": {"id", "name", "image_hash", "video_id", "thumbnail_url", "object_story_id", "effective_object_story_id"},
    "instagram_accounts": {"id", "id_v2", "username", "name"},
    "instagram_media": {"id", "caption", "media_type", "media_url", "thumbnail_url", "permalink", "instagram_actor_id", "instagram_user_id"},
    "conversion_goals": {"id", "name", "custom_event_type", "pixel_id"},
    "campaigns": {"id", "name", "status", "effective_status", "objective", "daily_budget", "lifetime_budget"},
    "adsets": {"id", "name", "status", "effective_status", "campaign_id", "daily_budget", "lifetime_budget", "targeting"},
    "ads": {"id", "name", "status", "effective_status", "campaign_id", "adset_id", "creative_id"},
    "insights": {"campaign_id", "adset_id", "ad_id", "date_start", "date_stop", "impressions", "clicks", "spend", "actions"},
    "locations": {"id", "key", "name", "type", "country_code", "region", "region_id"},
    "interests": {"id", "name", "path", "audience_size_lower_bound", "audience_size_upper_bound"},
    "behaviors": {"id", "name", "path", "description"},
    "locales": {"id", "name", "key"},
}
# Source-advertised targeting families share the reviewed unified sender.
from .editor_modules import installed_editor
TARGETING_TYPES = frozenset(installed_editor()['targeting_types'])
for _target_type in TARGETING_TYPES:
    RESOURCE_FIELDS.setdefault(_target_type, {'id','name','type','path','description',
        'audience_size_lower_bound','audience_size_upper_bound'})

_DATA_PATH = re.compile(r"data(?:\.[A-Za-z][A-Za-z0-9_]*)+")
_ITEM_PATH = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*")


class AdvertisingReadCatalog:
    def __init__(self, manifest=None):
        from .private_launch import PrivateLaunchContractStore, _PLACEHOLDER
        entries = (manifest or {}).get("reads", {})
        if not isinstance(entries, dict) or set(entries) - set(RESOURCE_FIELDS):
            raise fail("STATIC_MANIFEST_INVALID", "Unsupported advertising READ catalog")
        self.rows = {}
        for name, entry in entries.items():
            source_evidence(entry, name)
            row = entry.get("contract")
            required = {"operation_kind", "doc_id", "friendly_name", "variables", "account_path", "rows_path", "fields"}
            allowed = required | {"endpoint_url", "item_path", "permission_path", "pagination"}
            if (not isinstance(row, dict) or not required <= set(row) or set(row) - allowed
                    or row.get("operation_kind") != "query"
                    or not re.fullmatch(r"\d{5,40}", str(row.get("doc_id", "")))
                    or not re.fullmatch(r"[A-Za-z0-9_]{1,180}Query", str(row.get("friendly_name", "")))
                    or not isinstance(row.get("variables"), dict)
                    or "ad_account_id" not in _PLACEHOLDER.findall(json.dumps(row["variables"]))
                    or not isinstance(row.get("fields"), dict) or not row["fields"]
                    or set(row["fields"]) - RESOURCE_FIELDS[name]):
                raise fail("STATIC_MANIFEST_INVALID", "Incomplete advertising resource query " + name)
            identity = "hash" if name == "images" else ("key" if name == "locations" else "id")
            if name != "insights" and identity not in row["fields"]:
                raise fail("STATIC_MANIFEST_INVALID", "Advertising resource needs its exact identity field")
            paths = [row["account_path"], row["rows_path"]]
            if any(not isinstance(path, str) or not _DATA_PATH.fullmatch(path) for path in paths) or len(set(paths)) != 2:
                raise fail("STATIC_MANIFEST_INVALID", "Resource account and rows must be distinct data fields")
            for path in [*row["fields"].values(), *([row["item_path"]] if row.get("item_path") else [])]:
                if not isinstance(path, str) or not _ITEM_PATH.fullmatch(path):
                    raise fail("STATIC_MANIFEST_INVALID", "Resource projection requires exact observed field paths")
            if name == "pages" and (not row.get("permission_path") or not _ITEM_PATH.fullmatch(row["permission_path"])):
                raise fail("STATIC_MANIFEST_INVALID", "Page eligibility requires an explicit per-Page advertising permission")
            pagination = row.get("pagination")
            if pagination is not None:
                if (not isinstance(pagination, dict) or set(pagination) != {"has_next_path", "cursor_path"}
                        or any(not isinstance(path, str) or not _DATA_PATH.fullmatch(path) for path in pagination.values())
                        or "cursor" not in _PLACEHOLDER.findall(json.dumps(row["variables"]))):
                    raise fail("STATIC_MANIFEST_INVALID", "Pagination requires its observed cursor and completion paths")
            PrivateLaunchContractStore._reject_auth_material(row)
            PrivateLaunchContractStore._endpoint(row.get("endpoint_url"))
            self.rows[name] = row

    def status(self):
        return {name: {"configured": name in self.rows} for name in RESOURCE_FIELDS}

    async def read(self, web, name, values):
        from .private_launch import PrivateLaunchContractStore, _lookup, _render
        from .private_launch_verify import _complete
        row = self.rows.get(name)
        if row is None:
            raise fail("RESOURCE_CONTRACT_REQUIRED", "Reviewed advertising query is missing for " + name)
        records, seen, cursors = [], set(), set()
        cursor = ""
        for _ in range(5):
            response = await web.graphql(row["doc_id"], _render(row["variables"], {"cursor": cursor, **values}),
                friendly_name=row["friendly_name"], endpoint_url=PrivateLaunchContractStore._endpoint(row.get("endpoint_url")),
                business_context_id=values["business_id"])
            if not isinstance(response, dict) or not _complete(response):
                raise fail("RESOURCE_READ_INCOMPLETE", "Advertising resource query is incomplete", True)
            account = _lookup(response, row["account_path"])
            if not isinstance(account, str) or account.removeprefix("act_") != values["ad_account_id"]:
                raise fail("RESOURCE_SCOPE_MISMATCH", "Resource query returned another ad account")
            items = _lookup(response, row["rows_path"])
            if not isinstance(items, list) or len(items) > 500:
                raise fail("RESOURCE_READ_INCOMPLETE", "Resource rows are missing or exceed the bounded response")
            for item in items:
                if row.get("item_path"):
                    item = _lookup(item, row["item_path"])
                if not isinstance(item, dict):
                    raise fail("RESOURCE_READ_INCOMPLETE", "An advertising resource row is incomplete")
                projected = {field: _lookup(item, path) for field, path in row["fields"].items()}
                if name != "insights":
                    identity = "hash" if name == "images" else ("key" if name == "locations" else "id")
                    if not isinstance(projected.get(identity), str) or not 1 <= len(projected[identity]) <= 160:
                        raise fail("RESOURCE_READ_INCOMPLETE", "An advertising resource has no exact identity")
                if name == "pages":
                    if _lookup(item, row["permission_path"]) is not True:
                        continue
                    if not re.fullmatch(r"\d{5,40}", str(projected.get("id", ""))):
                        raise fail("RESOURCE_READ_INCOMPLETE", "Page response has no exact identity")
                    projected.update({"account_id": account.removeprefix("act_"),
                        "ad_account_page_access_verified": True, "source": "scoped_private_promotable_pages"})
                signature = json.dumps(projected, sort_keys=True, separators=(",", ":"))
                if signature not in seen:
                    seen.add(signature); records.append(projected)
                if len(records) > 500:
                    raise fail("RESOURCE_READ_INCOMPLETE", "Advertising catalog exceeds its bounded row budget")
            pagination = row.get("pagination")
            if pagination is None:
                break
            next_page = _lookup(response, pagination["has_next_path"])
            if next_page is False:
                break
            cursor = _lookup(response, pagination["cursor_path"])
            if next_page is not True or not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise fail("RESOURCE_READ_INCOMPLETE", "Advertising resource pagination is incomplete")
            cursors.add(cursor)
        else:
            raise fail("RESOURCE_READ_INCOMPLETE", "Advertising resource pagination exceeded its request budget", True)
        return {"data": records, "account_id": values["ad_account_id"], "account_scope_verified": True,
                "checked_live": True, "checked_at": int(time.time()), "source": "static_private_http_query"}

    async def pages(self, web, values):
        result = await self.read(web, "pages", values)
        return {**result, "ad_account_page_access_verified": True}


class AdvertisingResourceStore:
    """Persistent scoped browsing cache plus a reusable targeting-option index.

    Targeting options are suggestions only. The index never proves account access
    and is kept separate from the complete resource snapshots used for assets.
    """
    TARGETING_OPTION_TYPES = frozenset(TARGETING_TYPES | {"locations", "locales"})
    MAX_TARGETING_OPTIONS = 8000
    MAX_TARGETING_INDEX_BYTES = 4_000_000
    CACHE_MAX_AGE_SECONDS = 1800
    REFRESH_COOLDOWN_SECONDS = 60

    def __init__(self, catalog):
        self.catalog = catalog
        self.root = Path(os.getenv("REMASK_DATA_DIR") or "/var/lib/remask") / "advertising-resources"
        self._refresh_tasks = {}
        self._refresh_last_started = {}

    def _resource_scope(self, name, values, actor_id):
        scope = {key: values.get(key, "") for key in
                 ("profile_id", "business_id", "ad_account_id", "query", "limit", "execution_mode")}
        if name in TARGETING_TYPES:
            scope["offset"] = values.get("offset", 0)
        scope["contract_digest"] = hashlib.sha256(json.dumps(
            self.catalog.rows.get(name), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        scope["actor_id"] = actor_id
        scope["resource"] = name
        return scope

    def _targeting_scope(self, name, values, actor_id):
        return {
            "profile_id": str(values.get("profile_id") or ""),
            "business_id": str(values.get("business_id") or ""),
            "ad_account_id": str(values.get("ad_account_id") or ""),
            "actor_id": str(actor_id or ""),
            "resource": name,
            "contract_digest": hashlib.sha256(json.dumps(
                self.catalog.rows.get(name), sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        }

    def schedule_refresh_once(self, *, name, values, actor_id, callback):
        """Run at most one background refresh per exact account/query scope."""
        scope = self._resource_scope(name, values, actor_id)
        key = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        current = self._refresh_tasks.get(key)
        if current is not None and not current.done():
            return False
        now = time.monotonic()
        last_started = self._refresh_last_started.get(key)
        if last_started is not None and now - last_started < self.REFRESH_COOLDOWN_SECONDS:
            return False
        self._refresh_last_started[key] = now
        if len(self._refresh_last_started) > 512:
            cutoff = now - 5 * self.REFRESH_COOLDOWN_SECONDS
            self._refresh_last_started = {
                stored_key: started for stored_key, started in self._refresh_last_started.items()
                if started >= cutoff
            }
        task = asyncio.create_task(callback())
        self._refresh_tasks[key] = task

        def finished(done):
            if self._refresh_tasks.get(key) is done:
                self._refresh_tasks.pop(key, None)
            if not done.cancelled():
                # Retrieve unexpected exceptions so asyncio does not report an
                # unhandled task. Callers log only a sanitized error category.
                done.exception()

        task.add_done_callback(finished)
        return True

    @staticmethod
    def _targeting_identity(name, row):
        raw = row.get("key") if name == "locations" else (row.get("id") or row.get("key"))
        if isinstance(raw, int) and not isinstance(raw, bool):
            raw = str(raw)
        if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", raw):
            return ""
        return raw

    @classmethod
    def _clean_targeting_option(cls, name, row):
        if name not in cls.TARGETING_OPTION_TYPES or not isinstance(row, dict):
            return None
        row_type = row.get("type")
        if name in TARGETING_TYPES and row_type not in (None, name):
            return None
        identity = cls._targeting_identity(name, row)
        if not identity:
            return None
        clean = {}
        for key in ("name", "description", "type", "country_code", "region", "region_id"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                limit = 1000 if key == "description" else 256
                clean[key] = value.strip()[:limit]
        raw_path = row.get("path")
        if isinstance(raw_path, list):
            clean_path = [str(part).strip()[:160] for part in raw_path[:32]
                          if isinstance(part, str) and part.strip()]
            if clean_path:
                clean["path"] = clean_path
        if not clean.get("name"):
            return None
        if name == "locations":
            clean.update({"id": identity, "key": identity})
        else:
            clean["id"] = identity
            if name == "locales":
                raw_key = row.get("key")
                if isinstance(raw_key, (str, int)) and not isinstance(raw_key, bool):
                    key = str(raw_key)
                    if re.fullmatch(r"[A-Za-z0-9_-]{1,160}", key):
                        clean["key"] = key
        if name in TARGETING_TYPES:
            clean["type"] = name
        elif isinstance(row_type, str) and row_type.strip():
            clean["type"] = row_type.strip()[:80]
        return clean

    def _targeting_index_path(self, scope):
        digest = hashlib.sha256(json.dumps(
            scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return self.root / "targeting-options" / (digest + ".json")

    def _read_targeting_index_sync(self, path, scope, name):
        try:
            if not path.is_file() or path.stat().st_size > self.MAX_TARGETING_INDEX_BYTES:
                return {}, True
            payload = _strict_json(path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict) or payload.get("version") != 1
                    or payload.get("scope") != scope or payload.get("resource") != name
                    or not isinstance(payload.get("items"), list)):
                return {}, True
            items = {}
            for entry in payload["items"]:
                if not isinstance(entry, dict) or type(entry.get("checked_at")) is not int:
                    continue
                row = self._clean_targeting_option(name, entry.get("row"))
                if not row or not 0 < entry["checked_at"] <= int(time.time()) + 300:
                    continue
                identity = self._targeting_identity(name, row)
                items[identity] = {"row": row, "checked_at": entry["checked_at"]}
            return items, payload.get("truncated") is not False
        except (OSError, ValueError, AttributeError, RecursionError, TypeError):
            return {}, True

    async def _targeting_suggestions(self, *, name, values, actor_id):
        scope = self._targeting_scope(name, values, actor_id)
        path = self._targeting_index_path(scope)
        items, truncated = await asyncio.to_thread(self._read_targeting_index_sync, path, scope, name)
        query = unicodedata.normalize("NFKC", str(values.get("query") or "")).casefold()
        tokens = [token for token in query.split() if token]
        matches = []
        for identity, entry in items.items():
            row = entry["row"]
            searchable = " ".join([row.get("name", ""), row.get("description", ""),
                                   " ".join(row.get("path", [])), row.get("type", ""),
                                   row.get("country_code", ""), row.get("region", "")])
            searchable = unicodedata.normalize("NFKC", searchable).casefold()
            if tokens and not all(token in searchable for token in tokens):
                continue
            matches.append((identity, entry))
        matches.sort(key=lambda pair: (
            pair[1]["checked_at"], pair[1]["row"].get("name", "").casefold(), pair[0]), reverse=True)
        if not matches:
            return None
        try:
            offset = int(values.get("offset") or 0)
            limit = max(1, min(int(values.get("limit") or 100), 500))
        except (TypeError, ValueError):
            return None
        page = matches[offset:offset + limit]
        if not page:
            return None
        checked_at = min(entry["checked_at"] for _, entry in page)
        next_offset = None
        next_index = offset + len(page)
        if not tokens and next_index < len(matches) and next_index % 100 == 0:
            next_offset = next_index
        now = int(time.time())
        return {
            "data": [entry["row"] for _, entry in page],
            "account_id": str(values.get("ad_account_id") or ""),
            "checked_live": False,
            "account_scope_verified": False,
            "checked_at": checked_at,
            "query_required": False,
            "truncated": truncated or next_index < len(matches),
            "offset": offset,
            "next_offset": next_offset,
            "source": "private_meta_targeting_option_index",
            "_cache": {
                "hit": True,
                "cached_only": True,
                "index": True,
                "fetched_at": checked_at,
                "stale": now - checked_at > self.CACHE_MAX_AGE_SECONDS,
            },
        }

    def _merge_targeting_index_sync(self, path, scope, name, rows, checked_at):
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                items, truncated = self._read_targeting_index_sync(path, scope, name)
                for row in rows:
                    clean = self._clean_targeting_option(name, row)
                    if not clean:
                        continue
                    identity = self._targeting_identity(name, clean)
                    items[identity] = {"row": clean, "checked_at": checked_at}
                ordered = sorted(items.items(), key=lambda pair: (
                    pair[1]["checked_at"], pair[0]), reverse=True)
                overflow = len(ordered) > self.MAX_TARGETING_OPTIONS
                ordered = ordered[:self.MAX_TARGETING_OPTIONS]
                if overflow:
                    truncated = True
                payload = {
                    "version": 1,
                    "scope": scope,
                    "resource": name,
                    "truncated": truncated,
                    "items": [
                        {"row": row, "checked_at": entry["checked_at"]}
                        for _, entry in ordered
                    ],
                }
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                while len(encoded.encode("utf-8")) > self.MAX_TARGETING_INDEX_BYTES and ordered:
                    ordered.pop()
                    truncated = True
                    payload["truncated"] = truncated
                    payload["items"] = [
                        {"row": row, "checked_at": entry["checked_at"]}
                        for _, entry in ordered
                    ]
                    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    async def _save_targeting_options(self, name, values, actor_id, result):
        if (name not in self.TARGETING_OPTION_TYPES or result.get("checked_live") is not True
                or result.get("account_scope_verified") is not True
                or result.get("account_id") != str(values.get("ad_account_id") or "")
                or type(result.get("checked_at")) is not int
                or not isinstance(result.get("data"), list)):
            return
        rows = [clean for row in result["data"]
                if (clean := self._clean_targeting_option(name, row))]
        if not rows:
            return
        scope = self._targeting_scope(name, values, actor_id)
        path = self._targeting_index_path(scope)
        await asyncio.to_thread(
            self._merge_targeting_index_sync, path, scope, name, rows, result["checked_at"])

    async def get(self, *, name, values, actor_id, force=False, web=None):
        if name not in RESOURCE_FIELDS:
            raise fail("RESOURCE_UNAVAILABLE", "Unsupported advertising resource")
        scope = self._resource_scope(name, values, actor_id)
        key = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        path = self.root / (key + ".json")
        if not force:
            def cached():
                try:
                    # File replacement/deletion can race both is_file and stat.
                    # A lost cache file is an ordinary cache miss, not a 500.
                    if not path.is_file() or path.stat().st_size > 2_000_000:
                        return None
                    data = _strict_json(path.read_text(encoding="utf-8"))
                    return data if data.get("scope") == scope and data.get("resource") == name else None
                except (OSError, ValueError, AttributeError, RecursionError, TypeError):
                    return None
            saved = await asyncio.to_thread(cached)
            if saved:
                result = saved.get("result")
                valid = (isinstance(result, dict) and isinstance(result.get("data"), list)
                         and all(isinstance(item, dict) for item in result["data"])
                         and type(result.get("checked_at")) is int
                         and 0 < result["checked_at"] <= int(time.time()) + 300
                         and result.get("account_id") == values["ad_account_id"]
                         and result.get("checked_live") is True
                         and result.get("account_scope_verified") is True
                         and result.get("query_required") is not True
                         and result.get("truncated") is not True
                         and result.get("inventory_complete") is not False)
                if valid:
                    # Do not expose historical permission flags as live proof.
                    rows = [{field: value for field, value in item.items()
                             if field != "ad_account_page_access_verified"} for item in result["data"]]
                    return {**result, "data": rows, "checked_live": False,
                            "account_scope_verified": False,
                            "ad_account_page_access_verified": False,
                            "_cache": {"hit": True, "cached_only": True,
                                       "fetched_at": result["checked_at"],
                                       "stale": time.time() - result["checked_at"] > self.CACHE_MAX_AGE_SECONDS}}
            if name in self.TARGETING_OPTION_TYPES:
                suggestion = await self._targeting_suggestions(name=name, values=values, actor_id=actor_id)
                if suggestion is not None:
                    return suggestion
            return {"data": [], "availability": "NOT_LOADED", "checked_live": False,
                    "account_id": values["ad_account_id"], "_cache": {"hit": False, "cached_only": True}}
        if web is None:
            raise fail("HTTP_SESSION_REQUIRED", "Refreshing advertising resources requires the profile HTTP session")
                # Exact-Business DRAFT Page inventory is serialized across Meta's
        # paginated Business connection (up to 20 reads), after the separate
        # account and actor proofs. Give this bounded read enough time to
        # finish instead of cancelling it mid-pagination.
        timeout = 120 if name == "pages" and values.get("execution_mode") == "DRAFT" else 65
        result = await asyncio.wait_for(self.catalog.read(web, name, values), timeout=timeout)
        if (not isinstance(result, dict) or type(result.get("checked_at")) is not int
                or not 0 < result["checked_at"] <= int(time.time()) + 300):
            raise fail("RESOURCE_READ_INCOMPLETE", "Resource refresh has no valid collection time")
        if (not isinstance(result.get("data"), list)
                or any(not isinstance(item, dict) for item in result["data"])
                or result.get("account_id") != values["ad_account_id"]):
            raise fail("RESOURCE_SCOPE_MISMATCH", "Resource refresh did not return a complete scoped account result")
        # Each independently validated targeting row is a reusable suggestion,
        # even when the response is a bounded page. This index is separate from
        # complete inventories and cannot confer account permission.
        if (name in self.TARGETING_OPTION_TYPES and result.get("checked_live") is True
                and result.get("account_scope_verified") is True):
            await self._save_targeting_options(name, values, actor_id, result)
        # Browsing may intentionally return a preview, query-required response,
        # or a truncated selection. It is not a full confirmed inventory and
        # must never overwrite an older complete account-scoped cache.
        complete = (result.get("checked_live") is True
                    and result.get("account_scope_verified") is True
                    and result.get("query_required") is not True
                    and result.get("truncated") is not True
                    and result.get("inventory_complete") is not False
                    and not (name in TARGETING_TYPES and values.get("offset", 0)))
        if not complete:
            return {**result, "_cache": {"hit": False, "cached_only": False,
                    "fetched_at": result.get("checked_at"), "stale": True}}
        def save():
            self.root.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump({"scope": scope, "resource": name, "result": result}, stream, ensure_ascii=False)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        await asyncio.to_thread(save)
        return {**result, "_cache": {"hit": False, "cached_only": False,
                "fetched_at": result["checked_at"], "stale": False}}
