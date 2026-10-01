from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .models import ENTITY_RESULT_KEYS, ProvisioningSnapshot, ProvisioningStep


def _now() -> int:
    return int(time.time())


def _capture_ui_confirmed_ad_account_id(result: Any) -> str:
    """Extract an RK ID only from explicit saved Meta success UI evidence."""
    if not isinstance(result, dict):
        return ""

    business_id = str(result.get("business_id") or "").strip()
    account_name = str(
        result.get("account_name")
        or result.get("name")
        or ""
    ).strip()
    if not business_id.isdigit() or not account_name:
        return ""

    diagnostics: list[dict[str, Any]] = []
    direct = result.get("browser_diagnostic")
    if isinstance(direct, dict):
        diagnostics.append(direct)

    for failure in result.get("capture_failures") or []:
        if not isinstance(failure, dict):
            continue
        diagnostic = failure.get("diagnostic")
        if isinstance(diagnostic, dict):
            diagnostics.append(diagnostic)

    expected = account_name.casefold()
    success_markers = (
        "ad account created successfully",
        "advertising account created successfully",
        "has been created and added to the",
        "compte publicitaire a été créé",
        "рекламный аккаунт создан",
        "рекламний акаунт створено",
        "विज्ञापन अकाउंट बनाया गया",
        "विज्ञापन खाता बनाया गया",
        "tài khoản quảng cáo đã được tạo",
        "বিজ্ঞাপন অ্যাকাউন্ট তৈরি করা হয়েছে",
    )

    for diagnostic in diagnostics:
        ui_state = diagnostic.get("ui_state")
        if not isinstance(ui_state, dict):
            continue

        dialogs = " ".join(
            str(value or "").strip()
            for value in (ui_state.get("dialogs") or [])
            if str(value or "").strip()
        )
        folded = dialogs.casefold()
        if expected not in folded:
            continue
        if not any(marker in folded for marker in success_markers):
            continue

        controls = " ".join(
            str(value or "").strip()
            for value in (ui_state.get("controls") or [])
            if str(value or "").strip()
        )
        ids: list[str] = []
        for raw in re.findall(r"(?<!\d)(\d{8,30})(?!\d)", controls):
            if raw == business_id or raw in ids:
                continue
            ids.append(raw)

        if len(ids) == 1:
            return ids[0]

    return ""


