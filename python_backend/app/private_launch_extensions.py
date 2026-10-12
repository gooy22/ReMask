"""Durable media, family publication and delivery operations for private Launch.

This module supplies orchestration only. Operation IDs, variable envelopes and
READ paths must come from reviewed source evidence in the deployed manifest.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import re
import time

from fb_worker import AuthenticationError

from .action_result import ActionResult
from .private_auth import private_auth_error
from .provisioning.models import ProvisioningError

OPERATIONS = {"IMAGE_UPLOAD", "VIDEO_UPLOAD", "PUBLISH_FAMILY",
              "ACTIVATE_CAMPAIGN", "ACTIVATE_ADSET", "ACTIVATE_AD"}
FORMATS = {"SINGLE", "CAROUSEL", "INSTAGRAM_POST"}
_PATH = re.compile(r"data(?:\.(?:[A-Za-z][A-Za-z0-9_]*|[0-9]+))+")
_MEDIA_ID = re.compile(r"[A-Za-z0-9_-]{5,128}")
_RETRY_READ = {"READ_UNAVAILABLE", "READ_INCOMPLETE", "READ_NOT_READY",
               "SESSION_EXPIRED", "BUSINESS_LOGIN_GATE", "META_AUTH_TOKEN_UNAVAILABLE"}


@dataclass(frozen=True, slots=True)
class OperationKey:
    value: str


@dataclass(frozen=True, slots=True)
class ExtensionContract:
    name: str
    row: dict
    digest: str


def fail(code, message, retryable=False):
    return ProvisioningError("PRIVATE_LAUNCH_" + code, message, retryable=retryable)


def source_evidence(entry, name):
    evidence = entry.get("evidence") if isinstance(entry, dict) else None
    if not isinstance(evidence, dict) or any(not re.fullmatch(r"[a-f0-9]{64}", str(evidence.get(key, "")))
            for key in ("artifact_sha256", "sender_sha256", "read_sha256")):
        raise fail("STATIC_MANIFEST_INVALID", "Source evidence is missing for " + name)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(evidence.get("observed_at", ""))):
        raise fail("STATIC_MANIFEST_INVALID", "Observation date is missing for " + name)


def _validate_media_read(row):
    from .private_launch import PrivateLaunchContractStore
    required = {"operation_kind", "doc_id", "friendly_name", "variables", "id_path", "account_path"}
    allowed = required | {"endpoint_url", "ready_path", "ready_value"}
    if (not isinstance(row, dict) or not required <= set(row) or set(row) - allowed
            or row.get("operation_kind") != "query"
            or not re.fullmatch(r"\d{5,40}", str(row.get("doc_id", "")))
            or not re.fullmatch(r"[A-Za-z0-9_]{1,180}Query", str(row.get("friendly_name", "")))
            or not isinstance(row.get("variables"), dict) or "{{entity_id}}" not in json.dumps(row["variables"])):
        raise fail("MEDIA_READ_CONTRACT_REQUIRED", "Media requires an independent exact-object query")
    paths = [row["id_path"], row["account_path"]]
    if "ready_path" in row:
        if "ready_value" not in row or not isinstance(row["ready_value"], (str, bool, int)):
            raise fail("MEDIA_READ_CONTRACT_REQUIRED", "Media readiness requires an explicit observed state")
        paths.append(row["ready_path"])
    elif "ready_value" in row:
        raise fail("MEDIA_READ_CONTRACT_REQUIRED", "Media readiness has no response path")
    if any(not isinstance(path, str) or not _PATH.fullmatch(path) for path in paths) or len(set(paths)) != len(paths):
        raise fail("MEDIA_READ_CONTRACT_REQUIRED", "Media identity, account and readiness must have distinct paths")
    PrivateLaunchContractStore._endpoint(row.get("endpoint_url"))
    PrivateLaunchContractStore._reject_auth_material(row)


def extension_contract(name, entry):
    from .private_launch import PrivateLaunchContractStore, STEP_ORDER, _PLACEHOLDER
    from .private_launch_verify import validate_contract
    source_evidence(entry, name)
    row = entry.get("contract")
    allowed = {"doc_id", "friendly_name", "endpoint_url", "variables", "request_envelope",
               "output_paths", "verification", "file_field", "async_publication"}
    if (name not in OPERATIONS or not isinstance(row, dict) or set(row) - allowed
            or not re.fullmatch(r"\d{5,40}", str(row.get("doc_id", "")))
            or not re.fullmatch(r"[A-Za-z0-9_]{1,180}", str(row.get("friendly_name", "")))
            or not isinstance(row.get("variables"), dict) or not row["variables"]
            or not isinstance(row.get("output_paths", {}), dict)):
        raise fail("STATIC_MANIFEST_INVALID", "Incomplete advertising operation " + name)
    PrivateLaunchContractStore._reject_auth_material(row)
    PrivateLaunchContractStore._endpoint(row.get("endpoint_url"))
    PrivateLaunchContractStore._clean_envelope(row.get("request_envelope"))
    bindings = set(_PLACEHOLDER.findall(json.dumps(row["variables"])))
    required_bindings = {"ad_account_id"}
    paths = row.get("output_paths", {})
    verification = row.get("verification")
    if name in {"IMAGE_UPLOAD", "VIDEO_UPLOAD"}:
        if (set(paths) != {"entity_id"} or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", str(row.get("file_field", "")))):
            raise fail("STATIC_MANIFEST_INVALID", "Media upload needs an observed multipart field and exact returned reference")
        _validate_media_read(verification)
        if name == "VIDEO_UPLOAD" and not verification.get("ready_path"):
            raise fail("MEDIA_READ_CONTRACT_REQUIRED", "Video must have an observed processing-ready state")
    elif name == "PUBLISH_FAMILY":
        output_names = {step.value.lower() + "_id" for step in STEP_ORDER}
        asynchronous = row.get("async_publication")
        if asynchronous is not None:
            from .private_launch_publication import validate_publication
            if paths or not re.fullmatch(r"[a-f0-9]{64}", str(entry["evidence"].get("poll_sha256", ""))):
                raise fail("STATIC_MANIFEST_INVALID", "Asynchronous publication needs separate status-source evidence and no immediate object paths")
            validate_publication(asynchronous, output_names)
        elif set(paths) != output_names:
            raise fail("STATIC_MANIFEST_INVALID", "Family publication requires all four exact object paths")
        if not isinstance(verification, dict) or set(verification) != {step.value for step in STEP_ORDER}:
            raise fail("VERIFICATION_CONTRACT_REQUIRED", "Family publication requires independent reads of every object")
        for step in STEP_ORDER:
            validate_contract(verification[step.value], step.value)
        # A family sender must bind the normalized PAUSED configuration itself.
        if "payload" not in bindings and not {"payload." + step.value.lower() for step in STEP_ORDER} <= bindings:
            raise fail("STATIC_MANIFEST_INVALID", "Family sender must transmit every complete advertising section")
        required_bindings |= {"page_id"}
    else:
        if paths or row.get("file_field"):
            raise fail("STATIC_MANIFEST_INVALID", "Activation operates on the already verified object")
        required_bindings |= {"entity_id", "target_status"}
        validate_contract(verification, name.removeprefix("ACTIVATE_"))
    if (not required_bindings <= bindings
            or any(not isinstance(path, str) or not _PATH.fullmatch(path) for path in paths.values())
            or len(set(paths.values())) != len(paths)):
        raise fail("STATIC_MANIFEST_INVALID", "Advertising operation lacks exact scope, input or response bindings")
    if name not in {"IMAGE_UPLOAD", "VIDEO_UPLOAD"} and row.get("file_field"):
        raise fail("STATIC_MANIFEST_INVALID", "Only media upload may carry a file")
    if name != "PUBLISH_FAMILY" and "async_publication" in row:
        raise fail("STATIC_MANIFEST_INVALID", "Asynchronous publication belongs only to the family sender")
    digest = hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ExtensionContract(name, row, digest)


class LaunchExtensionCatalog:
    def __init__(self, manifest=None):
        manifest = manifest or {}
        entries = manifest.get("operations", {})
        if not isinstance(entries, dict) or set(entries) - OPERATIONS:
            raise fail("STATIC_MANIFEST_INVALID", "Unsupported advertising extension catalog")
        self.operations = {name: extension_contract(name, entry) for name, entry in entries.items()}
        formats = manifest.get("creative_formats", ["SINGLE"])
        if not isinstance(formats, list) or not formats or set(formats) - FORMATS or len(formats) != len(set(formats)):
            raise fail("STATIC_MANIFEST_INVALID", "Unsupported creative format declaration")
        self.formats = tuple(formats)

    def get(self, name):
        contract = self.operations.get(name)
        if contract is None:
            raise fail(name + "_CONTRACT_REQUIRED", "Reviewed private request contract is missing for " + name)
        return contract

    def status(self):
        return {name: {"configured": name in self.operations, "active": name in self.operations,
                       "verification_configured": name in self.operations,
                       "code": "" if name in self.operations else "PRIVATE_LAUNCH_" + name + "_CONTRACT_REQUIRED"}
                for name in sorted(OPERATIONS)}


async def _verification(web, contract, values, outputs):
    from .private_launch import PrivateLaunchContractStore, STEP_ORDER, _complete_response, _lookup, _render
    from .private_launch_verify import verify, _complete
    row = contract.row
    if contract.name == "PUBLISH_FAMILY":
        proofs = {}
        for step in STEP_ORDER:
            proofs[step.value] = await verify(web, row["verification"][step.value], step.value,
                {**values, **outputs, "entity_id": outputs[step.value.lower() + "_id"]})
            if proofs[step.value].get("verified") is not True:
                return {**proofs[step.value], "steps": proofs}
        return {"verified": True, "code": "READ_CONFIRMED", "steps": proofs, "browser_started": False}
    if contract.name.startswith("ACTIVATE_"):
        return await verify(web, row["verification"], contract.name.removeprefix("ACTIVATE_"),
                            {**values, "expected_status": values["target_status"]})
    read = row["verification"]
    response = await web.graphql(read["doc_id"], _render(read["variables"], {**values, **outputs}),
        friendly_name=read["friendly_name"], endpoint_url=PrivateLaunchContractStore._endpoint(read.get("endpoint_url")),
        business_context_id=values["business_id"])
    if not _complete_response(response) or not _complete(response):
        return {"verified": False, "code": "READ_INCOMPLETE"}
    if _lookup(response, read["id_path"]) != outputs["entity_id"]:
        return {"verified": False, "code": "READ_ENTITY_MISMATCH"}
    account = _lookup(response, read["account_path"])
    if not isinstance(account, str) or account.removeprefix("act_") != values["ad_account_id"]:
        return {"verified": False, "code": "READ_ACCOUNT_MISMATCH"}
    if read.get("ready_path") and _lookup(response, read["ready_path"]) != read["ready_value"]:
        return {"verified": False, "code": "READ_NOT_READY"}
    return {"verified": True, "code": "READ_CONFIRMED", "source": "independent_private_http_query", "browser_started": False}


class LaunchOperationRunner:
    def __init__(self, state):
        self.state = state

    async def check_saved(self, launch_key, key, values, fingerprint):
        prior = await self.state.step(launch_key, key)
        if not prior:
            return None
        if (tuple(str(prior[field]) for field in ("profile_id", "business_id", "ad_account_id")) !=
                tuple(str(values[field]) for field in ("profile_id", "business_id", "ad_account_id"))
                or prior.get("result", {}).get("evidence", {}).get("operation_fingerprint") != fingerprint):
            raise fail("KEY_PAYLOAD_MISMATCH", "Saved operation belongs to another target, configuration or contract")
        return prior

    async def run(self, *, web, contract, values, launch_key, launch_fingerprint, media=None):
        from .private_launch import PrivateLaunchContractStore, _complete_response, _lookup, _render
        from .private_launch_publication import OPERATION_ID, pending_operation, poll_publication
        key = OperationKey(contract.name + (":" + media.sha256 if media else ""))
        fingerprint = hashlib.sha256(json.dumps({"launch": launch_fingerprint, "contract": contract.digest,
            "media": media.public() if media else None, "target_status": values.get("target_status"),
            "entity_id": values.get("entity_id")}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        values = {**values, **({"media": media.public()} if media else {})}
        values["client_mutation_id"] = hashlib.sha256((launch_key + ":" + key.value).encode()).hexdigest()[:32]
        prior = await self.check_saved(launch_key, key, values, fingerprint)
        evidence = {"operation_fingerprint": fingerprint, "launch_fingerprint": launch_fingerprint,
                    "contract_digest": contract.digest, "outputs": {}}
        if prior:
            evidence = prior["result"].get("evidence", {})
            outputs = evidence.get("outputs", {})
            asynchronous = bool(contract.row.get("async_publication") and pending_operation(evidence))
            saved_success = prior["status"] == "SUCCESS" and evidence.get("verification", {}).get("verified") is True
            if saved_success and not contract.name.startswith("ACTIVATE_"):
                return outputs
            if not saved_success and not asynchronous and (not outputs or prior["status"] not in {"RECONCILE_REQUIRED", "SUBMITTING"}
                    or (prior.get("error_code", "").endswith("READ_PENDING") and time.time() - prior["updated_at"] < 90)
                    or (prior["status"] == "SUBMITTING" and time.time() - prior["updated_at"] < 90)):
                raise fail("RECONCILE_REQUIRED", "Saved advertising operation cannot be submitted again")
            code = evidence.get("verification", {}).get("code")
            if not saved_success and code and code not in _RETRY_READ:
                raise fail("RECONCILE_REQUIRED", "Independent read returned a conflicting object proof")
        else:
            row = contract.row
            variables = _render(row["variables"], values)
            # Bootstrap/auth happens inside graphql before this callback. A
            # missing session/proxy therefore cannot leave a submit claim.
            outputs = {"entity_id": values["entity_id"]} if contract.name.startswith("ACTIVATE_") else {}
            evidence["outputs"] = outputs
            intent = ActionResult.reconcile(key.value, code="PRIVATE_LAUNCH_" + contract.name + "_SUBMITTING",
                message="Advertising operation has a durable submit intent", evidence=evidence)
            intent.entity_id = outputs.get("entity_id", "")
            claimed_here = False
            async def claim():
                nonlocal claimed_here
                await self.state.save(launch_key=launch_key, profile_id=values["profile_id"],
                    business_id=values["business_id"], ad_account_id=values["ad_account_id"], step=key,
                    status="SUBMITTING", entity_id=intent.entity_id, result=intent.as_dict(), error_code=intent.code, submitted=None)
                claimed_here = True
            handle = None
            try:
                if media:
                    # Hash once more immediately before upload; the metadata
                    # resolver and open descriptor must describe identical bytes.
                    handle = await asyncio.to_thread(media.path.open, "rb")
                    def file_digest():
                        digest = hashlib.sha256()
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                        handle.seek(0)
                        return digest.hexdigest()
                    if await asyncio.to_thread(file_digest) != media.sha256:
                        raise fail("MEDIA_INVALID", "Media changed before upload")
                options = {"friendly_name": row["friendly_name"],
                           "endpoint_url": PrivateLaunchContractStore._endpoint(row.get("endpoint_url")),
                           "request_envelope": PrivateLaunchContractStore._clean_envelope(row.get("request_envelope")),
                           "business_context_id": values["business_id"], "before_submit": claim}
                if handle:
                    options["multipart_files"] = {row["file_field"]: (media.path.name, handle, media.mime_type)}
                response = await web.graphql(row["doc_id"], variables, **options)
            except Exception as exc:
                submitted = await self.state.step(launch_key, key)
                if not submitted or not claimed_here:
                    if isinstance(exc, AuthenticationError):
                        raise private_auth_error(exc) from exc
                    if isinstance(exc, ProvisioningError):
                        raise
                    raise fail(contract.name + "_NOT_SUBMITTED", "Advertising request stopped before submission", True) from exc
                # Never infer that an exception means the POST failed to reach Meta.
                if not outputs:
                    raise fail("RECONCILE_REQUIRED", "Advertising operation has an uncertain submit; it cannot be replayed") from exc
                # A delivery operation already knows its exact target ID.
                # Reconcile it by READ even when the mutation response is lost.
                response = {}
            finally:
                if handle:
                    handle.close()
            from .private_launch_verify import _complete
            if row.get("async_publication"):
                # Even a partial mutation may supply a usable operation ID.
                # It is only a READ handle: the exact operation/account and all
                # object IDs must still be proven by the reviewed status query.
                operation_id = _lookup(response, row["async_publication"]["operation_id_path"])
                if not isinstance(operation_id, str) or not OPERATION_ID.fullmatch(operation_id):
                    raise fail("RECONCILE_REQUIRED", "Publication returned no exact operation handle; replay is blocked")
                evidence = {**evidence, "publication": {"operation_id": operation_id,
                    "code": "PUBLISH_PENDING", "poll_count": 0}, "outputs": {}}
            elif not outputs and _complete_response(response) and _complete(response):
                outputs = {name: _lookup(response, path) for name, path in row["output_paths"].items()}
                valid = all(isinstance(value, str) and (_MEDIA_ID.fullmatch(value) if contract.name == "IMAGE_UPLOAD"
                            else re.fullmatch(r"\d{5,40}", value)) for value in outputs.values())
                if not outputs or not valid:
                    outputs = {}
                if contract.name == "PUBLISH_FAMILY" and outputs and len(set(outputs.values())) != 4:
                    outputs = {}
            if not outputs and not pending_operation(evidence):
                raise fail("RECONCILE_REQUIRED", "The advertising response did not contain all exact object references")
            evidence = {**evidence, "outputs": outputs}
            pending = ActionResult.reconcile(key.value, code="PRIVATE_LAUNCH_" + contract.name + "_READ_PENDING",
                message="Advertising operation returned references; independent read is pending", submitted=True, evidence=evidence)
            pending.entity_id = outputs.get("entity_id") or outputs.get("ad_id") or pending_operation(evidence)
            await self.state.save(launch_key=launch_key, profile_id=values["profile_id"], business_id=values["business_id"],
                ad_account_id=values["ad_account_id"], step=key, status=pending.status.value,
                entity_id=pending.entity_id, result=pending.as_dict(), error_code=pending.code, submitted=True)
            prior = await self.state.step(launch_key, key)
        if contract.row.get("async_publication") and not outputs:
            prior, outputs = await poll_publication(self.state, web, contract, values, prior)
            evidence = prior["result"].get("evidence", {})
            if prior["status"] == "SUCCESS" and evidence.get("verification", {}).get("verified") is True:
                return outputs
        try:
            proof = await asyncio.wait_for(_verification(web, contract, values, outputs), timeout=60)
        except AuthenticationError as exc:
            proof = {"verified": False, "code": private_auth_error(exc).code}
        except Exception:
            proof = {"verified": False, "code": "READ_UNAVAILABLE"}
        evidence = {**evidence, "verification": proof}
        if proof.get("verified") is True:
            result = ActionResult.success(key.value, entity_id=prior["entity_id"],
                code="PRIVATE_LAUNCH_" + contract.name + "_CONFIRMED", evidence=evidence)
        else:
            result = ActionResult.reconcile(key.value, code="PRIVATE_LAUNCH_" + contract.name + "_READ_UNCONFIRMED",
                message="Independent read has not confirmed this advertising operation", submitted=True, evidence=evidence)
            result.entity_id = prior["entity_id"]
        if not await self.state.finish_read(prior, result):
            latest = await self.check_saved(launch_key, key, values, fingerprint)
            if latest and latest["status"] == "SUCCESS" and latest["result"]["evidence"].get("verification", {}).get("verified") is True:
                return latest["result"]["evidence"]["outputs"]
            raise fail("RECONCILE_REQUIRED", "Advertising operation changed during independent read")
        if proof.get("verified") is not True:
            raise fail(contract.name + "_READ_UNCONFIRMED", result.message, proof.get("code") in _RETRY_READ)
        return outputs
