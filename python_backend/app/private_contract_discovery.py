"""Read Meta's persisted operation and its sender schema without executing JS.

A nearby doc_id is insufficient: require the exact Relay artifact and a
commitMutation config or immutable RelayHooks.useMutation sender referring to
that artifact with an explicit variables object. Dynamic fields must have a caller-supplied typed binding; unknown
expressions, spreads, computed properties and conflicting schemas fail closed.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
from urllib.parse import urljoin, urlsplit

from tree_sitter import Language, Parser
import tree_sitter_javascript

log = logging.getLogger("remask_worker")
_LANGUAGE = Language(tree_sitter_javascript.language())


def _walk(node):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.named_children))


def _literal(node):
    if node is None:
        raise ValueError("missing value")
    text = node.text.decode("utf-8")
    if node.type == "string":
        # JS single quoted literals are decoded as data, never evaluated.
        if text.startswith('"'):
            return json.loads(text)
        inner = text[1:-1].replace('"', r'\"').replace(r"\'", "'")
        return json.loads('"' + inner + '"')
    if node.type in {"true", "false", "null", "number"}:
        return json.loads(text)
    if node.type == "unary_expression" and re.fullmatch(r"-[0-9]+", text):
        return int(text)
    if node.type == "unary_expression" and re.fullmatch(r"!\s*[01]", text):
        return not bool(int(text[1:].strip()))
    raise ValueError("not a literal")


def _pairs(node):
    if node is None or node.type != "object":
        raise ValueError("object required")
    values = {}
    for child in node.named_children:
        if child.type == "comment":
            continue
        if child.type != "pair":
            raise ValueError("spread or shorthand property")
        key = child.child_by_field_name("key")
        if key.type == "property_identifier":
            name = key.text.decode()
        elif key.type == "string":
            name = _literal(key)
        else:
            raise ValueError("computed property")
        if name in values or name.startswith("__"):
            raise ValueError("duplicate or internal property")
        values[name] = child.child_by_field_name("value")
    return values


def _read(node, bindings, *, key="", resolve=None):
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    if compact in {"fbdtsg", "lsd", "cookie", "cookies", "accesstoken", "authorization", "jazoest"}:
        raise ValueError("credentials in variables")
    if resolve is not None and compact not in bindings:
        node = resolve(node)
    if node.type == "object":
        return {name: _read(value, bindings, key=name, resolve=resolve) for name, value in _pairs(node).items()}
    if node.type == "array":
        return [_read(child, bindings, resolve=resolve) for child in node.named_children
                if child.type != "comment"]
    # Bind even literal canary values in caller identity/immutable fields.
    if compact in bindings:
        value = bindings[compact]
        if node.type == "number" and isinstance(_literal(node), int) and not isinstance(_literal(node), bool):
            return int(value)
        return value
    return _literal(node)


def _binding_metadata(module):
    declarations = {}
    blocked = set()
    counts = {}
    for node in _walk(module):
        if node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name is not None:
                for child in _walk(name):
                    if child.type == "identifier":
                        counts[child.text] = counts.get(child.text, 0) + 1
            if name is not None and name.type == "identifier" and value is not None:
                declarations.setdefault(name.text, []).append(value)
        elif node.type == "formal_parameters":
            blocked.update(child.text for child in _walk(node) if child.type == "identifier")
        elif node.type in {"arrow_function", "catch_clause"}:
            parameter = node.child_by_field_name("parameter")
            if parameter is not None:
                blocked.update(child.text for child in _walk(parameter) if child.type == "identifier")
        elif node.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"}:
            left = node.child_by_field_name("left") or node.child_by_field_name("argument")
            while left is not None and left.type in {"member_expression", "subscript_expression"}:
                left = left.child_by_field_name("object")
            if left is not None and left.type == "identifier":
                blocked.add(left.text)
            elif left is not None and left.type in {"array_pattern", "object_pattern"}:
                blocked.update(child.text for child in _walk(left) if child.type == "identifier")
        elif node.type in {"function_declaration", "function_expression", "class_declaration"}:
            name = node.child_by_field_name("name")
            if name is not None:
                blocked.add(name.text)
    return declarations, blocked, counts


def _alias_resolver(module):
    """Follow a single declaration without writes or parameter shadowing."""
    declarations, blocked, counts = _binding_metadata(module)
    def resolve(node):
        seen = set()
        while node.type == "identifier":
            name = node.text
            values = declarations.get(name, [])
            if name in seen or name in blocked or counts.get(name) != 1 or len(values) != 1 or values[0].start_byte >= node.start_byte:
                raise ValueError("unresolved or mutable alias")
            seen.add(name)
            if len(seen) > 8:
                raise ValueError("alias depth")
            node = values[0]
        return node
    return resolve


def _imports(node, name, resolve):
    node = resolve(node)
    if node.type != "call_expression":
        return False
    args = node.child_by_field_name("arguments")
    return args is not None and len(args.named_children) == 1 and _literal(args.named_children[0]) == name


def _hook_configs(module, friendly, resolve):
    """Associate commit({variables}) with its exact immutable Relay hook.

    Only a literal RelayHooks import and the first array return binding count;
    similarly named helpers, reassignment and ambiguous bindings are rejected.
    """
    _, blocked, counts = _binding_metadata(module)
    hooks = {}
    for node in _walk(module):
        if node.type != "variable_declarator":
            continue
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name is None or name.type != "array_pattern" or len(name.children) < 2:
            continue
        first = name.children[1]
        if first.type != "identifier" or first.text in blocked or counts.get(first.text) != 1:
            continue
        try:
            value = resolve(value)
            if value.type != "call_expression":
                continue
            function = value.child_by_field_name("function")
            if function is None or function.type != "member_expression":
                continue
            prop = function.child_by_field_name("property")
            if prop is None or prop.type != "property_identifier" or prop.text != b"useMutation":
                continue
            if not _imports(function.child_by_field_name("object"), "RelayHooks", resolve):
                continue
            args = value.child_by_field_name("arguments")
            if args is None or len(args.named_children) != 1 or not _imports(args.named_children[0], friendly + ".graphql", resolve):
                continue
            hooks[first.text] = node.end_byte
        except (ValueError, TypeError, AttributeError):
            continue
    for node in _walk(module):
        if node.type != "call_expression":
            continue
        function = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if (function is not None and function.type == "identifier" and function.text in hooks
                and node.start_byte > hooks[function.text] and args is not None and len(args.named_children) == 1):
            yield args.named_children[0]


def _module_nodes(source):
    root = Parser(_LANGUAGE).parse(source.encode()).root_node
    result = []
    for node in _walk(root):
        if node.type != "call_expression":
            continue
        function = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if function is None or function.text != b"__d" or args is None:
            continue
        children = args.named_children
        if len(children) < 3 or children[2].has_error:
            continue
        try:
            name = _literal(children[0])
        except ValueError:
            continue
        if isinstance(name, str):
            result.append((name, children[2]))
    return result


def _export_contains(metadata, module, node):
    exports = []
    for child in _walk(module):
        if child.type != "assignment_expression":
            continue
        left = child.child_by_field_name("left")
        if left.type == "member_expression" and left.child_by_field_name("property").text == b"exports":
            exports.append(child.child_by_field_name("right"))
    if len(exports) != 1:
        return False
    value, seen = exports[0], set()
    while value.type == "identifier":
        if value.text in seen:
            return False
        seen.add(value.text)
        value = metadata._alias(value)
    return value.start_byte <= node.start_byte and value.end_byte >= node.end_byte


class WebModuleContracts:
    def __init__(self, *, friendly_names, bindings, name_filter=None):
        self.names = tuple(friendly_names)
        self.name_filter = name_filter
        self.bindings = bindings
        self.ids = {name: set() for name in self.names}
        self.payloads = {name: {} for name in self.names}
        self.evidence = set()
        self.modules = {}
        self._compiled = False

    def observe(self, source: str):
        if not source or len(source.encode()) > 8_000_000:
            return
        self._compiled = False
        if self.name_filter is not None:
            for name in re.findall(r"[\"']([A-Za-z0-9_]+)\.graphql[\"']", source):
                if name not in self.names and self.name_filter(name):
                    self.names += (name,)
                    self.ids[name] = set()
                    self.payloads[name] = {}
        for module_name, module in _module_nodes(source):
            # Keep complete generated artifacts and their sender dependencies.
            # Their persisted IDs can arrive in a later HTTP bundle.
            if (module_name.endswith("_facebookRelayOperation")
                    or module_name in {"useMutationWithReauthHandling", "BizKitSettingsCreateAdAccountModal.react"}
                    or any(friendly in module_name or friendly.encode() in module.text for friendly in self.names)):
                self.modules.setdefault(module_name, {})[hashlib.sha256(module.text).hexdigest()] = module

    def _compile(self):
        from .private_inventory_queries import QueryArtifacts
        metadata = QueryArtifacts()
        metadata.modules = self.modules
        self.ids = {name: set() for name in self.names}
        self.payloads = {name: {} for name in self.names}
        self.evidence = set()
        from .private_mutation_schema import flat_configs, flat_timezone_is_string, _resolve
        flat_string_timezone = flat_timezone_is_string(self.modules)
        flat_schemas = {}
        for friendly in self.names:
            versions = self.modules.get(friendly + ".graphql", {})
            if len(versions) != 1:
                continue
            for node in _walk(next(iter(versions.values()))):
                if node.type != "object":
                    continue
                try:
                    pairs = _pairs(node)
                    if not {"params", "operation"}.issubset(pairs):
                        continue
                    if not _export_contains(metadata, next(iter(versions.values())), node):
                        continue
                    params, operation = metadata._read(pairs["params"]), metadata._read(pairs["operation"])
                    selections = operation.get("selections", [])
                    if (params.get("name") != friendly or params.get("operationKind") != "mutation"
                            or operation.get("name") != friendly or len(selections) != 1):
                        continue
                    field = selections[0]
                    if field.get("name") != "business_settings_create_ad_account" or field.get("concreteType") != "AdAccount":
                        continue
                    arguments = field.get("args", [])
                    mapping = {a["variableName"]: a["name"] for a in arguments if a.get("kind") == "Variable"}
                    if (len(mapping) != len(arguments) or set(mapping.values()) != {
                            "business_id", "ad_account_name", "currency", "timezone_id", "end_advertiser_id", "qpl_join_id"}
                            or set(mapping) != {a["name"] for a in operation.get("argumentDefinitions", [])}):
                        continue
                    flat_schemas[friendly] = mapping
                except (ValueError, TypeError, KeyError):
                    continue
        for module_name, versions in self.modules.items():
            for module in versions.values():
                body = module.text.decode()
                if not any(module_name == friendly + ".graphql" or friendly in body for friendly in self.names):
                    continue
                resolve = _alias_resolver(module)
                for friendly in self.names:
                    if module_name == friendly + ".graphql":
                        for node in _walk(module):
                            if node.type != "object":
                                continue
                            try:
                                pairs = _pairs(node)
                                if not {"id", "name", "operationKind"}.issubset(pairs):
                                    continue
                                if not _export_contains(metadata, module, node):
                                    continue
                                if _literal(pairs["name"]) != friendly or _literal(pairs["operationKind"]) != "mutation":
                                    continue
                                doc = metadata._read(pairs["id"])
                                if isinstance(doc, str) and re.fullmatch(r"\d{5,40}", doc):
                                    self.ids[friendly].add(doc)
                                    self.evidence.add(hashlib.sha256(module.text).hexdigest())
                            except (ValueError, TypeError):
                                continue
                    if friendly + ".graphql" not in body and friendly not in body:
                        continue
                    if friendly in flat_schemas:
                        if flat_string_timezone:
                            bindings = dict(self.bindings)
                            bindings["timezoneid"] = str(bindings["timezoneid"])
                            bindings["qpljoinid"] = "private-contract-discovery"
                            for variables_node in flat_configs(self.modules, module, friendly):
                                try:
                                    variables = _read(variables_node, bindings, resolve=_resolve)
                                    mapping = flat_schemas[friendly]
                                    if set(variables) != set(mapping):
                                        continue
                                    # Certify variable names against the actual root arguments.
                                    if any(re.sub(r"[^a-z0-9]", "", key.lower()) != re.sub(r"[^a-z0-9]", "", argument)
                                            for key, argument in mapping.items()):
                                        continue
                                    encoded = json.dumps(variables, sort_keys=True, separators=(",", ":"))
                                    self.payloads[friendly][encoded] = variables
                                    for dependency in (friendly + ".graphql", friendly + "_facebookRelayOperation",
                                            friendly, module_name, "useMutationWithReauthHandling", "BizKitSettingsCreateAdAccountModal.react"):
                                        self.evidence.update(self.modules.get(dependency, {}))
                                except (ValueError, TypeError, KeyError):
                                    continue
                    configs = [(node, False) for node in _walk(module) if node.type == "object"]
                    configs.extend((node, True) for node in _hook_configs(module, friendly, resolve))
                    for node, hook in configs:
                        try:
                            pairs = _pairs(resolve(node))
                            if "variables" not in pairs or (not hook and "mutation" not in pairs):
                                continue
                            if "mutation" in pairs and not _imports(pairs["mutation"], friendly + ".graphql", resolve):
                                continue
                            variables = _read(pairs["variables"], self.bindings, resolve=resolve)
                            if not isinstance(variables, dict) or not isinstance(variables.get("input"), dict):
                                continue
                            encoded = json.dumps(variables, sort_keys=True, separators=(",", ":"))
                            self.payloads[friendly][encoded] = variables
                            self.evidence.add(hashlib.sha256(module.text).hexdigest())
                        except (ValueError, TypeError):
                            continue

    def result(self):
        if not self._compiled:
            self._compile()
            self._compiled = True
        results = []
        for name in self.names:
            if len(self.ids[name]) == 1 and len(self.payloads[name]) == 1:
                results.append({"doc_id": next(iter(self.ids[name])), "friendly_name": name,
                    "variables": next(iter(self.payloads[name].values())),
                    "source": "live_private_web_modules", "schema_source": "web_module",
                    "module_sha256": sorted(self.evidence)})
        # Two supported operations are still two different potential actions.
        return results[0] if len(results) == 1 else None


def _script_urls(body, base):
    result = []
    patterns = (r'<script[^>]+src=["\']([^"\']+)["\']',
                r'["\'](?:src|uri)["\']\s*:\s*["\']([^"\']+\.js(?:\?[^"\']*)?)["\']')
    for pattern in patterns:
        for match in re.finditer(pattern, html.unescape(body).replace(r"\/", "/"), re.I):
            url = urljoin(base, match.group(1))
            try:
                parts = urlsplit(url)
                host = parts.hostname or ""
                allowed = (host in {"www.facebook.com", "business.facebook.com"}
                    or host.endswith(".fbcdn.net"))
                if not allowed or parts.scheme != "https" or parts.port or parts.username or parts.password:
                    continue
            except ValueError:
                continue
            if url not in result:
                result.append(url)
    return result[:24]


async def discover_private_ad_account_contract(web, *, business_id, account_name, currency, timezone_id, actor_id):
    from .facebook_ad_account_create import CREATE_AD_ACCOUNT_FRIENDLY_NAMES, _validate_rewritten_capture_variables
    if not str(business_id).isdigit() or not str(actor_id).isdigit():
        return None
    values = {"businessid": str(business_id), "endadvertiserid": str(business_id),
        "name": account_name, "accountname": account_name, "adaccountname": account_name,
        "currency": currency, "currencycode": currency, "timezoneid": int(timezone_id),
        "timezone": int(timezone_id), "actorid": str(actor_id), "userid": str(actor_id),
        "clientmutationid": "private-contract-discovery"}
    observed = WebModuleContracts(friendly_names=CREATE_AD_ACCOUNT_FRIENDLY_NAMES, bindings=values,
        name_filter=lambda name: "createadaccount" in name.lower() and "mutation" in name.lower()
            and not any(word in name.lower() for word in ("delete", "remove", "request", "query", "usage")))
    entry = "https://business.facebook.com/latest/settings/ad_accounts/?business_id=" + str(business_id)
    contract = await _discover_web_modules(web, entry=entry, observed=observed, label="RK")
    if contract is None:
        return None
    validation = _validate_rewritten_capture_variables(contract["variables"], business_id=str(business_id),
        account_name=account_name, currency=currency, timezone_id=int(timezone_id))
    if not validation["ok"]:
        return None
    return {**contract, "endpoint_url": "https://business.facebook.com/api/graphql/",
        "business_id": str(business_id), "canary_name": account_name,
        "currency": currency, "timezone_id": int(timezone_id), "request_envelope": {}}


async def _discover_web_modules(web, *, entry, observed, label, prepare_document=None):
    # All operations here are GET. Mutation authorization is elsewhere.
    async with asyncio.timeout(55):
        status, body, final = await web.fetch_text(entry, max_bytes=3_000_000)
        from .private_inventory import _auth_gate
        gate = _auth_gate(final, body)
        if gate:
            from .provisioning.models import ProvisioningError
            raise ProvisioningError(gate, 'Meta contract discovery requires the profile session to be restored.', retryable=True)
        final_parts = urlsplit(final)
        if (status != 200 or final_parts.hostname not in {urlsplit(entry).hostname, "facebook.com"}
                or any(word in final_parts.path.lower() for word in ("/login", "/checkpoint"))):
            return None
        if prepare_document is not None:
            prepare_document(body)
        for script in re.findall(r"<script[^>]*>(.*?)</script>", body, flags=re.I | re.S):
            observed.observe(script)
        urls = _script_urls(body, final)
        total_bytes = len(body.encode())
        count = 0
        offset = 0
        contract = observed.result()
        while contract is None and offset < len(urls):
            remaining_slots = (40_000_000 - total_bytes) // 8_000_000
            if remaining_slots <= 0:
                break
            async def read_script(url):
                try:
                    response = await web.fetch_text(url, max_bytes=8_000_001, referer=final)
                    parts = urlsplit(response[2])
                    host = parts.hostname or ""
                    if response[0] == 200 and (host in {"www.facebook.com", "business.facebook.com"} or host.endswith(".fbcdn.net")):
                        if len(response[1].encode()) <= 8_000_000:
                            return response[1]
                except Exception:
                    pass
                return ""
            batch = await asyncio.gather(*(read_script(url) for url in urls[offset:offset + min(4, remaining_slots)]))
            offset += len(batch)
            for script in batch:
                count += 1
                total_bytes += len(script.encode())
                observed.observe(script)
            contract = observed.result()
        log.info("[%s] %s private_discovery scripts=%d bytes=%d artifacts=%d schemas=%d resolved=%s",
            getattr(getattr(web, "profile", None), "name", ""), label, count, total_bytes,
            sum(len(x) for x in observed.ids.values()), sum(len(x) for x in observed.payloads.values()), bool(contract))
    return contract


async def discover_private_fan_page_contract(web, *, name, category, bio, actor_id):
    from .fan_page_contracts import valid_page_create
    if not str(actor_id).isdigit():
        return None
    values = {"name": name, "pagename": name, "additionalprofilename": name,
        "bio": bio, "description": bio, "actorid": str(actor_id),
        "clientmutationid": "private-contract-discovery"}
    category_ids = set()
    def read_categories(body):
        # Category identity is read from response entities, never inferred from
        # the operator's label or copied from another Page's CREATE.
        def visit(value):
            if isinstance(value, dict):
                typename = str(value.get("__typename") or "").replace("_", "").lower()
                if "pagecategory" in typename and str(value.get("name") or "").strip().casefold() == category.strip().casefold():
                    ident = str(value.get("id") or "")
                    if ident.isdigit():
                        category_ids.add(ident)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        for raw in re.findall(r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>', body, re.I | re.S):
            try:
                visit(json.loads(html.unescape(raw)))
            except ValueError:
                pass
        if len(category_ids) == 1:
            ident = next(iter(category_ids))
            values.update({"categories": [ident], "categoryids": [ident],
                "categorylist": [ident], "categoryid": ident, "category": ident})
    def page_operation(friendly):
        folded = friendly.lower()
        return (("page" in folded or "additionalprofile" in folded)
            and ("create" in folded or "creation" in folded) and "mutation" in folded
            and not any(word in folded for word in ("draft", "preview", "suggest", "delete", "update")))
    observed = WebModuleContracts(friendly_names=(), bindings=values, name_filter=page_operation)
    contract = await _discover_web_modules(web, entry="https://www.facebook.com/pages/creation/",
        observed=observed, label="FP", prepare_document=read_categories)
    if contract is None or len(category_ids) != 1:
        return None
    contract["endpoint_url"] = "https://www.facebook.com/api/graphql/"
    if not valid_page_create(contract, name=name, actor_id=str(actor_id)):
        return None
    # Certify that the emitted final category IDs match the exact current label.
    input_data = contract["variables"]["input"]
    emitted = []
    for key, value in input_data.items():
        if re.sub(r"[^a-z0-9]", "", key.lower()) in {"category", "categories", "categoryid", "categoryids", "categorylist"}:
            emitted.extend(value if isinstance(value, list) else [value])
    if {str(value) for value in emitted} != category_ids:
        return None
    return contract
