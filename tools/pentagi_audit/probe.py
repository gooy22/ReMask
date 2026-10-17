"""Fixed read-only sample, executed by a real PentAGI agent's terminal tool."""
import json
import urllib.error
import urllib.request

BASE = 'http://audit-gateway:8080'
PATHS = ['/', '/ready.php', '/accounts.json', '/bundles.json', '/bundles.json.bak',
         '/BUNDLES.JSON', '/.env', '/.git/HEAD', '/classes/', '/server-status',
         '/ajax/metaBundles.php', '/ajax/pythonWorkerJobs.php', '/ajax/pythonProfileContext.php']

def metadata(path):
    with urllib.request.urlopen(BASE+path, timeout=25) as response:
        return json.load(response)

def main():
    target=metadata('/_audit/target')
    assert target['target']=='https://remask-production-7cbf.up.railway.app'
    state=metadata('/_audit/state')
    observed={row['path'] for row in state['requests']}
    for path in PATHS:
        if path in observed:
            continue
        try:
            response=urllib.request.urlopen(BASE+path, timeout=25)
        except urllib.error.HTTPError as error:
            response=error
        with response:
            response.read(32768)  # Discard bodies, including private-body redaction notices.
    print(json.dumps(metadata('/_audit/state'),ensure_ascii=False))

if __name__=='__main__':
    main()
