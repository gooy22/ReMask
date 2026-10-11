"""Literal advertising source evidence, without executing the module loader."""
from collections import deque
import html
import json
import re
from urllib.parse import urlsplit

from ..private_contract_discovery import _LANGUAGE, _literal, _walk
from tree_sitter import Parser


def public_script_url(url):
    try:
        value = urlsplit(url)
        return (value.scheme == "https" and (value.hostname or "").endswith(".fbcdn.net")
                and not value.username and not value.password and not value.port
                and value.path.endswith(".js") and not value.fragment)
    except (TypeError, ValueError):
        return False


def deferred_script_urls(document):
    """Follow only unambiguous resource/component maps observed in the document."""
    maps = {"rsrcMap": {}, "compMap": {}}
    conflicts = {key: set() for key in maps}
    source = html.unescape(document).replace(r"\/", "/")
    decoder = json.JSONDecoder()
    for key, target in maps.items():
        for match in re.finditer('"' + key + r'"\s*:\s*', source):
            try:
                values, _ = decoder.raw_decode(source, match.end())
            except ValueError:
                continue
            if not isinstance(values, dict):
                continue
            for name, value in values.items():
                if name in target and target[name] != value:
                    conflicts[key].add(name)
                else:
                    target[name] = value
    components, resources = maps["compMap"], maps["rsrcMap"]
    roots = sorted(name for name in components if re.match(r"(?:Ads|useAds)", name)
                   and re.search(r"Publish|Create|Draft|Promotable|Editor|Graph|Campaign|Adgroup|AdSet", name))
    queue, seen, urls = deque(roots), set(), []
    while queue and len(seen) < 4000:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)
        row = components.get(name)
        if name in conflicts["compMap"] or not isinstance(row, dict):
            continue
        tiers = [row] + [row[key] for key in ("rdfds", "rds") if isinstance(row.get(key), dict)]
        for tier in tiers:
            if isinstance(tier.get("m"), list):
                queue.extend(value for value in tier["m"] if isinstance(value, str) and value in components)
            for identity in tier.get("r", []) if isinstance(tier.get("r"), list) else []:
                if not isinstance(identity, (str, int)) or isinstance(identity, bool):
                    continue
                identity = str(identity)
                resource = resources.get(identity)
                if identity in conflicts["rsrcMap"] or not isinstance(resource, dict) or resource.get("type") != "js":
                    continue
                url = resource.get("src")
                if public_script_url(url) and url not in urls:
                    urls.append(url)
    return urls


def module_records(source):
    """Keep literal dependency lists and the __d wrapper as compileable evidence."""
    root = Parser(_LANGUAGE).parse(source.encode()).root_node
    for node in _walk(root):
        if node.type != "call_expression":
            continue
        function, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
        if function is None or function.text != b"__d" or args is None or len(args.named_children) < 3:
            continue
        name, dependencies, body = args.named_children[:3]
        while body.type == "parenthesized_expression" and len(body.named_children) == 1:
            body = body.named_children[0]
        if dependencies.type != "array" or body.type != "function_expression" or body.has_error:
            continue
        try:
            name = _literal(name)
            dependencies = [_literal(value) for value in dependencies.named_children if value.type != "comment"]
        except ValueError:
            continue
        if not isinstance(name, str) or not all(isinstance(value, str) for value in dependencies):
            continue
        if not re.match(r"(?:Ads|useAds|BizKit.*Ads|cr:)", name):
            continue
        raw = body.text.decode()
        if len(raw) <= 180_000:
            yield {"name": name, "dependencies": dependencies, "source": raw,
                   "module_source": node.text.decode() + ";"}
