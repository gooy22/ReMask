from __future__ import annotations

from typing import Any, Awaitable, Callable

from .business_handler import business_handler
from .ad_account_handler import ad_account_handler
from .funding_handler import funding_handler

Handler = Callable[..., Awaitable[dict[str, Any]]]

PROVISIONING_HANDLERS: dict[str, Handler] = {
    "BUSINESS": business_handler,
    "AD_ACCOUNT": ad_account_handler,
    "FUNDING": funding_handler,
}


def get_handler(step: str) -> Handler:
    normalized = step.strip().upper()
    try:
        return PROVISIONING_HANDLERS[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported provisioning handler: {normalized}") from exc
