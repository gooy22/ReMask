"""Page CREATE over profile HTTP, followed by independent inventory proof."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .fan_page_contracts import valid_page_create
from .facebook_business_browser import BrowserBusinessError
from .facebook_fan_page_create import confirmed_created_page
from .facebook_docids import classify_cache_failure

log = logging.getLogger("remask.python_worker")


async def create_fan_page_private(
    web: Any, contract: dict[str, Any], *, actor_id: str, page_name: str,
    category: str, before_ids: set[str], before_submit: Callable,
    verify: Callable, store: Any,
) -> dict[str, Any]:
    if not valid_page_create(contract, name=page_name, actor_id=actor_id):
        raise BrowserBusinessError("FAN_PAGE_CREATE_CONTRACT_INVALID",
                                   "Page CREATE contract is not scoped to this profile and name; no POST was sent.",
                                   retryable=False, diagnostic={"safe_before_submit": True})
    submitted = False

    def retire_stale(payload: Any, exception: Exception | None = None) -> None:
        if (isinstance(payload, dict) and not payload.get("data")
                and (payload.get("errors") or payload.get("error"))
                and classify_cache_failure(payload=payload, exception=exception) == "stale_schema"):
            try:
                store.set_status(category=category, doc_id=contract["doc_id"], status="stale")
                log.info("FP contract stage=invalidated doc_id=%s reason=stale_schema", contract["doc_id"])
            except OSError:
                log.warning("FP stale contract cache write failed")

    async def intent() -> None:
        nonlocal submitted
        await before_submit({"page_name": page_name, "category": category,
                             "before_ids": sorted(before_ids),
                             "transport": "facebook_private_http_contract"})
        submitted = True
        log.info("FP contract stage=submit_intent doc_id=%s source=%s",
                 contract["doc_id"], contract.get("source", ""))

    try:
        payload = await web.graphql(
            contract["doc_id"], contract["variables"],
            friendly_name=contract["friendly_name"],
            endpoint_url=contract["endpoint_url"], before_submit=intent,
        )
    except Exception as exc:
        retire_stale(getattr(exc, "meta_payload", None), exc)
        may_sent = getattr(exc, "request_may_have_been_sent", None)
        if type(exc).__name__ == "AuthenticationError":
            raise BrowserBusinessError("SESSION_EXPIRED",
                                       "Facebook requires profile session restoration before Page verification can continue.",
                                       retryable=False, diagnostic={"safe_before_submit": may_sent is False or not submitted,
                                                                   "page_name": page_name}) from exc
        if may_sent is False or not submitted:
            raise BrowserBusinessError("FAN_PAGE_CREATE_PRE_SUBMIT_TRANSPORT",
                                       "Page HTTP session precheck failed; no CREATE POST was sent.",
                                       retryable=True, diagnostic={"safe_before_submit": True,
                                                                  "error_type": type(exc).__name__}) from exc
        # Any exception after the durable intent is uncertain. The handler
        # retains that intent and reconciles; it must not capture or POST again.
        raise BrowserBusinessError("FAN_PAGE_CREATE_RESULT_UNKNOWN",
                                   "Page HTTP CREATE may have reached Meta; verify before another submission.",
                                   retryable=True, diagnostic={"stage": "fan_page_private_http_unknown",
                                                              "error_type": type(exc).__name__,
                                                              "page_name": page_name}) from exc

    retire_stale(payload)
    meta = {"method": "POST", "url": contract["endpoint_url"],
            "friendly_name": contract["friendly_name"], "body_decodable": True,
            "input": contract["variables"].get("input"), "actor_ids": [actor_id]}
    confirmed = confirmed_created_page(meta, payload, actor_id=actor_id,
                                       page_name=page_name, before_ids=before_ids)
    # A direct mutation ID is useful evidence, but never replaces a fresh
    # managed-Page inventory. Partial/error responses may still have committed.
    try:
        rows = await asyncio.wait_for(verify(), timeout=25.0)
    except Exception as exc:
        raise BrowserBusinessError("FAN_PAGE_CREATE_RESULT_UNKNOWN",
                                   "Page CREATE response was received, but fresh managed-Page verification is unavailable.",
                                   retryable=True, diagnostic={"stage": "fan_page_private_verify_unavailable",
                                                              "page_name": page_name,
                                                              "response_page_id": confirmed["id"] if confirmed else "",
                                                              "error_type": type(exc).__name__}) from exc
    matches = [row for row in rows if isinstance(row, dict)
               and str(row.get("id") or "").isdigit()
               and str(row["id"]) not in before_ids
               and str(row.get("name") or "").strip() == page_name
               and not str(row.get("business_id") or "").strip()]
    if len(matches) != 1 or (confirmed and str(matches[0]["id"]) != confirmed["id"]):
        raise BrowserBusinessError("FAN_PAGE_CREATE_RESULT_UNKNOWN",
                                   "Fresh inventory did not uniquely confirm the created Page; duplicate protection retained.",
                                   retryable=True, diagnostic={"stage": "fan_page_private_verify_inconclusive",
                                                              "page_name": page_name,
                                                              "response_page_id": confirmed["id"] if confirmed else "",
                                                              "matching_count": len(matches)})
    page_id = str(matches[0]["id"])
    # An uncertain response reconciled by inventory completes this action,
    # but does not certify that the cached response schema remains valid.
    if confirmed:
        try:
            store.set_status(category=category, doc_id=contract["doc_id"], status="verified")
        except OSError:
            log.warning("FP contract verification cache write failed")
    log.info("FP contract stage=verified doc_id=%s page_id=%s response_confirmed=%s",
             contract["doc_id"], page_id, bool(confirmed))
    return {"page_id": page_id, "name": page_name, "category": category,
            "reused": False, "before_ids": sorted(before_ids),
            "after_ids": sorted(before_ids | {page_id}),
            "transport": "facebook_private_http_contract_verified"}
