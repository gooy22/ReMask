from __future__ import annotations

import math
import os
from typing import Iterable

from .models import ProvisioningStep


def _env_float(name: str) -> float | None:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    try:
        return max(1, int(raw)) if raw else max(1, int(default))
    except (TypeError, ValueError):
        return max(1, int(default))


def browser_queue_waves() -> int:
    """Concurrent worker waves competing for the bounded Chromium pool."""
    workers = _env_int("REMASK_WORKER_CONCURRENCY", 30)
    # Match FacebookBusinessBrowser's actual default Chromium pool size.
    browsers = _env_int("REMASK_BM_BROWSER_CONCURRENCY", 1)
    return max(1, math.ceil(workers / browsers))


def private_create_step_timeout(step: ProvisioningStep) -> float:
    """BM/RK HTTP budget independent of the Chromium pool and its queue."""
    if step not in {ProvisioningStep.BUSINESS, ProvisioningStep.AD_ACCOUNT}:
        raise ValueError(f"unsupported private CREATE step: {step}")
    explicit = _env_float("REMASK_PRIVATE_" + step.value + "_STEP_TIMEOUT")
    return max(60.0, min(explicit, 900.0)) if explicit is not None else 240.0


def provisioning_hard_timeout(steps: Iterable[ProvisioningStep | str]) -> float:
    normalized = {ProvisioningStep(str(step.value if isinstance(step, ProvisioningStep) else step).strip().upper()) for step in steps}
    if not normalized:
        return 0.0
    single_overrides = {
        ProvisioningStep.FAN_PAGES: "REMASK_ADD_FP_HARD_TIMEOUT_SECONDS",
        ProvisioningStep.BUSINESS: "REMASK_ADD_BM_HARD_TIMEOUT_SECONDS",
        ProvisioningStep.AD_ACCOUNT: "REMASK_ADD_RK_HARD_TIMEOUT_SECONDS",
    }
    if len(normalized) == 1:
        name = single_overrides.get(next(iter(normalized)))
        explicit = _env_float(name) if name else None
        if explicit is not None:
            return max(120.0, min(explicit, 7200.0))
    return min(7200.0, sum(private_create_step_timeout(step)
        if step in {ProvisioningStep.BUSINESS, ProvisioningStep.AD_ACCOUNT}
        else browser_step_timeout(step) for step in normalized) + 120.0)


def browser_step_timeout(step: ProvisioningStep) -> float:
    """Total queue + active-runtime guard for one browser-backed step.

    Active Meta operations keep their shorter in-handler watchdogs. This outer
    guard additionally allows time for a task to wait for the bounded Chromium
    pool during bulk jobs.
    """
    waves = browser_queue_waves()

    if step is ProvisioningStep.FAN_PAGES:
        explicit = _env_float("REMASK_FAN_PAGES_STEP_TIMEOUT")
        if explicit is not None:
            return max(90.0, min(explicit, 7200.0))
        return min(7200.0, max(420.0, waves * 210.0 + 180.0))

    if step is ProvisioningStep.BUSINESS:
        explicit = _env_float("REMASK_BUSINESS_STEP_TIMEOUT")
        if explicit is not None:
            return max(60.0, min(explicit, 7200.0))
        return min(7200.0, max(300.0, waves * 180.0 + 120.0))

    if step is ProvisioningStep.PAGE_ACCESS:
        return min(7200.0,max(420.0,waves*210.0+180.0))
    if step is ProvisioningStep.AD_ACCOUNT:
        explicit = _env_float("REMASK_AD_ACCOUNT_STEP_TIMEOUT")
        if explicit is not None:
            return max(45.0, min(explicit, 7200.0))
        # create_ad_account is bounded to 115s after browser open. 150s per
        # wave leaves room for Chromium/context setup and teardown.
        return min(7200.0, max(300.0, waves * 150.0 + 120.0))

    raise ValueError(f"unsupported browser step: {step}")



def prepare_hard_timeout(ad_accounts: int = 2) -> float:
    """Hard wall-clock guard sized for the requested Prepare RK target."""
    explicit = _env_float("REMASK_PREPARE_HARD_TIMEOUT_SECONDS")
    if explicit is not None:
        return max(300.0, min(explicit, 7200.0))

    try:
        rk_count = max(1, min(int(ad_accounts), 20))
    except (TypeError, ValueError):
        rk_count = 2

    total = (
        browser_step_timeout(ProvisioningStep.FAN_PAGES)
        + private_create_step_timeout(ProvisioningStep.BUSINESS)
        + rk_count * (
            private_create_step_timeout(ProvisioningStep.AD_ACCOUNT)
            + browser_step_timeout(ProvisioningStep.PAGE_ACCESS)
        )
        + 180.0
    )
    return min(7200.0, max(300.0, total))

def browser_provisioning_hard_timeout(
    steps: Iterable[ProvisioningStep | str],
) -> float:
    """Hard wall-clock guard for a task containing browser-backed steps."""
    normalized: set[ProvisioningStep] = set()
    for raw in steps:
        if isinstance(raw, ProvisioningStep):
            step = raw
        else:
            try:
                step = ProvisioningStep(str(raw).strip().upper())
            except ValueError:
                continue
        if step in {
            ProvisioningStep.FAN_PAGES,
            ProvisioningStep.BUSINESS,
            ProvisioningStep.AD_ACCOUNT,
            ProvisioningStep.PAGE_ACCESS,
        }:
            normalized.add(step)

    if not normalized:
        return 0.0

    if normalized == {ProvisioningStep.FAN_PAGES}:
        explicit = _env_float("REMASK_ADD_FP_HARD_TIMEOUT_SECONDS")
        if explicit is not None:
            return max(120.0, min(explicit, 7200.0))

    if normalized == {ProvisioningStep.BUSINESS}:
        explicit = _env_float("REMASK_ADD_BM_HARD_TIMEOUT_SECONDS")
        if explicit is not None:
            return max(90.0, min(explicit, 7200.0))

    if normalized == {ProvisioningStep.AD_ACCOUNT}:
        explicit = _env_float("REMASK_ADD_RK_HARD_TIMEOUT_SECONDS")
        if explicit is not None:
            return max(90.0, min(explicit, 7200.0))

    explicit_total = _env_float(
        "REMASK_BROWSER_PROVISIONING_HARD_TIMEOUT_SECONDS"
    )
    if explicit_total is not None:
        return max(180.0, min(explicit_total, 7200.0))

    total = sum(browser_step_timeout(step) for step in normalized) + 180.0
    return min(7200.0, max(300.0, total))
