"""Explicit, expiring operator opt-in for one profile's direct test requests."""
from __future__ import annotations

import os
import time


def direct_test_deadline(profile_id: str) -> int:
    selected = os.getenv('REMASK_DIRECT_TEST_PROFILE', '').strip()
    if not selected or selected != str(profile_id):
        return 0
    try:
        deadline = int(os.getenv('REMASK_DIRECT_TEST_UNTIL', '0'))
    except (TypeError, ValueError):
        return 0
    now = time.time()
    return deadline if now < deadline <= now + 7200 else 0


def direct_test_result(profile_id: str) -> dict | None:
    deadline = direct_test_deadline(profile_id)
    if not deadline:
        return None
    return {'mode': 'DIRECT_TEST', 'profile_id': str(profile_id),
            'expires_at': deadline, 'proxy_verified': False,
            'exit_ip': '', 'latency_ms': 0}
