"""Account pixel READs from the recovered Ads Manager sender modules."""
from __future__ import annotations

import copy
import time

from .private_ads_draft import fail, identity
from .private_ads_http import AdsManagerPrivateHttp

PIXEL_READ = {"edge": "adspixels", "fields": ["id", "name", "is_unavailable"],
              "limit": 300, "source": "SignalsConversionDataManager"}


async def read_pixels(client, *, query="", limit=500):
    """Never follow credential-bearing next URLs; keep the exact account route."""
    rows, cursors, seen = [], set(), {}
    params = {"fields": PIXEL_READ["fields"], "limit": PIXEL_READ["limit"]}
    path = "/act_" + identity(client.context.account_id) + "/adspixels"
    for _ in range(5):
        response = await client.get(path, params)
        items = response.get("data") if isinstance(response, dict) and not (response.get("error") or response.get("errors")) else None
        if not isinstance(items, list) or len(items) > 500:
            raise fail("RESOURCE_READ_INCOMPLETE", "Account pixel rows are incomplete", True)
        for row in items:
            if not isinstance(row, dict):
                raise fail("RESOURCE_READ_INCOMPLETE", "Account pixel row is malformed", True)
            pixel = identity(row.get("id"))
            name = row.get("name")
            projected = {"id": pixel, "name": name if isinstance(name, str) else ""}
            observed = {**projected, "is_unavailable": row.get("is_unavailable")}
            if pixel in seen:
                if seen[pixel] != observed:
                    raise fail("RESOURCE_READ_INCOMPLETE", "Pixel paging returned conflicting rows", True)
                continue
            seen[pixel] = observed
            if len(seen) > 500:
                raise fail("RESOURCE_READ_INCOMPLETE", "Pixel inventory exceeds its row budget", True)
            if row.get("is_unavailable") is True:
                continue
            rows.append(projected)
        paging = response.get("paging", {})
        if not isinstance(paging, dict):
            raise fail("RESOURCE_READ_INCOMPLETE", "Pixel paging is malformed", True)
        if "next" not in paging:
            break
        next_page = paging["next"]
        cursor = paging.get("cursors", {}).get("after") if isinstance(paging.get("cursors"), dict) else None
        if (not isinstance(next_page, str) or not next_page or not isinstance(cursor, str)
                or not 1 <= len(cursor) <= 4096 or cursor in cursors):
            raise fail("RESOURCE_READ_INCOMPLETE", "Pixel paging did not advance", True)
        cursors.add(cursor)
        params = {**params, "after": cursor}
    else:
        raise fail("RESOURCE_READ_INCOMPLETE", "Pixel paging exceeded its request budget", True)
    needle = query.casefold()
    visible = [row for row in rows if not needle or needle in row["name"].casefold() or needle in row["id"]]
    return {"data": visible[:limit], "account_id": client.context.account_id,
            "account_scope_verified": True, "checked_live": True, "inventory_complete": len(visible) <= limit,
            "truncated": len(visible) > limit,
            "checked_at": int(time.time()), "source": "private_ads_manager_account_pixels"}


class AdsManagerResourceCatalog:
    """Keep installed Relay reads while adding the recovered versioned pixel edge."""
    def __init__(self, base):
        self.base = base
        self.rows = {**base.rows, "pixels": copy.deepcopy(PIXEL_READ)}

    def status(self):
        return {**self.base.status(), "pixels": {"configured": True, "transport": "private_http_h2"}}

    async def read(self, web, name, values):
        if name != "pixels":
            return await self.base.read(web, name, values)
        client = await AdsManagerPrivateHttp.open(web, account_id=values["ad_account_id"],
            business_id=values["business_id"], actor_id=values["actor_id"])
        row = await client.get("/act_" + client.context.account_id, {"fields": ["id", "business"]})
        if (not isinstance(row, dict) or row.get("id") not in
                {client.context.account_id, "act_" + client.context.account_id}
                or not isinstance(row.get("business"), dict)
                or row["business"].get("id") != client.context.business_id):
            raise fail("RESOURCE_SCOPE_MISMATCH", "Pixel read did not confirm the selected account/Business")
        return await read_pixels(client, query=values.get("query", ""), limit=values.get("limit", 100))

    async def pages(self, web, values):
        return await self.base.pages(web, values)
