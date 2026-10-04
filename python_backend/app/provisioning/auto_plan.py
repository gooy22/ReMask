"""Expand automatic work once, before its generated parameters are persisted."""
from copy import deepcopy
import re
import secrets
from types import SimpleNamespace
from typing import Any

from ..models import TaskInput

WORDS = ("Amber", "Cedar", "Meadow", "Harbor", "Willow", "Maple", "Orchid", "Silver")


def expand_auto_profiles(profiles: list[Any], job_id: str) -> list[Any]:
    expanded = []
    for row_index, profile in enumerate(profiles):
        auto_tasks = [task for task in profile.tasks if task.action == "provisioning" and task.payload.get("auto_generate") is True]
        if not auto_tasks:
            if sum(task.action == "provisioning" for task in profile.tasks) > 1:
                # Step checkpoints are keyed by item+step. Give each provisioning
                # task its own item rather than silently reusing the first result.
                expanded.extend(SimpleNamespace(profile_id=profile.profile_id, tasks=[task]) for task in profile.tasks)
            else:
                expanded.append(profile)
            continue
        if len(profile.tasks) != 1:
            raise ValueError("Automatic provisioning requires one template task per profile")
        template = auto_tasks[0]
        count = template.payload.get("batch_count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 20:
            raise ValueError("batch_count must be an integer from 1 to 20")
        steps = template.payload.get("steps")
        allowed = ["PROXY_CHECK", "FAN_PAGES", "BUSINESS", "AD_ACCOUNT"]
        if steps not in [allowed[:2], allowed[:3], allowed]:
            raise ValueError("Automatic steps must be PROXY_CHECK → FAN_PAGES → BUSINESS → AD_ACCOUNT, or a prefix")
        for unit in range(count):
            payload = deepcopy(template.payload)
            payload.pop("batch_count", None)
            payload.pop("auto_generate", None)
            scope = f"auto-{job_id}-{row_index}-{unit}"
            payload["scope_key"] = scope
            params = payload.setdefault("parameters", {})
            if not isinstance(params, dict) or any(not isinstance(v, dict) for v in params.values()):
                raise ValueError("parameters must contain objects")
            suffix = secrets.token_hex(5)
            title = f"{secrets.choice(WORDS)} Studio {suffix}"
            params["FAN_PAGES"] = {"names": [title], "category": "Digital creator", **params.get("FAN_PAGES", {})}
            # Exactly one Page belongs to each automatically generated unit.
            params["FAN_PAGES"].pop("base_name", None)
            params["FAN_PAGES"]["names"] = [title]
            params["FAN_PAGES"]["count"] = 1
            params["FAN_PAGES"]["mode"] = "create"
            params["FAN_PAGES"]["confirm_main_business"] = True
            for key in ("page_id", "existing_page_id", "business_id", "bm_id", "ad_account_id"):
                params["FAN_PAGES"].pop(key, None)
            if "BUSINESS" in steps:
                params["BUSINESS"] = {**params.get("BUSINESS", {}), "name": title,
                    "user_email": secrets.token_hex(12) + "@gmail.com", "use_created_page": True,
                    "attach_page": False}
                params["BUSINESS"].pop("page_id", None)
                params["BUSINESS"].pop("primary_page_id", None)
            if "AD_ACCOUNT" in steps:
                rk = params.setdefault("AD_ACCOUNT", {})
                for key in ("business_id", "bm_id", "ad_account_id"):
                    rk.pop(key, None)
                currency = str(rk.get("currency") or "").upper()
                timezone = rk.get("timezone_id")
                if not re.fullmatch(r"[A-Z]{3}", currency) or isinstance(timezone, bool) or not isinstance(timezone, int) or timezone < 0:
                    raise ValueError("AD_ACCOUNT requires currency and a nonnegative integer Meta timezone_id")
                rk.update(name=f"{title} Ads", currency=currency)
            payload["generated"] = {"unit": unit + 1, "contact_email_registered": False}
            task = TaskInput(action="provisioning", payload=payload, idempotency_key=scope)
            expanded.append(SimpleNamespace(profile_id=profile.profile_id, tasks=[task]))
            if len(expanded) > 500:
                raise ValueError("A batch may contain at most 500 work items")
    if len(expanded) > 500:
        raise ValueError("A batch may contain at most 500 work items")
    return expanded
