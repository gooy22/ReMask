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

import httpx

from ..private_auth import private_auth_error
from ..private_inventory import _auth_gate
from ..private_launch_read import inspect_pages
from ..static_payment_read import inspect_methods
from ..session import ProfileSession
from ..provisioning.models import ProvisioningError
from fb_worker import AuthenticationError, RemoteRequestError

log = logging.getLogger("remask.launch_http_audit")

SCRIPT_LIMIT = 400
SCRIPT_BYTE_LIMIT = 5_000_000
CAPTURE_BYTE_LIMIT = 50_000_000


def error_diagnostic(exc):
    """Report typed failure evidence, never exception text or connection URLs."""
    auth = private_auth_error(exc) if isinstance(exc, AuthenticationError) else None
    payload = getattr(exc, "meta_payload", None)
    transport = payload.get("transport_failure", {}) if isinstance(payload, dict) else {}
    kind = transport.get("kind", "") if isinstance(transport, dict) else ""
    kind = kind if isinstance(kind, str) else ""
    codes = {"ProxyError": "PROFILE_PROXY_UNAVAILABLE",
        "ConnectError": "PRIVATE_HTTP_CONNECTION_UNAVAILABLE", "ConnectTimeout": "PRIVATE_HTTP_CONNECTION_UNAVAILABLE",
        "ReadTimeout": "PRIVATE_HTTP_TIMEOUT", "WriteTimeout": "PRIVATE_HTTP_TIMEOUT",
        "PoolTimeout": "PRIVATE_HTTP_TIMEOUT", "TimeoutError": "PRIVATE_HTTP_TIMEOUT",
        "RemoteProtocolError": "PRIVATE_HTTP_PROTOCOL_UNAVAILABLE", "LocalProtocolError": "PRIVATE_HTTP_PROTOCOL_UNAVAILABLE",
        "HTTPStatusError": "PRIVATE_HTTP_STATUS_REJECTED"}
    if not kind and type(exc).__name__ in codes:
        kind = type(exc).__name__
    code = auth.code if auth else getattr(exc, "code", "") or codes.get(kind, "ADS_HTTP_AUDIT_INCONCLUSIVE")
    stage = getattr(exc, "transport_stage", "")
    return {"code": code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,100}", code) else "ADS_HTTP_AUDIT_INCONCLUSIVE",
        "transport_kind": kind if kind in codes else "",
        "transport_stage": stage if isinstance(stage, str) and stage in {"bootstrap", "before_graphql_submit", "graphql_submit", "business_auth_precheck"} else "",
        "post_not_sent": getattr(exc, "request_may_have_been_sent", None) is False}


def inspect_saved_sources():
    """Review bounded public source evidence without another Meta request."""
    from .ads_sources import PUBLICATION_TARGETS
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
    selected.update(PUBLICATION_TARGETS)
    selection_order = list(PUBLICATION_TARGETS)
    # Exact public module names only: no path, URL, expression or runtime eval.
    requested = os.getenv("REMASK_LAUNCH_HTTP_AUDIT_MODULES", "").split(",")
    if any(value.strip() for value in requested):
        selected = {value.strip() for value in requested[:24]
                    if re.fullmatch(r"[A-Za-z0-9_.:$-]{1,180}", value.strip())}
        selection_order = [value.strip() for value in requested[:24] if value.strip() in selected]
    present = {row.get("name") for row in rows}
    report = []
    for name in sorted(selected):
        matched = [row for row in rows if row.get("name") == name]
        report.append({"name": name, "present": bool(matched), "variants": len(matched),
            "bytes": sum(len(row.get("source", "").encode()) for row in matched),
            "missing_dependencies": sorted({dependency for row in matched
                for dependency in row.get("dependencies", []) if dependency not in present})[:40]})
    log.info("launch saved public module selection=%s", json.dumps(report))
    budget = 200_000
    rank = {name: index for index, name in reversed(list(enumerate(selection_order)))}
    for row in sorted(rows, key=lambda row: (rank.get(row.get("name"), len(rank)),
                                           row.get("name", ""), row.get("sha256", ""))):
        source = row.get("source", "")
        if row.get("name") not in selected:
            continue
        if len(source.encode()) > budget:
            log.info("launch saved public module omitted=%s", json.dumps({"name": row["name"],
                "reason": "EXPORT_BUDGET", "bytes": len(source.encode())}))
            continue
        if row.get("source_kind") != "public_cdn" and re.search(r"fb_dtsg|access_token|authorization|cookie", source, re.I):
            continue
        budget -= len(source.encode())
        chunks = [source[i:i+1400] for i in range(0, len(source), 1400)]
        for index, chunk in enumerate(chunks):
            log.info("launch saved public module evidence=%s", json.dumps({
                "name": row["name"], "sha256": row.get("sha256"),
                "dependencies": row.get("dependencies", []),
                "chunk": index, "chunks": len(chunks), "source": chunk}))


