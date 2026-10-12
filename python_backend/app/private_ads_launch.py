"""The existing Launch payload/journal adapted to private draft publication.

Creation is PAUSED; requested delivery follows verified publication. Media
and account resources use the same fresh profile transport and durable journal.
"""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

from .private_ads_draft import fail, fragment_params, identity
from .private_ads_creative import resolved_creative, validate_creative
from .private_ads_http import AdsManagerPrivateHttp
from .private_ads_media import upload_image, verify_image
from .private_ads_video import upload_video, verify_video
from .private_ads_publication import AdsManagerDraftPublisher
from .private_ads_state import AdsManagerSubmitter
from .private_ads_verify import verify_published_objects
from .private_launch_extensions import OperationKey

SOURCE_REVISION = "ads-manager-private-2026-10-13-media-resources"

class AdsManagerLaunch:
    def __init__(self, owner):
        self.owner = owner
        from .private_ads_catalog import AdsManagerRouteCatalog
        self.catalog = AdsManagerRouteCatalog()
        digest = hashlib.sha256((SOURCE_REVISION + self.catalog.digest).encode())
        for name in ("private_ads_launch.py", "private_ads_http.py", "private_ads_draft.py",
                     "private_ads_state.py", "private_ads_media.py", "private_ads_publication.py",
                     "private_ads_verify.py", "private_ads_creative.py", "private_ads_video.py", "private_ads_delivery.py", "private_ads_catalog.py", "private_ads_reads.py"):
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
        if set(selected) - set(self.catalog.data["resource_fields"]):
            raise fail("ASSET_READ_UNAVAILABLE", "Selected asset has no reviewed account read")
        for section in ("campaign", "adset", "ad"):
            values = launch[section]
            if (set(values) - set(self.catalog.data["object_fields"][section])
                    or set(values) & {"id", "account_id", "campaign_id", "adset_id", "creative"}
                    or values.get("acting_business_id", target["business_id"]) != target["business_id"]):
                raise fail("OBJECT_FIELDS_UNAVAILABLE", "Advertising fields are unreviewed or supplied for another object")
        # Encode every level before media POSTs, including nested auth and
        # nonfinite/unsupported values. Invalid Ad input must not leave uploads.
        placeholders = [{"image_hash": "planned-image-hash"}] * (len(creative["carousel_cards"])
                        if creative.get("format") == "CAROUSEL" else 1)
        story = resolved_creative(creative, target["page_id"], placeholders,
            {"id": creative.get("source_instagram_media_id")})
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
            "delivery": launch.get("delivery", {key: "PAUSED" for key in ("campaign", "adset", "ad")}),
            "steps": [{"stage": stage, "label": label} for stage, label in
                (("MEDIA", "Медиа рекламы"), ("DRAFT", "Черновик рекламы"),
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
            {"fields": ["id", "account_status", "business", "ad_account_promotable_objects"]})
        business = row.get("business") if isinstance(row, dict) else None
        promotable = row.get("ad_account_promotable_objects") if isinstance(row, dict) else None
        pages = promotable.get("promotable_page_ids") if isinstance(promotable, dict) else None
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

    async def _prepare_live(self, *, profile_id, context, session, payload, saved_media=None):
        target, launch, references, fingerprint = self.prepare(profile_id, payload)
        if self.owner._inventory_row(context, target["business_id"], target["ad_account_id"]) is None:
            raise fail("ACCOUNT_CONTEXT_UNAVAILABLE", "The selected account is absent from this profile inventory")
        actor = identity(str(context.cookies.get("c_user") or ""))
        from .private_launch_media import LaunchMediaResolver
        media = await LaunchMediaResolver().resolve(references) if references and saved_media is None else []
        if (saved_media is None and len(media) != len(references)) or any(row.id != reference["id"] or row.media_type not in {"image", "video"}
                for row, reference in zip(media, references)):
            raise fail("MEDIA_FORMAT_UNAVAILABLE", "Managed media type is unsupported")
        web = await session.facebook_web()
        from fb_worker import RemoteRequestError
        try:
            client = await AdsManagerPrivateHttp.open(web, account_id=target["ad_account_id"],
                business_id=target["business_id"], actor_id=actor)
        except RemoteRequestError:
            raise fail("TRANSPORT_UNAVAILABLE", "Profile HTTP connection did not reach Ads Manager", True) from None
        preflight = await self._preflight(client, web, payload, target)
        selections = self.owner._asset_selections(launch, bool(references))
        preflight["asset_checks"] = await self.owner._verify_assets(web,
            {name: wanted for name, wanted in selections.items() if name not in {"images", "videos", "instagram_media"}}, target)
        creative = launch["creative"]
        if creative.get("format") == "INSTAGRAM_POST":
            result = await self.owner.reads.read(web, "instagram_media", {**target, "query": "", "limit": 100})
            matches = [row for row in result["data"] if row.get("id") == creative["source_instagram_media_id"]]
            if len(matches) != 1:
                raise fail("INSTAGRAM_UNVERIFIED", "Selected post is not available for advertising in this account")
            if creative.get("instagram_actor_id") and matches[0].get("instagram_actor_id") != creative["instagram_actor_id"]:
                raise fail("INSTAGRAM_ACTOR_MISMATCH", "Selected post belongs to another Instagram actor")
            preflight["instagram_media"] = matches[0]
        elif not media:
            selected = saved_media or (creative["carousel_cards"] if creative.get("format") == "CAROUSEL" else
                        [{"image_hash": creative.get("existing_image_hash"), "video_id": creative.get("existing_video_id")}])
            preflight["confirmed_media"] = []
            for item in selected:
                proof = (await verify_video(client, item["video_id"]) if item.get("video_id")
                         else await verify_image(client, item["image_hash"]))
                preflight["confirmed_media"].append(proof)
        # Validate video/Instagram wire values before any upload/draft POST.
        placeholders = [{"video_id": "100001", "thumbnail_uri": "https://example.invalid/thumbnail"}
                        if row.media_type == "video" else {"image_hash": "planned-image-hash"} for row in media]
        story = resolved_creative(creative, target["page_id"], placeholders or preflight.get("confirmed_media", []),
                                 preflight.get("instagram_media"))
        fragment_params("100000", level="ad", values={**launch["ad"], "creative": story})
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
        saved_steps = await self.owner.state.all_steps(key)
        for prior in saved_steps:
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
        published = await self.owner.state.step(key, OperationKey("ADS_MANAGER:PUBLISH"))
        if (launch.get("delivery") and published and published.get("status") == "SUCCESS"
                and published.get("result", {}).get("evidence", {}).get("verification", {}).get("verified") is True):
            return await self._finish_delivery(profile_id, context, session, payload, target, launch, key,
                {"status": "SUCCESS", "entities": published["result"]["evidence"]["verification"]["entities"],
                 "launch_key": key, **target, "verified": True, "browser_started": False})
        saved_media = []
        for reference in references:
            candidates = []
            for prior in saved_steps:
                prefix, separator, sha = prior["step"].removeprefix("ADS_MANAGER:").partition(":")
                receipt = prior.get("result", {}).get("evidence", {}).get("receipt", {})
                if (separator and prefix in {"IMAGE", "VIDEO_FINISH"} and len(sha) == 64
                        and reference["id"] == "m-" + sha[:24]
                        and (not reference.get("sha256") or reference["sha256"] == sha)):
                    candidates.append({field: receipt[field] for field in ("image_hash", "video_id") if field in receipt})
            if len(candidates) != 1 or not candidates[0]:
                saved_media = None
                break
            saved_media.append(candidates[0])
        target, launch, fingerprint, actor, client, media, proof = await self._prepare_live(
            profile_id=profile_id, context=context, session=session, payload=payload,
            saved_media=saved_media if references else None)
        if proof["payment_selection_required"]:
            raise fail("PAYMENT_SELECTION_REQUIRED", "Choose a currently linked card before creating advertising")
        sender = AdsManagerSubmitter(self.owner.state, launch_key=key, profile_id=profile_id,
            business_id=target["business_id"], account_id=target["ad_account_id"], actor_id=actor,
            launch_fingerprint=fingerprint, source_digest=self.source_digest)
        selection = launch["creative"]
        uploaded = []
        for row in media:
            uploaded.append(await upload_video(client, sender, row) if row.media_type == "video"
                            else {"image_hash": await upload_image(client, sender, row)})
        creative = resolved_creative(selection, target["page_id"], uploaded or proof.get("confirmed_media", []),
                                     proof.get("instagram_media"))
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
        return await self._finish_delivery(profile_id, context, session, payload, target, launch, key, result)

    async def _finish_delivery(self, profile_id, context, session, payload, target, launch, key, result):
        if launch.get("delivery"):
            delivery_key = "ads-delivery:" + hashlib.sha256(key.encode()).hexdigest()
            delivery = await self.run_delivery(profile_id=profile_id, context=context, session=session,
                payload={**payload, **target, "source_launch_key": key, "delivery_key": delivery_key,
                         "status": "ACTIVE", "delivery": launch["delivery"]})
            result.update(delivery=delivery["delivery"], delivery_key=delivery_key)
            result["steps"] = result.get("steps", []) + delivery["steps"]
        return result

    async def run_delivery(self, *, profile_id, context, session, payload, task_idempotency_key=None):
        from .private_ads_delivery import run_delivery
        return await run_delivery(self, profile_id=profile_id, context=context, session=session,
                                  payload=payload, task_idempotency_key=task_idempotency_key)

    def status(self):
        return {"protocol": "private_ads_manager_versioned", "source_revision": SOURCE_REVISION,
            "contracts": {step: {"configured": True, "active": True, "verification_configured": True,
                "friendly_name": "AdsDraftPublishDataManager", "doc_id_configured": False}
                for step in ("CAMPAIGN", "ADSET", "CREATIVE", "AD")},
            "operations": {name: {"configured": True, "active": True} for name in ("IMAGE_UPLOAD", "VIDEO_UPLOAD", "ACTIVATE_CAMPAIGN", "ACTIVATE_ADSET", "ACTIVATE_AD")},
            "creative_formats": ["SINGLE", "CAROUSEL", "INSTAGRAM_POST"], "resources": self.owner.reads.status(), "source_catalog_digest": self.catalog.digest, "checked_live": False}