class ProvisioningStateStore:
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            with con:
                yield con
        finally:
            con.close()

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS provisioning_entities (
                    profile_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    business_id TEXT,
                    ad_account_id TEXT,
                    funding_source_id TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(profile_id, scope_key)
                );

                CREATE TABLE IF NOT EXISTS provisioning_steps (
                    item_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    step TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(item_id, step)
                );

                CREATE INDEX IF NOT EXISTS idx_provisioning_steps_profile
                    ON provisioning_steps(profile_id, scope_key, updated_at);
                """
            )
            con.execute(
                "UPDATE provisioning_steps SET status='QUEUED',updated_at=? WHERE status='RUNNING'",
                (_now(),),
            )

    async def snapshot(self, profile_id: str, scope_key: str) -> ProvisioningSnapshot:
        return await asyncio.to_thread(self._snapshot_sync, profile_id, scope_key)

    def _snapshot_sync(self, profile_id: str, scope_key: str) -> ProvisioningSnapshot:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT profile_id,scope_key,business_id,ad_account_id,funding_source_id
                FROM provisioning_entities
                WHERE profile_id=? AND scope_key=?
                """,
                (profile_id, scope_key),
            ).fetchone()
            if not row:
                return ProvisioningSnapshot(profile_id=profile_id, scope_key=scope_key)
            return ProvisioningSnapshot(
                profile_id=str(row["profile_id"]),
                scope_key=str(row["scope_key"]),
                business_id=row["business_id"],
                ad_account_id=row["ad_account_id"],
                funding_source_id=row["funding_source_id"],
            )

    async def step(self, item_id: str, step: ProvisioningStep) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._step_sync, item_id, step)

    def _step_sync(self, item_id: str, step: ProvisioningStep) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM provisioning_steps WHERE item_id=? AND step=?",
                (item_id, step.value),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            if data.get("result_json"):
                data["result"] = json.loads(data["result_json"])
            data.pop("result_json", None)
            return data

    async def latest_business_resume_for_page(
        self,
        profile_id: str,
        page_id: str,
        *,
        exclude_item_id: str = "",
    ) -> dict[str, Any]:
        """
        Return the newest resumable BUSINESS checkpoint for profile+Page.

        This deliberately crosses scope_key/item boundaries so Jobs created
        before stable Add-BM scopes can still protect later Jobs from duplicate
        Business creation.
        """
        return await asyncio.to_thread(
            self._latest_business_resume_for_page_sync,
            profile_id,
            page_id,
            exclude_item_id,
        )

    def _latest_business_resume_for_page_sync(
        self,
        profile_id: str,
        page_id: str,
        exclude_item_id: str,
    ) -> dict[str, Any]:
        profile = str(profile_id or "").strip()
        page = str(page_id or "").strip()
        excluded = str(exclude_item_id or "").strip()
        if not profile or not page:
            return {}

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT item_id,scope_key,status,result_json,error_code,error_message,
                       created_at,updated_at
                FROM provisioning_steps
                WHERE profile_id=? AND step=? AND result_json IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 250
                """,
                (profile, ProvisioningStep.BUSINESS.value),
            ).fetchall()

        for row in rows:
            if excluded and str(row["item_id"] or "") == excluded:
                continue

            raw = row["result_json"]
            if not raw:
                continue
            try:
                result = json.loads(str(raw))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue

            candidate_page = str(
                result.get("selected_page_id")
                or result.get("primary_page_id")
                or result.get("page_id")
                or ""
            ).strip()
            if candidate_page != page:
                continue

            business_id = str(result.get("business_id") or "").strip()
            response_id = str(
                result.get("create_response_business_id")
                or result.get("response_business_id")
                or ""
            ).strip()
            response_path = str(
                result.get("create_response_path")
                or result.get("response_path")
                or ""
            ).strip()
            phase = str(
                result.get("phase")
                or result.get("resume_from")
                or ""
            ).strip().upper()

            numeric_business = (
                business_id.isdigit()
                and 5 <= len(business_id) <= 30
            )
            numeric_response = (
                response_id.isdigit()
                and 5 <= len(response_id) <= 30
            )
            uncertain_create = phase in {
                "CREATE_SUBMITTED",
                "CREATE_CLICK_INTENT",
                "CREATE_PENDING_SUBMIT",
                "CREATE_RESULT_UNKNOWN",
            }

            if not numeric_business and not numeric_response and not uncertain_create:
                continue

            return {
                "item_id": str(row["item_id"] or ""),
                "scope_key": str(row["scope_key"] or ""),
                "status": str(row["status"] or ""),
                "error_code": str(row["error_code"] or ""),
                "error_message": str(row["error_message"] or ""),
                "result": result,
                "updated_at": int(row["updated_at"] or 0),
                "response_path": response_path,
            }

        return {}

    async def latest_ad_account_resume_for_business(
        self,
        profile_id: str,
        business_id: str,
        *,
        exclude_item_id: str = "",
    ) -> dict[str, Any]:
        """
        Return the newest confirmed or uncertain AD_ACCOUNT checkpoint for a
        profile+Business. This protects a later Job from creating a second RK
        after an earlier CREATE had an ambiguous response.
        """
        return await asyncio.to_thread(
            self._latest_ad_account_resume_for_business_sync,
            profile_id,
            business_id,
            exclude_item_id,
        )

    def _latest_ad_account_resume_for_business_sync(
        self,
        profile_id: str,
        business_id: str,
        exclude_item_id: str,
    ) -> dict[str, Any]:
        profile = str(profile_id or "").strip()
        business = str(business_id or "").strip()
        excluded = str(exclude_item_id or "").strip()
        if not profile or not business:
            return {}

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT item_id,scope_key,status,result_json,error_code,error_message,
                       created_at,updated_at
                FROM provisioning_steps
                WHERE profile_id=? AND step=? AND result_json IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 250
                """,
                (profile, ProvisioningStep.AD_ACCOUNT.value),
            ).fetchall()

        uncertain = {
            # Final UI click may have reached Meta even if the GraphQL gate or
            # worker process died before CREATE_SUBMITTED was persisted.
            # Reconcile inventory before ever allowing another CREATE.
            "CREATE_CLICK_INTENT",
            "CREATE_SUBMIT_INTENT",
            "CREATE_SUBMITTED",
            "CREATE_RESULT_UNKNOWN",
            "CREATE_RESULT_UNVERIFIED",
            "RECONCILE_CREATE",
        }

        for row in rows:
            if excluded and str(row["item_id"] or "") == excluded:
                continue

            raw = row["result_json"]
            if not raw:
                continue
            try:
                result = json.loads(str(raw))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue

            if str(result.get("business_id") or "").strip() != business:
                continue

            raw_id = str(result.get("ad_account_id") or "").strip()
            numeric_id = raw_id[4:] if raw_id.lower().startswith("act_") else raw_id

            phase = str(
                result.get("phase")
                or result.get("resume_from")
                or ""
            ).strip().upper()
            confirmed = (
                numeric_id.isdigit()
                and 5 <= len(numeric_id) <= 30
                and phase != "CREATE_RESULT_UNVERIFIED"
            )

            # A newer explicit CREATE_NOT_SUBMITTED checkpoint is authoritative
            # evidence that this later Job did not send CREATE. Because rows are
            # ordered newest-first, it supersedes any older ambiguous
            # CREATE_RESULT_UNKNOWN/CLICK_INTENT for the same Business. Without
            # this tombstone, a fixed pre-submit Job can still be blocked forever
            # by stale uncertainty from an older attempt.
            if not confirmed and phase == "CREATE_NOT_SUBMITTED":
                return {}

            if not confirmed and phase not in uncertain:
                continue

            # Skip derivative duplicate-guard failures that never interacted
            # with Meta. Otherwise a guard-only Job becomes the newest
            # uncertain row and hides the original browser diagnostic that
            # actually tells us whether CREATE may have been sent.
            error_code = str(row["error_code"] or "").strip()
            error_message = str(row["error_message"] or "").strip()
            browser_diagnostic = (
                result.get("browser_diagnostic")
                if isinstance(result.get("browser_diagnostic"), dict)
                else {}
            )
            activity = str(result.get("activity") or "").strip().upper()

            guard_message = "previous job may already have submitted create for business"
            combined_error = " ".join(
                [
                    error_code,
                    error_message,
                    str(result.get("last_error_code") or ""),
                    str(result.get("last_error") or ""),
                ]
            ).strip().casefold()

            has_real_browser_evidence = bool(browser_diagnostic) or activity in {
                "AD_ACCOUNT_CREATE_CLICK_INTENT",
                "AD_ACCOUNT_CREATE_SUBMITTED",
                "AD_ACCOUNT_FINAL_CLICK_UNMATCHED",
                "AD_ACCOUNT_RESPONSE_UNCONFIRMED",
                "AD_ACCOUNT_USAGE_STEP_OPENED",
            }

            guard_only = (
                not confirmed
                and phase in {"CREATE_RESULT_UNKNOWN", "RECONCILE_CREATE"}
                and guard_message in combined_error
                and not has_real_browser_evidence
            )
            if guard_only:
                continue

            return {
                "item_id": str(row["item_id"] or ""),
                "scope_key": str(row["scope_key"] or ""),
                "status": str(row["status"] or ""),
                "error_code": str(row["error_code"] or ""),
                "error_message": str(row["error_message"] or ""),
                "result": result,
                "updated_at": int(row["updated_at"] or 0),
            }

        return {}

    async def latest_profile_entities(
        self,
        profile_id: str,
    ) -> dict[str, Any]:
        """
        Return the newest confirmed BM/RK pair for a profile.

        Add BM and Add RK intentionally use different stable scopes. Prefer
        confirmed SUCCESS step results, because they preserve the exact
        business_id -> ad_account_id relation even when the entity rows live
        in different scopes.
        """
        return await asyncio.to_thread(
            self._latest_profile_entities_sync,
            profile_id,
        )

    def _latest_profile_entities_sync(
        self,
        profile_id: str,
    ) -> dict[str, Any]:
        profile = str(profile_id or "").strip()
        if not profile:
            return {
                "profile_id": "",
                "scope_key": "",
                "business_id": "",
                "ad_account_id": "",
                "funding_source_id": "",
            }

        with self._connect() as con:
            entity_rows = con.execute(
                """
                SELECT profile_id,scope_key,business_id,ad_account_id,
                       funding_source_id,updated_at
                FROM provisioning_entities
                WHERE profile_id=?
                ORDER BY updated_at DESC
                LIMIT 100
                """,
                (profile,),
            ).fetchall()
            step_rows = con.execute(
                """
                SELECT item_id,scope_key,step,status,result_json,updated_at
                FROM provisioning_steps
                WHERE profile_id=?
                  AND status='SUCCESS'
                  AND result_json IS NOT NULL
                  AND step IN (?,?)
                ORDER BY updated_at DESC
                LIMIT 250
                """,
                (
                    profile,
                    ProvisioningStep.AD_ACCOUNT.value,
                    ProvisioningStep.BUSINESS.value,
                ),
            ).fetchall()

        business_id = ""
        ad_account_id = ""
        funding_source_id = ""
        scope_key = ""

        # A successful AD_ACCOUNT result is the strongest relation proof:
        # the handler only completes after the created/existing RK is
        # reconciled and the result contains its target Business ID.
        for row in step_rows:
            if str(row["step"] or "") != ProvisioningStep.AD_ACCOUNT.value:
                continue
            try:
                result = json.loads(str(row["result_json"] or "{}"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            candidate_business = str(result.get("business_id") or "").strip()
            candidate_account = str(result.get("ad_account_id") or "").strip()
            if candidate_account.lower().startswith("act_"):
                candidate_account = candidate_account[4:]
            if candidate_business.isdigit() and candidate_account.isdigit():
                business_id = candidate_business
                ad_account_id = candidate_account
                scope_key = str(row["scope_key"] or "")
                break

        # Fall back to a successful Business result only when there is no
        # confirmed AD_ACCOUNT relation yet.
        if not business_id:
            for row in step_rows:
                if str(row["step"] or "") != ProvisioningStep.BUSINESS.value:
                    continue
                try:
                    result = json.loads(str(row["result_json"] or "{}"))
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(result, dict):
                    continue
                candidate_business = str(result.get("business_id") or "").strip()
                if candidate_business.isdigit():
                    business_id = candidate_business
                    scope_key = str(row["scope_key"] or "")
                    break

        # Legacy entity rows remain useful for funding and for old Jobs that
        # predate relation-rich SUCCESS results.
        for row in entity_rows:
            row_business = str(row["business_id"] or "").strip()
            row_ad_account = str(row["ad_account_id"] or "").strip()
            row_funding = str(row["funding_source_id"] or "").strip()

            if not business_id and row_business:
                business_id = row_business
                scope_key = str(row["scope_key"] or "")

            if not ad_account_id and row_ad_account:
                if not row_business or not business_id or row_business == business_id:
                    ad_account_id = row_ad_account
                    scope_key = str(row["scope_key"] or scope_key)

            if (
                ad_account_id
                and row_ad_account == ad_account_id
                and not funding_source_id
                and row_funding
            ):
                funding_source_id = row_funding

        return {
            "profile_id": profile,
            "scope_key": scope_key,
            "business_id": business_id,
            "ad_account_id": ad_account_id,
            "funding_source_id": funding_source_id,
        }

    async def latest_uncertain_fan_page(self, profile_id: str, page_name: str, *, exclude_item_id: str = "") -> dict[str, Any]:
        return await asyncio.to_thread(self._latest_uncertain_fan_page_sync, profile_id, page_name, exclude_item_id)

    def _latest_uncertain_fan_page_sync(self, profile_id: str, page_name: str, exclude_item_id: str) -> dict[str, Any]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT item_id,result_json FROM provisioning_steps WHERE profile_id=? AND step=? AND result_json IS NOT NULL ORDER BY updated_at DESC LIMIT 250",
                (profile_id, ProvisioningStep.FAN_PAGES.value),
            ).fetchall()
        for row in rows:
            if str(row["item_id"]) == exclude_item_id:
                continue
            try:
                result = json.loads(row["result_json"])
            except (ValueError, TypeError):
                continue
            if not isinstance(result, dict):
                continue
            if str(result.get("active_page_name") or "").strip().casefold() != page_name.strip().casefold():
                continue
            if str(result.get("phase") or "").upper() in {"PAGE_CREATE_CLICK_INTENT", "PAGE_CREATE_RESULT_UNKNOWN"}:
                return {"item_id": str(row["item_id"]), "result": result}
        return {}

    async def latest_profile_fan_pages(
        self,
        profile_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Return confirmed Pages, including completed Pages from an interrupted batch.

        These results are independent from Graph /me/accounts propagation, so
        a newly created Page can immediately be selected as Primary Page for a
        subsequent Add BM operation.
        """
        return await asyncio.to_thread(
            self._latest_profile_fan_pages_sync,
            profile_id,
            limit,
        )

    def _latest_profile_fan_pages_sync(
        self,
        profile_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        profile = str(profile_id or "").strip()
        bounded_limit = max(1, min(int(limit or 100), 500))
        if not profile:
            return []

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT item_id,scope_key,result_json,updated_at
                FROM provisioning_steps
                WHERE profile_id=?
                  AND step=?
                  AND result_json IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 250
                """,
                (profile, ProvisioningStep.FAN_PAGES.value),
            ).fetchall()

        pages: list[dict[str, Any]] = []
        seen: set[str] = set()

        for row in rows:
            try:
                result = json.loads(str(row["result_json"] or "{}"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue

            result_pages = [*(result.get("pages") if isinstance(result.get("pages"), list) else []),
                            *(result.get("created_pages") if isinstance(result.get("created_pages"), list) else [])]

            for page in result_pages:
                if not isinstance(page, dict):
                    continue
                page_id = str(page.get("id") or page.get("page_id") or "").strip()
                if not page_id.isdigit() or page_id in seen:
                    continue

                seen.add(page_id)
                pages.append(
                    {
                        "id": page_id,
                        "page_id": page_id,
                        "name": str(page.get("name") or page_id).strip(),
                        "category": str(
                            page.get("category")
                            or result.get("category")
                            or ""
                        ).strip(),
                        "reused": bool(page.get("reused")),
                        "business_id": str(page.get("business_id") or ""),
                        "ad_account_id": str(page.get("ad_account_id") or ""),
                        "attached": bool(page.get("attached")),
                        "already_attached": bool(page.get("already_attached")),
                        "source": "python_worker_confirmed",
                        "scope_key": str(row["scope_key"] or ""),
                        "updated_at": int(row["updated_at"] or 0),
                    }
                )
                if len(pages) >= bounded_limit:
                    return pages

        return pages

    async def latest_profile_fan_page_batch(
        self,
        profile_id: str,
    ) -> list[dict[str, Any]]:
        """Return only the newest successful FAN_PAGES result batch.

        REMASK_CURRENT_FAN_PAGE_BATCH_V1
        Full FAN_PAGES history is useful for duplicate-create protection, but
        it is not current profile inventory. Workspace fallback must never
        render every Page ever created by old jobs as if all were current.
        """
        return await asyncio.to_thread(
            self._latest_profile_fan_page_batch_sync,
            profile_id,
        )

    def _latest_profile_fan_page_batch_sync(
        self,
        profile_id: str,
    ) -> list[dict[str, Any]]:
        profile = str(profile_id or "").strip()
        if not profile:
            return []

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT item_id,scope_key,result_json,updated_at
                FROM provisioning_steps
                WHERE profile_id=?
                  AND step=?
                  AND status='SUCCESS'
                  AND result_json IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 250
                """,
                (profile, ProvisioningStep.FAN_PAGES.value),
            ).fetchall()

        for row in rows:
            try:
                result = json.loads(str(row["result_json"] or "{}"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            result_pages = result.get("pages")
            if not isinstance(result_pages, list) or not result_pages:
                continue

            pages: list[dict[str, Any]] = []
            seen: set[str] = set()
            for page in result_pages:
                if not isinstance(page, dict):
                    continue
                page_id = str(
                    page.get("id") or page.get("page_id") or ""
                ).strip()
                if not page_id.isdigit() or page_id in seen:
                    continue
                seen.add(page_id)
                pages.append(
                    {
                        "id": page_id,
                        "page_id": page_id,
                        "name": str(page.get("name") or page_id).strip(),
                        "category": str(
                            page.get("category")
                            or result.get("category")
                            or ""
                        ).strip(),
                        "reused": bool(page.get("reused")),
                        "business_id": str(
                            page.get("business_id")
                            or result.get("business_id")
                            or ""
                        ).strip(),
                        "ad_account_id": str(
                            page.get("ad_account_id")
                            or result.get("ad_account_id")
                            or ""
                        ).strip(),
                        "attached": bool(
                            page.get("attached")
                            or result.get("attached")
                        ),
                        "source": "python_worker_latest_batch",
                        "item_id": str(row["item_id"] or ""),
                        "scope_key": str(row["scope_key"] or ""),
                        "updated_at": int(row["updated_at"] or 0),
                    }
                )
            if pages:
                return pages

        return []

    async def confirmed_business_page_bindings_for_profile(
        self,
        profile_id: str,
        *,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        """Return durable BM -> Page relations confirmed by BUSINESS steps.

        These bindings are hints for fresh live revalidation only. They never
        make Page inventory current by themselves.
        """
        return await asyncio.to_thread(
            self._confirmed_business_page_bindings_for_profile_sync,
            profile_id,
            limit,
        )

    def _confirmed_business_page_bindings_for_profile_sync(
        self,
        profile_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        profile = str(profile_id or "").strip()
        bounded_limit = max(1, min(int(limit or 250), 1000))
        if not profile:
            return []

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT scope_key,status,result_json,updated_at
                FROM provisioning_steps
                WHERE profile_id=?
                  AND step=?
                  AND result_json IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (
                    profile,
                    ProvisioningStep.BUSINESS.value,
                    bounded_limit,
                ),
            ).fetchall()

        bindings: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for row in rows:
            try:
                result = json.loads(str(row["result_json"] or "{}"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue

            business_id = str(result.get("business_id") or "").strip()
            page_id = str(
                result.get("selected_page_id")
                or result.get("primary_page_id")
                or result.get("page_id")
                or ""
            ).strip()
            if not business_id.isdigit() or not page_id.isdigit():
                continue

            status = str(row["status"] or "").strip().upper()
            phase = str(
                result.get("phase")
                or result.get("resume_from")
                or ""
            ).strip().upper()
            confirmed = (
                status == "SUCCESS"
                or phase == "PAGE_CONFIRMED"
                or phase == "DONE"
            )
            if not confirmed:
                continue

            identity = (business_id, page_id)
            if identity in seen:
                continue
            seen.add(identity)
            bindings.append(
                {
                    "business_id": business_id,
                    "page_id": page_id,
                    "name": str(
                        result.get("page_name")
                        or result.get("primary_page_name")
                        or page_id
                    ).strip(),
                    "business_name": str(
                        result.get("business_name")
                        or business_id
                    ).strip(),
                    "scope_key": str(row["scope_key"] or ""),
                    "updated_at": int(row["updated_at"] or 0),
                    "source": "python_worker_business_page_history",
                }
            )

        return bindings

    async def confirmed_ad_account_bindings_for_profile(
        self,
        profile_id: str,
        *,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        """Return every confirmed BM -> RK relation for one FB profile.

        ReMask allows one RK per Business, and one FB profile may own several
        Businesses. Workspace therefore must not collapse provisioning history
        to one newest pair per profile.
        """
        return await asyncio.to_thread(
            self._confirmed_ad_account_bindings_for_profile_sync,
            profile_id,
            limit,
        )

    def _confirmed_ad_account_bindings_for_profile_sync(
        self,
        profile_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        profile = str(profile_id or "").strip()
        bounded_limit = max(1, min(int(limit or 250), 1000))
        if not profile:
            return []

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT scope_key,status,result_json,updated_at
                FROM provisioning_steps
                WHERE profile_id=?
                  AND step=?
                  AND result_json IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (
                    profile,
                    ProvisioningStep.AD_ACCOUNT.value,
                    bounded_limit,
                ),
            ).fetchall()

        bindings: list[dict[str, Any]] = []
        seen_businesses: set[str] = set()

        for row in rows:
            try:
                result = json.loads(str(row["result_json"] or "{}"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue

            business_id = str(result.get("business_id") or "").strip()
            if (
                not business_id.isdigit()
                or business_id in seen_businesses
            ):
                continue

            status = str(row["status"] or "").strip().upper()
            ad_account_id = str(result.get("ad_account_id") or "").strip()
            if ad_account_id.lower().startswith("act_"):
                ad_account_id = ad_account_id[4:]

            source = ""
            if (
                status == "SUCCESS"
                and ad_account_id.isdigit()
            ):
                source = "python_worker_success_history"
            else:
                ad_account_id = _capture_ui_confirmed_ad_account_id(result)
                if ad_account_id.isdigit():
                    source = "python_worker_capture_ui_history"

            if not source:
                continue

            seen_businesses.add(business_id)
            bindings.append(
                {
                    "business_id": business_id,
                    "ad_account_id": ad_account_id,
                    "account_name": str(
                        result.get("account_name")
                        or result.get("name")
                        or ""
                    ).strip(),
                    "scope_key": str(row["scope_key"] or ""),
                    "updated_at": int(row["updated_at"] or 0),
                    "source": source,
                }
            )

        return bindings

    async def confirmed_ad_account_binding_groups(
        self,
    ) -> dict[str, dict[str, Any]]:
        """Return every durable BM -> RK relation grouped by profile.

        The Workspace volume format is multi-Business:
        profile -> ad_accounts -> business_id -> binding.
        Never collapse a profile to only its newest RK during worker startup.
        """
        return await asyncio.to_thread(
            self._confirmed_ad_account_binding_groups_sync,
        )

    def _confirmed_ad_account_binding_groups_sync(
        self,
    ) -> dict[str, dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT profile_id,scope_key,status,result_json,updated_at
                FROM provisioning_steps
                WHERE step=?
                  AND result_json IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 10000
                """,
                (ProvisioningStep.AD_ACCOUNT.value,),
            ).fetchall()

        groups: dict[str, dict[str, Any]] = {}
        seen: set[tuple[str, str]] = set()

        for row in rows:
            profile = str(row["profile_id"] or "").strip()
            if not profile:
                continue
            try:
                result = json.loads(str(row["result_json"] or "{}"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue

            business_id = str(result.get("business_id") or "").strip()
            if not business_id.isdigit():
                continue
            identity = (profile, business_id)
            if identity in seen:
                continue

            status = str(row["status"] or "").strip().upper()
            ad_account_id = str(result.get("ad_account_id") or "").strip()
            if ad_account_id.lower().startswith("act_"):
                ad_account_id = ad_account_id[4:]

            source = ""
            if status == "SUCCESS" and ad_account_id.isdigit():
                source = "python_worker_success_history"
            else:
                ad_account_id = _capture_ui_confirmed_ad_account_id(result)
                if ad_account_id.isdigit():
                    source = "python_worker_capture_ui_history"

            if not source:
                continue

            seen.add(identity)
            profile_group = groups.setdefault(
                profile,
                {"ad_accounts": {}},
            )
            accounts = profile_group.setdefault("ad_accounts", {})
            accounts[business_id] = {
                "business_id": business_id,
                "ad_account_id": ad_account_id,
                "account_name": str(
                    result.get("account_name")
                    or result.get("name")
                    or ""
                ).strip(),
                "scope_key": str(row["scope_key"] or ""),
                "updated_at": int(row["updated_at"] or 0),
                "source": source,
            }

        return groups

    async def confirmed_ad_account_bindings(
        self,
    ) -> dict[str, dict[str, Any]]:
        """
        Return the newest successful AD_ACCOUNT relation for every profile.

        This is used at worker startup to rebuild Workspace's persistent
        BM -> RK binding file from durable provisioning history.
        """
        return await asyncio.to_thread(
            self._confirmed_ad_account_bindings_sync,
        )

    def _confirmed_ad_account_bindings_sync(
        self,
    ) -> dict[str, dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT profile_id,scope_key,status,result_json,updated_at
                FROM provisioning_steps
                WHERE step=?
                  AND result_json IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 5000
                """,
                (ProvisioningStep.AD_ACCOUNT.value,),
            ).fetchall()

        bindings: dict[str, dict[str, Any]] = {}
        for row in rows:
            profile = str(row["profile_id"] or "").strip()
            if not profile or profile in bindings:
                continue
            try:
                result = json.loads(str(row["result_json"] or "{}"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue

            business_id = str(result.get("business_id") or "").strip()
            status = str(row["status"] or "").strip().upper()
            ad_account_id = str(result.get("ad_account_id") or "").strip()
            if ad_account_id.lower().startswith("act_"):
                ad_account_id = ad_account_id[4:]

            source = ""
            if status == "SUCCESS" and business_id.isdigit() and ad_account_id.isdigit():
                source = "python_worker_success_history"
            else:
                # Historical Add-RK Jobs could be marked FAILED/RESULT_UNKNOWN
                # even after Meta rendered its explicit success dialog because
                # the private GraphQL mutation was not captured. Recover only
                # from that strong same-session evidence; never from a generic
                # candidate ID or ambiguous failed checkpoint.
                ad_account_id = _capture_ui_confirmed_ad_account_id(result)
                if business_id.isdigit() and ad_account_id.isdigit():
                    source = "python_worker_capture_ui_history"

            if not source:
                continue

            bindings[profile] = {
                "business_id": business_id,
                "ad_account_id": ad_account_id,
                "updated_at": int(row["updated_at"] or 0),
                "source": source,
                "scope_key": str(row["scope_key"] or ""),
            }

        return bindings

    async def set_running(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
    ) -> None:
        await asyncio.to_thread(self._set_running_sync, item_id, profile_id, scope_key, step)

    def _set_running_sync(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
    ) -> None:
        now = _now()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO provisioning_steps(
                    item_id,profile_id,scope_key,step,status,attempt,created_at,updated_at
                ) VALUES(?,?,?,?,?,1,?,?)
                ON CONFLICT(item_id,step) DO UPDATE SET
                    status='RUNNING',
                    attempt=provisioning_steps.attempt+1,
                    error_code=NULL,
                    error_message=NULL,
                    updated_at=excluded.updated_at
                """,
                (item_id, profile_id, scope_key, step.value, "RUNNING", now, now),
            )

    async def checkpoint(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Atomically merge a partial step result without marking the step SUCCESS.

        This is used for resumable multi-phase operations such as BUSINESS:
        CREATE may have succeeded while ATTACH_PAGE is still pending. The
        checkpoint is persisted inside provisioning_steps.result_json only;
        provisioning_entities is deliberately untouched until complete().
        """
        if not isinstance(patch, dict):
            raise TypeError("checkpoint patch must be a dict")

        return await asyncio.to_thread(
            self._checkpoint_sync,
            item_id,
            profile_id,
            scope_key,
            step,
            patch,
        )

    def _checkpoint_sync(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        now = _now()

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")

            row = con.execute(
                """
                SELECT profile_id,scope_key,status,result_json
                FROM provisioning_steps
                WHERE item_id=? AND step=?
                """,
                (item_id, step.value),
            ).fetchone()

            if row is None:
                con.rollback()
                raise RuntimeError(
                    f"cannot checkpoint {step.value}: step row does not exist for item {item_id}"
                )

            stored_profile_id = str(row["profile_id"] or "")
            stored_scope_key = str(row["scope_key"] or "")

            if stored_profile_id != str(profile_id):
                con.rollback()
                raise RuntimeError(
                    f"cannot checkpoint {step.value}: profile mismatch "
                    f"{stored_profile_id!r} != {profile_id!r}"
                )

            if stored_scope_key != str(scope_key):
                con.rollback()
                raise RuntimeError(
                    f"cannot checkpoint {step.value}: scope mismatch "
                    f"{stored_scope_key!r} != {scope_key!r}"
                )

            current: dict[str, Any] = {}
            raw_result = row["result_json"]

            if raw_result:
                try:
                    decoded = json.loads(str(raw_result))
                except (json.JSONDecodeError, ValueError) as exc:
                    con.rollback()
                    raise RuntimeError(
                        f"cannot checkpoint {step.value}: stored result_json is invalid"
                    ) from exc

                if not isinstance(decoded, dict):
                    con.rollback()
                    raise RuntimeError(
                        f"cannot checkpoint {step.value}: stored result_json is not an object"
                    )

                current.update(decoded)

            current.update(patch)

            result_json = json.dumps(
                current,
                separators=(",", ":"),
                ensure_ascii=False,
            )

            cursor = con.execute(
                """
                UPDATE provisioning_steps
                SET result_json=?,updated_at=?
                WHERE item_id=? AND step=?
                """,
                (
                    result_json,
                    now,
                    item_id,
                    step.value,
                ),
            )

            if cursor.rowcount != 1:
                con.rollback()
                raise RuntimeError(
                    f"cannot checkpoint {step.value}: update affected {cursor.rowcount} rows"
                )

            con.commit()
            return current

    async def remember_entity(
        self,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        result: dict[str, Any],
    ) -> None:
        """
        Persist a confirmed remote entity before a multi-phase step finishes.

        BUSINESS uses this immediately after Meta confirms business_id so a
        later Job with the same stable scope can resume Page attach instead of
        creating a duplicate Business Portfolio.
        """
        entity_key = ENTITY_RESULT_KEYS.get(step)
        entity_value = (
            str(result.get(entity_key) or "").strip()
            if entity_key and isinstance(result, dict)
            else ""
        )
        if not entity_key or not entity_value:
            return

        await asyncio.to_thread(
            self._remember_entity_sync,
            profile_id,
            scope_key,
            entity_key,
            entity_value,
        )

    def _remember_entity_sync(
        self,
        profile_id: str,
        scope_key: str,
        entity_key: str,
        entity_value: str,
    ) -> None:
        column = {
            "business_id": "business_id",
            "ad_account_id": "ad_account_id",
            "funding_source_id": "funding_source_id",
        }.get(entity_key)
        if not column:
            return

        now = _now()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """
                INSERT INTO provisioning_entities(
                    profile_id,scope_key,business_id,ad_account_id,funding_source_id,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(profile_id,scope_key) DO NOTHING
                """,
                (profile_id, scope_key, None, None, None, now, now),
            )
            con.execute(
                f"UPDATE provisioning_entities SET {column}=?,updated_at=? "
                "WHERE profile_id=? AND scope_key=?",
                (entity_value, now, profile_id, scope_key),
            )
            con.commit()


    async def forget_entity(
        self,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        *,
        expected_value: str = "",
    ) -> None:
        """Clear a cached entity that failed independent live verification."""
        entity_key = ENTITY_RESULT_KEYS.get(step)
        column = {
            "business_id": "business_id",
            "ad_account_id": "ad_account_id",
            "funding_source_id": "funding_source_id",
        }.get(entity_key or "")
        if not column:
            return
        await asyncio.to_thread(
            self._forget_entity_sync,
            profile_id,
            scope_key,
            column,
            str(expected_value or "").strip(),
        )

    def _forget_entity_sync(
        self,
        profile_id: str,
        scope_key: str,
        column: str,
        expected_value: str,
    ) -> None:
        now = _now()
        with self._connect() as con:
            if expected_value:
                con.execute(
                    f"UPDATE provisioning_entities SET {column}=NULL,updated_at=? "
                    f"WHERE profile_id=? AND scope_key=? AND {column}=?",
                    (now, profile_id, scope_key, expected_value),
                )
            else:
                con.execute(
                    f"UPDATE provisioning_entities SET {column}=NULL,updated_at=? "
                    "WHERE profile_id=? AND scope_key=?",
                    (now, profile_id, scope_key),
                )

    async def complete(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        result: dict[str, Any],
    ) -> None:
        await asyncio.to_thread(
            self._complete_sync, item_id, profile_id, scope_key, step, result
        )

    def _complete_sync(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        result: dict[str, Any],
    ) -> None:
        now = _now()
        result_json = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        entity_key = ENTITY_RESULT_KEYS.get(step)
        entity_value = str(result.get(entity_key) or "").strip() if entity_key else ""

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """
                INSERT INTO provisioning_steps(
                    item_id,profile_id,scope_key,step,status,attempt,result_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,1,?,?,?)
                ON CONFLICT(item_id,step) DO UPDATE SET
                    status='SUCCESS',
                    result_json=excluded.result_json,
                    error_code=NULL,
                    error_message=NULL,
                    updated_at=excluded.updated_at
                """,
                (item_id, profile_id, scope_key, step.value, "SUCCESS", result_json, now, now),
            )

            if entity_key and entity_value:
                con.execute(
                    """
                    INSERT INTO provisioning_entities(
                        profile_id,scope_key,business_id,ad_account_id,funding_source_id,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(profile_id,scope_key) DO NOTHING
                    """,
                    (profile_id, scope_key, None, None, None, now, now),
                )
                column = {
                    "business_id": "business_id",
                    "ad_account_id": "ad_account_id",
                    "funding_source_id": "funding_source_id",
                }[entity_key]
                con.execute(
                    f"UPDATE provisioning_entities SET {column}=?,updated_at=? "
                    "WHERE profile_id=? AND scope_key=?",
                    (entity_value, now, profile_id, scope_key),
                )
            con.commit()

    async def fail(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        code: str,
        message: str,
    ) -> None:
        await asyncio.to_thread(
            self._fail_sync, item_id, profile_id, scope_key, step, code, message
        )

    def _fail_sync(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        code: str,
        message: str,
    ) -> None:
        now = _now()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO provisioning_steps(
                    item_id,profile_id,scope_key,step,status,attempt,error_code,error_message,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,1,?,?,?,?)
                ON CONFLICT(item_id,step) DO UPDATE SET
                    status='FAILED',
                    error_code=excluded.error_code,
                    error_message=excluded.error_message,
                    updated_at=excluded.updated_at
                """,
                (
                    item_id,
                    profile_id,
                    scope_key,
                    step.value,
                    "FAILED",
                    code,
                    message[:1000],
                    now,
                    now,
                ),
            )
