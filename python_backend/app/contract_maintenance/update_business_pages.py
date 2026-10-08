"""Offline, credential-free Page contract candidate compiler. Never hot-updates."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from ..business_fan_page_contracts import MANIFEST
from ..private_contract_discovery import _module_nodes, _pairs, _walk, _literal
from ..private_inventory_queries import QueryArtifacts


def compile_candidate(source_file):
    source = Path(source_file).read_text()
    metadata = QueryArtifacts()
    for name, module in _module_nodes(source):
        metadata.modules.setdefault(name, {})[hashlib.sha256(module.text).hexdigest()] = module
    value = json.loads(MANIFEST.read_text())
    result = copy.deepcopy(value)
    result['revision'] += '-candidate'
    for operation, row in result['operations'].items():
        versions = metadata.modules.get(row['friendly_name'] + '.graphql', {})
        if len(versions) != 1:
            raise ValueError('Ambiguous/missing Page artifact: ' + operation)
        artifacts = []
        for node in _walk(next(iter(versions.values()))):
            if node.type != 'object':
                continue
            try:
                pairs = _pairs(node)
                if not {'params', 'operation'}.issubset(pairs):
                    continue
                params, artifact = metadata._read(pairs['params']), metadata._read(pairs['operation'])
                if params.get('name') != row['friendly_name'] or params.get('operationKind') != row['operation_kind']:
                    raise ValueError('Page artifact kind changed')
                arguments = {arg['name'] for arg in artifact['argumentDefinitions']}
                if arguments != set(row['variables']):
                    raise ValueError('Page top-level variable schema changed: ' + operation)
                artifacts.append(params['id'])
            except (KeyError, AttributeError):
                continue
        if len(set(artifacts)) != 1:
            raise ValueError('Page artifact cannot be compiled: ' + operation)
        row['doc_id'] = artifacts[0]
        row['source_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    modal = metadata.modules.get('BizKitSettingsCreatePageModal.react', {})
    if len(modal) != 1:
        raise ValueError('Page sender missing or ambiguous')
    schemas = []
    for node in _walk(next(iter(modal.values()))):
        if node.type != 'object':
            continue
        try:
            pairs = _pairs(node)
            if 'variables' not in pairs:
                continue
            variables = _pairs(pairs['variables'])
            if set(variables) != {'input'}:
                continue
            fields = _pairs(variables['input'])
            if 'creation_source' not in fields:
                continue
            if _literal(fields['creation_source']) != 'meta_business_suite':
                raise ValueError('Page creation source changed')
            schemas.append(set(fields))
        except (KeyError, AttributeError):
            continue
    if schemas != [set(result['operations']['CREATE_FP']['variables']['input'])]:
        raise ValueError('Observed Business Page input schema changed')
    category_sender = metadata.modules.get('useBizKitSettingsPageCategorySearchSource', {})
    if len(category_sender) != 1:
        raise ValueError('Category search sender missing or ambiguous')
    category_schemas = []
    for node in _walk(next(iter(category_sender.values()))):
        if node.type != 'object':
            continue
        try:
            pairs = _pairs(node)
            if 'variables' not in pairs:
                continue
            variables = _pairs(pairs['variables'])
            if set(variables) == {'params'}:
                category_schemas.append(set(_pairs(variables['params'])))
        except (KeyError, AttributeError):
            continue
    if category_schemas != [{'search_string'}]:
        raise ValueError('Category search nested variable schema changed')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-file', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    target = Path(args.output)
    if target.exists() or target.resolve() == MANIFEST.resolve():
        parser.error('Choose a new candidate path; production manifest cannot be overwritten')
    target.write_text(json.dumps(compile_candidate(args.source_file), indent=2) + '\n')


if __name__ == '__main__':
    main()
