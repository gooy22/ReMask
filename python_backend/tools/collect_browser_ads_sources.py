"""Collect only public Meta JS URLs observed in an authenticated browser's DOM.

This maintenance tool never connects to browser debugging, reads cookies,
downloads a profile document, submits an action, or installs a contract.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.contract_maintenance.ads_sources import (
    PUBLICATION_TARGETS, compact_record, deferred_script_urls, module_records,
    public_script_url,
)


async def collect(input_path, output, *, script_limit=600, byte_limit=160_000_000,
                  seconds=180, workers=6):
    evidence = json.loads(input_path.read_text())
    if evidence.get("scope") != "public_source_candidates_only":
        raise ValueError("Expected the public-only browser evidence export")
    document = json.dumps({key: evidence.get(key, {}) for key in ("compMap", "rsrcMap")})
    deferred = deferred_script_urls(document, preferred=PUBLICATION_TARGETS)
    direct = evidence.get("script_urls", [])
    if not isinstance(direct, list):
        raise ValueError("Invalid observed script list")
    # Include the loaded entry bundles before unrelated deferred UI resources.
    preferred = list(dict.fromkeys(deferred[:80] + direct + deferred[80:]))
    urls = [url for url in preferred if public_script_url(url)][:script_limit]
    output.mkdir(parents=True, exist_ok=True)
    sources = output / "public-scripts"
    sources.mkdir(exist_ok=True)
    pending = iter(urls)
    rows, modules = [], {}
    remaining, reserved = byte_limit, 0
    started = time.monotonic()
    condition = asyncio.Condition()

    async def worker(client):
        nonlocal remaining, reserved
        while True:
            async with condition:
                while remaining - reserved <= 0 and reserved:
                    await condition.wait()
                budget = min(6_000_000, remaining - reserved)
                url = next(pending, None) if budget > 0 else None
                if not url:
                    return
                reserved += budget
            consumed = budget
            row = {"url": url, "status": "UNAVAILABLE"}
            try:
                # No cookie jar, authorization, proxy, origin/referrer or env
                # configuration from the authenticated browser is copied here.
                async with client.get(url, allow_redirects=False) as response:
                    row["http_status"] = response.status
                    if response.status != 200:
                        row["status"] = "HTTP_REJECTED"
                    else:
                        parts, size = [], 0
                        async for part in response.content.iter_chunked(65_536):
                            size += len(part)
                            if size > budget:
                                row["status"] = "BODY_BUDGET_EXCEEDED"
                                break
                            parts.append(part)
                        else:
                            raw = b"".join(parts)
                            consumed = size
                            sha = hashlib.sha256(raw).hexdigest()
                            source = raw.decode("utf-8")
                            if "__d(" not in source:
                                row["status"] = "NO_MODULE_FACTORIES"
                            else:
                                (sources / (sha + ".js")).write_bytes(raw)
                                observed = await asyncio.to_thread(
                                    lambda: list(module_records(source, selected=PUBLICATION_TARGETS)))
                                row.update(status="CAPTURED", sha256=sha, bytes=size,
                                           module_names=[module["name"] for module in observed])
                                for module in observed:
                                    module_sha = hashlib.sha256(module["module_source"].encode()).hexdigest()
                                    identity = (module["name"], module_sha)
                                    if identity not in modules:
                                        modules[identity] = compact_record(module) | {
                                            "sha256": module_sha, "script_sha256": sha,
                                            "observed_url": url}
            except asyncio.CancelledError:
                row["status"] = "TIME_BUDGET_EXCEEDED"
                raise
            except Exception as exc:
                # Exception text can include URL values: retain the type only.
                row["error_type"] = type(exc).__name__
            finally:
                rows.append(row)
                async with condition:
                    remaining -= consumed
                    reserved -= budget
                    condition.notify_all()
                if len(rows) % 25 == 0:
                    print(json.dumps({"scripts": len(rows), "modules": len(modules),
                                      "charged_bytes": byte_limit - remaining}), flush=True)

    timeout = aiohttp.ClientTimeout(total=25)
    timed_out = False
    # Public CDN reads use the execution environment's outbound gateway. This
    # is separate from, and never changes, the profile-bound Meta transport.
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True,
                                    cookie_jar=aiohttp.DummyCookieJar()) as client:
        try:
            async with asyncio.timeout(seconds):
                async with asyncio.TaskGroup() as group:
                    for _ in range(workers):
                        group.create_task(worker(client))
        except TimeoutError:
            timed_out = True
    report = {"scope": "candidate_only_not_installed", "origin": "browser_public_script_export",
              "network_request_bodies_captured": False, "mutation_posts": 0,
              "capture": {"scripts_planned": len(urls), "scripts_requested": len(rows),
                          "charged_bytes": byte_limit - remaining, "timed_out": timed_out,
                          "elapsed_seconds": round(time.monotonic() - started, 2)},
              "publication_targets": {name: any(key[0] == name for key in modules)
                                      for name in PUBLICATION_TARGETS},
              "scripts": rows, "modules": list(modules.values())}
    target = output / "ads-browser-modules-candidate.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False))
    temporary.replace(target)
    print(json.dumps({"candidate": str(target), **report["capture"],
                      "publication_targets": report["publication_targets"]}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--script-limit", type=int, default=600)
    parser.add_argument("--byte-limit", type=int, default=160_000_000)
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.script_limit <= 1000 or not 1_000_000 <= args.byte_limit <= 200_000_000 or not 1 <= args.seconds <= 300:
        parser.error("Capture budgets exceed maintenance bounds")
    asyncio.run(collect(args.input, args.output, script_limit=args.script_limit,
                        byte_limit=args.byte_limit, seconds=args.seconds))
