"""The existing Launch payload/journal adapted to private draft publication.

Supported formats are PAUSED single images and image carousels. Other delivery
stop during preparation, before upload or draft creation.
"""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

from .private_ads_draft import fail, fragment_params, identity
from .private_ads_creative import carousel_creative, image_creative, validate_creative
from .private_ads_http import AdsManagerPrivateHttp
from .private_ads_media import upload_image, verify_image
from .private_ads_publication import AdsManagerDraftPublisher
from .private_ads_state import AdsManagerSubmitter
from .private_ads_verify import verify_published_objects

SOURCE_REVISION = "ads-manager-private-2026-10-12"

class AdsManagerLaunch:
    def __init__(self, owner):
        self.owner = owner
        digest = hashlib.sha256(SOURCE_REVISION.encode())
        for name in ("private_ads_launch.py", "private_ads_http.py", "private_ads_draft.py",
                     "private_ads_state.py", "private_ads_media.py", "private_ads_publication.py",
                     "private_ads_verify.py", "private_ads_creative.py"):
            digest.update(name.encode() + b"\0" + Path(__file__).with_name(name).read_bytes())
        self.source_digest = digest.hexdigest()

    def prepare(self, profile_id, payload):
        from .private_launch import _effective_launch_payload, _launch_fingerprint
        from .private_launch_media import local_references
        if not isinstance(payload, dict) or not str(profile_id or "").strip():
            raise fail("INVALID_INPUT", "Advertising profile and configuration are required")
        target = {"profile_id": str(profile_id), **{key: identity(payload.get(key))
                  for key in ("business_id", "ad_account_id", "page_id")}}
        launch = _effective_launch_payload(payload.get("launch"))
        if any(value != "PAUSED" for value in launch.get("delivery", {}).values()):
            raise fail("DELIVERY_UNAVAILABLE", "Private draft activation is not enabled; select PAUSED creation")
        for section in ("campaign", "adset"):
            for field in ("daily_budget", "lifetime_budget"):
                value = launch[section].get(field)
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                    raise fail("BUDGET_INVALID", "Advertising budgets require positive integer minor currency units")
        if not any(launch[section].get(field) for section in ("campaign", "adset")
                   for field in ("daily_budget", "lifetime_budget")):
            raise fail("BUDGET_REQUIRED", "Advertising requires a campaign or AdSet budget")
        creative = launch["creative"]
        allowed = {"name", "message", "link", "headline", "description", "call_to_action", "url_tags",
            "page_id", "format", "staged_media", "media_library_id", "existing_image_hash",
            "existing_video_id", "existing_creative_id", "source_instagram_media_id", "instagram_actor_id",
            "carousel_cards", "carousel_media_library_ids"}
        if set(creative) - allowed:
            raise fail("CREATIVE_FIELDS_UNAVAILABLE", "Selected creative options require their private draft adapter")
        for section in ("campaign", "adset", "ad"):
            if not isinstance(launch[section].get("name"), str) or not launch[section]["name"].strip():
                raise fail("INVALID_INPUT", "Campaign, AdSet and Ad names are required")
        if creative.get("page_id") not in (None, "", target["page_id"]):
            raise fail("PAGE_MISMATCH", "Creative Page differs from the selected account binding")
        references = local_references(creative)
        validate_creative(creative, references)
        if len(launch["campaign"]["name"]) > 120:
            raise fail("INVALID_INPUT", "Draft name is too long")
        selected = self.owner._asset_selections(launch, bool(references))
        if set(selected) - {"images"}:
            raise fail("ASSET_READ_UNAVAILABLE", "A selected advertising asset requires a scoped private read adapter")
        # Encode every level before media POSTs, including nested auth and
        # nonfinite/unsupported values. Invalid Ad input must not leave uploads.
        placeholders = ["planned-image-hash"] * (len(creative["carousel_cards"])
                        if creative.get("format") == "CAROUSEL" else 1)
        story = (carousel_creative(creative, target["page_id"], placeholders)
                 if creative.get("format") == "CAROUSEL" else image_creative(creative, target["page_id"], placeholders[0]))
        for level, values in (("campaign", launch["campaign"]), ("ad_set", launch["adset"]),
                              ("ad", {**launch["ad"], "creative": story})):
            try:
                fragment_params("100000", level=level, values=values)
            except (TypeError, ValueError):
                raise fail("INVALID_INPUT", "Advertising values cannot be encoded") from None
        return target, launch, references, _launch_fingerprint(payload, target["page_id"])

    def plan(self, *, profile_id, payload):
        target, launch, references, fingerprint = self.prepare(profile_id, payload)
        return {"configuration_valid": True, "ready": False, "checked_live": False,
            "mutation_posts": 0, "target": target, "creative_format": launch["creative"].get("format", "SINGLE"),
            "delivery": {key: "PAUSED" for key in ("campaign", "adset", "ad")},
            "steps": [{"stage": stage, "label": label} for stage, label in
                (("MEDIA", "Изображения"), ("DRAFT", "Черновик рекламы"),
                 ("CAMPAIGN", "Кампания"), ("ADSET", "Группа объявлений"),
                 ("AD", "Объявление"), ("PUBLISH", "Публикация"), ("VERIFY", "Проверка объектов"))],
            "local_media": references, "media_resolution_required": bool(references),
            "missing_contracts": [], "protocol": "private_ads_manager_versioned",
            "launch": launch, "launch_fingerprint": fingerprint, "source_revision": SOURCE_REVISION}

    async def _preflight(self, client, web, payload, target):
        from .private_launch import _payment_selector, _safe_payment_methods, _select_payment_method
        from .static_payment_read import inspect_methods
        account = target["ad_account_id"]
        row = await client.get("/act_" + account,
            {"fields": ["id", "account_status", "business", "promotable_page_ids"]})
        business = row.get("business") if isinstance(row, dict) else None
        pages = row.get("promotable_page_ids") if isinstance(row, dict) else None
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or row["id"].removeprefix("act_") != account
                or not isinstance(business, dict) or business.get("id") != target["business_id"]):
            raise fail("ACCOUNT_SCOPE_UNVERIFIED", "Live account read did not confirm the selected Business relation")
        if type(row.get("account_status")) not in {int, str} or str(row["account_status"]) != "1":
            raise fail("ACCOUNT_NOT_ACTIVE", "Selected advertising account is not active")
        if not isinstance(pages, list) or target["page_id"] not in pages:
            raise fail("PAGE_UNVERIFIED", "Live account response did not confirm advertising access to this Page")
        funding = await inspect_methods(web, account=account, business_id=target["business_id"])
        if (funding.get("account_scope_verified") is not True or funding.get("checked_live") is not True
                or funding.get("card_linked") is not True or funding.get("inventory_complete") is not True):
            raise fail("PAYMENT_UNVERIFIED", "A fresh linked payment method is required on this exact account")
        methods = _safe_payment_methods(funding.get("payment_methods"))
        selection = _payment_selector(payload)
        required, selected, source = selection is None and len(methods) > 1, {}, "selection_required"
        if not required:
            try:
                selected, source = _select_payment_method(methods, selection)
            except Exception as exc:
                if getattr(exc, "code", "") != "PRIVATE_LAUNCH_PAYMENT_SELECTION_UNAVAILABLE":
                    raise
                required = True
                source = "selection_unavailable"
        matched = [method for method in funding.get("payment_methods", []) if selected
                   and method.get("last4") == selected["last4"]
                   and str(method.get("type", "")).casefold() == selected["type"].casefold()]
        if any(method.get("is_expired") is True for method in matched):
            raise fail("PAYMENT_EXPIRED", "The selected linked card has expired")
        if any(method.get("needs_verification") is True or method.get("verification_tasks") for method in matched):
            raise fail("PAYMENT_CONFIRMATION_REQUIRED", "Meta requires confirmation of the linked card")
        return {**target, "account_status": 1, "browser_started": False, "transport": "private_http_h2",
            "page_access_checked_live": True, "payment_checked_live": True,
            "payment_selection_required": required, "payment_methods": methods,
            "selected_payment_method": selected, "payment_selection_source": source}

    async def _prepare_live(self, *, profile_id, context, session, payload):
        target, launch, references, fingerprint = self.prepare(profile_id, payload)
        if self.owner._inventory_row(context, target["business_id"], target["ad_account_id"]) is None:
            raise fail("ACCOUNT_CONTEXT_UNAVAILABLE", "The selected account is absent from this profile inventory")
        actor = identity(str(context.cookies.get("c_user") or ""))
        from .private_launch_media import LaunchMediaResolver
        media = await LaunchMediaResolver().resolve(references) if references else []
        if len(media) != len(references) or any(row.id != reference["id"] or row.media_type != "image"
                for row, reference in zip(media, references)):
            raise fail("MEDIA_FORMAT_UNAVAILABLE", "This private draft adapter supports image uploads")
        web = await session.facebook_web()
        from fb_worker import RemoteRequestError
        try:
            client = await AdsManagerPrivateHttp.open(web, account_id=target["ad_account_id"],
                business_id=target["business_id"], actor_id=actor)
        except RemoteRequestError:
            raise fail("TRANSPORT_UNAVAILABLE", "Profile HTTP connection did not reach Ads Manager", True) from None
        preflight = await self._preflight(client, web, payload, target)
        if not media:
            creative = launch["creative"]
            hashes = ([card["image_hash"] for card in creative["carousel_cards"]]
                      if creative.get("format") == "CAROUSEL" else [creative["existing_image_hash"]])
            for selected_hash in dict.fromkeys(hashes):
                await verify_image(client, selected_hash)
        return target, launch, fingerprint, actor, client, media, preflight

    async def review(self, *, profile_id, context, payload, transport=None):
        if transport is None:
            from .session import ProfileSession
            async with ProfileSession(context) as session:
                return await self.review(profile_id=profile_id, context=context, payload=payload,
                                         transport=SimpleNamespace(session=session))
        target, launch, fingerprint, actor, client, media, proof = await self._prepare_live(
            profile_id=profile_id, context=context, session=transport.session, payload=payload)
        return {"ready": not proof["payment_selection_required"], **target, "launch": launch,
            "preflight": proof, "protocol": "private_ads_manager_versioned",
            "source_revision": SOURCE_REVISION, "contracts": self.status()["contracts"]}

    async def run(self, *, item_id, profile_id, context, session, payload, task_idempotency_key=None):
        target, launch, references, fingerprint = self.prepare(profile_id, payload)
        key = str(payload.get("launch_key") or task_idempotency_key or
                  f"{profile_id}:{target['business_id']}:{target['ad_account_id']}").strip()
        if not key or len(key) > 200:
            raise fail("INVALID_INPUT", "Advertising launch key is missing or too long")
        await self.owner.state.init()
        for prior in await self.owner.state.all_steps(key):
            evidence = prior.get("result", {}).get("evidence", {})
            if (not prior["step"].startswith("ADS_MANAGER:")
                    or any(str(prior.get(name)) != str(target[name]) for name in
                           ("profile_id", "business_id", "ad_account_id"))
                    or evidence.get("launch_fingerprint") != fingerprint
                    or evidence.get("source_digest") != self.source_digest):
                raise fail("SAVED_OPERATION_MISMATCH", "Launch key belongs to another protocol, target or configuration")
            if not evidence.get("receipt"):
                raise fail("RECONCILE_REQUIRED", "An advertising submission has an unknown reply; replay is blocked")
        actor = identity(str(context.cookies.get("c_user") or ""))
        digest = hashlib.sha256((self.source_digest + actor).encode()).hexdigest()
        await self.owner.state.bind_plan(key, target, fingerprint, digest)
        target, launch, fingerprint, actor, client, media, proof = await self._prepare_live(
            profile_id=profile_id, context=context, session=session, payload=payload)
        if proof["payment_selection_required"]:
            raise fail("PAYMENT_SELECTION_REQUIRED", "Choose a currently linked card before creating advertising")
        sender = AdsManagerSubmitter(self.owner.state, launch_key=key, profile_id=profile_id,
            business_id=target["business_id"], account_id=target["ad_account_id"], actor_id=actor,
            launch_fingerprint=fingerprint, source_digest=self.source_digest)
        selection = launch["creative"]
        if media:
            hashes = [await upload_image(client, sender, row) for row in media]
        elif selection.get("format") == "CAROUSEL":
            hashes = [card["image_hash"] for card in selection["carousel_cards"]]
        else:
            hashes = [selection["existing_image_hash"]]
        creative = (carousel_creative(selection, target["page_id"], hashes)
                    if selection.get("format") == "CAROUSEL" else image_creative(selection, target["page_id"], hashes[0]))
        levels = {"campaign": copy.deepcopy(launch["campaign"]), "ad_set": copy.deepcopy(launch["adset"]),
                  "ad": {**copy.deepcopy(launch["ad"]), "creative": creative}}
        publisher = AdsManagerDraftPublisher(sender)
        result = await publisher.run(client, name=launch["campaign"]["name"],
                                     levels=levels, verify_objects=verify_published_objects)
        result.update({"launch_key": key, **target, "preflight": proof,
            "source_revision": SOURCE_REVISION,
            "steps": [{"step": name, "status": "SUCCESS", "entity_id": result["entities"][field]}
                      for name, field in (("CAMPAIGN", "campaign_id"), ("ADSET", "adset_id"),
                                          ("CREATIVE", "creative_id"), ("AD", "ad_id"))]})
        return result

    @staticmethod
    def status():
        return {"protocol": "private_ads_manager_versioned", "source_revision": SOURCE_REVISION,
            "contracts": {step: {"configured": True, "active": True, "verification_configured": True,
                "friendly_name": "AdsDraftPublishDataManager", "doc_id_configured": False}
                for step in ("CAMPAIGN", "ADSET", "CREATIVE", "AD")},
            "operations": {"IMAGE_UPLOAD": {"configured": True, "active": True}},
            "creative_formats": ["SINGLE", "CAROUSEL"], "resources": {}, "checked_live": False}
