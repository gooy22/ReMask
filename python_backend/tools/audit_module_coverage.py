"""Review every saved module and compare exact installed sender provenance.

Offline only: no cookies, browser, network calls, JS evaluation or Meta writes.
The report distinguishes recorded code from implemented runtime operations.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.contract_maintenance.ads_sources import module_records, public_script_url
from app.contract_maintenance.module_registry import build_registry
from app.contract_maintenance.module_refresh import baselines, select_updates


def audit(folder):
    rows, scripts, variants = [], [], {}
    for entry in json.loads((folder / 'index.json').read_text()):
        if not public_script_url(entry['url']):
            raise ValueError('Foreign source URL')
        source = (folder / (entry['sha'] + '.js')).read_text()
        if hashlib.sha256(source.encode()).hexdigest() != entry['sha']:
            raise ValueError('Source digest mismatch')
        parsed = list(module_records(source, all_modules=True))
        for row in parsed:
            digest = hashlib.sha256(row['module_source'].encode()).hexdigest()
            row['_module_evidence'] = {'module': row['name'], 'module_sha256': digest,
                'script_sha256': entry['sha'], 'observed_public_url': entry['url']}
            variants.setdefault(row['name'], set()).add(digest)
        scripts.append({'sha256': entry['sha'], 'bytes': len(source.encode()),
            'declared_modules': len(entry['names']), 'parsed_modules': len(parsed),
            'unparsed_names': sorted(set(entry['names']) - {row['name'] for row in parsed})})
        rows.extend(parsed)
    registry = build_registry(rows)
    modules = registry['modules']
    missing = sorted({dep for row in modules.values() for dep in row['dependencies'] if dep not in modules})
    groups = {'launch': r'Ads|ads|AdCampaign|Adgroup|AdCreative|GraphAPI|ApiClient',
        'sync': r'Business|BizKit|Inventory|Page|ProfilePlus|AdAccount',
        'targeting': r'Targeting|Audience|Locale|Language|Device|Placement',
        'payment': r'Billing|Payment|CreditCard|Wallet'}
    counts = Counter('relay_mutation' if name.endswith('Mutation.graphql') else
        'relay_query' if name.endswith('Query.graphql') else
        'relay_fragment' if name.endswith('.graphql') else
        'relay_id' if name.endswith('_facebookRelayOperation') else
        'ui' if name.endswith('.react') else 'module' for name in modules)
    selection = select_updates(rows, baselines(), now=0)
    catalog = json.loads((Path(__file__).resolve().parents[1] / 'app/contracts/ads_manager_http_20261012.json').read_text())
    routes = {}
    for name, route in catalog['routes'].items():
        matches = [row for row in rows if row['name'] == route['source_module']]
        expected = {(proof['module_sha256'], proof['script_sha256']) for proof in route['sources']}
        seen = {(row['_module_evidence']['module_sha256'], row['_module_evidence']['script_sha256']) for row in matches}
        routes[name] = {'method': route['method'], 'path': route['path'],
            'module': route['source_module'], 'module_captured': bool(matches),
            'exact_source_captured': bool(expected & seen), 'live_verified': False}
    return {'version': 1, 'scope': 'all_saved_scripts_and_literal_module_factories',
        'source_bytes': sum(row['bytes'] for row in scripts), 'script_count': len(scripts),
        'module_count': len(modules), 'module_wrappers': len(rows),
        'every_declared_module_parsed': all(not row['unparsed_names'] for row in scripts),
        'module_kinds': dict(counts), 'conflicting_modules': registry['ambiguous'],
        'missing_dependency_count': len(missing), 'missing_dependencies': missing,
        'groups': {group: sorted(name for name in modules if re.search(pattern, name)) for group, pattern in groups.items()},
        'reviewed_relay_operations': {'complete_proofs': sorted(selection['accepted']),
            'changed_or_incomplete_proofs': selection['blocked'],
            'not_in_this_capture': sorted(set(baselines()) - set(selection['accepted']) - set(selection['blocked']))},
        'installed_ads_routes': routes, 'scripts': scripts, 'live_verified': False,
        'limits': ['Only saved resources are available offline; this is not all Facebook code.',
            'A captured module or Relay artifact is not an implemented adapter or a live success.',
            'Missing dependencies require further advertised source capture; no CDN URL is guessed.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = audit(args.folder)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('script_count', 'source_bytes', 'module_count',
        'every_declared_module_parsed', 'module_kinds', 'missing_dependency_count')}, ensure_ascii=False))
