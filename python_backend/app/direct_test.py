"""Explicit, expiring operator opt-in for one profile's direct test requests."""
from __future__ import annotations

import os
import time
import json
from pathlib import Path


def direct_test_deadline(profile_id: str, actor_id: str = '') -> int:
    selected = os.getenv('REMASK_DIRECT_TEST_PROFILE', '').strip()
    now = time.time()
    if selected and selected == str(profile_id):
        try:
            deadline = int(os.getenv('REMASK_DIRECT_TEST_UNTIL', '0'))
        except (TypeError, ValueError):
            deadline = 0
        if now < deadline <= now + 7200:
            return deadline
    # An authenticated Profile Manager save can authorize the same bounded
    # lease without changing Railway variables or restarting the worker.
    if not actor_id:
        return 0
    root = os.getenv('REMASK_DATA_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '/var/lib/remask'
    path = Path(root) / 'profile-direct-test.json'
    try:
        if path.stat().st_size > 2048:
            return 0
        row = json.loads(path.read_text())
        deadline = row.get('expires_at')
        if (row.get('profile_id') == str(profile_id) and row.get('actor_id') == str(actor_id)
                and type(deadline) is int and now < deadline <= now + 7200):
            return deadline
    except (OSError, ValueError, AttributeError, RecursionError):
        pass
    return 0


def direct_test_result(profile_id: str, actor_id: str = '') -> dict | None:
    deadline = direct_test_deadline(profile_id, actor_id)
    if not deadline:
        return None
    return {'mode': 'DIRECT_TEST', 'profile_id': str(profile_id),
            'expires_at': deadline, 'proxy_verified': False,
            'exit_ip': '', 'latency_ms': 0}
