"""Build ID-only refresh fingerprints from installed, reviewed source fixtures."""
import json
from pathlib import Path

from .ads_sources import module_records
from .module_refresh import fingerprint


def build(root):
    app = root / 'app'
    operations = {}
    for path in (app / 'contracts').glob('meta_*.json'):
        if path.name == 'meta_module_refresh_20261013.json':
            continue
        data = json.loads(path.read_text())
        for spec in data.get('operations', {}).values():
            if spec.get('friendly_name') and spec.get('doc_id'):
                operations[spec['friendly_name']] = spec['doc_id']
    names = {name + suffix for name in operations for suffix in ('.graphql', '_facebookRelayOperation')}
    records = []
    # These snapshots were already selected/reviewed by the existing
    # maintenance compilers. No new downloaded code is a trusted baseline.
    sources = sorted((root / 'tests' / 'fixtures').glob('meta_*observed*.js'))
    for path in sources:
        # Select all named modules in an observed snapshot, including callers.
        source = path.read_text()
        import re
        selected = re.findall(r'__d\([\"\']([^\"\']+)', source)
        records.extend(module_records(source, selected=selected))
    by_name = {}
    for row in records:
        by_name.setdefault(row['name'], []).append(row)
    result = {}
    for friendly, doc_id in sorted(operations.items()):
        artifact = friendly + '.graphql'
        if artifact not in by_name:
            continue
        specs = {}
        for name in (artifact, friendly + '_facebookRelayOperation'):
            rows = by_name.get(name, [])
            hashes = {fingerprint(row, doc_id) for row in rows}
            if len(hashes) == 1:
                specs[name] = hashes.pop()
        if artifact not in specs:
            continue
        senders = {}
        for name, rows in by_name.items():
            if name.endswith(('.graphql', '_facebookRelayOperation')):
                continue
            eligible = [row for row in rows if artifact in row['dependencies'] or
                (friendly + '$Parameters') in row['dependencies'] or
                any(re.search(r'[\"\']' + re.escape(target) + r'[\"\']', row['source'])
                    for target in (artifact, friendly + '$Parameters'))]
            hashes = {fingerprint(row, doc_id) for row in eligible}
            if len(hashes) == 1:
                senders[name] = hashes.pop()
        if senders:
            result[friendly] = {'doc_id': doc_id, 'artifact_modules': specs, 'senders': senders}
    return {'version': 1, 'policy': 'exact_artifact_and_sender_id_only', 'operations': result}


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    output = root / 'app/contracts/meta_module_refresh_20261013.json'
    output.write_text(json.dumps(build(root), indent=2, ensure_ascii=False) + '\n')
    print('Reviewed module refresh baselines:', len(json.loads(output.read_text())['operations']))
