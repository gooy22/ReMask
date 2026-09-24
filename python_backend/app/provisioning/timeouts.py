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
    browsers = _env_int("REMASK_BM_BROWSER_CONCURRENCY", 2)
    return max(1, math.ceil(workers / browsers))


def browser_step_timeout(step: ProvisioningStep) -> float:
    """Total queue + active-runtime guard for one browser-backed step.

    Active Meta operations keep their shorter in-handler watchdogs. This outer
    guard additionally allows time for a task to wait for the bounded Chromium
    pool during bulk jobs.
    """
    waves = browser_queue_waves()

    if step is ProvisioningStep.BUSINESS:
        explicit = _env_float("REMASK_BUSINESS_STEP_TIMEOUT")
        if explicit is not None:
            return max(60.0, min(explicit, 7200.0))
        return min(7200.0, max(300.0, waves * 180.0 + 120.0))

    if step is ProvisioningStep.AD_ACCOUNT:
        explicit = _env_float("REMASK_AD_ACCOUNT_STEP_TIMEOUT")
        if explicit is not None:
            return max(45.0, min(explicit, 7200.0))
        # create_ad_account is bounded to 115s after browser open. 150s per
        # wave leaves room for Chromium/context setup and teardown.
        return min(7200.0, max(300.0, waves * 150.0 + 120.0))

    raise ValueError(f"unsupported browser step: {step}")


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
        if step in {ProvisioningStep.BUSINESS, ProvisioningStep.AD_ACCOUNT}:
            normalized.add(step)

    if not normalized:
        return 0.0

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
