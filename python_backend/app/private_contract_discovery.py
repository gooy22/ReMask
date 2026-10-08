"""Read Meta's persisted operation and its sender schema without executing JS.

A nearby doc_id is insufficient: require the exact Relay artifact and a
commitMutation config referring to that artifact with an explicit variables
object. Dynamic fields must have a caller-supplied typed binding; unknown
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


def _alias_resolver(module):
    """Follow only a single declaration with no write or parameter shadowing.

    This supports Relay's imported artifact / input-object aliases without
    executing code or treating a computed runtime object as observed schema.
    """
    declarations = {}
    blocked = set()
    for node in _walk(module):
        if node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name is not None and name.type == "identifier" and value is not None:
                declarations.setdefault(name.text, []).append(value)
        elif node.type == "formal_parameters":
            blocked.update(child.text for child in _walk(node) if child.type == "identifier")
        elif node.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"}:
            left = node.child_by_field_name("left") or node.child_by_field_name("argument")
            while left is not None and left.type in {"member_expression", "subscript_expression"}:
                left = left.child_by_field_name("object")
            if left is not None and left.type == "identifier":
                blocked.add(left.text)
    def resolve(node):
        seen = set()
        while node.type == "identifier":
            name = node.text
            values = declarations.get(name, [])
            if name in seen or name in blocked or len(values) != 1 or values[0].start_byte >= node.start_byte:
                raise ValueError("unresolved or mutable alias")
            seen.add(name)
            if len(seen) > 8:
                raise ValueError("alias depth")
            node = values[0]
        return node
    return resolve


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


class WebModuleContracts:
    def __init__(self, *, friendly_names, bindings, name_filter=None):
        self.names = tuple(friendly_names)
        self.name_filter = name_filter
        self.bindings = bindings
        self.ids = {name: set() for name in self.names}
        self.payloads = {name: {} for name in self.names}
        self.evidence = set()

    def observe(self, source: str):
        if not source or len(source.encode()) > 2_000_000:
            return
        if self.name_filter is not None:
            for name in re.findall(r"[\"']([A-Za-z0-9_]+)\.graphql[\"']", source):
                if name not in self.names and self.name_filter(name):
                    self.names += (name,)
                    self.ids[name] = set()
                    self.payloads[name] = {}
        for module_name, module in _module_nodes(source):
            body = module.text.decode()
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
                            if _literal(pairs["name"]) != friendly or _literal(pairs["operationKind"]) != "mutation":
                                continue
                            doc = _literal(pairs["id"])
                            if isinstance(doc, str) and re.fullmatch(r"\d{5,40}", doc):
                                self.ids[friendly].add(doc)
                                self.evidence.add(hashlib.sha256(module.text).hexdigest())
                        except (ValueError, TypeError):
                            continue
                if friendly + ".graphql" not in body:
                    continue
                for node in _walk(module):
                    if node.type != "object":
                        continue
                    try:
                        pairs = _pairs(node)
                        if not {"mutation", "variables"}.issubset(pairs):
                            continue
                        mutation = resolve(pairs["mutation"])
                        if mutation.type != "call_expression":
                            continue
                        arguments = mutation.child_by_field_name("arguments")
                        if arguments is None or len(arguments.named_children) != 1:
                            continue
                        if _literal(arguments.named_children[0]) != friendly + ".graphql":
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
        status, body, final = await web.fetch_text(entry, max_bytes=2_000_000)
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
        for offset in range(0, len(urls), 4):
            remaining_slots = (16_000_000 - total_bytes) // 2_000_000
            if remaining_slots <= 0:
                break
            async def read_script(url):
                try:
                    response = await web.fetch_text(url, max_bytes=2_000_000, referer=final)
                    parts = urlsplit(response[2])
                    host = parts.hostname or ""
                    if response[0] == 200 and (host in {"www.facebook.com", "business.facebook.com"} or host.endswith(".fbcdn.net")):
                        return response[1]
                except Exception:
                    pass
                return ""
            batch = await asyncio.gather(*(read_script(url) for url in urls[offset:offset + min(4, remaining_slots)]))
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
