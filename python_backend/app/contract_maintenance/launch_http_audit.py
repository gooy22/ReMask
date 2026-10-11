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


def inspect_saved_sources():
    """Review bounded public source evidence without another Meta request."""
    target = Path(os.getenv("REMASK_DATA_DIR") or "/var/lib/remask") / "maintenance" / "ads-public-modules-candidate.json"
    if not target.is_file() or target.stat().st_size > 20_000_000:
        return
    rows = json.loads(target.read_text()).get("modules", [])
    filters = [value.strip() for value in os.getenv("REMASK_LAUNCH_HTTP_AUDIT_NAME_FILTERS", "").split(",")[:12]
               if re.fullmatch(r"[A-Za-z0-9_.:$-]{1,80}", value.strip())]
    relevant = sorted({row["name"] for row in rows if (any(value in row.get("name", "") for value in filters)
        if filters else re.search(r"PromotablePage|Create|Publish|Footer|(?:Graph|Batch)(?:API|Api)", row.get("name", "")))})
    for offset in range(0, min(len(relevant), 600), 75):
        log.info("launch saved public module names=%s", json.dumps(relevant[offset:offset+75]))
    selected = {"AdsPromotablePageListStoreSourceServerQuery.graphql",
        "AdsPromotablePageListStoreSourceLoadedQuery.graphql",
        "AdsPromotablePageListStoreSource", "AdsPromotablePageListStoreSourceServer",
        "AdsPromotablePageListStoreSourceFragment.graphql",
        "AdsPromotablePageListLoaderGAPI", "AdsPromotablePageListDataManager",
        "AdsPromotablePageDataProviderPlugin"}
    # Exact public module names only: no path, URL, expression or runtime eval.
    requested = os.getenv("REMASK_LAUNCH_HTTP_AUDIT_MODULES", "").split(",")
    if any(value.strip() for value in requested):
        selected = {value.strip() for value in requested[:24]
                    if re.fullmatch(r"[A-Za-z0-9_.:$-]{1,180}", value.strip())}
    budget = 60_000
    for row in rows:
        source = row.get("source", "")
        if row.get("name") not in selected or len(source) > budget:
            continue
        if row.get("source_kind") != "public_cdn" and re.search(r"fb_dtsg|access_token|authorization|cookie", source, re.I):
            continue
        budget -= len(source)
        chunks = [source[i:i+1400] for i in range(0, len(source), 1400)]
        for index, chunk in enumerate(chunks):
            log.info("launch saved public module evidence=%s", json.dumps({
                "name": row["name"], "sha256": row.get("sha256"),
                "dependencies": row.get("dependencies", []),
                "chunk": index, "chunks": len(chunks), "source": chunk}))


async def collect_sources(web, *, account, business):
    from ..private_contract_discovery import _script_urls
    from .ads_sources import deferred_script_urls, module_records, public_script_url
    entry = ("https://adsmanager.facebook.com/adsmanager/manage/campaigns"
             f"?act={account}&business_id={business}")
    status, document, final = await web.fetch_text(entry, max_bytes=5_000_000, document_navigation=True)
    gate = _auth_gate(final, document)
    if gate:
        raise ProvisioningError(gate, "Advertising source read requires a restored HTTP session.")
    if not 200 <= status < 300 or urlsplit(final).hostname not in {"adsmanager.facebook.com", "business.facebook.com"}:
        return {"code": "ADS_SOURCE_DOCUMENT_UNAVAILABLE", "http_status": status}
    modules = {}
    source_budget = 15_000_000
    def observe(source, kind):
        nonlocal source_budget
        for row in module_records(source):
            raw, name = row["source"], row["name"]
            # Inline document code can carry session values. CDN JavaScript is
            # public static code; references to credential *variables* there
            # are not credential values and must not hide request wrappers.
            if kind != "public_cdn" and re.search(r"fb_dtsg|access_token|authorization|cookie", raw, re.I):
                continue
            digest = hashlib.sha256(raw.encode()).hexdigest()
            identity = name + ":" + digest
            cost = len(json.dumps(row).encode())
            if identity not in modules and cost <= source_budget:
                source_budget -= cost
                modules[identity] = {**row, "sha256": digest, "source_kind": kind}
    observe(document, "document")
    semaphore = asyncio.Semaphore(3)
    downloaded = 0
    async def read(url):
        nonlocal downloaded
        async with semaphore:
            if downloaded >= 50_000_000:
                return
            try:
                code, source, final_url = await web.fetch_text(url, max_bytes=5_000_000, referer=entry)
                downloaded += len(source.encode())
                if 200 <= code < 300 and public_script_url(final_url) and not _auth_gate(final_url, source):
                    observe(source, "public_cdn")
            except AuthenticationError:
                raise
            except Exception:
                return
    direct = [url for url in _script_urls(document, final, limit=24) if public_script_url(url)]
    preferred = [value.strip() for value in os.getenv("REMASK_LAUNCH_HTTP_AUDIT_MODULES", "").split(",")[:24]
                 if re.fullmatch(r"[A-Za-z0-9_.:$-]{1,180}", value.strip())]
    deferred = deferred_script_urls(document, preferred=preferred)
    urls = list(dict.fromkeys(direct + deferred))[:80]
    timed_out = False
    try:
        async with asyncio.timeout(75):
            await asyncio.gather(*(read(url) for url in urls))
    except TimeoutError:
        timed_out = True
    root = Path(os.getenv("REMASK_DATA_DIR") or "/var/lib/remask") / "maintenance"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "ads-public-modules-candidate.json"
    temporary = target.with_suffix(".tmp")
    if not modules:
        return {"code": "ADS_SOURCE_NO_MODULES", "scripts": len(urls), "modules": 0, "timed_out": timed_out}
    temporary.write_text(json.dumps({"scope": "candidate_only_not_installed",
        "modules": list(modules.values()), "deferred_scripts_observed": len(deferred)}, ensure_ascii=False))
    temporary.replace(target)
    return {"code": "ADS_SOURCE_CANDIDATE_SAVED", "scripts": len(urls), "modules": len(modules),
            "deferred_scripts_observed": len(deferred), "downloaded_bytes": downloaded,
            "timed_out": timed_out,
            "operation_modules": sorted({row["name"] for row in modules.values()
                if row["name"].endswith((".graphql", "_facebookRelayOperation"))})[:100]}


async def run(pool):
    if os.getenv("REMASK_LAUNCH_HTTP_AUDIT_SAVED") == "1":
        try:
            inspect_saved_sources()
        except Exception as exc:
            log.info("launch saved public source read type=%s", type(exc).__name__)
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
                        if os.getenv("REMASK_LAUNCH_HTTP_AUDIT_SAVED") == "1":
                            inspect_saved_sources()
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
