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
    # Generic transport messages say "login/checkpoint" for either route.
    # Only structured evidence of the actual redirect proves a checkpoint.
    if "checkpoint_redirect" in reasons:
        code = "CHECKPOINT_REQUIRED"
    elif any(isinstance(row, dict) and "/business/loginpage" in str(row.get("final_url", "")) for row in attempts):
        code = "BUSINESS_LOGIN_GATE"
    else:
        code = "SESSION_EXPIRED"
    unsent = getattr(exc, "request_may_have_been_sent", None) is False
    message = ("The worker's Meta Business HTTP session requires restoration."
        if code == "BUSINESS_LOGIN_GATE" else "The selected profile's Meta HTTP session requires restoration.")
    message += (" No GraphQL POST was sent." if unsent else " Any retained submit must be verified before retry.")
    error = ProvisioningError(code, message, retryable=True)
    error.inventory_diagnostics = [{"auth_gate": code,
        "transport_stage": str(getattr(exc, "transport_stage", "")),
        "post_not_sent": unsent}]
    return error
