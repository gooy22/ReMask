"""Validate the existing supported Launch subset without rewriting selections.

This contract is a conservative creation subset, not a live eligibility grant.
The original Launch stays intact for fingerprinting and operator review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .private_ads_draft import fail, identity
from .private_ads_bidding import native_bidding

MANIFEST = Path(__file__).with_name("contracts") / "private_ads_objectives_20261021.json"


class ObjectiveContract:
    def __init__(self):
        raw = MANIFEST.read_bytes()
        self.data = json.loads(raw)
        if self.data.get("version") != 1 or not self.data.get("sources"):
            raise fail("OBJECTIVE_CONTRACT_INVALID", "Private objective contract is unavailable")
        self.digest = hashlib.sha256(raw).hexdigest()

    def validate(self, launch):
        campaign, adset = launch["campaign"], launch["adset"]
        objective = campaign.get("objective")
        spec = self.data["objectives"].get(objective) if isinstance(objective, str) else None
        if spec is None:
            raise fail("OBJECTIVE_UNSUPPORTED", "Select a supported modern Campaign objective")
        if campaign.get("buying_type", "AUCTION") != "AUCTION":
            raise fail("BUYING_TYPE_UNSUPPORTED", "This private creation flow supports AUCTION buying")
        goal = adset.get("optimization_goal")
        if not isinstance(goal, str) or goal not in spec["optimization_goals"]:
            raise fail("OPTIMIZATION_INCOMPATIBLE", "Selected optimization is incompatible with " + objective)
        billing = adset.get("billing_event")
        if not isinstance(billing, str) or billing not in self.data["billing_events"][goal]:
            raise fail("BILLING_INCOMPATIBLE", "Selected billing event is incompatible with " + goal)
        destination = adset.get("destination_type")
        if destination != spec["destination_type"]:
            raise fail("DESTINATION_INCOMPATIBLE", "Selected destination is incompatible with this objective flow")
        promoted = adset.get("promoted_object", {})
        if not isinstance(promoted, dict):
            raise fail("PROMOTED_OBJECT_INVALID", "Promoted object must be an object")
        if goal in {"OFFSITE_CONVERSIONS", "VALUE"}:
            if set(promoted) - {"pixel_id", "custom_event_type", "custom_conversion_id"}:
                raise fail("PROMOTED_OBJECT_INCOMPATIBLE", "Selected promoted object needs a different conversion flow")
            identity(promoted.get("pixel_id"))
            event = promoted.get("custom_event_type")
            if event not in self.data["conversion_events"] or goal == "VALUE" and event != "PURCHASE":
                raise fail("CONVERSION_EVENT_INCOMPATIBLE", "Select a supported Pixel conversion event")
            if "custom_conversion_id" in promoted:
                identity(promoted["custom_conversion_id"])
        elif promoted:
            raise fail("PROMOTED_OBJECT_INCOMPATIBLE", "Selected promoted object is unsupported for this optimization; review it explicitly")
        _, _, bidding = native_bidding(campaign, adset)
        targeting = adset.get("targeting")
        if not isinstance(targeting, dict) or not isinstance(targeting.get("geo_locations"), dict) or not targeting["geo_locations"]:
            raise fail("GEO_REQUIRED", "Select advertising geography before creation")
        lower, upper = targeting.get("age_min", 18), targeting.get("age_max", 65)
        if type(lower) is not int or type(upper) is not int or not 18 <= lower <= upper <= 65:
            raise fail("AGE_INVALID", "Advertising ages must be ordered integers between 18 and 65")
        return {"revision": self.data["revision"], "compatible": True,
                "selected_objective": objective,
                "bidding": bidding,
                "account_eligibility_verified": False, "editor_verified": False}
