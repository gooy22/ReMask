#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mkdir -p "$AUDIT_RESULTS"
python3 - <<'PY'
import os,secrets
from pathlib import Path
path=Path('.env')
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as stream:
    stream.write('AUDIT_DB_PASSWORD='+secrets.token_hex(24)+'\n')
    stream.write('AUDIT_COOKIE_SALT='+secrets.token_hex(36)+'\n')
names=['simple','simple_json','primary_agent','assistant','generator','refiner',
       'adviser','reflector','searcher','enricher','coder','installer','pentester']
config=''
for name in names:
    tokens=256 if name=='simple' else (512 if name=='simple_json' else
        (3072 if name in ('primary_agent','pentester','coder') else 1536))
    config+=f'{name}:\n  model: remask-audit-qwen\n  max_tokens: {tokens}\n  temperature: 0.1\n  n: 1\n'
    if name=='simple_json': config+='  json: true\n'
Path('ollama.provider.yml').write_text(config)
PY
docker build -f worker.Dockerfile -t remask-audit-worker:local .
docker compose -p remask-pentagi -f compose.yml up -d postgres ollama gateway
for attempt in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null; then break; fi
  sleep 2
done
echo 'Pulling local tool-calling model (no cloud API key).'
docker compose -p remask-pentagi -f compose.yml exec -T ollama ollama pull qwen3:4b-instruct >/tmp/remask-qwen-pull.log 2>&1 || { tail -30 /tmp/remask-qwen-pull.log; exit 1; }
docker compose -p remask-pentagi -f compose.yml exec -T ollama ollama pull nomic-embed-text >/tmp/remask-embed-pull.log 2>&1 || { tail -30 /tmp/remask-embed-pull.log; exit 1; }
python3 - <<'PY'
import urllib.request,json,os
from pathlib import Path
body={'model':'remask-audit-qwen','from':'qwen3:4b-instruct','stream':False,
      'parameters':{'num_ctx':24576,'num_predict':3072,'num_thread':4,'temperature':0.1}}
request=urllib.request.Request('http://127.0.0.1:11434/api/create',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=120) as response:
    result=json.load(response)
assert result.get('status')=='success',result
with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=20) as response:
    tags=json.load(response)
Path(os.environ['AUDIT_RESULTS'],'model-manifests.json').write_text(json.dumps(tags,indent=2))
print('REAL_LOCAL_MODEL_READY',body['model'])
request=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=json.dumps({
    'model':'remask-audit-qwen','stream':False,
    'messages':[{'role':'user','content':'Call audit_ready with status ready. Do not write prose.'}],
    'tools':[{'type':'function','function':{'name':'audit_ready','description':'Confirm readiness',
        'parameters':{'type':'object','properties':{'status':{'type':'string'}},'required':['status']}}}],
    'options':{'num_predict':192,'num_ctx':24576,'temperature':0.1}
}).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=180) as response:
    smoke=json.load(response)
calls=smoke.get('message',{}).get('tool_calls',[])
assert any(row.get('function',{}).get('name')=='audit_ready' for row in calls), 'Native tool call failed'
Path(os.environ['AUDIT_RESULTS'],'model-tool-smoke.json').write_text(json.dumps(smoke,indent=2))
print('REAL_LOCAL_MODEL_NATIVE_TOOL_CALL_PASSED')
PY
test "$(docker network inspect remask-pentagi-workers --format '{{.Internal}}')" = true
# Verify worker isolation and gateway access without sending an upstream request.
docker run --rm --network remask-pentagi-workers remask-audit-worker:local python -c '
import urllib.request,socket,json
with urllib.request.urlopen("http://audit-gateway:8080/_audit/target",timeout=10) as r:
 assert json.load(r)["max_requests"]==80
try:
 socket.create_connection(("remask-production-7cbf.up.railway.app",443),timeout=4).close()
except OSError:
 print("WORKER_DIRECT_EGRESS_BLOCKED; GATEWAY_AVAILABLE")
else:
 raise SystemExit("Worker isolation failed")
'
docker compose -p remask-pentagi -f compose.yml up -d pentagi
docker image inspect vxcontrol/pentagi:latest --format '{{json .RepoDigests}}' >"$AUDIT_RESULTS/pentagi-image-digests.json"
python3 controller.py
