"""Audit public, persisted Meta country-query artifacts without executing JavaScript.

Maintenance diagnostics only. Never dispatch a query or infer account scope
from source metadata. Every recorded query must have a unique observed Relay
operation ID and a matching read-only artifact.
"""
from __future__ import annotations

import re

from ..private_contract_discovery import _module_nodes, _pairs, _walk
from ..private_inventory_queries import QueryArtifacts


def _operation_fields(selections, prefix="", depth=0):
    if depth > 24 or not isinstance(selections, list):
        return []
    fields = []
    for node in selections:
        if not isinstance(node, dict):
            continue
        kind = node.get("kind")
        if kind == "RequiredField":
            fields.extend(_operation_fields([node.get("field")], prefix, depth + 1))
            continue
        if kind in {"LinkedField", "ScalarField"}:
            name = node.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,100}", name):
                continue
            path = prefix + "." + name if prefix else name
            if name != "__typename":
                fields.append({"path": path})
            if kind == "LinkedField":
                fields.extend(_operation_fields(node.get("selections"), path, depth + 1))
        elif kind in {"InlineFragment", "ClientExtension", "Condition", "Defer", "Stream"}:
            fields.extend(_operation_fields(node.get("selections"), prefix, depth + 1))
    return fields


def public_country_query_audit(payload):
    """Describe exact public Relay country-validation queries as source evidence.

    Source names, query IDs, arguments and selected fields are reported only
    when obtained from one immutable public graphQL artifact plus its matching
    Relay operation module. The result is not an authorization to call Meta.
    """
    modules = payload.get("modules") if isinstance(payload, dict) else None
    if not isinstance(modules, list) or len(modules) > 2048:
        return {"queries": []}
    decoder = QueryArtifacts()
    for row in modules:
        source = row.get("source") if isinstance(row, dict) else None
        if isinstance(source, str) and len(source.encode("utf-8")) <= 3_000_000:
            decoder.observe(source)
    result = []
    for module_name, versions in sorted(decoder.modules.items()):
        if (len(versions) != 1 or not module_name.endswith("Query.graphql")
                or not module_name.startswith("BillingCountryVerificationUtils")):
            continue
        module = next(iter(versions.values()))
        for node in _walk(module):
            if node.type != "object":
                continue
            try:
                parts = _pairs(node)
                if not {"params", "operation"}.issubset(parts):
                    continue
                params = decoder._read(parts["params"])
                operation = decoder._read(parts["operation"])
                name = module_name.removesuffix(".graphql")
                doc_id = params.get("id")
                if (params.get("name") != name or params.get("operationKind") != "query"
                        or operation.get("name") != name or operation.get("kind") != "Operation"
                        or not isinstance(doc_id, str) or not re.fullmatch(r"[0-9]{5,40}", doc_id)):
                    continue
                arguments = operation.get("argumentDefinitions")
                if not isinstance(arguments, list) or not all(
                        isinstance(a, dict) and a.get("kind") == "LocalArgument"
                        and isinstance(a.get("name"), str) for a in arguments):
                    continue
                names = [a["name"] for a in arguments]
                if len(names) != len(set(names)):
                    continue
                fields = _operation_fields(operation.get("selections"))
                result.append({"doc_id": doc_id, "name": name, "variables": sorted(names),
                               "fields": list({f["path"]: f for f in fields}.values())})
                break
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return {"queries": result}
