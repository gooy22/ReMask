"""Run and collect a real PentAGI flow; no mocked model or fabricated report."""
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

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
    return len(result['requests'])

def collect(flow_id):
    for kind in ['msglogs', 'agentlogs', 'termlogs', 'toolcalls', 'tasks', 'usage']:
        try:
            result=api(f'/flows/{flow_id}/{kind}/')
            (OUT/(kind+'.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        except (OSError, ValueError, RuntimeError) as error:
            (OUT/(kind+'-unavailable.json')).write_text(json.dumps({'error_type':type(error).__name__}))
    evidence()

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
    providers=api('/providers/')
    choices=[row for row in providers if row.get('type')=='ollama']
    if not choices:
        raise RuntimeError('Real Ollama provider is not available in PentAGI')
    selected=choices[0]['name']
    (OUT/'provider.json').write_text(json.dumps({'name':selected,'type':'ollama',
        'model':choices[0].get('default_model'), 'mock':False}, indent=2))
    result=api('/flows/', {'provider':selected, 'input':(ROOT/'scope.md').read_text(),
        'functions':{'disabled':[{'name':'browser'}]}})
    flow_id=result['id']
    print('PENTAGI_FLOW_CREATED',flow_id,flush=True)
    deadline=time.monotonic()+28*60
    status=None
    try:
        while time.monotonic()<deadline:
            flow=api(f'/flows/{flow_id}')
            status=flow.get('status')
            (OUT/'flow.json').write_text(json.dumps(flow, ensure_ascii=False, indent=2))
            print('PENTAGI_PROGRESS',json.dumps({'id':flow_id,'status':status,
                  'real_origin_requests':evidence()}),flush=True)
            if status in ('finished','failed'):
                break
            time.sleep(20)
        if status not in ('finished','failed'):
            api(f'/flows/{flow_id}',{'action':'stop'},method='PUT')
            status='time_limit_stopped'
        collect(flow_id)
    finally:
        summary={'tool':'PentAGI','mock':False,'model':'qwen3:4b-instruct',
                 'flow_id':flow_id,'status':status,'real_origin_requests':evidence(),
                 'mode':'read-only, unauthenticated, isolated gateway',
                 'limitations':['local 4B model','no authenticated testing','origin TLS via verified gateway']}
        (OUT/'run-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print('PENTAGI_RESULT',json.dumps(summary),flush=True)
    if status!='finished':
        raise SystemExit('PentAGI did not finish; partial evidence retained.')

if __name__=='__main__':
    main()
