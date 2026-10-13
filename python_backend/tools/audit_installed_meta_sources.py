"""Offline comparison of installed contracts with observed public JS sources.

Does not contact Meta, read profiles, execute JS, or install contract candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.contract_maintenance.ads_sources import module_records
from app.contract_maintenance.ads_evidence import index_sources


def audit(source_dirs, contracts_dir):
    installed = []
    for path in sorted(contracts_dir.glob("meta_*.json")):
        manifest = json.loads(path.read_text())
        for name,row in manifest.get("operations",{}).items():
            if isinstance(row,dict) and row.get("friendly_name") and row.get("doc_id"):
                installed.append({"manifest":path.name,"operation":name,
                    "revision":manifest.get("revision"),"row":row})
    names = {entry["row"]["friendly_name"] for entry in installed}
    targets = {name+suffix for name in names for suffix in (".graphql","_facebookRelayOperation")}
    files = sorted({file.resolve() for folder in source_dirs for file in folder.glob("*.js")})
    if not files or len(files) > 5000:
        raise ValueError("Provide between one and 5000 observed public source files")
    sources, origins, total = [], {}, 0
    for path in files:
        size = path.stat().st_size; total += size
        if size > 8_000_000 or total > 250_000_000:
            raise ValueError("Public source collection exceeds maintenance budget")
        source = path.read_text()
        selected = {name for name in targets if '__d("'+name+'"' in source}
        if not selected:
            continue
        script_sha = hashlib.sha256(source.encode()).hexdigest()
        for module in module_records(source,selected=selected):
            if module["name"] in selected:
                sources.append((script_sha,module["module_source"]))
                origins.setdefault(module["name"],set()).add(script_sha)
    indexed = index_sources(sources)
    candidates = {row["friendly_name"]:row for row in indexed["operation_candidates"]}
    rows = []
    for entry in installed:
        row = entry["row"]
        candidate = candidates.get(row["friendly_name"])
        result = {key:entry[key] for key in ("manifest","operation","revision")}
        result.update(friendly_name=row["friendly_name"],installed_doc_id=row["doc_id"],
            source_seen=candidate is not None,live_response_verified=False)
        if candidate:
            definitions = {arg["name"]:arg for arg in candidate["variable_definitions"]}
            existing = row.get("variables",{})
            result.update(observed_doc_id=candidate["doc_id"],doc_id_equal=candidate["doc_id"] == row["doc_id"],
                installed_variable_names=sorted(existing),source_variable_names=sorted(definitions),
                missing_argument_defaults={name:arg.get("defaultValue") for name,arg in definitions.items() if name not in existing},
                unknown_installed_arguments=sorted(set(existing)-set(definitions)),
                artifact_factory_sha256=candidate["factory_sha256"],
                script_sha256=sorted(origins[row["friendly_name"]+".graphql"]),
                server_fields=candidate["server_fields"])
        rows.append(result)
    return {"version":1,"scope":"offline_public_source_review","live_requests":0,
        "mutation_posts":0,"runtime_candidates_installed":0,"scripts_examined":len(files),
        "operations":rows,"modules":indexed["modules"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir",action="append",type=Path,required=True)
    parser.add_argument("--contracts-dir",type=Path,default=Path(__file__).resolve().parents[1]/"app/contracts")
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    result = audit(args.source_dir,args.contracts_dir)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"operations":len(result["operations"]),
        "source_seen":sum(row["source_seen"] for row in result["operations"]),"live_requests":0}))
