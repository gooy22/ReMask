"""Synthetic regression tests for the offline, aggregate-only latency report."""
from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.latency_report import main, summarize


class LatencyReportTests(unittest.TestCase):
    def test_http_percentiles_and_phase_distributions(self):
        logs = [
            "[worker] private_http_operation " + json.dumps({
                "operation": "sync_profile", "outcome": "success",
                "trace_id": "PRIVATE-TRACE", "profile": "PRIVATE-PROFILE",
                "operation_wall_ms": 100 if i < 19 else 31000,
                "phase_ms": {"resolver": 5, "inventory_read": 80,
                             "PRIVATE-COOKIE": 700},
            })
            for i in range(20)
        ]
        report = summarize(logs)
        stats = report["groups"]["http.sync_profile"]
        self.assertEqual(stats["count"], 20)
        self.assertEqual(stats["p50_ms"], 100)
        self.assertEqual(stats["p95_ms"], 100)
        self.assertEqual(stats["max_ms"], 31000)
        self.assertEqual(stats["slow_count"], 1)
        self.assertEqual(stats["slow_pct"], 5)
        self.assertFalse(stats["low_sample_warning"])
        self.assertEqual(report["outcomes"]["http.sync_profile"], {"success": 20})
        self.assertEqual(report["groups"]["http.sync_profile.phase.resolver"]["count"], 20)
        self.assertNotIn("PRIVATE-COOKIE", json.dumps(report))
        self.assertNotIn("PRIVATE-PROFILE", json.dumps(report))
        self.assertNotIn("PRIVATE-TRACE", json.dumps(report))

    def test_unknown_resource_labels_are_not_exported_or_grouped(self):
        payloads = [
            {"operation":"resource_interests", "operation_wall_ms":120,
             "outcome":"success"},
            {"operation":"resource_personal_identifier_secret", "operation_wall_ms":30000,
             "outcome":"failed"},
            {"operation":"resource_OTHER", "operation_wall_ms":1000,
             "outcome":"failed"},
            {"operation":"resource_a"*1000, "operation_wall_ms":1000,
             "outcome":"failed"},
        ]
        lines=["private_http_operation " + json.dumps(payload) for payload in payloads]
        report=summarize(lines)
        self.assertEqual(list(report["groups"]), ["http.resource_interests"])
        self.assertEqual(report["groups"]["http.resource_interests"]["p50_ms"], 120)
        self.assertEqual(report["ignored_lines"], 3)
        self.assertNotIn("personal_identifier_secret", json.dumps(report))

    def test_ui_php_and_dispatcher_are_separate_samples(self):
        lines = [
            "2026 [ReMask sync latency] " + json.dumps({
                "ui_wall_ms": 30750, "ui_render_ms": 15,
                "worker_bridge_ms_sum": 30000, "php_total_ms_sum": 30200,
                "ui_http_complete": False,
                "profile": "PRIVATE-PROFILE", "meta_reads": {"token":"SECRET"},
            }),
            "worker_dispatch_latency item=SECRET-ID queue_age_ms=18000 "
            "profile_lock_ms=1000.5 resolver_ms=12000",
            "2026 [ReMask sync failure timing] " + json.dumps({
                "worker_bridge": 30200, "php_total": 31500,
                "sync_error":"SECRET-COOKIE",
            }),
        ]
        report = summarize(lines)
        self.assertEqual(report["groups"]["ui.ui_wall_ms"]["p50_ms"], 30750)
        self.assertEqual(report["groups"]["dispatcher.queue_age_ms"]["p50_ms"], 18000)
        self.assertEqual(report["groups"]["dispatcher.profile_lock_ms"]["p50_ms"], 1000.5)
        self.assertEqual(report["groups"]["php_failed.php_total"]["slow_count"], 1)
        self.assertEqual(report["incomplete_ui_http_records"], 1)
        serialized = json.dumps(report)
        for private in ("SECRET-ID", "SECRET-COOKIE", "PRIVATE-PROFILE", "token"):
            self.assertNotIn(private, serialized)

    def test_invalid_overlong_untrusted_and_unknown_labels_do_not_leak(self):
        bad = [
            "private_http_operation {bad-json",
            "private_http_operation " + json.dumps({
                "operation": "session-secret-PRIVATE", "operation_wall_ms": 100}),
            "private_http_operation " + json.dumps({
                "operation": "sync_profile", "operation_wall_ms": True,
                "phase_ms": {"resolver": -7}}),
            "[ReMask sync latency] " + json.dumps({
                "ui_wall_ms": float("nan"), "ui_render_ms": -5}),
            "worker_dispatch_latency item=SECRET queue_age_ms=NaN "
            "profile_lock_ms=5 resolver_ms=5",
            "X" * 100001,
        ]
        result = summarize(bad)
        self.assertEqual(result["groups"], {})
        self.assertGreaterEqual(result["ignored_lines"], 3)
        self.assertNotIn("PRIVATE", json.dumps(result))
        for invalid in (0, -1, float("nan"), float("inf"), 4_000_000):
            with self.assertRaises(ValueError):
                summarize([], slow_ms=invalid)

    def test_cli_reads_file_without_printing_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "logs.txt"
            path.write_text(
                "PRIVATE-SECRET private_http_operation " + json.dumps({
                    "operation":"resource_other",
                    "operation_wall_ms": 29000,
                    "outcome":"failed",
                }) + "\n"
            )
            output = StringIO()
            with patch("sys.stdout", output):
                rc = main([str(path), "--slow-ms", "20000"])
            self.assertEqual(rc, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["groups"]["http.resource_other"]["slow_count"], 1)
            self.assertEqual(report["outcomes"]["http.resource_other"]["failed"], 1)
            self.assertNotIn("PRIVATE-SECRET", output.getvalue())


if __name__ == "__main__":
    unittest.main()
