"""Offline compiler for a directory of observed public scripts and URL index."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.contract_maintenance.ads_sources import module_records,public_script_url
from app.contract_maintenance.module_registry import build_registry
from app.editor_modules import compile_editor,MANIFEST,MODULES


def build(folder):
    rows=[]
    for entry in json.loads((folder/'index.json').read_text()):
        if not public_script_url(entry['url']):raise ValueError('Foreign source URL')
        source=(folder/(entry['sha']+'.js')).read_text()
        if hashlib.sha256(source.encode()).hexdigest()!=entry['sha']:raise ValueError('Source digest mismatch')
        for row in module_records(source,selected=MODULES,all_modules=True):
            row['_module_evidence']={'module':row['name'],'module_sha256':hashlib.sha256(row['module_source'].encode()).hexdigest(),
                'script_sha256':entry['sha'],'observed_public_url':entry['url']}
            rows.append(row)
    fields=json.loads((MANIFEST.parent/'ads_manager_http_20261012.json').read_text())['object_fields']
    editor=compile_editor(rows,fields);registry=build_registry(rows)
    editor['module_count']=len(registry['modules'])
    return editor,registry,rows


if __name__=='__main__':
    editor,registry,rows=build(Path(sys.argv[1]))
    MANIFEST.write_text(json.dumps(editor,indent=2,ensure_ascii=False)+'\n')
    target=MANIFEST.parent/'meta_module_catalog_20261014.json'
    target.write_text(json.dumps(registry,ensure_ascii=False,separators=(',',':'))+'\n')
    # Small reproducible fixture only for source adapters, no raw page/session.
    names=set(MODULES)
    fixture=MANIFEST.parents[2]/'tests/fixtures/meta_editor_modules_observed_20261014.js'
    seen=set();sources=[]
    for row in rows:
        if (row['name'] in names or row['name'] in editor['object_definitions']) and row['name'] not in seen:
            seen.add(row['name']);sources.append(row['module_source'])
    fixture.write_text('\n'.join(sources)+'\n')
    print('Inventoried modules:',len(registry['modules']),'conflicts:',len(registry['ambiguous']))
    print('Compiled controls:',sum(len(v) for v in editor['field_controls'].values()),'targeting families:',len(editor['targeting_types']))
