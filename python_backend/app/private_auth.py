"""Preserve authentication failures across private action boundaries."""
from __future__ import annotations

from .provisioning.models import ProvisioningError


def private_auth_error(exc):
    from fb_worker import AuthenticationError
    if not isinstance(exc, AuthenticationError):
        return None
    payload = getattr(exc, "meta_payload", {}) or {}
    attempts = payload.get("business_precheck", []) if isinstance(payload, dict) else []
    reasons = {row.get("auth_reason") for row in attempts if isinstance(row, dict)}
    reauth_required = any(
        isinstance(row, dict)
        and row.get("business_login_handoff_outcome") == "continuation_redirected_to_auth"
        for row in attempts
    )
    # Generic transport messages say "login/checkpoint" for either route.
    # Only structured evidence of the actual redirect proves a checkpoint.
    if "checkpoint_redirect" in reasons:
        code = "CHECKPOINT_REQUIRED"
    elif reauth_required:
        code = "META_REAUTH_REQUIRED"
    elif any(isinstance(row, dict) and "/business/loginpage" in str(row.get("final_url", "")) for row in attempts):
        code = "BUSINESS_LOGIN_GATE"
    elif not reasons and getattr(exc, 'transport_stage', '') == 'bootstrap':
        code = 'META_AUTH_TOKEN_UNAVAILABLE'
    else:
        code = "SESSION_EXPIRED"
    unsent = getattr(exc, "request_may_have_been_sent", None) is False
    if code == "META_REAUTH_REQUIRED":
        message = "Meta redirected the saved profile to its sign-in flow after the Business continuation."
    elif code == "BUSINESS_LOGIN_GATE":
        message = "The worker's Meta Business HTTP session requires restoration."
    else:
        message = "The selected profile's Meta HTTP session requires restoration."
    message += (" No GraphQL POST was sent." if unsent else " Any retained submit must be verified before retry.")
    error = ProvisioningError(code, message, retryable=not reauth_required)
    error.inventory_diagnostics = [{"auth_gate": code,
        "transport_stage": str(getattr(exc, "transport_stage", "")),
        "observed_reasons": sorted(str(r) for r in reasons if r in
            ('checkpoint_redirect', 'login_redirect', 'login_form', 'redirected_host', 'http_auth')),
        "business_login_handoff_outcome": (
            "continuation_redirected_to_auth" if reauth_required else ""
        ),
        "reauth_required": reauth_required,
        "post_not_sent": unsent}]
    return error
