"""Compile and refresh public Meta module metadata without running vendor code.

Module inventory is comprehensive; editable bindings are explicit source-backed
adapters. New code is inventoried, never treated as permission to publish.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import time
from tree_sitter import Parser
from .private_contract_discovery import _LANGUAGE, _walk
from .targeting_reference import literal

MANIFEST = Path(__file__).parent/'contracts/meta_editor_modules_20261014.json'
# Exact fields and enum modules observed in Ads Manager. Primitive types describe
# literal payloads; object IDs retain the source sender's id/name representation.
BINDINGS = (
    ('campaign','objective','AdsAPIObjectives','enum'),
    ('campaign','buying_type','AdsBuyingTypes','enum'),
    ('campaign','bid_strategy','AdsAPIBidStrategies','enum'),
    ('campaign','special_ad_categories','AdsRegulatedCategory','list<enum>'),
    ('campaign','pacing_type','AdsAPIPacingType','list<enum>'),
    ('adset','pacing_type','AdsAPIPacingType','list<enum>'),
    ('adset','optimization_sub_event','AdsAPIOptimizationSubEvent','enum'),
    ('adset','optimization_goal','AdsAPIOptimizationGoals','enum'),
    ('adset','billing_event','AdsAPIBillingEvents','enum'),
    ('adset','bid_strategy','AdsAPIBidStrategies','enum'),
    ('adset','destination_type','AdCampaignDestination','enum'),
    ('targeting','publisher_platforms','AdsAPIPublisherPlatform','list<enum>'),
    ('targeting','facebook_positions','AdsAPIFacebookPosition','list<enum>'),
    ('targeting','instagram_positions','AdsAPIInstagramPosition','list<enum>'),
    ('targeting','messenger_positions','AdsAPIMessengerPosition','list<enum>'),
    ('targeting','audience_network_positions','AdsAPIAudienceNetworkPosition','list<enum>'),
    ('targeting','threads_positions','AdsAPIThreadsPosition','list<enum>'),
    ('targeting','whatsapp_positions','AdsAPIWhatsAppPosition','list<enum>'),
    ('targeting','oculus_positions','AdsAPIOculusPosition','list<enum>'),
    ('targeting','streaming_services_positions','AdsAPIStreamingServicesPosition','list<enum>'),
    ('targeting','device_platforms','AdsAPIDevicePlatform','list<enum>'),
    ('targeting','app_install_state','AdsAPITargetAppInstallState','enum'),
    ('targeting','wireless_carrier','AdsWirelessCarriers','list<enum>'),
    ('targeting','education_statuses','AdsTargetingEducation','list<unsigned int>'),
    ('targeting','relationship_statuses','AdsTargetingRelationship','list<unsigned int>'),
    ('creative','applink_treatment','AdsApplinkTreatments','enum'),
)
EXTRA_MODULES = ('AdsFlexibleTargetingConstants','AdsTargetingDevices.dbcigar',
                 'AppAdsConfig.dbcigar','AdsUserOSUtils','AdsBrandSafetyContentFilterLevelConstants',
                 'AdsBrandSafetyExcludeContentType')
MODULES = tuple(dict.fromkeys([v[2] for v in BINDINGS] + list(EXTRA_MODULES)))


def property_table(row, key):
    root = Parser(_LANGUAGE).parse(row['source'].encode()).root_node
    values = []
    for node in _walk(root):
        if node.type != 'pair': continue
        field = node.child_by_field_name('key')
        name = literal(field) if field.type == 'string' else field.text.decode()
        if name == key: values.append(literal(node.child_by_field_name('value')))
    if len(values) != 1: raise ValueError('Ambiguous module property')
    return values[0]


def enum_table(row):
    root = Parser(_LANGUAGE).parse(row['source'].encode()).root_node
    tables = []
    for node in _walk(root):
        if node.type != 'object': continue
        try: value = literal(node)
        except ValueError: continue
        if value and all(re.fullmatch(r'[A-Z0-9][A-Z0-9_]*',k) and
            (isinstance(v,str) and 0 < len(v) <= 160 or type(v) is int and 0 <= v <= 100)
            for k,v in value.items()): tables.append(value)
    if len(tables) != 1: raise ValueError('Ambiguous enum module')
    return tables[0]


def evidence(row):
    return row['_module_evidence']


def compile_editor(rows, object_fields):
    versions = {}
    for row in rows:
        if row['name'] not in MODULES and not row['name'].endswith('Fields'): continue
        versions.setdefault(row['name'], {})[hashlib.sha256(row['source'].encode()).hexdigest()] = row
    unique = {n:next(iter(v.values())) for n,v in versions.items() if len(v)==1}
    schema, controls, references, sources = {}, {}, {}, {}
    definitions={}
    for name,row in unique.items():
        if not name.endswith('Fields'):continue
        try: table=enum_table(row)
        except ValueError:continue
        if all(isinstance(v,str) and re.fullmatch(r'[a-z][a-z0-9_]*',v) for v in table.values()):
            definitions[name]=list(dict.fromkeys(table.values()))
            sources[name]=evidence(row)
    for group,field,module,kind in BINDINGS:
        if module not in unique or field not in object_fields[group]: continue
        row = unique[module]
        table = enum_table(row)
        values = list(dict.fromkeys(table.values()))
        if kind == 'list<unsigned int>' and any(type(v) is not int or v < 1 for v in values):
            raise ValueError('Invalid numeric enum')
        schema.setdefault(group,{'fields':{},'enums':{}})['fields'][field]=kind
        schema[group]['enums'][field]=values
        if kind.startswith('list<'):
            controls.setdefault(group,{})[field]={'kind':'choices','representation':'numbers' if kind=='list<unsigned int>' else 'strings',
                'options':[{'id':str(v),'name':k.replace('_',' ').title(),'value':v} for k,v in table.items()]}
        sources[module]=evidence(row)
    module='AdsFlexibleTargetingConstants'
    types={}
    if module in unique:
        # Select the literal type->path map; never read MOCK_DATA or sample IDs.
        root=Parser(_LANGUAGE).parse(unique[module]['source'].encode()).root_node
        maps=[]
        for node in _walk(root):
            if node.type!='object':continue
            try: table=literal(node)
            except ValueError:continue
            if {'interests','behaviors','education_schools','work_positions'} <= set(table) and all(
                isinstance(v,list) and v and all(isinstance(s,str) for s in v) for v in table.values()):maps.append(table)
        if len(maps)!=1:raise ValueError('Detailed targeting map unavailable')
        types={k:v for k,v in maps[0].items() if k in object_fields['targeting']}
        for field,path in types.items():
            schema.setdefault('targeting',{'fields':{},'enums':{}})['fields'].setdefault(field,'JSON')
            if field in {'education_statuses','relationship_statuses'}:continue
            controls.setdefault('targeting',{})[field]={'kind':'lookup','lookup':field,
                'representation':'numbers' if field in {'college_years','interested_in'} else 'objects','path':path}
        sources[module]=evidence(unique[module])
    module='AdsTargetingDevices.dbcigar'
    if module in unique:
        devices=property_table(unique[module],'allDevices')
        if not isinstance(devices,list) or not 100 <= len(devices) <= 10000 or any(
            not isinstance(d,str) or not 0<len(d)<=160 for d in devices):raise ValueError('Invalid device catalog')
        devices=list(dict.fromkeys(devices))
        references['user_device']=[{'id':d,'name':d} for d in devices]
        for field in ('user_device','excluded_user_device'):
            controls.setdefault('targeting',{})[field]={'kind':'choices','lookup':'user_device','representation':'strings',
                'options':[{'id':d,'name':d,'value':d} for d in devices]}
            schema.setdefault('targeting',{'fields':{},'enums':{}})['fields'][field]='list<string>'
        sources[module]=evidence(unique[module])
    module='AppAdsConfig.dbcigar'
    if module in unique and 'AdsUserOSUtils' in unique:
        # toUserOSString in the reviewed sender encodes minimum versions this way.
        helper=unique['AdsUserOSUtils']['source']
        if not all(s in helper for s in ('"_ver_"','"_and_above"','"_to_"')):raise ValueError('Changed OS encoder')
        systems=property_table(unique[module],'mobileOsVersions')
        options=[]
        for system,versions in systems.items():
            if not isinstance(versions,list) or not versions or any(not isinstance(v,str) or not re.fullmatch(r'\d+(?:\.\d+){0,2}',v) for v in versions):
                raise ValueError('Invalid OS versions')
            options.append({'id':system,'name':system,'value':system})
            options += [{'id':system+'_ver_'+v+'_and_above','name':system+' '+v+' и новее','value':system+'_ver_'+v+'_and_above'} for v in versions]
        controls.setdefault('targeting',{})['user_os']={'kind':'choices','representation':'strings','options':options}
        schema.setdefault('targeting',{'fields':{},'enums':{}})['fields']['user_os']='list<string>'
        sources[module]=evidence(unique[module]);sources['AdsUserOSUtils']=evidence(unique['AdsUserOSUtils'])
    module='AdsBrandSafetyContentFilterLevelConstants'
    if module in unique:
        root=Parser(_LANGUAGE).parse(unique[module]['source'].encode()).root_node
        options=[]
        for node in _walk(root):
            if node.type!='object':continue
            try:table=literal(node)
            except ValueError:continue
            if table and all(k==v and re.fullmatch(r'(FACEBOOK|AN|FEED)_(RELAXED|STANDARD|STRICT|SELECT)',k) for k,v in table.items()):
                options += [{'id':v,'name':v,'value':v} for v in table.values()]
        if not options:raise ValueError('Missing brand safety levels')
        controls.setdefault('targeting',{})['brand_safety_content_filter_levels']={'kind':'choices','representation':'strings','options':options}
        schema.setdefault('targeting',{'fields':{},'enums':{}})['fields']['brand_safety_content_filter_levels']='list<string>'
        sources[module]=evidence(unique[module])
    return {'version':1,'source':'reviewed_private_ads_manager_modules','captured_at':'2026-10-14',
        'checked_live':False,'schema':schema,'field_controls':controls,'targeting_types':types,
        'references':references,'object_definitions':definitions,'source_evidence':list(sources.values())}


@lru_cache(maxsize=1)
def installed_editor():
    return json.loads(MANIFEST.read_text())


def editor_snapshot():
    installed=deepcopy(installed_editor())
    try:
        path=Path(os.getenv('REMASK_DATA_DIR') or '/var/lib/remask')/'private-editor-modules.json'
        fresh=json.loads(path.read_text())
        if fresh.get('version')==1 and fresh.get('source')==installed['source'] and 0<=time.time()-fresh['refreshed_at']<=1800:
            for key in ('schema','field_controls','references','object_definitions','source_evidence'):
                installed[key]=fresh[key]
    except (OSError,ValueError,KeyError,TypeError):pass
    return installed


def form_schema(catalog):
    form=deepcopy(catalog.data['form_schema']); editor=editor_snapshot()
    for group,row in editor['schema'].items():
        for field,kind in row['fields'].items():
            if field in catalog.data['object_fields'][group]:form['schema'][group]['fields'][field]=kind
        form['schema'][group]['enums'].update(row['enums'])
    form['field_controls']=editor['field_controls'];form['targeting_types']=editor['targeting_types']
    form['placement_options']={k:v for k,v in form['schema']['targeting']['enums'].items() if k.endswith('_positions') or k in {'publisher_platforms','device_platforms'}}
    form['object_definitions']=editor.get('object_definitions',{})
    form['module_catalog']={'module_count':editor['module_count'],'captured_at':editor['captured_at']}
    sources={s['module']:s for s in form['source_evidence']};sources.update({s['module']:s for s in editor['source_evidence']})
    form['source_evidence']=list(sources.values())
    form['source_digest']=hashlib.sha256(json.dumps(form,sort_keys=True).encode()).hexdigest()
    return form


def persist_public(name,data):
    import fcntl,tempfile
    root=Path(os.getenv('REMASK_DATA_DIR') or '/var/lib/remask');root.mkdir(parents=True,exist_ok=True)
    target=root/name
    with open(str(target)+'.lock','a') as lock:
        os.chmod(str(target)+'.lock',0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        if callable(data):
            try:existing=json.loads(target.read_text())
            except (OSError,ValueError):existing=None
            data=data(existing)
        fd,tmp=tempfile.mkstemp(dir=root,suffix='.tmp')
        try:
            os.fchmod(fd,0o644)
            with os.fdopen(fd,'w') as output:json.dump(data,output,ensure_ascii=False,separators=(',',':'))
            os.replace(tmp,target)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)


def refresh_editor(rows,object_fields):
    updated=compile_editor(rows,object_fields)
    if not updated['source_evidence']:return
    def merge(saved):
        current=saved if isinstance(saved,dict) and saved.get('version')==1 else deepcopy(installed_editor())
        for group,row in updated['schema'].items():
            current['schema'].setdefault(group,{'fields':{},'enums':{}})
            for key in ('fields','enums'):current['schema'][group][key].update(row[key])
        for group,row in updated['field_controls'].items():current['field_controls'].setdefault(group,{}).update(row)
        current['references'].update(updated['references'])
        current.setdefault('object_definitions',{}).update(updated['object_definitions'])
        sources={s['module']:s for s in current['source_evidence']};sources.update({s['module']:s for s in updated['source_evidence']})
        current.update(source_evidence=list(sources.values()),refreshed_at=int(time.time()))
        return current
    persist_public('private-editor-modules.json',merge)
