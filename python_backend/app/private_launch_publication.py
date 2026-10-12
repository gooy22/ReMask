"""Resume a reviewed asynchronous publication by READ, never by replaying POST.

The public draft sender demonstrates that accepting a publish is not the same
as completing it. No endpoint or operation ID is inferred from those sources:
this adapter requires an independently reviewed private query in the manifest.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from fb_worker import AuthenticationError

from .action_result import ActionResult
from .private_auth import private_auth_error

OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{5,128}")
RETRY_CODES = {"PUBLISH_PENDING", "READ_UNAVAILABLE", "READ_INCOMPLETE",
               "SESSION_EXPIRED", "BUSINESS_LOGIN_GATE", "META_AUTH_TOKEN_UNAVAILABLE"}


def validate_publication(row, output_names):
    from .private_launch import PrivateLaunchContractStore, _PLACEHOLDER
    from .private_launch_extensions import _PATH, fail
    required = {"operation_id_path", "status_query", "pending_statuses", "success_status", "failed_statuses"}
    if not isinstance(row, dict) or set(row) - (required | {"max_polls", "poll_interval_seconds"}) or not required <= set(row):
        raise fail("STATIC_MANIFEST_INVALID", "Asynchronous publication needs an exact operation reference and status query")
    query = row["status_query"]
    fields = {"operation_kind", "doc_id", "friendly_name", "variables", "operation_id_path",
              "account_path", "status_path", "output_paths"}
    if (not isinstance(query, dict) or not fields <= set(query)
            or set(query) - (fields | {"endpoint_url", "request_envelope"})
            or query["operation_kind"] != "query"
            or not re.fullmatch(r"\d{5,40}", str(query["doc_id"]))
            or not re.fullmatch(r"[A-Za-z0-9_]{1,180}Query", str(query["friendly_name"]))
            or not isinstance(query["variables"], dict)
            or not {"operation_id", "ad_account_id"} <= set(_PLACEHOLDER.findall(json.dumps(query["variables"])))
            or not isinstance(query["output_paths"], dict) or set(query["output_paths"]) != set(output_names)):
        raise fail("STATIC_MANIFEST_INVALID", "Publish status requires a pinned query for this exact account and operation")
    paths = [query[key] for key in ("operation_id_path", "account_path", "status_path")] + list(query["output_paths"].values())
    if (not isinstance(row["operation_id_path"], str) or not _PATH.fullmatch(row["operation_id_path"])
            or any(not isinstance(path, str) or not _PATH.fullmatch(path) for path in paths)
            or len(set(paths)) != len(paths)):
        raise fail("STATIC_MANIFEST_INVALID", "Publish status paths must identify distinct observed fields")
    pending, failed, success = row["pending_statuses"], row["failed_statuses"], row["success_status"]
    if (not isinstance(pending, list) or not pending or not isinstance(failed, list) or not failed
            or any(not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value)
                   for value in pending + failed + [success])
            or len(set(pending + failed + [success])) != len(pending + failed) + 1):
        raise fail("STATIC_MANIFEST_INVALID", "Publication states must be explicit, observed and disjoint")
    for key, default, minimum, maximum in (("max_polls", 6, 1, 12), ("poll_interval_seconds", 3, 1, 10)):
        value = row.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise fail("STATIC_MANIFEST_INVALID", "Publication polling limit is invalid")
    PrivateLaunchContractStore._endpoint(query.get("endpoint_url"))
    PrivateLaunchContractStore._clean_envelope(query.get("request_envelope"))
    PrivateLaunchContractStore._reject_auth_material(row)


def pending_operation(evidence):
    value = evidence.get("publication", {}).get("operation_id", "")
    return value if isinstance(value, str) and OPERATION_ID.fullmatch(value) else ""


async def poll_publication(state, web, contract, values, prior):
    from .private_launch import PrivateLaunchContractStore, _complete_response, _lookup, _render
    from .private_launch_verify import _complete
    from .private_launch_extensions import fail
    evidence = dict(prior["result"].get("evidence", {}))
    operation_id = pending_operation(evidence)
    if not operation_id:
        raise fail("RECONCILE_REQUIRED", "Publication has no durable operation reference; replay is blocked")
    old = evidence.get("publication", {})
    if old.get("code") and old["code"] not in RETRY_CODES and not evidence.get("outputs"):
        raise fail("RECONCILE_REQUIRED", "Saved publication returned a conflicting or failed status")
    if evidence.get("outputs"):
        return prior, evidence["outputs"]
    row = contract.row["async_publication"]
    query = row["status_query"]
    started = time.monotonic()
    for attempt in range(row.get("max_polls", 6)):
        remaining = 55 - (time.monotonic() - started)
        if remaining <= 0:
            break
        code, status, outputs = "PUBLISH_PENDING", "", {}
        try:
            response = await asyncio.wait_for(web.graphql(query["doc_id"],
                _render(query["variables"], {**values, "operation_id": operation_id}),
                friendly_name=query["friendly_name"],
                endpoint_url=PrivateLaunchContractStore._endpoint(query.get("endpoint_url")),
                request_envelope=PrivateLaunchContractStore._clean_envelope(query.get("request_envelope")),
                business_context_id=values["business_id"]), timeout=min(20, remaining))
            if not _complete_response(response) or not _complete(response):
                code = "READ_INCOMPLETE"
            elif _lookup(response, query["operation_id_path"]) != operation_id:
                code = "PUBLISH_OPERATION_MISMATCH"
            else:
                account = _lookup(response, query["account_path"])
                if not isinstance(account, str) or account.removeprefix("act_") != values["ad_account_id"]:
                    code = "READ_ACCOUNT_MISMATCH"
                else:
                    observed = _lookup(response, query["status_path"])
                    status = observed if isinstance(observed, str) and len(observed) <= 80 else ""
                    if status == row["success_status"]:
                        outputs = {key: _lookup(response, path) for key, path in query["output_paths"].items()}
                        if (not all(isinstance(value, str) and re.fullmatch(r"\d{5,40}", value)
                                    for value in outputs.values()) or len(set(outputs.values())) != 4):
                            outputs, code = {}, "PUBLISH_OUTPUT_MISMATCH"
                        else:
                            code = "PUBLISH_COMPLETED"
                    elif status in row["failed_statuses"]:
                        code = "PUBLISH_FAILED"
                    elif status not in row["pending_statuses"]:
                        code = "PUBLISH_STATUS_UNKNOWN"
        except AuthenticationError as exc:
            code = private_auth_error(exc).code
        except Exception:
            code = "READ_UNAVAILABLE"
        publication = {"operation_id": operation_id, "code": code, "status": status,
                       "poll_count": int(evidence.get("publication", {}).get("poll_count", 0)) + 1,
                       "last_polled_at": int(time.time())}
        evidence = {**evidence, "publication": publication, "outputs": outputs}
        result = ActionResult.reconcile(prior["step"],
            code="PRIVATE_LAUNCH_PUBLISH_FAMILY_" + ("READ_PENDING" if outputs else code),
            message="Publication accepted; object verification pending" if outputs else "Publication status has not confirmed all objects",
            submitted=True, evidence=evidence)
        result.entity_id = outputs.get("ad_id") or operation_id
        result.retryable = code in RETRY_CODES or bool(outputs)
        if not await state.finish_read(prior, result):
            from .private_launch_extensions import OperationKey
            latest = await state.step(prior["launch_key"], OperationKey(prior["step"]))
            latest_evidence = latest.get("result", {}).get("evidence", {}) if latest else {}
            if (latest and pending_operation(latest_evidence) == operation_id
                    and latest_evidence.get("operation_fingerprint") == evidence.get("operation_fingerprint")
                    and latest_evidence.get("outputs")):
                return latest, latest_evidence["outputs"]
            raise fail("PUBLISH_PENDING", "Another worker updated publication progress; retry its saved READ", True)
        from .private_launch_extensions import OperationKey
        prior = await state.step(prior["launch_key"], OperationKey(prior["step"]))
        if outputs:
            return prior, outputs
        if code != "PUBLISH_PENDING":
            raise fail("PUBLISH_FAMILY_" + code, result.message, code in RETRY_CODES)
        if attempt + 1 < row.get("max_polls", 6):
            await asyncio.sleep(min(row.get("poll_interval_seconds", 3), max(0, 55 - (time.monotonic() - started))))
    raise fail("PUBLISH_FAMILY_PENDING", "Meta is still processing the saved publication; retry resumes only its status query", True)
