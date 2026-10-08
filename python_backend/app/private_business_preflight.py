"""Read-only readiness of the production BM HTTP action, without UI startup."""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

from .facebook_business_create import CREATE_BM_OPERATION, discover_current_scope_selector_create_candidate
from .facebook_docids import list_candidates
from .provisioning.private_create_handlers import _business_inventory


async def _inspect(session, context):
    state = {"session_ready": False, "contract_ready": False, "inventory_complete": False,
        "browser_started": False, "transport": "private_http", "error_code": "", "error": ""}
    try:
        async with asyncio.timeout(55):
            web = await session.facebook_web()
            web.private_only = True
            bootstrap = await web.bootstrap()
            bootstrap = await web._business_bootstrap(bootstrap, business_create=True)
            actor = str(getattr(bootstrap, "actor_id", "") or "")
            current = str((getattr(context, "cookies", {}) or {}).get("c_user") or "")
            if not current.isdigit() or actor != current or not getattr(bootstrap, "fb_dtsg", ""):
                state.update(error_code="SESSION_EXPIRED", error="Private BM authentication did not confirm the current profile.")
                return state
            state.update(session_ready=True, actor_present=True, fb_dtsg_present=True,
                lsd_present=bool(getattr(bootstrap, "lsd", "")), jazoest_present=bool(getattr(bootstrap, "jazoest", "")))
            source = urlsplit(str(getattr(bootstrap, "source_url", "") or ""))
            state["auth_document"] = source.scheme + "://" + (source.hostname or "") + source.path
            inventory = await asyncio.wait_for(_business_inventory(web), timeout=25)
            state.update(inventory_complete=inventory["complete"],
                businesses=[{"id": key, "name": value} for key, value in inventory["rows"].items()])
            try:
                candidate = await asyncio.wait_for(discover_current_scope_selector_create_candidate(web), timeout=15)
            except Exception as exc:
                if (exc.__class__.__name__ == "AuthenticationError"
                        or getattr(exc, "code", "") in {"SESSION_EXPIRED", "CHECKPOINT_REQUIRED", "BUSINESS_LOGIN_GATE"}):
                    raise
                candidate = None
            confirmed = list_candidates(CREATE_BM_OPERATION, confirmed_only=True)
            candidates = [candidate] if candidate is not None else confirmed
            state["candidates"] = [{"doc_id": row.doc_id, "friendly_name": row.friendly_name,
                "source": row.source} for row in candidates]
            state["contract_ready"] = bool(candidates)
            if not inventory["complete"]:
                state.update(error_code="PRIVATE_BM_INVENTORY_INCONCLUSIVE",
                    error="Private BM inventory did not confirm completeness; CREATE readiness is unconfirmed.")
            elif not candidates:
                state.update(error_code="PRIVATE_BM_CONTRACT_UNCONFIRMED",
                    error="No current or confirmed private BM mutation candidate is available.")
    except TimeoutError:
        state.update(error_code="PRIVATE_BM_PREFLIGHT_TIMEOUT", error="Private BM readiness exceeded its HTTP budget.")
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc).casefold()
        if "checkpoint" in message:
            code = "CHECKPOINT_REQUIRED"
        elif exc.__class__.__name__ == "AuthenticationError":
            code = "SESSION_EXPIRED"
        if code in {"CHECKPOINT_REQUIRED", "SESSION_EXPIRED", "BUSINESS_LOGIN_GATE"}:
            state["session_ready"] = False
        state.update(error_code=code or "PRIVATE_BM_PREFLIGHT_INCONCLUSIVE",
            error="Private BM readiness was not confirmed (" + exc.__class__.__name__ + ").")
    return state


async def private_business_preflight(session, context, profile_id):
    started = time.monotonic()
    # Both checks use the same profile/proxy and are drained before returning.
    proxy_result, inspection = await asyncio.gather(session.proxy_check(), _inspect(session, context), return_exceptions=True)
    if isinstance(proxy_result, BaseException):
        raise proxy_result
    if isinstance(inspection, BaseException):
        raise inspection
    auth_code = inspection.get("error_code", "")
    auth_blocked = auth_code in {"CHECKPOINT_REQUIRED", "SESSION_EXPIRED", "BUSINESS_LOGIN_GATE"}
    ready = bool(inspection["session_ready"] and inspection["contract_ready"] and inspection["inventory_complete"] and not auth_blocked)
    pages = [{key: row[key] for key in ("id", "name", "category", "tasks", "business_id", "is_owned", "ownership_verified", "ownership_source") if key in row}
        for row in (getattr(context, "pages", []) or [])
        if isinstance(row, dict) and str(row.get("id", "")).isdigit()]
    return {"ok": True, "profile_id": str(profile_id), "purpose": "business",
        "profile_context": "ok", "proxy": "ok", "proxy_exit_ip": str(proxy_result.get("exit_ip", "")),
        "proxy_latency_ms": int(proxy_result.get("latency_ms", 0)), "total_ms": int((time.monotonic()-started)*1000),
        "facebook_session": "private_http", "facebook_session_ready": inspection["session_ready"],
        "auth_blocked": auth_blocked, "auth_error_code": auth_code if auth_blocked else "",
        "browser_business": {"ready": False, "session_ready": inspection["session_ready"],
            "create_surface_ready": False, "browser_started": False, "transport": "private_http", "error_code": ""},
        "private_business": inspection, "web_error": inspection.get("error", ""),
        **{key: inspection.get(key, False) for key in ("actor_present", "fb_dtsg_present", "lsd_present", "jazoest_present")},
        "graph_api": {"ready": False, "token_present": False, "error": "not used by Add BM"},
        "private_pages": {"ready": False, "pages": [], "error": "BM readiness is independent of Page inventory"},
        "pages": pages, "saved_pages_count": len(pages), "pages_count": len(pages),
        "pages_source": "saved_profile_pages" if pages else "",
        "businesses": inspection.get("businesses", []), "businesses_count": len(inspection.get("businesses", [])),
        "create_bm_candidates": inspection.get("candidates", []),
        **{key+"_present": bool(getattr(context, key, "")) for key in ("email", "first_name", "last_name", "display_name")},
        "bm_routes": {"browser_ui": False, "official_graph_api": False, "web_page_backed_candidate": False,
            "web_scope_selector_candidate": inspection["contract_ready"], "web_dynamic_or_manual": inspection["contract_ready"]},
        "bm_route_ready": ready, "browser_started": False, "transport": "private_http"}
