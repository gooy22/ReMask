from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    RETRY = "RETRY"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    BLOCKED = "BLOCKED"


@dataclass(slots=True)
class ActionResult:
    action: str
    status: ActionStatus
    code: str = ""
    message: str = ""
    submitted: bool | None = False
    verified: bool = False
    retryable: bool = False
    entity_id: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "status": self.status.value,
            "code": self.code,
            "message": self.message,
            "submitted": self.submitted,
            "verified": self.verified,
            "retryable": self.retryable,
            "entity_id": self.entity_id,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def success(
        cls,
        action: str,
        *,
        entity_id: str = "",
        code: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> "ActionResult":
        return cls(
            action=action,
            status=ActionStatus.SUCCESS,
            code=code,
            submitted=True,
            verified=True,
            retryable=False,
            entity_id=str(entity_id or ""),
            evidence=dict(evidence or {}),
        )

    @classmethod
    def reconcile(
        cls,
        action: str,
        *,
        code: str,
        message: str,
        submitted: bool | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> "ActionResult":
        return cls(
            action=action,
            status=ActionStatus.RECONCILE_REQUIRED,
            code=code,
            message=message,
            submitted=submitted,
            verified=False,
            retryable=False,
            evidence=dict(evidence or {}),
        )

    @classmethod
    def blocked(
        cls,
        action: str,
        *,
        code: str,
        message: str,
        evidence: dict[str, Any] | None = None,
    ) -> "ActionResult":
        return cls(
            action=action,
            status=ActionStatus.BLOCKED,
            code=code,
            message=message,
            submitted=False,
            verified=False,
            retryable=False,
            evidence=dict(evidence or {}),
        )

    @classmethod
    def retry(
        cls,
        action: str,
        *,
        code: str,
        message: str,
        evidence: dict[str, Any] | None = None,
    ) -> "ActionResult":
        return cls(
            action=action,
            status=ActionStatus.RETRY,
            code=code,
            message=message,
            submitted=False,
            verified=False,
            retryable=True,
            evidence=dict(evidence or {}),
        )
