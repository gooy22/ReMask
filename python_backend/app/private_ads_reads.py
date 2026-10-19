"""Private account-bound reads. Cached rows never stand in for permissions."""
from __future__ import annotations

import re
import time
import json

from .private_ads_catalog import identity
from .private_ads_draft import fail
from .private_ads_http import AdsManagerPrivateHttp
from .private_advertising_reads import RESOURCE_FIELDS, TARGETING_TYPES


class _ReadClient:
    """Adapt catalog reads to the same fresh profile HTTP/2 sender as Launch."""
    def __init__(self, web, catalog, values):
        self.web, self.catalog = web, catalog
        self.account = identity(values["ad_account_id"])
        self.business = identity(values["business_id"])
        self.actor = identity(str(web.profile.cookies.get("c_user") or ""))
        self.http = None

    async def request(self, name, *, values=None, params=None):
        row, path = self.catalog.route(name, {"ad_account_id": self.account, **(values or {})})
        if row["method"] != "GET" or set(params or {}) - set(row["params"]):
            raise fail("INVALID_RESOURCE_ROUTE", "Resource request differs from its reviewed source")
        if self.http is None:
            self.http = await AdsManagerPrivateHttp.open(self.web, account_id=self.account,
                business_id=self.business, actor_id=self.actor)
        return await self.http.get(path.removeprefix("/" + self.catalog.data["api_version"]), params or {})

    async def account_record(self, *, require_active=True, currency=False, pages=False, reference=False):
        fields = ["id", "account_id", "business"]
        if not reference:
            fields.append('account_status')
        if pages:
            fields.append('ad_account_promotable_objects')
        if currency:
            fields.append("currency")
        row = await self.request("ACCOUNT_READ", params={"fields":
            fields})
        business = row.get("business")
        if (not isinstance(row.get("id"), str) or row["id"].removeprefix("act_") != self.account
                or not isinstance(business, dict) or business.get("id") != self.business):
            raise fail("RESOURCE_SCOPE_MISMATCH", "Resource account did not confirm the exact Business")
        if require_active and (type(row.get("account_status")) not in {int, str} or str(row["account_status"]) != "1"):
            raise fail("ACCOUNT_INACTIVE", "Resource account is not active")
        return row


