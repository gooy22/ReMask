"""Preserve authentication failures across private action boundaries."""
from __future__ import annotations

from .provisioning.models import ProvisioningError
from .meta_auth_document import auth_route_gate


def auth_cookie_deletion_names(values):
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    names = {value.strip().casefold() for value in values if isinstance(value, str)}
    return sorted(names & {"c_user", "xs"})


def response_auth_cookie_deletions(response):
    extensions = getattr(response, "extensions", None)
    directive = extensions.get("remask_meta_auth_cookie_directive") if isinstance(extensions, dict) else None
    return auth_cookie_deletion_names(directive.get("deletions")) if isinstance(directive, dict) else []


def auth_cookie_deletion_error(names, stage, *, request_may_have_been_sent=False,
                               request_rejected=False):
    from fb_worker import AuthenticationError
    error = AuthenticationError("Meta explicitly removed a selected-profile authentication cookie.")
    error.meta_payload = {"auth_cookie_deletions": names}
    error.request_may_have_been_sent = request_may_have_been_sent
    error.request_rejected = request_rejected
    error.transport_stage = stage
    return error


def observed_auth_cookie_deletions(exc):
    payload = getattr(exc, "meta_payload", None)
    return auth_cookie_deletion_names(payload.get("auth_cookie_deletions")) if isinstance(payload, dict) else []


def private_auth_error(exc):
    from fb_worker import AuthenticationError
    if not isinstance(exc, AuthenticationError):
        return None
    payload = getattr(exc, "meta_payload", {}) or {}
    attempts = payload.get("business_precheck", []) if isinstance(payload, dict) else []
    rows = [row for row in attempts if isinstance(row, dict)]
    reasons = {row.get("auth_reason") for row in rows}
    route_gates = {auth_route_gate(row.get("final_url")) for row in rows}
    selected_actor_confirmed = any(row.get("actor_verified") is True for row in rows)
    identity_unconfirmed = (
        not selected_actor_confirmed
        and any(reason in {"actor_mismatch", "actor_unobserved"} for reason in reasons)
    )
    # Generic transport messages say "login/checkpoint" for either route.
    # A Facebook login redirect or an unconfirmed selected actor takes
    # precedence over a Business-only gate from a later fallback route.
    if "CHECKPOINT_REQUIRED" in route_gates or "checkpoint_redirect" in reasons:
        code = "CHECKPOINT_REQUIRED"
    elif "META_LOGIN_REQUIRED" in route_gates or "meta_login_redirect" in reasons:
        code = "META_LOGIN_REQUIRED"
    elif "SESSION_EXPIRED" in route_gates:
        code = "META_LOGIN_REQUIRED"
    elif identity_unconfirmed:
        code = "PROFILE_IDENTITY_UNCONFIRMED"
    elif isinstance(payload, dict) and payload.get("bootstrap_fallback_exhausted") is True:
        code = "META_AUTH_TOKEN_UNAVAILABLE"
    elif "BUSINESS_LOGIN_GATE" in route_gates:
        code = "BUSINESS_LOGIN_GATE"
    elif not reasons and getattr(exc, 'transport_stage', '') == 'bootstrap':
        code = 'META_AUTH_TOKEN_UNAVAILABLE'
    else:
        code = "META_AUTH_TOKEN_UNAVAILABLE"
    unsent = getattr(exc, "request_may_have_been_sent", None) is False
    cookie_deletions = observed_auth_cookie_deletions(exc)
    message = ("Meta explicitly removed a selected-profile authentication cookie."
        if cookie_deletions else
        "Meta redirected the selected profile to its Ads Manager login flow; cookie age was not checked."
        if code == "META_LOGIN_REQUIRED" else
        "Meta's read-only page did not confirm the selected profile identity."
        if code == "PROFILE_IDENTITY_UNCONFIRMED" else
        "Meta returned the Business login gate; cookie age was not checked."
        if code == "BUSINESS_LOGIN_GATE" else
        "Meta did not provide a verified authentication context; cookie age was not checked.")
    message += (" No GraphQL POST was sent." if unsent else " Any retained submit must be verified before retry.")
    error = ProvisioningError(code, message, retryable=True)
    diagnostic = {"auth_gate": code,
        "transport_stage": str(getattr(exc, "transport_stage", "")),
        "observed_reasons": sorted(str(r) for r in reasons if r in
            ('checkpoint_redirect', 'meta_login_redirect', 'login_redirect', 'login_form', 'redirected_host', 'http_auth',
             'actor_mismatch', 'actor_unobserved')),
        "post_not_sent": unsent}
    if cookie_deletions:
        diagnostic["auth_cookie_deletions"] = cookie_deletions
    error.inventory_diagnostics = [diagnostic]
    return error
