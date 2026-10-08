"""Execute observed Relay query artifacts for private RK inventory.

HTML is an entry document, not a substitute for Relay's read operations.
This compiler reads literal persisted-query metadata/defaults, never evaluates
JavaScript, and never accepts a mutation as an inventory operation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from .private_contract_discovery import _literal, _module_nodes, _pairs, _script_urls, _walk

log = logging.getLogger("remask_worker")
_FUNCTIONS = {"function_expression", "arrow_function", "function_declaration"}
_ACCOUNTS = {"adaccounts", "ownedadaccounts", "clientadaccounts", "advertisingaccounts"}
_ASSETS = {"assets", "businessassets", "businesssettingsassets", "connectedobjects"}


def _compact(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _scope(node):
    while node.parent is not None and node.type not in _FUNCTIONS:
        node = node.parent
    return node


class QueryArtifacts:
    def __init__(self):
        self.modules = {}
        self.modules_scanned = 0
        self._preload_bindings = None

    def observe(self, source):
        if not source or len(source.encode()) > 3_000_000:
            return
        self._preload_bindings = None
        for name, node in _module_nodes(source):
            self.modules_scanned += 1
            # Generic UI modules must not consume the inventory budget or end
            # scanning before a later persisted query/operation-id module.
            if not (name.endswith("Query.graphql") or name.endswith("_facebookRelayOperation") or name.endswith(".entrypoint")):
                continue
            self.modules.setdefault(name, {})[hashlib.sha256(node.text).hexdigest()] = node

    def _alias(self, node):
        scope = _scope(node)
        declarations = []
        for child in _walk(scope):
            if child.type == "variable_declarator" and _scope(child) == scope:
                name = child.child_by_field_name("name")
                if name is not None and name.text == node.text:
                    declarations.append(child.child_by_field_name("value"))
            elif child.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"}:
                left = child.child_by_field_name("left") or child.child_by_field_name("argument")
                while left is not None and left.type in {"member_expression", "subscript_expression"}:
                    left = left.child_by_field_name("object")
                if left is not None and left.text == node.text:
                    raise ValueError("mutable query metadata")
        if len(declarations) != 1 or declarations[0] is None or declarations[0].start_byte >= node.start_byte:
            raise ValueError("unresolved query alias")
        return declarations[0]

    def _read(self, node, seen=frozenset()):
        if node is None or len(seen) > 16:
            raise ValueError("unresolved query metadata")
        if node.type == "unary_expression" and re.fullmatch(rb"!\s*[01]", node.text):
            return not bool(int(node.text.strip()[1:].strip()))
        if node.type == "identifier":
            scope = _scope(node)
            key = (scope.start_byte, scope.end_byte, node.text)
            if key in seen:
                raise ValueError("cyclic query metadata")
            return self._read(self._alias(node), seen | {key})
        if node.type == "object":
            return {key: self._read(value, seen) for key, value in _pairs(node).items()}
        if node.type == "array":
            return [self._read(value, seen) for value in node.named_children if value.type != "comment"]
        if node.type == "call_expression":
            args = node.child_by_field_name("arguments")
            function = node.child_by_field_name("function")
            if function is None or function.type != "identifier" or args is None or len(args.named_children) != 1:
                raise ValueError("computed query metadata")
            name = _literal(args.named_children[0])
            modules = self.modules.get(name, {})
            if len(modules) != 1 or not str(name).endswith("_facebookRelayOperation"):
                raise ValueError("unconfirmed persisted query id")
            module = next(iter(modules.values()))
            exports = []
            for child in _walk(module):
                if child.type == "assignment_expression":
                    left = child.child_by_field_name("left")
                    prop = left.child_by_field_name("property") if left is not None and left.type == "member_expression" else None
                    if prop is not None and prop.text == b"exports":
                        exports.append(self._read(child.child_by_field_name("right"), seen | {str(name)}))
            if len(exports) != 1:
                raise ValueError("ambiguous persisted query id")
            return exports[0]
        return _literal(node)

    def _preload_asset_types(self, friendly):
        """Bind only a literal route asset type flowing into this exact query.

        Current Meta: route entryPointParams.assetType -> getPreloadProps
        parameter.assetType -> variables.assetTypes. No JS is executed.
        """
        if self._preload_bindings is not None:
            return self._preload_bindings.get(friendly, {})
        def imported(node):
            if node is None or node.type != "call_expression":
                return ""
            function, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
            if function is None or function.type != "identifier" or args is None or len(args.named_children) != 1:
                return ""
            try:
                return _literal(args.named_children[0])
            except ValueError:
                return ""
        routes = set()
        for name, versions in self.modules.items():
            if not name.endswith(".entrypoint") or len(versions) != 1:
                continue
            for node in _walk(next(iter(versions.values()))):
                if node.type != "object":
                    continue
                try:
                    pairs = _pairs(node)
                    target = imported(pairs.get("entryPoint"))
                    params = _pairs(pairs.get("entryPointParams"))
                    if target.endswith(".entrypoint") and self._read(params.get("assetType")) == "AD_ACCOUNT":
                        routes.add(target)
                except (ValueError, TypeError, AttributeError):
                    continue
        bindings = {}
        for module_name in routes:
            versions = self.modules.get(module_name, {})
            if len(versions) != 1:
                continue
            for node in _walk(next(iter(versions.values()))):
                if node.type != "object":
                    continue
                try:
                    pairs = _pairs(node)
                    artifact = imported(pairs.get("parameters"))
                    if not isinstance(artifact, str) or not artifact.endswith(("$Parameters", ".graphql")):
                        continue
                    target_query = artifact.removesuffix("$Parameters").removesuffix(".graphql")
                    for name, value in _pairs(pairs.get("variables")).items():
                        if _compact(name) != "assettypes" or value.type != "array" or len(value.named_children) != 1:
                            continue
                        leaf = value.named_children[0]
                        if leaf.type == "identifier":
                            leaf = self._alias(leaf)
                        if leaf.type != "member_expression":
                            continue
                        prop, obj = leaf.child_by_field_name("property"), leaf.child_by_field_name("object")
                        parameters = _scope(leaf).child_by_field_name("parameters")
                        if (prop is not None and prop.text == b"assetType" and obj is not None
                                and parameters is not None and len(parameters.named_children) == 1
                                and parameters.named_children[0].text == obj.text):
                            bindings.setdefault(target_query, {})[name] = ["AD_ACCOUNT"]
                except (ValueError, TypeError, AttributeError):
                    continue
        self._preload_bindings = bindings
        return bindings.get(friendly, {})

    def contracts(self, business_id):
        output = {}
        for module_name, versions in self.modules.items():
            if (not module_name.endswith("Query.graphql") or len(versions) != 1
                    or not re.search(r"adaccount|businessasset|settingsasset|businessinventory", module_name, re.I)):
                continue
            module = next(iter(versions.values()))
            for node in _walk(module):
                if node.type != "object":
                    continue
                try:
                    pairs = _pairs(node)
                    if not {"params", "operation"}.issubset(pairs):
                        continue
                    params = self._read(pairs["params"])
                    operation = self._read(pairs["operation"])
                    friendly = params.get("name", "")
                    if (params.get("operationKind") != "query" or module_name != friendly + ".graphql"
                            or not re.fullmatch(r"\d{5,40}", str(params.get("id", "")))):
                        continue
                    contract = _compile_query(str(params["id"]), friendly, operation, business_id,
                        self._preload_asset_types(friendly))
                    if contract:
                        contract["module_sha256"] = next(iter(versions))
                        output.setdefault((contract["doc_id"], json.dumps(contract["variables"], sort_keys=True)), contract)
                except (ValueError, TypeError, AttributeError):
                    continue
        # Execute only a bounded set of distinct, proven read operations.
        return list(output.values())[:3]


def _compile_query(doc, friendly, operation, business, observed_variables=None):
    if not str(business).isdigit():
        return None
    if any(word in friendly.lower() for word in ("mutation", "create", "delete", "update", "usage", "permission")):
        return None
    definitions = operation.get("argumentDefinitions", [])
    variables = {}
    for row in definitions:
        if not isinstance(row, dict) or row.get("kind") != "LocalArgument" or "defaultValue" not in row:
            return None
        name = row.get("name")
        if not isinstance(name, str) or _compact(name) in {"fbdtsg", "cookie", "cookies", "lsd", "authorization", "accesstoken", "actorid", "userid", "profileid", "accountid", "adaccountid"}:
            return None
        variables[name] = row["defaultValue"]
    for name, value in (observed_variables or {}).items():
        if name in variables and _compact(name) == "assettypes" and value == ["AD_ACCOUNT"]:
            variables[name] = value
    fields, business_vars, counts = set(), set(), set()
    unsafe_arguments, asset_arguments = [], []
    def walk(value):
        if isinstance(value, dict):
            if value.get("kind") == "LinkedField":
                field = _compact(value.get("name", ""))
                fields.add(field)
                for arg in value.get("args") or []:
                    argument = _compact(arg.get("name"))
                    if field == "connectedobjects" and argument == "includediscoveryassets" and arg.get("kind") == "Variable":
                        # Inventory counts attached assets, not suggestions.
                        if arg.get("variableName") in variables:
                            variables[arg["variableName"]] = False
                    if field in _ASSETS and argument in {"assettype", "assettypes", "businessassettypes"}:
                        asset_arguments.append(variables.get(arg.get("variableName")) if arg.get("kind") == "Variable" else arg.get("value"))
                    if any(word in argument for word in ("filter", "search", "status", "after", "before")):
                        bound = variables.get(arg.get("variableName")) if arg.get("kind") == "Variable" else arg.get("value")
                        if bound not in (None, "", [], {}):
                            unsafe_arguments.append(argument)
                    if arg.get("kind") != "Variable":
                        continue
                    variable = arg.get("variableName")
                    if field in {"business", "bizkitbusiness", "businessportfolio"} and _compact(arg.get("name")) == "id":
                        business_vars.add(variable)
                    if _compact(arg.get("name")) == "first":
                        counts.add(variable)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(operation.get("selections", []))
    if unsafe_arguments or not business_vars or not business_vars.issubset(variables) or not fields.intersection(_ACCOUNTS | _ASSETS):
        return None
    if (fields.intersection(_ACCOUNTS) and not fields.intersection({"adaccounts", "advertisingaccounts"})
            and not {"ownedadaccounts", "clientadaccounts"}.issubset(fields)):
        return None
    for name in business_vars:
        default = variables[name]
        variables[name] = int(business) if isinstance(default, int) and not isinstance(default, bool) else str(business)
    for name in counts:
        if name not in variables:
            return None
        variables[name] = 100
    # An asset query needs an observed AD_ACCOUNT filter. An unfiltered generic
    # asset list cannot prove absence of RK. Other restrictive filters fail.
    asset_scope = bool(asset_arguments) and all(value == "AD_ACCOUNT" or value == ["AD_ACCOUNT"] for value in asset_arguments)
    for name, value in variables.items():
        compact = _compact(name)
        if compact in {"assettype", "assettypes", "businesstype", "businessassettypes"}:
            if value not in (None, "AD_ACCOUNT", ["AD_ACCOUNT"]):
                return None
        elif any(word in compact for word in ("filter", "search", "status")) and value not in (None, "", [], {}):
            return None
    if not fields.intersection(_ACCOUNTS) and not asset_scope:
        return None
    return {"doc_id": doc, "friendly_name": friendly, "operation_kind": "query",
        "variables": variables, "business_id": str(business), "asset_scope": asset_scope,
        "response_collection": "connected_objects" if "connectedobjects" in fields else ""}


async def read_private_inventory_queries(web, *, business_id, document, entry_url):
    """Query POSTs are reads; no CREATE intent or mutation is performed here."""
    from .private_inventory import _auth_gate, _json_payloads, _inventory_shape, _normalize_connected_inventory
    from urllib.parse import urlsplit
    diagnostic = {"phase": "private_graphql_inventory", "operation_kind": "query",
        "contracts": 0, "query_posts": 0, "query_attempts": 0, "query_names": [], "error_type": ""}
    payloads = []
    try:
        async with asyncio.timeout(65):
            contracts = getattr(web, "_private_inventory_query_cache", {}).get(str(business_id))
            if contracts is None:
                observed = QueryArtifacts()
                for script in re.findall(r"<script\b[^>]*>(.*?)</script\s*>", document, re.I | re.S):
                    observed.observe(script)
                urls = _script_urls(document, entry_url)
                total = len(document.encode())
                contracts = observed.contracts(business_id)
                fetched = 0
                for offset in range(0, len(urls), 4):
                    if contracts:
                        break
                    async def fetch(url):
                        try:
                            status, body, final = await web.fetch_text(url, max_bytes=3_000_000, referer=entry_url)
                            parts = urlsplit(final)
                            if status == 200 and parts.scheme == "https" and not (parts.port or parts.username or parts.password) and (parts.hostname in {"business.facebook.com", "www.facebook.com"}
                                    or (parts.hostname or "").endswith(".fbcdn.net")) and not _auth_gate(final, body):
                                return body
                        except Exception:
                            pass
                        return ""
                    remaining_slots = (24_000_000 - total) // 3_000_000
                    if remaining_slots <= 0:
                        break
                    batch = await asyncio.gather(*(fetch(url) for url in urls[offset:offset+min(4, remaining_slots)]))
                    fetched += len(batch)
                    for source in batch:
                        total += len(source.encode())
                        observed.observe(source)
                    contracts = observed.contracts(business_id)
                diagnostic.update(modules=len(observed.modules), modules_scanned=observed.modules_scanned,
                    scripts=fetched, bytes=total)
                if contracts:
                    cache = getattr(web, "_private_inventory_query_cache", {})
                    cache[str(business_id)] = contracts
                    web._private_inventory_query_cache = cache
            diagnostic["contracts"] = len(contracts)
            for contract in contracts:
                if (contract.get("operation_kind") != "query" or contract.get("business_id") != str(business_id)
                        or not re.fullmatch(r"\d{5,40}", str(contract.get("doc_id", "")))):
                    raise ValueError("inventory mutation rejected")
                diagnostic["query_names"].append(contract["friendly_name"])
                diagnostic["query_attempts"] += 1
                async def query_submit():
                    diagnostic["query_posts"] += 1
                result = await web.graphql(contract["doc_id"], contract["variables"],
                    friendly_name=contract["friendly_name"], endpoint_url="https://business.facebook.com/api/graphql/",
                    business_context_id=str(business_id), before_submit=query_submit)
                decoded = _json_payloads(json.dumps(result))
                diagnostic["payload_count"] = diagnostic.get("payload_count", 0) + len(decoded)
                diagnostic["inventory_shape"] = (diagnostic.get("inventory_shape", []) + _inventory_shape(decoded))[:20]
                if contract.get("response_collection") == "connected_objects":
                    decoded = _normalize_connected_inventory(decoded, str(business_id))
                payloads.append({"payloads": decoded, "asset_scope": contract["asset_scope"],
                    "has_errors": bool(result.get("errors") or result.get("error"))})
    except Exception as exc:
        diagnostic["error_type"] = type(exc).__name__
        if type(exc).__name__ == "AuthenticationError":
            diagnostic["auth_gate"] = "CHECKPOINT_REQUIRED" if "checkpoint" in str(exc).lower() else "SESSION_EXPIRED"
        if str(business_id) in getattr(web, "_private_inventory_query_cache", {}):
            web._private_inventory_query_cache.pop(str(business_id), None)
    log.info("[%s] RK private read_queries business=%s diagnostic=%s",
        getattr(getattr(web, "profile", None), "name", ""), business_id, json.dumps(diagnostic, separators=(",", ":")))
    return payloads, diagnostic
