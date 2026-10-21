"""Extract literal targeting IDs from observed modules; never execute vendor JS."""
import hashlib
import json
from pathlib import Path
import re
from tree_sitter import Parser
from .private_contract_discovery import _LANGUAGE, _walk
from .contract_maintenance.ads_sources import module_records

MODULES = ('AdsAssetFeedLanguageMappingsStatic', 'AdsCountryGroupConfig.dbcigar', 'AdsCountryRegionsConfig.dbcigar')

def literal(node):
    if node.type == 'object':
        out = {}
        for pair in node.named_children:
            if pair.type == 'comment': continue
            if pair.type != 'pair': raise ValueError('Nonliteral map')
            key, val = pair.child_by_field_name('key'), pair.child_by_field_name('value')
            name = literal(key) if key.type == 'string' else key.text.decode()
            if name in out: raise ValueError('Duplicate map key')
            out[name] = literal(val)
        return out
    if node.type == 'array': return [literal(n) for n in node.named_children if n.type != 'comment']
    if node.type == 'string':
        text = node.text.decode()
        if not text.startswith('"'): raise ValueError('Unreviewed string')
        return json.loads(re.sub(r'\\x([a-fA-F0-9]{2})', r'\\u00\1', text))
    if node.type == 'number' and re.fullmatch(r'\d+', node.text.decode()): return int(node.text)
    raise ValueError('Nonliteral value')

def build(scripts):
    found, evidence = {}, {}
    for url, source in scripts:
        for row in module_records(source, selected=MODULES):
            if row['name'] not in MODULES: continue
            table = module_table(row)
            name = row['name']
            if name in found and found[name] != table: raise ValueError('Conflicting module table')
            found[name] = table
            evidence[name] = {'module':name,'module_sha256':hashlib.sha256(row['module_source'].encode()).hexdigest(),
                'script_sha256':hashlib.sha256(source.encode()).hexdigest(),'observed_public_url':url}
    return compile_tables(found, evidence)


def module_table(row):
    root = Parser(_LANGUAGE).parse(row['source'].encode()).root_node
    prop = {'AdsAssetFeedLanguageMappingsStatic':'language_codes_to_locales',
            'AdsCountryGroupConfig.dbcigar':'countryGroupsMetadata',
            'AdsCountryRegionsConfig.dbcigar':'countryRegionsMetadata'}[row['name']]
    objects = [n for n in _walk(root) if n.type == 'object' and any(
        p.type == 'pair' and (literal(p.child_by_field_name('key'))
            if p.child_by_field_name('key').type == 'string'
            else p.child_by_field_name('key').text.decode()) == prop for p in n.named_children)]
    if len(objects) != 1: raise ValueError('Missing or ambiguous literal table')
    return literal(objects[0])


def compile_tables(found, evidence):
    if set(found) != set(MODULES): raise ValueError('Missing targeting modules')
    lang = found[MODULES[0]]
    locales, ids = [], set()
    for code, values in lang['language_codes_to_locales'].items():
        name = lang['language_code_to_language_name'][code]
        if not re.fullmatch(r'[a-z]{2,3}_[A-Z]{2}',code) or not isinstance(name,str) or not name:
            raise ValueError('Invalid language code/name')
        for value in values:
            if type(value) != int or value <= 0 or value in ids: raise ValueError('Invalid locale ID')
            if lang['locales_to_language_codes'].get(str(value)) != code:
                raise ValueError('Conflicting reverse locale map')
            ids.add(value)
            locales.append({'id':str(value),'key':str(value),'name':name,'language_code':code,'type':'locales'})
    worldwide = [r for r in found[MODULES[1]]['countryGroupsMetadata'] if r['id']=='worldwide']
    if len(worldwide) != 1: raise ValueError('Missing worldwide countries')
    locations = []
    for code in worldwide[0]['countries']:
        if not re.fullmatch(r'[A-Z]{2}',code): raise ValueError('Invalid country ID')
        locations.append({'id':code,'key':code,'name':code,'type':'country','country_code':code})
    skipped = []
    for row in found[MODULES[2]]['countryRegionsMetadata']:
        if len(row['regions']) != len(row['regionIDs']) or not row['regionIDs']:
            skipped.append(row['country']); continue
        for name, key in zip(row['regions'],row['regionIDs']):
            if not re.fullmatch(r'\d+',key): raise ValueError('Invalid region ID')
            locations.append({'id':key,'key':key,'name':name,'type':'region','country_code':row['country']})
    if len({(r['type'],r['key']) for r in locations}) != len(locations): raise ValueError('Conflicting location IDs')
    if not 40 <= len(locales) <= 500: raise ValueError('Incomplete language table')
    return {'version':1,'source':'private_meta_module_reference','captured_at':'2026-10-14',
        'checked_live':False,'source_evidence':list(evidence.values()),'locales':locales,'locations':locations,
        'skipped_region_countries':skipped}


