"""Independent, pinned private query proof for a created advertising object."""
import re

from .provisioning.models import ProvisioningError

_PATH = re.compile(r"data(?:\.(?:[A-Za-z][A-Za-z0-9_]*|0|[1-9][0-9]*))+")
_RELATIONS = {"CAMPAIGN": set(), "ADSET": {"campaign_id"},
              "CREATIVE": {"page_id"}, "AD": {"campaign_id", "adset_id", "creative_id"}}


def validate_contract(row, step):
    from .private_launch import PrivateLaunchContractStore
    required = {"operation_kind", "doc_id", "friendly_name", "variables", "id_path", "account_path", "relations"}
    allowed = required | {"endpoint_url", "status_path"}
    if (not isinstance(row, dict) or set(row) - allowed or not required <= set(row)
            or row.get("operation_kind") != "query"
            or not re.fullmatch(r"\d{5,40}", str(row.get("doc_id", "")))
            or not re.fullmatch(r"[A-Za-z0-9_]{1,180}Query", str(row.get("friendly_name", "")))
            or not isinstance(row.get("variables"), dict)
            or "{{entity_id}}" not in str(row.get("variables"))
            or not isinstance(row.get("relations"), dict)
            or set(row["relations"]) != _RELATIONS[step]
            or (step != "CREATIVE" and not row.get("status_path"))):
        raise ProvisioningError("PRIVATE_LAUNCH_VERIFICATION_CONTRACT_REQUIRED",
            f"{step} requires a complete observed private READ contract before CREATE", retryable=False)
    paths = [row["id_path"], row["account_path"], *row["relations"].values()]
    if row.get("status_path"):
        paths.append(row["status_path"])
    if (any(not isinstance(value, str) or not _PATH.fullmatch(value) for value in paths)
            or len(paths) != len(set(paths))):
        raise ProvisioningError("PRIVATE_LAUNCH_VERIFICATION_CONTRACT_REQUIRED",
            "Independent READ identity, account, state and relation paths must be distinct data fields", retryable=False)
    PrivateLaunchContractStore._endpoint(row.get("endpoint_url"))
    PrivateLaunchContractStore._reject_auth_material(row)
    return row


def _complete(value):
    if isinstance(value, dict):
        if (value.get("errors") or value.get("error") or value.get("hasNext") is True
                or (isinstance(value.get("extensions"), dict) and value["extensions"].get("is_final") is False)):
            return False
        return all(_complete(child) for child in value.values())
    return not isinstance(value, list) or all(_complete(child) for child in value)


def proof(payload, row, values):
    from .private_launch import _lookup
    if not isinstance(payload, dict) or not _complete(payload):
        return {"verified": False, "code": "READ_INCOMPLETE"}
    if _lookup(payload, row["id_path"]) != values["entity_id"]:
        return {"verified": False, "code": "READ_ENTITY_MISMATCH"}
    account = _lookup(payload, row["account_path"])
    if not isinstance(account, str) or account.removeprefix("act_") != values["ad_account_id"]:
        return {"verified": False, "code": "READ_ACCOUNT_MISMATCH"}
    expected_status = values.get("expected_status", "PAUSED")
    if not isinstance(expected_status, str) or expected_status not in {"PAUSED", "ACTIVE"}:
        return {"verified": False, "code": "READ_STATE_INVALID"}
    if row.get("status_path") and _lookup(payload, row["status_path"]) != expected_status:
        return {"verified": False, "code": "READ_" + expected_status + "_UNCONFIRMED"}
    if any(_lookup(payload, path) != values.get(key) for key, path in row["relations"].items()):
        return {"verified": False, "code": "READ_RELATION_MISMATCH"}
    return {"verified": True, "code": "READ_CONFIRMED", "source": "independent_private_http_query",
            "browser_started": False}


async def verify(web, row, step, values):
    from .private_launch import PrivateLaunchContractStore, _render
    row = validate_contract(row, step)
    web.private_only = True
    response = await web.graphql(row["doc_id"], _render(row["variables"], values),
        friendly_name=row["friendly_name"], endpoint_url=PrivateLaunchContractStore._endpoint(row.get("endpoint_url")),
        business_context_id=values["business_id"])
    return proof(response, row, values)
