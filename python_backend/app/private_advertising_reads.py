"""Pinned advertising resource reads and a durable, explicitly refreshed catalog."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from .private_launch_extensions import fail, source_evidence

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
    """Cache is for browsing resources; it never replaces Launch permission reads."""
    def __init__(self, catalog):
        self.catalog = catalog
        self.root = Path(os.getenv("REMASK_DATA_DIR") or "/var/lib/remask") / "advertising-resources"

    async def get(self, *, name, values, actor_id, force=False, web=None):
        if name not in RESOURCE_FIELDS:
            raise fail("RESOURCE_UNAVAILABLE", "Unsupported advertising resource")
        scope = {key: values.get(key, "") for key in ("profile_id", "business_id", "ad_account_id", "query", "limit")}
        scope["contract_digest"] = hashlib.sha256(json.dumps(self.catalog.rows.get(name),
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        scope["actor_id"] = actor_id
        scope["resource"] = name
        key = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        path = self.root / (key + ".json")
        if not force:
            def cached():
                if not path.is_file() or path.stat().st_size > 2_000_000:
                    return None
                try:
                    data = json.loads(path.read_text())
                    return data if data.get("scope") == scope and data.get("resource") == name else None
                except (OSError, ValueError, AttributeError):
                    return None
            saved = await asyncio.to_thread(cached)
            if not saved:
                return {"data": [], "availability": "NOT_LOADED", "checked_live": False,
                        "account_id": values["ad_account_id"], "_cache": {"hit": False, "cached_only": True}}
            result = saved["result"]
            # Do not expose historical permission flags as live proof.
            rows = [{field: value for field, value in item.items() if field != "ad_account_page_access_verified"}
                    for item in result["data"]]
            return {**result, "data": rows, "checked_live": False, "account_scope_verified": False,
                    "_cache": {"hit": True, "cached_only": True, "fetched_at": result["checked_at"],
                               "stale": time.time() - result["checked_at"] > 1800}}
        if web is None:
            raise fail("HTTP_SESSION_REQUIRED", "Refreshing advertising resources requires the profile HTTP session")
        result = await asyncio.wait_for(self.catalog.read(web, name, values), timeout=65)
        def save():
            self.root.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
            try:
                with os.fdopen(descriptor, "w") as stream:
                    json.dump({"scope": scope, "resource": name, "result": result}, stream, ensure_ascii=False)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        await asyncio.to_thread(save)
        return {**result, "_cache": {"hit": False, "cached_only": False, "fetched_at": result["checked_at"], "stale": False}}
