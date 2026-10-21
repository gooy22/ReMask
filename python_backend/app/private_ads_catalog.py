"""Reviewed public-source routes and fields for the existing private Ads sender."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from .private_ads_draft import fail, identity as _identity

MANIFEST = Path(__file__).with_name("contracts") / "ads_manager_http_20261012.json"
ORIGIN = "https://adsmanager-graph.facebook.com"

def identity(value, label="object"):
    return _identity(value)

class AdsManagerRouteCatalog:
    def __init__(self, path=MANIFEST):
        try:
            self.data = json.loads(Path(path).read_text())
            if (self.data.get("version") != 1 or self.data.get("origin") != ORIGIN
                    or not re.fullmatch(r"v[0-9]+\.[0-9]+", self.data.get("api_version", ""))
                    or not isinstance(self.data.get("routes"), dict) or not self.data["routes"]):
                raise ValueError("Invalid private Ads Manager catalog")
            for name, row in self.data["routes"].items():
                if (not re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", name)
                        or row.get("method") not in {"GET", "POST"}
                        or not re.fullmatch(r"/(?:[A-Za-z0-9_]+|\{[a-z_]+\})(?:/(?:[A-Za-z0-9_]+|\{[a-z_]+\}))*", row.get("path", ""))
                        or not isinstance(row.get("params"), list)
                        or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", key) for key in row["params"])
                        or not isinstance(row.get("sources"), list) or not row["sources"]
                        or not re.fullmatch(r"[A-Za-z0-9_:./-]{1,180}", row.get("request_name", ""))
                        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", row.get("source_module", ""))):
                    raise ValueError("Incomplete route")
                for source in row["sources"]:
                    if any(not re.fullmatch(r"[a-f0-9]{64}", source.get(key, ""))
                           for key in ("module_sha256", "script_sha256")):
                        raise ValueError("Missing source evidence")
            self.revision = self.data["revision"]
            form = self.data.get("form_schema")
            if (not isinstance(form, dict) or form.get("source") != "reviewed_private_ads_manager_modules"
                    or not isinstance(form.get("schema"), dict) or not form.get("source_evidence")):
                raise ValueError("Missing reviewed form schema")
            for group, spec in form["schema"].items():
                if (group not in self.data["object_fields"] or not isinstance(spec.get("fields"), dict)
                        or set(spec["fields"]) - set(self.data["object_fields"][group])):
                    raise ValueError("Unreviewed form field")
            for source in form["source_evidence"]:
                if any(not re.fullmatch(r"[a-f0-9]{64}", source.get(key, ""))
                       for key in ("module_sha256", "script_sha256")):
                    raise ValueError("Missing form source evidence")
            preview = self.data.get('native_reads', {}).get('CREATIVE_PREVIEW', {})
            if (preview.get('path') != '/ads/lwi/ad_preview_generator/' or preview.get('method') != 'POST'
                    or preview.get('operation_kind') != 'read'
                    or set(preview.get('query_params', [])) != {'ad_account_id','ad_preview_format','creative_spec','is_sponsored'}
                    or not preview.get('sources')):
                raise ValueError('Unreviewed native preview route')
            for source in preview['sources']:
                if any(not re.fullmatch(r'[a-f0-9]{64}', source.get(key, ''))
                       for key in ('module_sha256','script_sha256')):
                    raise ValueError('Missing native preview source evidence')
            self.digest = hashlib.sha256(json.dumps(self.data, sort_keys=True).encode()).hexdigest()
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise fail("MANIFEST_INVALID", "Installed Ads Manager source catalog is invalid") from exc

    def route(self, name, values):
        row = self.data["routes"].get(name)
        if row is None:
            raise fail("ROUTE_REQUIRED", "The installed source catalog has no requested route")
        path = row["path"]
        for key in re.findall(r"\{([a-z_]+)\}", path):
            value = values.get(key)
            if key == "account_node":
                value = "act_" + identity(values.get("ad_account_id"), "advertising account")
            elif key == "draft_node":
                value = "addraft_" + identity(values.get("draft_id"), "draft")
            elif key == "fragment_node":
                value = "addraftfragment_" + identity(values.get("fragment_id"), "fragment")
            else:
                value = identity(value, key)
            path = path.replace("{" + key + "}", value)
        return row, "/" + self.data["api_version"] + path

    def validate_values(self, launch):
        """Use the installed UI's reviewed schema before constructing any POST.

        Field availability is still the pinned native allowlist. An enum in a
        public module is not account eligibility; this only rejects values the
        installed UI/contract never offered and preserves every valid value.
        """
        from .editor_modules import form_schema
        schema = form_schema(self)['schema']
        values = {name:launch[name] for name in ('campaign','adset','ad')}
        values['targeting'] = launch['adset'].get('targeting') or {}
        values['creative'] = launch['creative'].get('official_params') or {}
        for group, fields in values.items():
            spec = schema[group]
            for field, value in fields.items():
                kind = spec['fields'].get(field)
                choices = spec.get('enums', {}).get(field)
                label = group + '.' + field
                if choices is not None:
                    selected = value if kind and kind.startswith('list<') else [value]
                    if (not isinstance(selected,list) or any(type(v) not in {str,int} or v not in choices for v in selected)):
                        raise fail('FIELD_VALUE_UNAVAILABLE', label + ' has a value absent from the reviewed native schema')
                if kind and kind.startswith('list<') and not isinstance(value,list):
                    raise fail('FIELD_TYPE_INVALID', label + ' requires a list')
                if kind in {'unsigned int','int','integer'} and (type(value) is not int or (kind=='unsigned int' and value<0)):
                    raise fail('FIELD_TYPE_INVALID', label + ' requires an integer')
                if kind == 'bool' and type(value) is not bool:
                    raise fail('FIELD_TYPE_INVALID', label + ' requires a boolean')
                if kind == 'string' and not isinstance(value,str):
                    raise fail('FIELD_TYPE_INVALID', label + ' requires a string')

