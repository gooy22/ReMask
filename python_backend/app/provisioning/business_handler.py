# python_backend/app/provisioning/business_handler.py

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from fb_worker import ProxyError, RemoteRequestError

from ..facebook_business_create import (
    BusinessMutationError,
    attach_page_to_business,
    create_business_manager_v2,
)
from .meta_errors import classify_meta_request_error
from .models import ProvisioningError


log = logging.getLogger("remask_worker")


DATA_ROOT = (
    os.getenv("REMASK_DATA_DIR")
    or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    or "/var/lib/remask"
)

JOB_DB_PATH = Path(
    os.getenv(
        "REMASK_JOB_DB",
        os.path.join(DATA_ROOT, "python-worker", "jobs.sqlite3"),
    )
)


def _now() -> int:
    return int(time.time())


def _checkpoint_connection() -> sqlite3.Connection:
    JOB_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(
        JOB_DB_PATH,
        timeout=30,
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _decode_result_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)

    text = str(raw or "").strip()
    if not text:
        return {}

    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}

    return decoded if isinstance(decoded, dict) else {}


def _load_business_checkpoint(
    *,
    profile_id: str,
    scope_key: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    state_business_id = str(
        state.get("business_id")
        or ""
    ).strip()

    state_resume_from = str(
        state.get("resume_from")
        or ""
    ).strip().upper()

    if (
        state_business_id
        and state_business_id.isdigit()
        and state_resume_from == "ATTACH_PAGE"
    ):
        return {
            "business_id": state_business_id,
            "resume_from": "ATTACH_PAGE",
            "source": "state",
        }

    if not JOB_DB_PATH.is_file():
        return {}

    try:
        with _checkpoint_connection() as con:
            row = con.execute(
                """
                SELECT
                    item_id,
                    result_json,
                    status,
                    attempt,
                    updated_at
                FROM provisioning_steps
                WHERE profile_id=?
                  AND scope_key=?
                  AND step='BUSINESS'
                ORDER BY
                    CASE
                        WHEN status='RUNNING' THEN 0
                        WHEN status='FAILED' THEN 1
                        ELSE 2
                    END,
                    updated_at DESC
                LIMIT 1
                """,
                (
                    profile_id,
                    scope_key,
                ),
            ).fetchone()

            if not row:
                return {}

            result = _decode_result_json(
                row["result_json"]
            )

            business_id = str(
                result.get("business_id")
                or ""
            ).strip()

            resume_from = str(
                result.get("resume_from")
                or ""
            ).strip().upper()

            if (
                business_id
                and business_id.isdigit()
                and resume_from == "ATTACH_PAGE"
            ):
                return {
                    **result,
                    "business_id": business_id,
                    "resume_from": "ATTACH_PAGE",
                    "item_id": str(
                        row["item_id"]
                        or ""
                    ),
                    "source": "provisioning_steps",
                }

    except sqlite3.Error as exc:
        log.error(
            "[%s] failed reading BUSINESS checkpoint: %s",
            profile_id,
            exc,
        )

    return {}


def _persist_business_checkpoint(
    *,
    profile_id: str,
    scope_key: str,
    business_id: str,
    page_id: str,
    business_name: str,
    create_doc_id: str = "",
    create_friendly_name: str = "",
    create_source: str = "",
    attach_error: str = "",
) -> dict[str, Any]:
    clean_business_id = str(
        business_id
        or ""
    ).strip()

    if not clean_business_id.isdigit():
        raise RuntimeError(
            "cannot persist BUSINESS checkpoint without valid business_id"
        )

    now = _now()

    with _checkpoint_connection() as con:
        con.execute("BEGIN IMMEDIATE")

        row = con.execute(
            """
            SELECT
                item_id,
                result_json
            FROM provisioning_steps
            WHERE profile_id=?
              AND scope_key=?
              AND step='BUSINESS'
            ORDER BY
                CASE
                    WHEN status='RUNNING' THEN 0
                    WHEN status='FAILED' THEN 1
                    ELSE 2
                END,
                updated_at DESC
            LIMIT 1
            """,
            (
                profile_id,
                scope_key,
            ),
        ).fetchone()

        if not row:
            con.rollback()
            raise RuntimeError(
                "BUSINESS provisioning step row was not found for checkpoint"
            )

        existing = _decode_result_json(
            row["result_json"]
        )

        attach_attempts = int(
            existing.get("attach_attempts")
            or 0
        )

        if attach_error:
            attach_attempts += 1

        checkpoint = {
            **existing,
            "business_id": clean_business_id,
            "business_name": str(
                business_name
                or ""
            ).strip(),
            "primary_page_id": str(
                page_id
                or ""
            ).strip(),
            "resume_from": "ATTACH_PAGE",
            "create_doc_id": str(
                create_doc_id
                or existing.get("create_doc_id")
                or ""
            ).strip(),
            "create_friendly_name": str(
                create_friendly_name
                or existing.get("create_friendly_name")
                or ""
            ).strip(),
            "create_source": str(
                create_source
                or existing.get("create_source")
                or ""
            ).strip(),
            "attach_attempts": attach_attempts,
            "last_attach_error": str(
                attach_error
                or ""
            )[:4000],
            "checkpoint_updated_at": now,
        }

        encoded = json.dumps(
            checkpoint,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        cursor = con.execute(
            """
            UPDATE provisioning_steps
            SET
                result_json=?,
                updated_at=?
            WHERE item_id=?
              AND step='BUSINESS'
            """,
            (
                encoded,
                now,
                str(
                    row["item_id"]
                    or ""
                ),
            ),
        )

        if cursor.rowcount != 1:
            con.rollback()
            raise RuntimeError(
                "BUSINESS checkpoint update affected unexpected row count"
            )

        con.commit()

    return checkpoint


async def business_handler(
    session: Any,
    params: dict[str, Any],
    state: dict[str, Any],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    context = session.context

    profile_id = str(
        state.get("profile_id")
        or getattr(
            context,
            "profile_id",
            "",
        )
        or ""
    ).strip()

    scope_key = str(
        state.get("scope_key")
        or params.get("scope_key")
        or "default"
    ).strip() or "default"

    if (
        not profile_id
        or profile_id.lower() == "none"
    ):
        raise ProvisioningError(
            "INVALID_INPUT",
            "profile_id is missing or invalid",
            retryable=False,
        )

    bm_name = str(
        params.get("name")
        or params.get("bm_name")
        or ""
    ).strip()

    if not bm_name:
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.name is required",
            retryable=False,
        )

    if len(bm_name) > 255:
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.name is too long",
            retryable=False,
        )

    page_id = str(
        params.get("page_id")
        or params.get("primary_page_id")
        or ""
    ).strip()

    if not re.fullmatch(
        r"\d{5,30}",
        page_id,
    ):
        raise ProvisioningError(
            "INVALID_PRIMARY_PAGE",
            "BUSINESS.page_id must be a numeric Facebook Page ID",
            retryable=False,
        )

    user_email = str(
        params.get("user_email")
        or params.get("email")
        or getattr(
            context,
            "email",
            "",
        )
        or ""
    ).strip()

    if not user_email:
        raise ProvisioningError(
            "BUSINESS_EMAIL_REQUIRED",
            "BUSINESS.user_email is required by current private Business creation flow",
            retryable=False,
        )

    if not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        user_email,
    ):
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.user_email is invalid",
            retryable=False,
        )

    first_name = str(
        params.get("user_first_name")
        or params.get("first_name")
        or getattr(
            context,
            "first_name",
            "",
        )
        or ""
    ).strip()

    last_name = str(
        params.get("user_last_name")
        or params.get("last_name")
        or getattr(
            context,
            "last_name",
            "",
        )
        or ""
    ).strip()

    display_name = str(
        getattr(
            context,
            "display_name",
            "",
        )
        or profile_id
    ).strip()

    manual_doc_id = str(
        params.get("manual_doc_id")
        or params.get("doc_id")
        or ""
    ).strip()

    if (
        manual_doc_id
        and not re.fullmatch(
            r"\d{5,40}",
            manual_doc_id,
        )
    ):
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.manual_doc_id must contain 5-40 digits",
            retryable=False,
        )

    checkpoint = _load_business_checkpoint(
        profile_id=profile_id,
        scope_key=scope_key,
        state=state,
    )

    resumed = bool(
        checkpoint.get("business_id")
        and str(
            checkpoint.get("resume_from")
            or ""
        ).upper()
        == "ATTACH_PAGE"
    )

    if resumed:
        business_id = str(
            checkpoint["business_id"]
        ).strip()

        log.warning(
            "[%s] BUSINESS resume checkpoint business_id=%s page_id=%s",
            profile_id,
            business_id,
            page_id,
        )

    else:
        log.info(
            "[%s] BUSINESS CREATE start name=%s page=%s manual_doc_id=%s",
            profile_id,
            bm_name,
            page_id,
            manual_doc_id or "<none>",
        )

        try:
            create_result = await create_business_manager_v2(
                session,
                params={
                    "name": bm_name,
                    "user_email": user_email,
                    "user_first_name": first_name,
                    "user_last_name": last_name,
                    "profile_display_name": display_name,
                    "manual_doc_id": manual_doc_id,
                },
                profile_id=profile_id,
            )

        except BusinessMutationError as exc:
            raise ProvisioningError(
                exc.code,
                str(exc),
                retryable=exc.retryable,
            ) from exc

        business_id = str(
            create_result.business_id
            or ""
        ).strip()

        if not business_id.isdigit():
            raise ProvisioningError(
                "INVALID_RESULT",
                "CREATE_BM returned invalid business_id",
                retryable=False,
            )

        try:
            checkpoint = _persist_business_checkpoint(
                profile_id=profile_id,
                scope_key=scope_key,
                business_id=business_id,
                page_id=page_id,
                business_name=bm_name,
                create_doc_id=create_result.candidate.doc_id,
                create_friendly_name=create_result.candidate.friendly_name,
                create_source=create_result.candidate.source,
            )

        except Exception as exc:
            log.critical(
                "[%s] BUSINESS created id=%s but checkpoint persistence failed: %s",
                profile_id,
                business_id,
                exc,
            )

            raise ProvisioningError(
                "BUSINESS_CREATE_CHECKPOINT_FAILED",
                (
                    f"Business {business_id} was created but ReMask could not "
                    "persist resume_from=ATTACH_PAGE. Do not retry CREATE automatically."
                ),
                retryable=False,
            ) from exc

    try:
        attach_result = await attach_page_to_business(
            session,
            business_id=business_id,
            business_name=bm_name,
            page_id=page_id,
            profile_id=profile_id,
        )

    except Exception as exc:
        try:
            _persist_business_checkpoint(
                profile_id=profile_id,
                scope_key=scope_key,
                business_id=business_id,
                page_id=page_id,
                business_name=bm_name,
                create_doc_id=str(
                    checkpoint.get("create_doc_id")
                    or ""
                ),
                create_friendly_name=str(
                    checkpoint.get("create_friendly_name")
                    or ""
                ),
                create_source=str(
                    checkpoint.get("create_source")
                    or ""
                ),
                attach_error=str(exc),
            )
        except Exception as checkpoint_exc:
            log.error(
                "[%s] failed updating ATTACH_PAGE checkpoint: %s",
                profile_id,
                checkpoint_exc,
            )

        if isinstance(
            exc,
            BusinessMutationError,
        ):
            detail = str(exc)
        else:
            detail = (
                f"{exc.__class__.__name__}: {exc}"
            )

        raise ProvisioningError(
            "BUSINESS_CREATED_PAGE_ATTACH_FAILED",
            (
                f"Business {business_id} already exists. "
                f"resume_from=ATTACH_PAGE. "
                f"Primary Page {page_id} attachment failed: {detail}"
            ),
            retryable=True,
        ) from exc

    return {
        "business_id": business_id,
        "primary_page_id": page_id,
        "resume_from": "DONE",
        "resumed": resumed,
        "transport": (
            "facebook_web_graphql_scope_selector_plus_primary_page"
        ),
        "create": {
            "doc_id": str(
                checkpoint.get("create_doc_id")
                or ""
            ),
            "friendly_name": str(
                checkpoint.get("create_friendly_name")
                or ""
            ),
            "source": str(
                checkpoint.get("create_source")
                or ""
            ),
        },
        "attach": {
            "doc_id": attach_result.candidate.doc_id,
            "friendly_name": attach_result.candidate.friendly_name,
            "source": attach_result.candidate.source,
        },
    }
