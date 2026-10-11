"""Explicit one-shot advertising HTTP audit; never creates advertising objects.

Source collection is maintenance only, separately enabled. It saves literal
public Ads frontend modules for review, never installs a contract at runtime.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from ..private_auth import private_auth_error
from ..private_inventory import _auth_gate
from ..private_launch_read import inspect_pages
from ..static_payment_read import inspect_methods
from ..session import ProfileSession
from ..provisioning.models import ProvisioningError
from fb_worker import AuthenticationError

log = logging.getLogger("remask.launch_http_audit")


async def collect_sources(web, *, account, business):
    from ..private_contract_discovery import _module_nodes, _script_urls
    entry = ("https://adsmanager.facebook.com/adsmanager/manage/campaigns"
             f"?act={account}&business_id={business}")
    status, document, final = await web.fetch_text(entry, max_bytes=5_000_000, document_navigation=True)
    gate = _auth_gate(final, document)
    if gate:
        raise ProvisioningError(gate, "Advertising source read requires a restored HTTP session.")
    if not 200 <= status < 300 or urlsplit(final).hostname not in {"adsmanager.facebook.com", "business.facebook.com"}:
        return {"code": "ADS_SOURCE_DOCUMENT_UNAVAILABLE", "http_status": status}
    modules = {}
    def observe(source):
        for name, node in _module_nodes(source):
            if not re.match(r"(?:Ads|useAds|BizKit.*Ads)", name):
                continue
            raw = node.text.decode("utf-8")
            if len(raw) > 180_000 or re.search(r"fb_dtsg|access_token|authorization|cookie", raw, re.I):
                continue
            digest = hashlib.sha256(raw.encode()).hexdigest()
            modules[name + ":" + digest] = {"name": name, "sha256": digest, "source": raw}
    observe(document)
    semaphore = asyncio.Semaphore(3)
    async def read(url):
        async with semaphore:
            try:
                code, source, final_url = await web.fetch_text(url, max_bytes=3_000_000, referer=entry)
                if 200 <= code < 300 and not _auth_gate(final_url, source):
                    observe(source)
            except AuthenticationError:
                raise
            except Exception:
                return
    urls = _script_urls(document, final)[:24]
    await asyncio.gather(*(read(url) for url in urls))
    root = Path(os.getenv("REMASK_DATA_DIR") or "/var/lib/remask") / "maintenance"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "ads-public-modules-candidate.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"scope": "candidate_only_not_installed",
        "modules": list(modules.values())}, ensure_ascii=False))
    temporary.replace(target)
    return {"code": "ADS_SOURCE_CANDIDATE_SAVED", "scripts": len(urls), "modules": len(modules),
            "operation_modules": sorted({row["name"] for row in modules.values()
                if row["name"].endswith((".graphql", "_facebookRelayOperation"))})[:100]}


async def run(pool):
    profile, account, business = (os.getenv(key, "").strip() for key in
        ("REMASK_LAUNCH_HTTP_AUDIT_PROFILE", "REMASK_LAUNCH_HTTP_AUDIT_ACCOUNT", "REMASK_LAUNCH_HTTP_AUDIT_BUSINESS"))
    if not profile or not all(re.fullmatch(r"\d{5,30}", value) for value in (account, business)):
        return
    # Report actual server configuration without IDs, variables or credentials.
    contracts = pool.private_launch.contracts.status()
    status = {step: {key: row.get(key) for key in ("configured", "active", "code", "friendly_name")}
              for step, row in contracts.items()}
    log.info("launch HTTP contract status browser_started=False contracts=%s", json.dumps(status))
    async with pool.profile_locks[profile]:
        try:
            async with asyncio.timeout(120):
                context = await pool.resolver.resolve(profile)
                async with ProfileSession(context, timeout_seconds=25) as session:
                    web = await session.facebook_web()
                    web.private_only = True
                    await web.bootstrap()
                    if os.getenv("REMASK_LAUNCH_HTTP_AUDIT_SOURCES") == "1":
                        source = await collect_sources(web, account=account, business=business)
                        log.info("launch HTTP source audit browser_started=False result=%s", json.dumps(source))
                    pages, funding = await asyncio.gather(inspect_pages(web, account=account, business_id=business),
                        inspect_methods(web, account=account, business_id=business))
            log.info("launch HTTP readiness profile=%s account=%s browser_started=False mutation_posts=0 result=%s",
                profile, account, json.dumps({"page_scope_verified": pages.get("account_scope_verified"),
                    "promotable_pages": len(pages.get("data", [])), "page_code": pages.get("diagnostic", {}).get("code"),
                    "payment_code": funding.get("code"), "linked_methods": len(funding.get("payment_methods", []))}))
        except Exception as exc:
            error = private_auth_error(exc) if isinstance(exc, AuthenticationError) else exc
            # Never log arbitrary exception text, which may contain Meta auth data.
            log.info("launch HTTP audit profile=%s account=%s browser_started=False mutation_posts=0 code=%s type=%s",
                profile, account, getattr(error, "code", "ADS_HTTP_AUDIT_INCONCLUSIVE"), type(exc).__name__)
