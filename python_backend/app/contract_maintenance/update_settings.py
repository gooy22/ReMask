"""Compile an observed module snapshot into a candidate static manifest.

Run separately: python -m app.contract_maintenance.update_settings
--sources-dir tests/fixtures --output /tmp/meta-settings-candidate.json
The output is a review candidate, never a hot update or a deployment. No profile
cookies, browser, HTTP requests or mutations are needed to compile it.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

from ..static_meta_contracts import MANIFEST, command
from ..private_asset_contracts import AssetContracts, CONFIG, PAGE, RIGHTS, CLAIM, ASSIGN
from ..private_inventory_queries import QueryArtifacts
from ..private_contract_discovery import WebModuleContracts, _module_nodes, _pairs, _walk


def compile_candidate(sources_dir):
    path = Path(sources_dir)
    manifest = json.loads(MANIFEST.read_text())
    output = copy.deepcopy(manifest)
    output['revision'] += '-candidate'
    def update(op, contract, source):
        if not contract:
            raise ValueError('Observed artifact/sender cannot confirm ' + op)
        row = output['operations'][op]
        # Exact schema drift must be reviewed as code, not just a new doc_id.
        current = command(op, business='123456789', page='987654321', user='111111111',
            asset='222222222', tasks=['333333333'], types=['PAGE', 'AD_ACCOUNT'], join='review',
            name='fixture', currency='USD', timezone=137, actor='111111111',mutation='review',first='First',last='Last',email='owner@example.com')
        if set(current['variables']) != set(contract['variables']):
            raise ValueError('Observed top-level schema changed: ' + op)
        row['doc_id'] = contract['doc_id']
        row['source_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    source = (path/'meta_page_access_observed_20261008.js').read_text()
    observed = AssetContracts(); observed.observe(source)
    for op, friendly in [('CONFIG', CONFIG), ('PAGE', PAGE), ('RIGHTS', RIGHTS), ('CLAIM', CLAIM), ('ASSIGN', ASSIGN)]:
        current = command(op, business='123456789', page='987654321', asset='222222222',
            user='111111111', tasks=['333333333'], types=['PAGE', 'AD_ACCOUNT'], join='review')
        compiled = (observed.query if current['operation_kind']=='query' else observed.mutation)(friendly, current['variables'])
        if not compiled or compiled['variables'] != current['variables']:
            raise ValueError('Observed variables no longer match ' + op)
        update(op, compiled, source)
    source = (path/'meta_settings_rk_observed_20261008.js').read_text()
    observed = QueryArtifacts(); observed.observe(source)
    rows = [row for row in observed.contracts('123456789') if row['friendly_name']==output['operations']['READ_RK']['friendly_name']]
    if len(rows)!=1 or rows[0]['variables']!=command('READ_RK', business='123456789')['variables']:
        raise ValueError('Observed exact-BM RK read changed')
    update('READ_RK', rows[0], source)
    source = (path/'meta_create_rk_observed_20261008.js').read_text()
    observed = WebModuleContracts(friendly_names=(output['operations']['CREATE_RK']['friendly_name'],),
        bindings={'businessid':'123456789','endadvertiserid':'123456789','adaccountname':'fixture','currency':'USD','timezoneid':137})
    observed.observe(source); row = observed.result()
    current = command('CREATE_RK', business='123456789', name='fixture', currency='USD', timezone=137, join='private-contract-discovery')
    if not row or row['variables']!=current['variables']:
        raise ValueError('Observed flat RK CREATE changed')
    update('CREATE_RK', row, source)
    # BM preload artifact binds all first-level scopes. The response schema
    # remains explicitly unverified: compiling source is not a live proof.
    source = (path/'meta_bm_scoping_observed_20261008.js').read_text()
    friendly = output['operations']['READ_BM']['friendly_name']
    ids = re.findall(r'__d\("'+re.escape(friendly)+r'_facebookRelayOperation".*?exports="(\d{5,40})"',source,re.S)
    if len(set(ids))!=1 or friendly+'$Parameters' not in source or 'allFirstLevelScopesQueryRef' not in source:
        raise ValueError('Observed BM preload is unavailable or ambiguous')
    metadata = QueryArtifacts()
    metadata.observe(source)
    versions = metadata.modules.get('NorthStarBusinessUnifiedScopingSelectorPopoverContainer.entrypoint', {})
    if len(versions) != 1:
        raise ValueError('BM preload sender is ambiguous')
    schemas = []
    for node in _walk(next(iter(versions.values()))):
        if node.type != 'object':
            continue
        try:
            pairs = _pairs(node)
            if 'allFirstLevelScopesQueryRef' not in pairs:
                continue
            preload = _pairs(pairs['allFirstLevelScopesQueryRef'])
            expression = preload['variables']
            if expression.child_by_field_name('function').text != b'babelHelpers.extends':
                raise ValueError('BM preload variable construction changed')
            args = expression.child_by_field_name('arguments').named_children
            if len(args) != 3 or _pairs(args[0]):
                raise ValueError('BM preload variable construction changed')
            base = _pairs(metadata._alias(args[1]))
            extra = _pairs(args[2])
            names = set(base) | set(extra) | {'__relay_internal__pv__IsBusinessPortfolioOverviewLinkEnabledrelayprovider'}
            if len(base) + len(extra) + 1 != len(names):
                raise ValueError('BM preload contains duplicate variables')
            schemas.append(names)
        except (KeyError, AttributeError):
            continue
    if schemas != [set(output['operations']['READ_BM']['variables'])]:
        raise ValueError('Observed top-level BM enumeration schema changed')
    output['operations']['READ_BM']['doc_id']=ids[0]
    output['operations']['READ_BM']['source_sha256']=hashlib.sha256(source.removeprefix('// Public selector metadata, preload sender and providers. Response not yet observed.\n').encode()).hexdigest()
    return output


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sources-dir',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    target=Path(args.output)
    if target.resolve()==MANIFEST.resolve() or target.exists():
        parser.error('Write a new candidate path; production manifest cannot be overwritten by this command')
    value=compile_candidate(args.sources_dir)
    target.write_text(json.dumps(value,indent=2)+'\n')
    print('Candidate written:', target, '; review schema, fixture tests and independent live read proof before deployment.')


if __name__=='__main__':
    main()
