"""Inventory every observed module and its dependencies; never execute a factory."""
import hashlib
import json
import os
from pathlib import Path
import time
from ..editor_modules import persist_public

MANIFEST=Path(__file__).parent.parent/'contracts/meta_module_catalog_20261014.json'


def module_metadata(row):
    proof=row['_module_evidence']
    return {'sha256':hashlib.sha256(row['module_source'].encode()).hexdigest(),
        'dependencies':list(dict.fromkeys(row['dependencies'])),
        'script_sha256':proof['script_sha256'],'observed_public_url':proof['observed_public_url']}


def build_registry(rows):
    modules={};ambiguous=set()
    for row in rows:
        name=row['name'];record=module_metadata(row)
        if name in modules and modules[name]['sha256']!=record['sha256']:
            ambiguous.add(name)
        else:modules[name]=record
    return {'version':1,'captured_at':'2026-10-14','source':'observed_private_meta_modules',
        'modules':modules,'ambiguous':sorted(ambiguous),'checked_live':False,
        'policy':'inventory_all_execute_installed_adapters'}


def refresh_registry(rows):
    if not rows:return
    updated=build_registry(rows)
    def merge(saved):
        current=saved if isinstance(saved,dict) and saved.get('version')==1 and isinstance(saved.get('modules'),dict) else json.loads(MANIFEST.read_text())
        current['modules'].update(updated['modules'])
        # Unknown modules and conflicts never become executable contracts.
        current['ambiguous']=sorted(set(current.get('ambiguous',[])) | set(updated['ambiguous']))
        if len(current['modules'])>30000:raise ValueError('Module inventory budget exceeded')
        current['refreshed_at']=int(time.time())
        return current
    persist_public('private-module-catalog.json',merge)
