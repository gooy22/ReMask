"""Offline index of literal advertising modules; never sends requests or runs JS.

Usage: python -m app.contract_maintenance.ads_evidence SOURCE_DIR --output PATH
Output is research evidence, deliberately incompatible with the launch manifest.
A persisted ID alone never enables a production operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from ..private_contract_discovery import _export_contains, _module_nodes, _pairs, _walk
from ..private_inventory_queries import QueryArtifacts


def _imports(node):
    result = set()
    for child in _walk(node):
        if child.type != "call_expression":
            continue
        function, args = child.child_by_field_name("function"), child.child_by_field_name("arguments")
        if function is None or function.type != "identifier" or args is None or len(args.named_children) != 1:
            continue
        from ..private_contract_discovery import _literal
        try:
            value = _literal(args.named_children[0])
            if isinstance(value, str):
                result.add(value)
        except ValueError:
            continue
    return sorted(result)


def _server_paths(reader, node, path="data", seen=frozenset()):
    """Describe only literal server selections, omitting client-only resolvers."""
    if node is None or len(seen) >= 32:
        return []
    try:
        if node.type == "identifier":
            identity = (node.start_byte, node.end_byte)
            if identity in seen:
                return []
            return _server_paths(reader, reader._alias(node), path, seen | {identity})
        if node.type == "array":
            return [row for child in node.named_children if child.type != "comment"
                    for row in _server_paths(reader, child, path, seen)]
        pairs = _pairs(node)
        kind = reader._read(pairs.get("kind"))
        if kind in {"ClientExtension", "RelayResolver", "RelayLiveResolver", "ClientComponent", "ClientEdgeToClientObject"}:
            return []
        if kind in {"Condition", "InlineFragment", "Defer", "Stream"}:
            return _server_paths(reader, pairs.get("selections"), path, seen)
        if kind not in {"ScalarField", "LinkedField"}:
            return []
        name = reader._read(pairs.get("name"))
        alias = reader._read(pairs["alias"]) if "alias" in pairs else None
        key = alias or name
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            return []
        current = path + "." + key
        plural = reader._read(pairs["plural"]) if "plural" in pairs else False
        row = {"path": current, "field": name, "kind": kind, "plural": bool(plural)}
        return [row] + _server_paths(reader, pairs.get("selections"), current + ("[*]" if plural else ""), seen)
    except (ValueError, TypeError, AttributeError):
        return []


def index_sources(sources):
    metadata, origins = QueryArtifacts(), {}
    total = 0
    for origin, source in sources:
        if len(source.encode()) > 8_000_000:
            raise ValueError("Source exceeds offline indexing limit")
        total += len(source.encode())
        if total > 50_000_000:
            raise ValueError("Source collection exceeds offline indexing limit")
        for name, node in _module_nodes(source):
            digest = hashlib.sha256(node.text).hexdigest()
            metadata.modules.setdefault(name, {})[digest] = node
            origins.setdefault((name, digest), []).append({"file": str(origin),
                "file_sha256": hashlib.sha256(source.encode()).hexdigest()})
    modules, operations = [], []
    for name, versions in sorted(metadata.modules.items()):
        imports = sorted({dependency for module in versions.values() for dependency in _imports(module)})
        absent = [dependency for dependency in imports if dependency not in metadata.modules]
        modules.append({"name": name, "factory_sha256": sorted(versions), "conflicting": len(versions) != 1,
                        "imports": imports, "missing_imports": absent,
                        "origins": [origin for digest in versions for origin in origins[(name, digest)]]})
        if not name.endswith(".graphql") or len(versions) != 1:
            continue
        module = next(iter(versions.values()))
        candidates = []
        for node in _walk(module):
            if node.type != "object":
                continue
            try:
                pairs = _pairs(node)
                if not {"params", "operation"} <= set(pairs) or not _export_contains(metadata, module, node):
                    continue
                params = _pairs(pairs["params"])
                friendly = metadata._read(params.get("name"))
                kind = metadata._read(params.get("operationKind"))
                doc_id = metadata._read(params.get("id"))
                operation = _pairs(pairs["operation"])
                if (friendly + ".graphql" != name or kind not in {"query", "mutation", "subscription"}
                        or metadata._read(operation.get("name")) != friendly
                        or not isinstance(doc_id, str) or not re.fullmatch(r"\d{5,40}", doc_id)):
                    continue
                providers = _imports(params["providedVariables"]) if "providedVariables" in params else []
                candidates.append({"friendly_name": friendly, "doc_id": doc_id, "operation_kind": kind,
                    "factory_sha256": next(iter(versions)), "variable_definitions": metadata._read(operation["argumentDefinitions"]),
                    "server_fields": _server_paths(metadata, operation.get("selections")),
                    "provider_imports": providers, "missing_imports": absent,
                    "request_schema_confirmed": False, "account_scope_confirmed": False,
                    "eligible_for_launch": False})
            except (ValueError, TypeError, AttributeError, KeyError):
                continue
        if len(candidates) == 1:
            operations.append(candidates[0])
    return {"schema_version": 1, "scope": "offline_source_research_only", "live_requests": 0,
            "browser_started": False, "production_contracts_enabled": 0,
            "modules": modules, "operation_candidates": operations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    files = sorted(path for path in args.source_dir.rglob("*") if path.is_file() and path.suffix in {".js", ".download"})
    if not files or len(files) > 500:
        parser.error("Provide between 1 and 500 local JavaScript source files")
    report = index_sources((str(path.relative_to(args.source_dir)), path.read_text(encoding="utf-8")) for path in files)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"modules": len(report["modules"]), "operation_candidates": len(report["operation_candidates"]),
                      "live_requests": 0, "production_contracts_enabled": 0}))


if __name__ == "__main__":
    main()