class AdsManagerReadCatalog:
    def __init__(self, catalog):
        self.catalog = catalog
        self.rows = {"pages": {"source": "ad_account_promotable_objects.promotable_page_ids",
                               "catalog_digest": catalog.digest}}
        for name, route in {"images":"IMAGE_READ", "videos":"VIDEO_READ",
                            "campaigns":"CAMPAIGNS_READ", "adsets":"ADSETS_READ",
                            "ads":"ADS_READ", "pixels":"PIXELS_READ",
                            "custom_audiences":"AUDIENCES_READ", "conversion_goals":"CONVERSIONS_READ",
                            "instagram_accounts":"INSTAGRAM_ACCOUNTS_READ", "creatives":"ADS_READ",
                            "interests":"TARGETING_SEARCH", "behaviors":"TARGETING_SEARCH",
                            "insights":"INSIGHTS_READ", "locations":"LOCATIONS_READ",
                            "locales":"LOCALES_READ"}.items():
            if route in catalog.data["routes"]:
                self.rows[name] = {"route": route, "catalog_digest": catalog.digest}
        for name in TARGETING_TYPES:
            self.rows[name] = {"route":"TARGETING_SEARCH", "catalog_digest":catalog.digest}
        if "INSTAGRAM_MEDIA_READ" in catalog.data["routes"]:
            self.rows["instagram_media"] = {"route":"INSTAGRAM_MEDIA_READ","catalog_digest":catalog.digest}

    def status(self):
        return {name:{"configured":name in self.rows, "transport":"ads_manager_private_http"}
                for name in RESOURCE_FIELDS}

    def client(self, web, values):
        return _ReadClient(web, self.catalog, values)

    async def read(self, web, name, values):
        if name not in self.rows:
            raise fail("RESOURCE_CONTRACT_REQUIRED", "Reviewed account query is unavailable for " + name)
        client = self.client(web, values)
        reference = name in TARGETING_TYPES | {"locations","locales"}
        account = await client.account_record(require_active=not reference, pages=name == 'pages', reference=reference)
        if not reference and str(account.get("account_status")) != "1":
            raise fail("ACCOUNT_INACTIVE", "The selected advertising account is not active")
        if name in TARGETING_TYPES and not str(values.get("query") or "").strip():
            return await self.browse_targeting(client, name, values)
        if name == "instagram_media":
            actors = await self.read(web,"instagram_accounts",{**values,"query":""})
            records, seen = [], set()
            requests = 0
            for actor in actors["data"]:
                actor_id = identity(actor.get("id_v2"),"Instagram user V2")
                params = {"ad_account_id":client.account,"fields":self.catalog.data["resource_fields"][name],"limit":100}
                cursors = set()
                for _ in range(5):
                    requests += 1
                    if requests > 25:
                        raise fail("RESOURCE_READ_INCOMPLETE", "Instagram media exceeded its total request budget", True)
                    response = await client.request("INSTAGRAM_MEDIA_READ",values={"instagram_id":actor_id},params=params)
                    items = response.get("data")
                    if not isinstance(items,list) or any(not isinstance(r,dict) for r in items):
                        raise fail("RESOURCE_READ_INCOMPLETE", "Advertisable Instagram media read is incomplete")
                    for row in items:
                        key = identity(row.get("id"),"Instagram media")
                        if key not in seen:
                            seen.add(key)
                            records.append({**{k:v for k,v in row.items() if k in RESOURCE_FIELDS[name]},
                                "instagram_actor_id": actor["id"], "instagram_user_id": actor_id})
                        if len(records) > 500:
                            raise fail("RESOURCE_READ_INCOMPLETE", "Instagram media exceeded its account row budget")
                    paging = response.get("paging") or {}
                    if not isinstance(paging,dict):
                        raise fail("RESOURCE_READ_INCOMPLETE", "Instagram media pagination is invalid")
                    if not paging.get("next"): break
                    cursor_data = paging.get("cursors")
                    cursor = cursor_data.get("after") if isinstance(cursor_data, dict) else None
                    if not isinstance(cursor,str) or not 1 <= len(cursor) <= 4096 or cursor in cursors:
                        raise fail("RESOURCE_READ_INCOMPLETE", "Instagram media pagination is incomplete")
                    cursors.add(cursor); params["after"] = cursor
                else:
                    raise fail("RESOURCE_READ_INCOMPLETE", "Instagram media exceeded its request budget", True)
        elif name == "pages":
            objects = account.get("ad_account_promotable_objects")
            ids = objects.get("promotable_page_ids") if isinstance(objects, dict) else None
            if not isinstance(ids, list) or len(ids) > 500 or len(set(str(v) for v in ids)) != len(ids):
                raise fail("PAGE_PERMISSION_UNCONFIRMED", "Account did not return its exact promotable Page list")
            records = [{"id":identity(v, "promotable Page"), "name":str(v),
                        "account_id":client.account, "ad_account_page_access_verified":True,
                        "source":"account_promotable_page_ids"} for v in ids]
        else:
            route = self.rows[name]["route"]
            fields = (["id","name","type","path","description","valid","audience_size_lower_bound","audience_size_upper_bound"]
                if name in TARGETING_TYPES else self.catalog.data.get("resource_fields", {}).get(name))
            if not isinstance(fields, list) or not fields:
                raise fail("RESOURCE_CONTRACT_REQUIRED", "Source field projection is missing for " + name)
            records, seen, cursors = [], set(), set()
            fields = list(fields)
            if name == "instagram_accounts":
                fields.append("is_connected_to_ad_account.ad_account_id("+client.account+")")
            params = {"fields":fields, "limit":min(100, int(values.get("limit",100)))}
            if name == "instagram_accounts": params["business_id"] = client.business
            if name in TARGETING_TYPES | {"locations"}:
                query = str(values.get("query") or "").strip()
                if not query:
                    return {"data":[],"account_id":client.account,"account_scope_verified":True,
                        "checked_live":False,"checked_at":int(time.time()),"query_required":True,
                        "source":"ads_manager_private_http"}
                if name == "locations":
                    params = {"type":"adgeolocation","qs":[query],"limit":min(100,int(values.get("limit",100))),
                        "ad_account_id":client.account,"place_fallback":True,"bulk_upload":False}
                else:
                    params.update(accountId=client.account,q=query,
                        whitelisted_types=[name])
            if name == "locales": params = {"type":"adlocale","limit":100}
            if name == "insights": params.update(level="ad",date_preset="last_30d")
            for _ in range(5):
                response = await client.request(route, params=params)
                items = response.get("data")
                if not isinstance(items, list) or len(items) > 500:
                    raise fail("RESOURCE_READ_INCOMPLETE", "Account edge returned an incomplete resource list")
                for row in items:
                    if not isinstance(row, dict):
                        raise fail("RESOURCE_READ_INCOMPLETE", "Account resource has an invalid row")
                    if name in TARGETING_TYPES and (row.get("valid") is False or row.get("type",name) != name):
                        continue
                    if name == "creatives":
                        if row.get("account_id") != client.account:
                            raise fail("RESOURCE_SCOPE_MISMATCH", "Creative parent belongs to another advertising account")
                        row = row.get("creative")
                        if not isinstance(row,dict) or row.get("account_id") != client.account:
                            raise fail("RESOURCE_READ_INCOMPLETE", "Account ad has no scoped creative projection")
                    if name == "instagram_accounts":
                        if row.get("has_create_ads_access") is not True or row.get("is_connected_to_ad_account") is not True:
                            continue
                    key = json.dumps({k:v for k,v in row.items() if k in RESOURCE_FIELDS[name]},sort_keys=True) if name == "insights" else row.get("key" if name in {"locations","locales"} else "hash" if name == "images" else "id")
                    if name in TARGETING_TYPES | {"locations","locales"} and isinstance(key,int) and not isinstance(key,bool): key = str(key)
                    if name != "insights" and (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", key)):
                        raise fail("RESOURCE_READ_INCOMPLETE", "Account resource has no exact identity")
                    if "account_id" in row and row["account_id"] != client.account:
                        raise fail("RESOURCE_SCOPE_MISMATCH", "Account edge returned a foreign resource")
                    if key in seen:
                        continue
                    seen.add(key)
                    clean = {k:v for k,v in row.items() if k in RESOURCE_FIELDS[name]}
                    if name in TARGETING_TYPES: clean["id"]=key
                    if name in {"locations","locales"}: clean.update(id=key,key=key)
                    if name == "conversion_goals" and isinstance(row.get("pixel"),dict):
                        clean["pixel_id"] = identity(row["pixel"].get("id"),"pixel")
                    if name == "ads" and isinstance(row.get("creative"),dict):
                        clean["creative_id"] = identity(row["creative"].get("id"), "creative")
                    records.append(clean)
                    if len(records) > 500:
                        raise fail("RESOURCE_READ_INCOMPLETE", "Account resource list exceeded its row budget")
                paging = response.get("paging", {})
                if not isinstance(paging, dict):
                    raise fail("RESOURCE_READ_INCOMPLETE", "Account resource pagination is invalid")
                if not paging.get("next"):
                    break
                # Never follow a server next URL, which can carry authorization.
                cursor_data = paging.get("cursors")
                cursor = cursor_data.get("after") if isinstance(cursor_data, dict) else None
                if (not isinstance(cursor,str) or not 1 <= len(cursor) <= 4096
                        or cursor in cursors):
                    raise fail("RESOURCE_READ_INCOMPLETE", "Account resource pagination is incomplete")
                cursors.add(cursor); params["after"] = cursor
            else:
                raise fail("RESOURCE_READ_INCOMPLETE", "Account resource pagination exceeded its request budget", True)
        query = str(values.get("query", "")).casefold()
        if query and name not in TARGETING_TYPES | {"locations","insights"}:
            records = [r for r in records if any(query in str(r.get(k,"")).casefold() for k in ("id","hash","name","title"))]
        return {"data":records, "account_id":client.account, "account_scope_verified":True,
                "checked_live":True, "checked_at":int(time.time()), "source":"ads_manager_private_http"}

    async def browse_targeting(self, client, name, values):
        """The recovered sender returns a flat tree; headings have no selectable ID."""
        response = await client.request("TARGETING_BROWSE", params={
            "fields":["raw_name","id","name","type","path","key","parent","description",
                      "audience_size_lower_bound","audience_size_upper_bound"],
            "include_nodes":True, "whitelisted_types":[name], "is_exclusion":False,
            "is_reserved":False, "regulated_categories":[], "regulated_countries":[]})
        rows = response.get("data")
        paging = response.get("paging", {})
        if (not isinstance(rows, list) or len(rows) > 20000
                or any(not isinstance(row, dict) for row in rows)
                or not isinstance(paging, dict) or paging.get("next")):
            raise fail("RESOURCE_READ_INCOMPLETE", "Targeting browse tree is incomplete", True)
        nodes = {}
        for row in rows:
            key = row.get("key")
            if isinstance(key, str):
                if key in nodes and nodes[key] != row:
                    raise fail("RESOURCE_READ_INCOMPLETE", "Conflicting targeting tree nodes")
                nodes[key] = row
        records, seen = [], set()
        for row in rows:
            if row.get("type") != name or row.get("valid") is False:
                continue
            key = str(row.get("id") or "")
            if not re.fullmatch(r"[0-9]{1,40}", key) or not isinstance(row.get("name"), str):
                continue
            clean = {k:v for k,v in row.items() if k in RESOURCE_FIELDS[name]}
            clean["id"] = key
            if not isinstance(clean.get("path"), list):
                path, visited, parent = [], set(), row.get("parent")
                while isinstance(parent, str) and parent in nodes and parent not in visited and len(visited) < 32:
                    visited.add(parent)
                    node = nodes[parent]
                    if node.get("name") not in {None, "", "__ROOT__"}: path.insert(0, str(node["name"]))
                    parent = node.get("parent")
                clean["path"] = path
            if key in seen:
                continue
            seen.add(key); records.append(clean)
        limit = max(1, min(20000, int(values.get("limit", 100))))
        return {"data":records[:limit], "account_id":client.account, "account_scope_verified":True,
                "checked_live":True, "checked_at":int(time.time()), "source":"private_meta_targetingbrowse",
                "catalog_count":len(records), "truncated":len(records)>limit}

    async def pages(self, web, values):
        return {**await self.read(web, "pages", values), "ad_account_page_access_verified":True}
