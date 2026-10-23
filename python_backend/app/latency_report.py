"""Offline, privacy-preserving latency summary for existing ReMask logs.

This tool performs NO network calls. It accepts worker/PHP/browser console
lines saved by an operator and emits aggregate timings only: never profiles,
item identifiers, tokens, URLs, request bodies, trace IDs or raw log text.

Run from python_backend:
    python -m app.latency_report /path/to/log.txt
    cat combined.log | python -m app.latency_report -
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re
import sys

# Intentionally keep the analyzer on Python's standard library. The HTTP
# recorder itself imports HTTPX; an offline diagnostic tool must not need it.
# Regression tests require parity with the recorder's emitted resource labels.
_RESOURCE_NAMES = frozenset({
    "interests", "behaviors", "locations", "locales", "pages", "images",
    "videos", "campaigns", "adsets", "ads", "pixels", "custom_audiences",
    "conversion_goals", "instagram_accounts", "instagram_media",
    "creatives", "insights", "college_years", "industries", "life_events",
    "family_statuses", "user_device", "user_os",
})

MAX_LINE = 100_000
MAX_SAMPLES_PER_GROUP = 250_000
MAX_DURATION_MS = 3_600_000
KNOWN_OPERATIONS = frozenset({
    "sync_profile", "private_launch", "private_delivery", "provision",
    "provisioning", "prepare", "prepare_profile", "proxy_check",
    "transparent_post", "template_reference", "resource_other", "other",
})
KNOWN_PHASES = frozenset({
    "resolver", "cache_lookup", "profile_session", "reference_discovery",
    "reference_read", "ads_document", "account_proof", "meta_read",
    "inventory_read", "resource_read", "queue_wait", "profile_lock",
    "store_read", "store_write",
})
UI_MEASUREMENTS = frozenset({
    "ui_wall_ms", "ui_render_ms", "ui_http_ms_sum", "ui_apply_ms_sum",
    "php_total_ms_sum", "php_context_ms_sum", "php_reconciliation_ms_sum",
    "worker_bridge_ms_sum", "worker_total_ms_sum",
    "worker_profile_context_ms_sum", "worker_durable_state_ms_sum",
    "worker_http_session_ms_sum", "worker_inventory_ms_sum",
    "worker_page_enrichment_ms_sum",
})
PHP_MEASUREMENTS = frozenset({
    "php_total", "php_context", "worker_bridge", "php_reconciliation",
})
POST_JOB_MEASUREMENTS = frozenset({
    "batch_wall_ms", "profile_read_ms_sum", "render_ms",
})
DISPATCH_PATTERN = re.compile(
    r"worker_dispatch_latency\b.*?queue_age_ms=([0-9.]+)"
    r"\s+profile_lock_ms=([0-9.]+)\s+resolver_ms=([0-9.]+)"
)


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    if 0 <= value <= MAX_DURATION_MS:
        return float(value)
    return None


def _json_after(line, marker):
    position = line.find(marker)
    if position < 0:
        return None
    try:
        result = json.loads(line[position + len(marker):].strip())
    except (json.JSONDecodeError, UnicodeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def _operation(name):
    if type(name) is not str:
        return None
    if name in KNOWN_OPERATIONS:
        return name
    # A raw log is untrusted input. Accept only resource categories emitted
    # by the actual HTTP metrics recorder; never echo arbitrary labels in the
    # aggregate report (including accidentally logged private identifiers).
    if name.startswith("resource_") and name[9:] in _RESOURCE_NAMES:
        return name
    return None


def _stats(values, slow_ms):
    ordered = sorted(values)
    if not ordered:
        return None
    n = len(ordered)
    slow = sum(value >= slow_ms for value in ordered)
    return {
        "count": n,
        "p50_ms": round(ordered[math.ceil(.5*n)-1], 2),
        "p95_ms": round(ordered[math.ceil(.95*n)-1], 2),
        "max_ms": round(ordered[-1], 2),
        "slow_count": slow,
        "slow_pct": round(100*slow/n, 2),
        "low_sample_warning": n < 20,
    }


def summarize(lines, *, slow_ms=30_000):
    """Only returns numeric aggregates and allowlisted category names."""
    if _number(slow_ms) is None or slow_ms <= 0:
        raise ValueError("slow_ms must be a positive number within one hour")
    values = defaultdict(list)
    outcomes = defaultdict(lambda: defaultdict(int))
    incomplete_ui = 0
    ignored = 0
    post_job_profile_totals = {"attempted": 0, "succeeded": 0, "failed": 0}

    def add(group, value):
        number = _number(value)
        if number is not None and len(values[group]) < MAX_SAMPLES_PER_GROUP:
            values[group].append(number)

    for raw in lines:
        if not isinstance(raw, str) or len(raw) > MAX_LINE:
            ignored += 1
            continue
        if "private_http_operation " in raw:
            data = _json_after(raw, "private_http_operation ")
            if data is None:
                ignored += 1
                continue
            name = _operation(data.get("operation"))
            if name is None:
                ignored += 1
                continue
            group = "http." + name
            add(group, data.get("operation_wall_ms"))
            outcome = data.get("outcome")
            if outcome in {"success", "failed", "partial", "cancelled"}:
                outcomes[group][outcome] += 1
            phases = data.get("phase_ms")
            if isinstance(phases, dict):
                for phase in KNOWN_PHASES:
                    if phase in phases:
                        add(group + ".phase." + phase, phases[phase])
            continue
        if "[ReMask sync latency]" in raw:
            data = _json_after(raw, "[ReMask sync latency]")
            if data is None:
                ignored += 1
                continue
            for name in UI_MEASUREMENTS:
                if name in data:
                    add("ui." + name, data[name])
            if data.get("ui_http_complete") is False:
                incomplete_ui += 1
            continue
        if "[ReMask sync failure timing]" in raw:
            data = _json_after(raw, "[ReMask sync failure timing]")
            if data is None:
                ignored += 1
                continue
            for name in PHP_MEASUREMENTS:
                if name in data:
                    add("php_failed." + name, data[name])
            continue
        if "[ReMask post-job refresh latency]" in raw:
            data = _json_after(raw, "[ReMask post-job refresh latency]")
            if data is None:
                ignored += 1
                continue
            for name in POST_JOB_MEASUREMENTS:
                if name in data:
                    add("post_job." + name, data[name])
            # Profile counts are NOT milliseconds. Aggregate them separately
            # and never include profile identifiers or individual rows.
            counts = [data.get(name) for name in ("attempted", "succeeded", "failed")]
            if (all(type(value) is int and 0 <= value <= 10000 for value in counts)
                    and counts[0] == counts[1] + counts[2]):
                for name, value in zip(post_job_profile_totals, counts):
                    post_job_profile_totals[name] += value
            continue
        if "worker_dispatch_latency " in raw:
            match = DISPATCH_PATTERN.search(raw)
            if match is None:
                ignored += 1
                continue
            for name, raw_number in zip(
                    ("queue_age_ms", "profile_lock_ms", "resolver_ms"),
                    match.groups()):
                try:
                    add("dispatcher." + name, float(raw_number))
                except ValueError:
                    pass

    report = {
        "slow_threshold_ms": slow_ms,
        "measurement_scope": (
            "independent worker, PHP and browser samples; "
            "different logs are not joined by profile or request"),
        "phase_warning": "phase durations can overlap: do not add them to wall time",
        "groups": {
            key: _stats(values[key], slow_ms)
            for key in sorted(values) if values[key]
        },
        "outcomes": {
            key: dict(sorted(status.items()))
            for key, status in sorted(outcomes.items())
        },
        "incomplete_ui_http_records": incomplete_ui,
        "post_job_profile_totals": post_job_profile_totals,
        "ignored_lines": ignored,
    }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", default="-", help="log path, or - for stdin")
    parser.add_argument("--slow-ms", type=float, default=30000,
                        help="slow threshold in milliseconds (default: 30000)")
    args = parser.parse_args(argv)
    try:
        if args.file == "-":
            result = summarize(sys.stdin, slow_ms=args.slow_ms)
        else:
            with Path(args.file).open("r", encoding="utf-8", errors="replace") as stream:
                result = summarize(stream, slow_ms=args.slow_ms)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