async def collect_sources(web, *, account, business):
    from ..private_contract_discovery import _script_urls
    from .ads_sources import PUBLICATION_TARGETS, compact_record, deferred_script_urls, module_records, public_script_url
    preferred = [value.strip() for value in os.getenv("REMASK_LAUNCH_HTTP_AUDIT_MODULES", "").split(",")[:24]
                 if re.fullmatch(r"[A-Za-z0-9_.:$-]{1,180}", value.strip())]
    preferred = list(dict.fromkeys(preferred + list(PUBLICATION_TARGETS)))
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
    skipped = {"inline_auth": 0, "source_budget": 0}
    def observe(source, kind):
        nonlocal source_budget
        for row in module_records(source, selected=preferred):
            raw, name = row["source"], row["name"]
            # Inline document code can carry session values. CDN JavaScript is
            # public static code; references to credential *variables* there
            # are not credential values and must not hide request wrappers.
            if kind != "public_cdn" and re.search(r"fb_dtsg|access_token|authorization|cookie", raw, re.I):
                skipped["inline_auth"] += 1
                continue
            digest = hashlib.sha256(raw.encode()).hexdigest()
            identity = name + ":" + digest
            row = compact_record(row)
            cost = len(json.dumps(row).encode())
            if identity not in modules and cost <= source_budget:
                source_budget -= cost
                modules[identity] = {**row, "sha256": digest, "source_kind": kind}
            elif identity not in modules:
                skipped["source_budget"] += 1
    observe(document, "document")
    direct = [url for url in _script_urls(document, final, limit=24) if public_script_url(url)]
    deferred = deferred_script_urls(document, preferred=preferred)
    # Request the observed sender/dependency resources before generic entry UI.
    # Download workers remain bounded independently of the 400-file window.
    urls = list(dict.fromkeys(deferred + direct))[:SCRIPT_LIMIT]
    pending = iter(urls)
    downloaded = charged = reserved = 0
    attempted = failed = oversized = 0
    condition = asyncio.Condition()
    async def read_worker():
        nonlocal downloaded, charged, reserved, attempted, failed, oversized
        while True:
            async with condition:
                while CAPTURE_BYTE_LIMIT - charged - reserved <= 0 and reserved:
                    await condition.wait()
                budget = min(SCRIPT_BYTE_LIMIT, CAPTURE_BYTE_LIMIT - charged - reserved)
                if budget <= 0:
                    return
                url = next(pending, None)
                if url is None:
                    return
                reserved += budget
                attempted += 1
            # A failed/partially consumed response costs its reserved upper
            # bound; an unavailable read must not replenish the byte budget.
            consumed, received = budget, 0
            try:
                code, source, final_url = await web.fetch_text(url, max_bytes=budget, referer=entry)
                size = len(source.encode())
                received = size
                consumed = min(size, budget)
                if size > budget:
                    oversized += 1
                elif 200 <= code < 300 and public_script_url(final_url) and not _auth_gate(final_url, source):
                    observe(source, "public_cdn")
                else:
                    failed += 1
            except AuthenticationError:
                raise
            except RemoteRequestError as exc:
                # fetch_text preserves its HTTP exception as __cause__. Stop
                # the whole maintenance batch when the profile route fails.
                if isinstance(exc.__cause__, (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout)):
                    raise
                failed += 1
            except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout):
                raise
            except Exception:
                failed += 1
            finally:
                async with condition:
                    downloaded += received
                    charged += consumed
                    reserved -= budget
                    condition.notify_all()
    timed_out = False
    try:
        async with asyncio.timeout(75):
            async with asyncio.TaskGroup() as group:
                for _ in range(3):
                    group.create_task(read_worker())
    except TimeoutError:
        timed_out = True
    except ExceptionGroup as exc:
        # Preserve auth/proxy classification across structured cancellation.
        # Other worker failures are counted above instead of reaching here.
        raise exc.exceptions[0] from exc
    root = Path(os.getenv("REMASK_DATA_DIR") or "/var/lib/remask") / "maintenance"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "ads-public-modules-candidate.json"
    temporary = target.with_suffix(".tmp")
    if not modules:
        return {"code": "ADS_SOURCE_NO_MODULES", "scripts": len(urls), "modules": 0,
            "timed_out": timed_out, "scripts_requested": attempted, "scripts_failed": failed,
            "scripts_oversized": oversized, "downloaded_bytes": downloaded, "charged_bytes": charged}
    temporary.write_text(json.dumps({"scope": "candidate_only_not_installed",
        "modules": list(modules.values()), "deferred_scripts_observed": len(deferred),
        "scripts_observed": list(dict.fromkeys(direct + deferred)),
        "capture": {"scripts_planned": len(urls), "scripts_requested": attempted,
            "scripts_failed": failed, "scripts_oversized": oversized,
            "downloaded_bytes": downloaded,
            "charged_bytes": charged,
            "timed_out": timed_out, "skipped": skipped}}, ensure_ascii=False))
    temporary.replace(target)
    return {"code": "ADS_SOURCE_CANDIDATE_SAVED", "scripts": len(urls), "modules": len(modules),
            "deferred_scripts_observed": len(deferred), "downloaded_bytes": downloaded,
            "charged_bytes": charged,
            "timed_out": timed_out,
            "scripts_requested": attempted, "scripts_failed": failed,
            "scripts_oversized": oversized,
            "skipped": skipped,
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
    private = getattr(pool.private_launch, "ads_manager", None)
    contracts = private.status()["contracts"] if private is not None else pool.private_launch.contracts.status()
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
                    if os.getenv("REMASK_LAUNCH_HTTP_AUDIT_ADS_BOOTSTRAP") == "1":
                        # Read the selected-account document and account relation. Do not
                        # inspect payment settings, export credentials, collect
                        # sources or submit any advertising operation in this mode.
                        from ..private_ads_http import AdsManagerPrivateHttp
                        from ..private_ads_draft import fail, identity
                        if pool.private_launch._inventory_row(context, business, account) is None:
                            raise fail("ACCOUNT_CONTEXT_UNAVAILABLE", "Audit target is absent from profile inventory")
                        actor = identity(str(context.cookies.get("c_user") or ""))
                        client = await AdsManagerPrivateHttp.open(web, account_id=account,
                                                                 business_id=business, actor_id=actor)
                        row = await client.get("/act_" + account, {"fields": ["id", "business"]})
                        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                                or row["id"].removeprefix("act_") != account
                                or not isinstance(row.get("business"), dict)
                                or row["business"].get("id") != business):
                            raise fail("ACCOUNT_SCOPE_UNVERIFIED", "Audit read did not confirm the exact account/Business")
                        log.info("launch Ads Manager bootstrap profile=%s account=%s browser_started=False mutation_posts=0 result=%s",
                            profile, account, json.dumps({"code": "ADS_MANAGER_BOOTSTRAP_READY",
                                "account_scope_verified": True, "actor_verified": True, "account_read_checked_live": True,
                                "origin": client.context.origin, "version": client.context.version}))
                        return
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
            # Never log arbitrary exception text, which may contain Meta auth data.
            log.info("launch HTTP audit profile=%s account=%s browser_started=False mutation_posts=0 diagnostic=%s",
                profile, account, json.dumps(error_diagnostic(exc)))
