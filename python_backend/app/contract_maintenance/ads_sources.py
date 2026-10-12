"""Literal advertising source evidence, without executing the module loader."""
from collections import deque
import html
import json
import re
from urllib.parse import urlsplit

from ..private_contract_discovery import _LANGUAGE, _literal, _walk
from tree_sitter import Parser

# Exact dependencies observed in the saved 2026-10-11 publication callers.
# These are capture targets, never executable or installed request contracts.
PUBLICATION_TARGETS = (
    "AdsDraftPublishDataManager", "AdsDraftPublishingManagerProvider",
    "AdsGraphAPI", "AsyncRequest", "adsDraftPublishingPublishAction",
    "adsDraftPublishingCreateFragmentAndPublishAction",
    "AdsDraftFragmentDataManager", "GraphAPI", "GraphAPICore", "GraphAPIRequestSpec", "ApiClient",
    "AdsBulkUploadImages", "AdsPEUploadUtils", "AdsWizardMediaPickerMultiUploadUtils",
    "AdsDraftFragmentDataManagerFields", "AdsDraftFragmentDataManagerUtils",
)

DEFAULT_SENDER_MODULES = PUBLICATION_TARGETS
_TRANSPORT_MODULES = {"ApiClient", "ApiBatcher", "ApiClientUtils", "CORSRequest", "UrlMap",
                      "RequestProcessorBase", "RequestProcessorDefault", "RequestConstants"}


def advertising_module_name(name):
    return isinstance(name, str) and (name in _TRANSPORT_MODULES or bool(re.match(
        r"(?:Ads|ads|useAds|BizKit.*Ads|cr:|GraphAPI|XAds|LWI|useLWI|WAWebBizAd|useWAWebBizAd|Ad(?:Account|Campaign|Creative|group|Draft|AsyncRequest|DeliveryStatus))", name)))


def public_script_url(url):
    try:
        value = urlsplit(url)
        return (value.scheme == "https" and (value.hostname or "").endswith(".fbcdn.net")
                and not value.username and not value.password and not value.port
                and value.path.endswith(".js") and not value.fragment)
    except (TypeError, ValueError):
        return False


def deferred_script_urls(document, *, preferred=()):
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
    preferred = {name: index for index, name in reversed(list(enumerate(preferred)))}
    def priority(name):
        if name in preferred:
            return (0, preferred[name])
        if "Publish" in name and "PostPublish" not in name:
            return (1, name)
        if "GraphAPI" in name:
            return (2, name)
        if "Promotable" in name:
            return (3, name)
        if "Create" in name and "SavedAudience" not in name:
            return (4, name)
        return (5, name)
    roots = sorted((name for name in components if (name in preferred or re.match(r"(?:Ads|ads|useAds|GraphAPI)", name))
                    and (name in preferred or re.search(r"Publish|Create|Draft|Promotable|Editor|Graph|Campaign|Adgroup|AdSet", name))), key=priority)
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
        dependencies = []
        for tier in tiers:
            if isinstance(tier.get("m"), list):
                dependencies.extend(value for value in tier["m"] if isinstance(value, str) and value in components)
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
        # Inspect a publication root's dependencies before unrelated roots.
        # Breadth-first traversal put its actual sender behind hundreds of UI
        # components and outside the transport's bounded script window.
        queue.extendleft(reversed(list(dict.fromkeys(dependencies))))
    return urls


def compact_record(row):
    """Retain the exact compileable wrapper without storing its body twice."""
    prefix, separator, suffix = row["module_source"].partition(row["source"])
    if not separator:
        raise ValueError("Module wrapper does not contain its observed factory")
    return {key: value for key, value in row.items() if key != "module_source"} | {
        "module_prefix": prefix, "module_suffix": suffix}


def module_source(row):
    """Reconstruct source evidence only; never evaluate it."""
    if "module_source" in row:
        return row["module_source"]
    return row["module_prefix"] + row["source"] + row["module_suffix"]


def module_records(source, *, selected=()):
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
        if name not in selected and not advertising_module_name(name):
            continue
        raw = body.text.decode()
        if len(raw) <= 180_000:
            yield {"name": name, "dependencies": dependencies, "source": raw,
                   "module_source": node.text.decode() + ";"}
