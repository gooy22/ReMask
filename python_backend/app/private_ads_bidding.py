"""Serialize the selected bid strategy at its reviewed budget-owning level.

Meta calls a Campaign `CampaignGroup` and an Ad Set `Campaign` in these
editor modules. Their legacy bidding booleans are part of the draft model.
This does not rewrite objectives, choose a bid amount, or repair old journals.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .private_ads_draft import fail

MANIFEST = Path(__file__).with_name("contracts") / "private_ads_bidding_20261021.json"


def native_bidding(campaign, adset):
    policy = json.loads(MANIFEST.read_bytes())
    campaign, adset = copy.deepcopy(campaign), copy.deepcopy(adset)
    cbo = any(campaign.get(field) for field in ("daily_budget", "lifetime_budget"))
    owner, child = (campaign, adset) if cbo else (adset, campaign)
    if not cbo and any(field in campaign for field in policy["roll_down_fields"]):
        raise fail("BID_STRATEGY_SCOPE", "Campaign bidding requires a Campaign budget; review the selected bidding level")
    # The builder has a separate Ad Set strategy control even in CBO mode.
    # Transfer that exact choice when Campaign has no explicit strategy.
    selected = owner.get("bid_strategy", child.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP") if cbo else "LOWEST_COST_WITHOUT_CAP")
    if not isinstance(selected, str) or selected not in policy["strategies"]:
        raise fail("BID_STRATEGY_UNSUPPORTED", "Selected bid strategy is unsupported in this creation flow")
    if cbo and "bid_strategy" in child and child["bid_strategy"] != selected:
        raise fail("BID_STRATEGY_CONFLICT", "Campaign and Ad Set bid strategies conflict; review both selections")
    flags = policy["strategies"][selected]["campaign" if cbo else "adset"]
    for field, expected in flags.items():
        for values in (owner, child) if cbo else (owner,):
            if field in values and (type(values[field]) is not bool or values[field] != expected):
                raise fail("BID_STRATEGY_CONFLICT", "Selected legacy bidding flags conflict with the bid strategy")
    if cbo:
        if "pacing_type" in child:
            if "pacing_type" in owner and owner["pacing_type"] != child["pacing_type"]:
                raise fail("BID_STRATEGY_CONFLICT", "Campaign and Ad Set pacing selections conflict")
            owner["pacing_type"] = copy.deepcopy(child["pacing_type"])
        for field in policy["roll_down_fields"]:
            child.pop(field, None)
    owner.update(bid_strategy=selected, **flags)
    amount = adset.get("bid_amount")
    if selected == "LOWEST_COST_WITHOUT_CAP":
        if amount is not None:
            raise fail("BID_AMOUNT_INCOMPATIBLE", "Selected bid amount requires a capped bid strategy")
    elif type(amount) is not int or not 0 < amount <= 9_007_199_254_740_991:
        raise fail("BID_AMOUNT_REQUIRED", "Selected bid strategy requires a positive integer bid amount")
    return campaign, adset, {"budget_level": "campaign" if cbo else "adset",
        "bid_strategy": selected, "checked_live": False}
