"""Inspect PUBLIC static JS from job 019f70a1. No account credentials or actions."""
import base64, concurrent.futures, hashlib, json, re, urllib.request
from pathlib import Path
from app.private_contract_discovery import _module_nodes, WebModuleContracts
urls=json.loads(Path("scripts/observed-rk-public-bundles.json").read_text())
assert len(urls)<=24
def fetch(pair):
 index,url=pair
 assert re.fullmatch(r"https://static\.xx\.fbcdn\.net/rsrc\.php/[A-Za-z0-9_./-]+\.js",url)
 try:
  with urllib.request.urlopen(url,timeout=20) as response: body=response.read(8_000_001)
  if len(body)>8_000_000: raise ValueError("body budget")
  return index,body.decode(), ""
 except Exception as exc: return index,"",type(exc).__name__
modules={}
for index,source,error in concurrent.futures.ThreadPoolExecutor(max_workers=4).map(fetch,enumerate(urls)):
 print("BUNDLE_REPORT="+json.dumps({"index":index,"bytes":len(source.encode()),"error_type":error}),flush=True)
 for name,node in _module_nodes(source): modules[name]=node
names=[n for n in modules if "adaccount" in n.lower() and any(s in n.lower() for s in ("create","creation","add"))]
print("CANDIDATE_NAMES="+json.dumps(names),flush=True)
mutations=[n for n in modules if n.endswith("Mutation.graphql")]
print("MUTATION_NAMES="+json.dumps(mutations),flush=True)
selected=[n for n in names if n.endswith(".graphql")]
related={n:node for n,node in modules.items() if n in names or any(s in node.text.decode() for s in selected)}
exported=0
for name,node in related.items():
 print("MODULE_REPORT="+json.dumps({"name":name,"bytes":len(node.text)}),flush=True)
 if exported<35 and len(node.text)<90000:
  wrapped="__d("+json.dumps(name)+",[],"+node.text.decode()+");"
  print("RELAY_MODULE_BASE64="+base64.b64encode(wrapped.encode()).decode(),flush=True)
  exported+=1