def refresh_reference(rows,*,observed_at=None):
    """Compile only complete literal maps; persist metadata, never vendor source."""
    import fcntl
    import os
    import tempfile
    import time
    from .contract_maintenance.source_observation import epoch_observation
    observed_at = epoch_observation(observed_at)
    versions, evidence = {}, {}
    for row in rows:
        name = row['name']
        if name not in MODULES: continue
        table = module_table(row)
        if name in versions and versions[name] != table: raise ValueError('Conflicting targeting maps')
        versions[name] = table
        evidence[name] = row['_targeting_evidence']
    if not versions: return
    installed = json.loads((Path(__file__).parent/'contracts/meta_targeting_reference_20261014.json').read_text())
    # Missing modules retain installed maps; observed modules are never guessed.
    # Independent groups can refresh even when another bundle is not advertised.
    language_only = set(versions) == {MODULES[0]}
    if set(versions) != set(MODULES) and not language_only: return
    if language_only:
        from copy import deepcopy
        stub = deepcopy(versions)
        stub[MODULES[1]]={'countryGroupsMetadata':[{'id':'worldwide','countries':[]}]}
        stub[MODULES[2]]={'countryRegionsMetadata':[]}
        updated=compile_tables(stub,evidence)
        groups=['locales']
    else:
        updated=compile_tables(versions,evidence);groups=['locales','locations']
        if not 100 <= len([row for row in updated['locations'] if row['type']=='country']) <= 300:
            raise ValueError('Incomplete country table')
    known = {group:{str(r['id']):r for r in installed[group]} for group in groups}
    for group in groups:
        for row in updated[group]:
            old = known[group].get(row['id'])
            if old and (row.get('language_code')==old.get('language_code') if group=='locales'
                        else row.get('type')==old.get('type') and row.get('country_code')==old.get('country_code')
                        and (row.get('type')=='country' or row.get('name')==old.get('name'))):
                # Labels are presentation; new IDs remain exactly as observed.
                row['aliases']=list(dict.fromkeys([row['name'],*old.get('aliases',[])]))
                row['name']=old['name']
                if old.get('country_name'):row['country_name']=old['country_name']
    root=Path(os.getenv('REMASK_DATA_DIR') or '/var/lib/remask');root.mkdir(parents=True,exist_ok=True)
    target=root/'private-targeting-reference.json'
    with open(str(target)+'.lock','a') as lock:
        os.chmod(str(target)+'.lock',0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        try: current=json.loads(target.read_text())
        except (OSError,ValueError):current=installed
        for group in groups:current[group]=updated[group]
        sources={row['module']:row for row in installed['source_evidence']}
        sources.update({row['module']:row for row in current.get('source_evidence',[])})
        sources.update(evidence)
        current.update(source='private_meta_module_reference',checked_live=False,
            captured_at=updated['captured_at'],refreshed_at=observed_at,source_evidence=list(sources.values()))
        fd,tmp=tempfile.mkstemp(dir=root,suffix='.tmp')
        try:
            # Only public reference IDs/labels are stored here; PHP reads the
            # same file while the embedded worker runs under its own UID.
            os.fchmod(fd,0o644)
            with os.fdopen(fd,'w') as output:json.dump(current,output,ensure_ascii=False)
            os.replace(tmp,target)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
