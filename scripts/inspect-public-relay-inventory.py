"""Inspect publicly observed Meta modules only; no profile credentials."""
import base64,concurrent.futures,json,re,urllib.request
from pathlib import Path
from app.private_contract_discovery import _module_nodes
urls=json.loads(Path("scripts/observed-rk-public-bundles.json").read_text())
assert len(urls)<=24
def fetch(pair):
 index,url=pair
 assert re.fullmatch(r"https://static\.xx\.fbcdn\.net/rsrc\.php/[A-Za-z0-9_./-]+\.js",url)
 try:
  with urllib.request.urlopen(url,timeout=20) as response:body=response.read(8_000_001)
  if len(body)>8_000_000:raise ValueError("body budget")
  return index,body.decode(),""
 except Exception as exc:return index,"",type(exc).__name__
modules={}
for index,source,error in concurrent.futures.ThreadPoolExecutor(max_workers=4).map(fetch,enumerate(urls)):
 print("BUNDLE_REPORT="+json.dumps({"index":index,"bytes":len(source.encode()),"error":error}),flush=True)
 for name,node in _module_nodes(source):modules[name]=node
selected={n for n in modules if any(x in n for x in ("BizSuiteSettingsPagesRootQuery", "BizSuiteSettingsPagesViewContainer", "BusinessAssetsListPaginationQuery", "BusinessAssetsList_assets", "BusinessAssetsGeoList_assets", "BusinessAssetsListItemName", "BizSuiteSettingsPages.entrypoint", "BizSuiteSettingsPagesViewContainer.entrypoint", "BizSuiteSettingsPages.react"))}
print("READ_CATALOG="+json.dumps(sorted(selected)),flush=True)
for name in sorted(selected):
 node=modules[name]
 if len(node.text)<160000:
  wrapped="__d("+json.dumps(name)+",[],"+node.text.decode()+");"
  print("RELAY_MODULE_BASE64="+base64.b64encode(wrapped.encode()).decode(),flush=True)
