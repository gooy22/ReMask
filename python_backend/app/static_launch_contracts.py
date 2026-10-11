"""Deployed advertising contracts, isolated from runtime maintenance candidates.

An operation is pinned only with literal artifact/sender evidence and an
independent read contract. Missing evidence means unavailable, never discovery
or browser fallback. Session credentials continue to come from ProfileSession.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from .private_launch import AUTO_RESULT_ID_PATH, PrivateLaunchContractStore, PrivateLaunchStep
from .private_launch_verify import validate_contract
from .provisioning.models import ProvisioningError

MANIFEST = Path(__file__).with_name("contracts") / "meta_launch_20261011.json"


class StaticLaunchContractStore(PrivateLaunchContractStore):
    def __init__(self, manifest: Path = MANIFEST):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("version") != 1
                    or not isinstance(data.get("steps"), dict) or not data.get("revision")):
                raise ValueError("Unsupported advertising manifest")
            rows = {}
            for name, entry in data["steps"].items():
                step = PrivateLaunchStep(name)
                if not isinstance(entry, dict) or not isinstance(entry.get("evidence"), dict):
                    raise ValueError("Advertising evidence must be an object")
                evidence = entry["evidence"]
                for key in ("artifact_sha256", "sender_sha256", "read_sha256"):
                    if not re.fullmatch(r"[a-f0-9]{64}", evidence.get(key, "")):
                        raise ValueError("Advertising source evidence missing: " + name)
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", evidence.get("observed_at", "")):
                    raise ValueError("Advertising observation date missing: " + name)
                row = entry["contract"]
                if not isinstance(row, dict):
                    raise ValueError("Advertising contract must be an object")
                if AUTO_RESULT_ID_PATH in row.get("result_id_paths", []):
                    raise ValueError("Static advertising parser must have an exact response path")
                validate_contract(row.get("verification"), step.value)
                rows[name] = row
            super().__init__(json.dumps(rows))
            self.revision = data["revision"]
            for name in rows:
                self.get(PrivateLaunchStep(name))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ProvisioningError("PRIVATE_LAUNCH_STATIC_MANIFEST_INVALID",
                "Deployed advertising manifest is invalid", retryable=False) from exc

    def register(self, step, row):
        raise ProvisioningError("PRIVATE_LAUNCH_STATIC_CONTRACT_REQUIRED",
            "Publish reviewed advertising contracts through the deployed manifest; runtime candidates cannot replace them.",
            retryable=False)

    def status(self):
        return {step: {**row, "revision": self.revision, "source": "static_deployed_manifest"}
                for step, row in super().status().items()}
