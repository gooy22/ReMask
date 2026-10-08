"""Current Meta Settings read and assignment contracts, without JS execution."""
from __future__ import annotations

import hashlib
import re

from .private_contract_discovery import (
    WebModuleContracts, _export_contains, _module_nodes, _pairs, _read, _walk,
)
from .private_inventory_queries import QueryArtifacts
from .private_mutation_schema import _resolve, flat_configs

CONFIG = "BizKitSettingsConfigProviderQuery"
PAGE = "BizKitSettingsClaimPageSelectionTypeaheadQuery"
RIGHTS = "BizKitManageAssetUserPermissionsModalStandaloneQuery"
CLAIM = "BizKitSettingsClaimPageMutation"
ASSIGN = "BizKitSettingsAddUserAssetConnectionMutation"


class AssetContracts(WebModuleContracts):
    def __init__(self):
        super().__init__(friendly_names=(CLAIM, ASSIGN), bindings={})

    def observe(self, source):
        if not source or len(source.encode()) > 8_000_000:
            return
        self._compiled = False
        # Permission helpers and the query preload entrypoint are dependencies
        # of the sender, not mutation candidates of their own.
        needed = {CONFIG, PAGE, RIGHTS, CLAIM, ASSIGN}
        helpers = {'useMutationWithReauthHandling', 'BizKitSettingsCreateAdAccountActions',
            'BizKitSettingsClaimPageStepperModal.react', 'BizKitManageAssetUserPermissionsModalStandalone.entrypoint',
            'BusinessClaimAssetEntryPoint'}
        for name, node in _module_nodes(source):
            base = name.removesuffix('.graphql').removesuffix('$Parameters').removesuffix('_facebookRelayOperation')
            if name not in helpers and base not in needed:
                continue
            # Copy a required module into its own small syntax tree. Keeping a
            # Node from an 8MB bundle would otherwise pin that whole AST for
            # the lifetime of every parallel profile job.
            wrapped = '__d("' + name + '",[], ' + node.text.decode() + ');'
            isolated = _module_nodes(wrapped)[0][1]
            self.modules.setdefault(name, {})[hashlib.sha256(isolated.text).hexdigest()] = isolated

    def claim_entrypoint(self):
        versions = self.modules.get('BusinessClaimAssetEntryPoint', {})
        if len(versions) != 1:
            return None
        module = next(iter(versions.values()))
        exports = []
        for child in _walk(module):
            if child.type != 'assignment_expression':
                continue
            left = child.child_by_field_name('left')
            if left.type == 'member_expression' and left.child_by_field_name('property').text == b'default':
                exports.append(child.child_by_field_name('right'))
        if len(exports) != 1:
            return None
        try:
            node = _resolve(exports[0])
        except ValueError:
            return None
        if node.type == 'call_expression' and node.child_by_field_name('function').text == b'Object.freeze':
            args = node.child_by_field_name('arguments').named_children
            if len(args) != 1:
                return None
            try:
                value = QueryArtifacts()._read(_pairs(args[0])['BIZWEB_SETTINGS'])
                if value == 'bizweb_settings':
                    return value
            except (ValueError, KeyError, TypeError):
                return None
        return None

    def query(self, friendly, variables):
        metadata = QueryArtifacts()
        metadata.modules = self.modules
        found = []
        for suffix in (".graphql", "$Parameters"):
            versions = self.modules.get(friendly + suffix, {})
            if len(versions) != 1:
                continue
            module = next(iter(versions.values()))
            for node in _walk(module):
                if node.type != "object":
                    continue
                try:
                    pairs = _pairs(node)
                    params = _pairs(pairs["params"]) if "params" in pairs else pairs
                    if not {"id", "name", "operationKind"}.issubset(params):
                        continue
                    if not _export_contains(metadata, module, node):
                        continue
                    name = metadata._read(params["name"])
                    kind = metadata._read(params["operationKind"])
                    doc = str(metadata._read(params["id"]))
                    if name != friendly or kind != "query" or not re.fullmatch(r"\d{5,40}", doc):
                        continue
                    if "operation" in pairs:
                        operation = metadata._read(pairs["operation"])
                        definitions = operation["argumentDefinitions"]
                        defaults = {row["name"]: row["defaultValue"] for row in definitions}
                        if not set(variables).issubset(defaults):
                            continue
                        # These three reads have no unresolved Relay providers.
                        if any(key.startswith("__") for key in defaults):
                            continue
                        bound = {**defaults, **variables}
                    elif friendly == RIGHTS:
                        # A generated preload artifact is read-only. Its exact
                        # variable names must also be observed in its entrypoint.
                        versions = self.modules.get("BizKitManageAssetUserPermissionsModalStandalone.entrypoint", {})
                        if len(versions) != 1:
                            continue
                        entry = next(iter(versions.values())).text.decode()
                        if friendly + "$Parameters" not in entry or set(variables) != {"assetID", "businessID", "userID", "surface"}:
                            continue
                        if not all(re.search(r"\b" + key + r"\s*:", entry) for key in variables):
                            continue
                        bound = dict(variables)
                    else:
                        continue
                    found.append({"doc_id": doc, "friendly_name": friendly, "variables": bound})
                except (ValueError, KeyError, TypeError):
                    continue
        unique = {(x["doc_id"], x["friendly_name"]): x for x in found}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def mutation(self, friendly, bindings):
        metadata = QueryArtifacts()
        metadata.modules = self.modules
        versions = self.modules.get(friendly + ".graphql", {})
        if len(versions) != 1:
            return None
        module = next(iter(versions.values()))
        artifacts = []
        expected_field = {CLAIM: "business_settings_add_owned_page", ASSIGN: "business_settings_add_user_asset_connection"}.get(friendly)
        for node in _walk(module):
            if node.type != "object":
                continue
            try:
                pairs = _pairs(node)
                if not {"params", "operation"}.issubset(pairs) or not _export_contains(metadata, module, node):
                    continue
                params = metadata._read(pairs["params"])
                operation = metadata._read(pairs["operation"])
                roots = operation["selections"]
                if params["operationKind"] != "mutation" or params["name"] != friendly or len(roots) != 1 or roots[0]["name"] != expected_field:
                    continue
                names = {row["name"] for row in operation["argumentDefinitions"]}
                arguments = roots[0].get("args") or []
                root_variables = {row["variableName"] for row in arguments if row.get("kind") == "Variable"}
                # assetTypes is a returned projection argument, not a root
                # mutation argument, in the observed assignment operation.
                if root_variables != (names - {"assetTypes"} if friendly == ASSIGN else names):
                    continue
                doc = str(params["id"])
                if re.fullmatch(r"\d{5,40}", doc):
                    artifacts.append((doc, names))
            except (ValueError, KeyError, TypeError):
                continue
        if len(artifacts) != 1:
            return None
        doc, names = artifacts[0]
        compact = {re.sub(r"[^a-z0-9]", "", k.lower()): v for k, v in bindings.items()}
        schemas = []
        for module_name, versions in self.modules.items():
            if len(versions) != 1:
                continue
            sender = next(iter(versions.values()))
            for node in flat_configs(self.modules, sender, friendly):
                try:
                    values = _read(node, compact, resolve=_resolve)
                    if set(values) == names and set(bindings) == names and values == bindings:
                        schemas.append(values)
                except (ValueError, KeyError, TypeError):
                    continue
        # Page-only and multi-asset UI senders may project different assetTypes;
        # select a sender that certifies this exact command's variables.
        if not schemas:
            return None
        return {"doc_id": doc, "friendly_name": friendly, "variables": dict(bindings)}

    def result(self):
        # Discovery stops only when both real senders and all required reads
        # have arrived. IDs alone cannot certify a writable contract.
        reads = (self.query(CONFIG, {"businessID": "123456789"}),
                 self.query(PAGE, {"businessID": "123456789", "pageID": "987654321", "isMMAPageClaim": False, "isMMAPageTransfer": False}),
                 self.query(RIGHTS, {"assetID": "987654321", "businessID": "123456789", "userID": "111111111", "surface": "LWI"}))
        claim = self.mutation(CLAIM, {"businessID": "123456789", "pageID": "987654321", "igAuthCode": None,
            "igOIDCToken": "", "shouldRemoveDirectUsersBeforeClaiming": "KEEP", "claimingEntryPoint": "bizweb_settings",
            "selectedFBUserID": None, "isMMAPageTransfer": False, "qplJoinID": "discovery"})
        assign = self.mutation(ASSIGN, {"businessID": "123456789", "userID": "111111111", "assetID": "987654321",
            "taskIDs": ["123456789"], "assetTypes": ["PAGE", "AD_ACCOUNT"]})
        return {"source": "live_private_web_modules"} if all(reads) and claim and assign and self.claim_entrypoint() else None
