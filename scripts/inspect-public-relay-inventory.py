"""One-off analysis of PUBLIC static JS observed in job c3631e6e. No profile cookies."""
import base64
import concurrent.futures
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from app.private_contract_discovery import _module_nodes, _pairs, _walk
from app.private_inventory_queries import QueryArtifacts

urls = json.loads(Path("scripts/observed-rk-public-bundles.json").read_text())
assert len(urls) <= 24
def fetch(pair):
    index, url = pair
    assert re.fullmatch(r"https://static\.xx\.fbcdn\.net/rsrc\.php/[A-Za-z0-9_./-]+\.js", url)
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            body = response.read(3_000_001)
        if len(body) > 3_000_000:
            raise ValueError("body budget")
        return index, body.decode("utf-8"), ""
    except Exception as exc:
        return index, "", type(exc).__name__

old, all_queries = QueryArtifacts(), QueryArtifacts()
modules, exported = {}, 0
for index, source, error in concurrent.futures.ThreadPoolExecutor(max_workers=4).map(fetch, enumerate(urls)):
    print("BUNDLE_REPORT=" + json.dumps({"index": index, "bytes":len(source.encode()), "error_type":error}), flush=True)
    old.observe(source)
    for name, node in _module_nodes(source):
        if name.endswith("Query.graphql") or name.endswith("_facebookRelayOperation"):
            key=hashlib.sha256(node.text).hexdigest()
            all_queries.modules.setdefault(name, {})[key] = node
        if ("BusinessCometBizSuiteSettingsAdAccountsRootQuery" in node.text.decode()
                and not name.endswith("Query.graphql")):
            modules[name]=node
print("TOTAL_REPORT=" + json.dumps({"old_modules":len(old.modules), "old_contracts":old.contracts("1428816905866955"),
    "query_modules":len(all_queries.modules), "uncapped_contracts":all_queries.contracts("1428816905866955")}), flush=True)
for name,node in modules.items():
    report={"name":name, "bytes":len(node.text)}
    for child in _walk(node):
        if child.type != "object":
            continue
        try:
            pairs=_pairs(child)
            if not {"params","operation"}.issubset(pairs):
                continue
            report["params"]=all_queries._read(pairs["params"])
            try:
                operation=all_queries._read(pairs["operation"])
                report["arguments"]=[{"name":arg.get("name"),"kind":arg.get("kind")} for arg in operation.get("argumentDefinitions",[])]
            except Exception as exc:
                report["operation_error"]=str(exc)[:120]
                report["unary_literals"]=sorted(set(n.text.decode() for n in _walk(pairs["operation"]) if n.type=="unary_expression" and len(n.text)<10))[:8]
        except Exception as exc:
            report["metadata_error"]=str(exc)[:120]
    print("MODULE_REPORT=" + json.dumps(report), flush=True)
    if exported < 20 and len(node.text) < 70000:
        wrapped="__d(" + json.dumps(name) + ",[]," + node.text.decode() + ");"
        print("RELAY_MODULE_BASE64=" + base64.b64encode(wrapped.encode()).decode(),flush=True)
        exported+=1
