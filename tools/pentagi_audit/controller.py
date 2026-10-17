"""Run and collect a real PentAGI flow; no mocked model or fabricated report."""
import http.cookiejar
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from probe import PATHS

ROOT = Path(__file__).resolve().parent
OUT = Path(os.environ.get('AUDIT_RESULTS', '.pentagi-results')).resolve()
OUT.mkdir(parents=True, exist_ok=True)
API = 'http://localhost:18443/api/v1'
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def api(path, data=None, method=None):
    request = urllib.request.Request(API+path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method, headers={'Content-Type': 'application/json'})
    with OPENER.open(request, timeout=45) as response:
        result = json.load(response)
    if result.get('status') == 'error' or result.get('ok') is False:
        raise RuntimeError('PentAGI API returned an error for '+path)
    return result.get('data', result)

def evidence():
    with urllib.request.urlopen('http://127.0.0.1:18081/_audit/state', timeout=10) as response:
        result=json.load(response)
    (OUT/'origin-evidence.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return sum('status' in row for row in result['requests'])

def collect(flow_id):
    for kind in ['msglogs', 'agentlogs', 'termlogs', 'toolcalls', 'tasks', 'usage']:
        try:
            query='' if kind=='usage' else '?page=1&pageSize=-1&type=init'
            result=api(f'/flows/{flow_id}/{kind}/'+query)
            (OUT/(kind+'.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        except (OSError, ValueError, RuntimeError) as error:
            (OUT/(kind+'-unavailable.json')).write_text(json.dumps({'error_type':type(error).__name__}))
    evidence()

def diagnostics(flow_id):
    """Save current service evidence and print only non-sensitive counters."""
    collect(flow_id)
    loaded=[]
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/ps', timeout=10) as response:
            loaded=[row.get('name') for row in json.load(response).get('models', [])]
    except (OSError, ValueError):
        pass
    for service in ('pentagi', 'ollama'):
        result=subprocess.run(['docker','compose','-p','remask-pentagi','-f',
            str(ROOT/'compose.yml'),'logs','--no-color','--tail=100',service],
            capture_output=True, text=True, timeout=15, cwd=ROOT)
        (OUT/(service+'-runtime.log')).write_text(result.stdout+result.stderr)
        if service=='pentagi':
            stages=re.findall(r'level=(?:info|warning|error) msg="([^"]+)"',result.stdout)
            print('PENTAGI_STAGES',json.dumps(stages[-6:]),flush=True)
    counts={}
    for kind in ('msglogs','agentlogs','termlogs','toolcalls','tasks'):
        try:
            counts[kind]=json.loads((OUT/(kind+'.json')).read_text()).get('total')
        except (OSError, ValueError, AttributeError):
            counts[kind]='unavailable'
    print('PENTAGI_DIAGNOSTICS',json.dumps({'loaded_models':loaded,'journal_counts':counts}),flush=True)

def main():
    deadline=time.monotonic()+300
    while True:
        try:
            api('/info')
            break
        except (OSError, ValueError):
            if time.monotonic()>deadline:
                raise RuntimeError('PentAGI API did not become ready')
            time.sleep(5)
    # This is the isolated disposable PentAGI instance's upstream bootstrap
    # account, never the owner's ReMask credentials. Cookies remain in memory.
    api('/auth/login', {'mail':'admin@pentagi.com','password':'admin'})
    prompts=json.loads((ROOT/'agent-prompts.json').read_text())
    for name,prompt in prompts.items():
        api('/prompts/'+name,{'prompt':prompt},method='PUT')
    (OUT/'agent-prompts.json').write_text(json.dumps(prompts,indent=2))
    providers=api('/providers/')
    choices=[row for row in providers if row.get('type')=='ollama']
    if not choices:
        raise RuntimeError('Real Ollama provider is not available in PentAGI')
    selected=choices[0]['name']
    (OUT/'provider.json').write_text(json.dumps({'name':selected,'type':'ollama',
        'model':choices[0].get('default_model'), 'mock':False}, indent=2))
    disabled=['browser','search','memorist','advice','maintenance','coder',
              'web_search','search_in_memory','search_guide','store_guide',
              'search_answer','store_answer','search_code','store_code','graphiti_search']
    result=api('/flows/', {'provider':selected, 'input':(ROOT/'scope.md').read_text(),
        'functions':{'disabled':[{'name':name} for name in disabled]}})
    flow_id=result['id']
    print('PENTAGI_FLOW_CREATED',flow_id,flush=True)
    deadline=time.monotonic()+28*60
    status=None
    next_diagnostics=0
    try:
        while time.monotonic()<deadline:
            flow=api(f'/flows/{flow_id}')
            status=flow.get('status')
            (OUT/'flow.json').write_text(json.dumps(flow, ensure_ascii=False, indent=2))
            print('PENTAGI_PROGRESS',json.dumps({'id':flow_id,'status':status,
                  'real_origin_requests':evidence()}),flush=True)
            if time.monotonic()>=next_diagnostics:
                diagnostics(flow_id)
                next_diagnostics=time.monotonic()+60
            if status in ('finished','failed'):
                break
            if status=='waiting':
                collect(flow_id)
                tasks=json.loads((OUT/'tasks.json').read_text()).get('tasks',[])
                if tasks and all(row.get('status') in ('finished','failed') for row in tasks):
                    # PentAGI normally waits for another owner task. Close this
                    # single-task audit after saving the actual agent reports.
                    api(f'/flows/{flow_id}',{'action':'finish'},method='PUT')
                    closed=api(f'/flows/{flow_id}')
                    (OUT/'flow.json').write_text(json.dumps(closed,ensure_ascii=False,indent=2))
                    status='finished' if all(row['status']=='finished' for row in tasks) and evidence()>0 else 'incomplete'
                    break
            time.sleep(20)
        if status not in ('finished','failed'):
            api(f'/flows/{flow_id}',{'action':'stop'},method='PUT')
            status='time_limit_stopped'
    finally:
        collect(flow_id)
        observed=json.loads((OUT/'origin-evidence.json').read_text())['requests']
        complete=set(PATHS).issubset({row['path'] for row in observed if 'status' in row})
        summary={'tool':'PentAGI','mock':False,'model':'qwen3:4b-instruct',
                 'flow_id':flow_id,'status':status,'real_origin_requests':evidence(),
                 'sample_complete':complete,'audit_completed':status=='finished' and complete,
                 'mode':'read-only, unauthenticated, isolated gateway',
                 'limitations':['local 4B model','no authenticated testing','origin TLS via verified gateway']}
        (OUT/'run-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print('PENTAGI_RESULT',json.dumps(summary),flush=True)
    if status!='finished' or not complete:
        raise SystemExit('PentAGI did not finish; partial evidence retained.')

if __name__=='__main__':
    main()
