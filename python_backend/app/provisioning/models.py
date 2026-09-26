from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ProvisioningStep(str, Enum):
    PROXY_CHECK = "PROXY_CHECK"
    FAN_PAGES = "FAN_PAGES"
    BUSINESS = "BUSINESS"
    AD_ACCOUNT = "AD_ACCOUNT"
    FUNDING = "FUNDING"


ENTITY_RESULT_KEYS: dict[ProvisioningStep, str] = {
    ProvisioningStep.BUSINESS: "business_id",
    ProvisioningStep.AD_ACCOUNT: "ad_account_id",
    ProvisioningStep.FUNDING: "funding_source_id",
}


class ProvisioningError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(slots=True)
class ProvisioningSnapshot:
    profile_id: str
    scope_key: str
    business_id: str | None = None
    ad_account_id: str | None = None
    funding_source_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "scope_key": self.scope_key,
            "business_id": self.business_id,
            "ad_account_id": self.ad_account_id,
            "funding_source_id": self.funding_source_id,
        }
