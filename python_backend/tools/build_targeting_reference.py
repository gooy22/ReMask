"""Rebuild installed targeting IDs from downloaded public module evidence."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.targeting_reference import build

if __name__ == '__main__':
    folder, output = map(Path,sys.argv[1:])
    index = json.loads((folder/'index.json').read_text())
    result = build([(r['url'],(folder/(r['sha']+'.js')).read_text()) for r in index if 'sha' in r])
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print({k:len(result[k]) for k in ('locales','locations','skipped_region_countries')})
