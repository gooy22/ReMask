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
from .private_ads_resources import AdsManagerResourceCatalog, read_pixels
from .private_ads_objectives import ObjectiveContract
from .private_ads_objective_wire import ObjectiveWireContract
from .private_ads_bidding import MANIFEST as BIDDING_MANIFEST, native_bidding
from .private_launch_extensions import OperationKey

SOURCE_REVISION = "ads-manager-private-2026-10-13-media-resources"
# Exact source/catalog digests of reviewed main 6b7dfc1, 7b6a985,
# b23917a (PR #228), 638d773 (PR #229), 2582080 (PR #230),
# and 842f2c6 (PR #231).
# Compatibility permits only fresh reads of an already accepted publication.
READ_ONLY_PUBLICATION_PREDECESSORS = frozenset({
    "e38dc7d32ba2ccd73d18e0b31b477be7e1d4f94624d1b1a1817efd692307a2c2",
    "cc0d9d896c67b95db8bdef3fce77279ba9d9446302f0bd0f1c10a1e62ba92645",
    "cb9b5fd76c6b9079d9584818a085848df404239437989499cd27f284f11e9353",
    "0ddf56b2f696365f7cad55886598a7dc95a1a89ba021839f0fb669df4add2c62",
    "40449a5bd6554241032975780e5d44f351ec77a479f803b3602cde90eb35882d",
    "55cf50aa8acfb55401f75100085b8a41eeb9fc02e4b81458d311e10807f235c3",
})

class AdsManagerLaunch:
    def __init__(self, owner):
        self.owner = owner
        from .private_ads_catalog import AdsManagerRouteCatalog
        self.catalog = AdsManagerRouteCatalog()
        self.objectives = ObjectiveContract()
        self.objective_wire = ObjectiveWireContract()
        digest = hashlib.sha256((SOURCE_REVISION + self.catalog.digest).encode())
        digest.update(self.objectives.digest.encode())
        digest.update(self.objective_wire.digest.encode())
        digest.update(BIDDING_MANIFEST.read_bytes())
        digest.update(Path(__file__).with_name('contracts').joinpath('media_layout_20261021.json').read_bytes())
        digest.update(Path(__file__).with_name('contracts').joinpath('private_ads_cta_20261021.json').read_bytes())
        for name in ("private_ads_launch.py", "private_ads_http.py", "private_ads_document.py", "private_ads_draft.py",
                     "private_ads_state.py", "private_ads_media.py", "private_ads_publication.py", "private_launch_media.py", "media_preflight.py", "private_ads_cta.py",
                     "private_ads_verify.py", "private_ads_creative.py", "private_ads_video.py", "private_ads_delivery.py", "private_ads_catalog.py", "private_ads_reads.py", "private_draft_pages.py", "private_ads_resources.py", "private_ads_helpers.py", "private_ads_preview.py", "private_ads_objectives.py", "private_ads_inspect.py", "private_ads_bidding.py", "private_ads_objective_wire.py", "private_ads_objective_repair.py"):
            digest.update(name.encode() + b"\0" + Path(__file__).with_name(name).read_bytes())
        self.source_digest = digest.hexdigest()

    def prepare(self, profile_id, payload):
        from .private_launch import _effective_launch_payload, _launch_fingerprint
        from .private_launch_media import local_references
        if not isinstance(payload, dict) or not str(profile_id or "").strip():
            raise fail("INVALID_INPUT", "Advertising profile and configuration are required")
        target = {"profile_id": str(profile_id), **{key: identity(payload.get(key))
                  for key in ("business_id", "ad_account_id", "page_id")}}
        raw_launch = payload.get("launch")
        if (isinstance(raw_launch, dict) and {"base", "override"} & set(raw_launch)
                and set(raw_launch) - {"base", "override"}):
            raise fail("INVALID_INPUT", "Launch overrides contain unsupported sections")
        launch = _effective_launch_payload(raw_launch)
        mode = launch.get("execution_mode", "PUBLISH")
        if not isinstance(mode, str) or mode not in {"DRAFT", "PUBLISH"}:
            raise fail("INVALID_INPUT", "Choose DRAFT or PUBLISH execution")
        if set(launch) - {"execution_mode", "campaign", "adset", "ad", "creative", "delivery"}:
            raise fail("INVALID_INPUT", "Launch contains unsupported sections")
        if mode == "DRAFT" and set(payload) - {"business_id", "ad_account_id", "page_id", "launch_key", "launch", "publish_saved_draft"}:
            raise fail("INVALID_INPUT", "Draft input contains unsupported fields")
        if mode == "DRAFT" and any(value != "PAUSED" for value in launch.get("delivery", {}).values()):
            raise fail("INVALID_INPUT", "A Meta draft cannot request active delivery")
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
            "carousel_cards", "carousel_media_library_ids", "official_params"}
        if set(creative) - allowed:
            raise fail("CREATIVE_FIELDS_UNAVAILABLE", "Selected creative options require their private draft adapter")
        for section in ("campaign", "adset", "ad"):
            if not isinstance(launch[section].get("name"), str) or not launch[section]["name"].strip():
                raise fail("INVALID_INPUT", "Campaign, AdSet and Ad names are required")
        if creative.get("page_id") not in (None, "", target["page_id"]):
            raise fail("PAGE_MISMATCH", "Creative Page differs from the selected account binding")
        references = local_references(creative)
        validate_creative(creative, references)
        from .private_ads_creative import creative_options, option_media
        creative_options(creative, self.catalog)
        option_media(creative)
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
        budgets = [values[field] for values in (launch['campaign'],launch['adset'])
                   for field in ('daily_budget','lifetime_budget') if field in values]
        if len(budgets)>1 or any(type(value) is not int or not 0 < value <= 9_007_199_254_740_991 for value in budgets):
            raise fail('BUDGET_INVALID','Use one positive integer budget in account minor units')
        targeting = launch['adset'].get('targeting') or {}
        if (not isinstance(targeting, dict)
                or set(targeting) - set(self.catalog.data['object_fields']['targeting'])
                or 'adgroup_id' in targeting
                or any(field.startswith('effective_') for field in targeting)):
            raise fail('TARGETING_FIELDS_UNAVAILABLE', 'Targeting contains unreviewed or read-only fields')
        # Encode every level before media POSTs, including nested auth and
        # nonfinite/unsupported values. Invalid Ad input must not leave uploads.
        self.objectives.validate(launch)
        campaign_values, adset_values, _ = native_bidding(launch["campaign"], launch["adset"])
        self.catalog.validate_values(launch)
        from .private_ads_cta import validate_cta
        validate_cta(launch)
        campaign_values = self.objective_wire.campaign(campaign_values)
        placeholders = [{"image_hash": "planned-image-hash"}] * (len(creative["carousel_cards"])
                        if creative.get("format") == "CAROUSEL" else 1)
        story = resolved_creative(creative, target["page_id"], placeholders,
            {"id": creative.get("source_instagram_media_id")})
        for level, values in (("campaign", campaign_values), ("ad_set", adset_values),
                              ("ad", {**launch["ad"], "creative": story})):
            try:
                fragment_params("100000", level=level, values=values)
            except (TypeError, ValueError):
                raise fail("INVALID_INPUT", "Advertising values cannot be encoded") from None
        return target, launch, references, _launch_fingerprint(payload, target["page_id"])

    def plan(self, *, profile_id, payload):
        if isinstance(payload, dict) and "publish_saved_draft" in payload:
            raise fail("INVALID_INPUT", "Draft review cannot request publication")
        target, launch, references, fingerprint = self.prepare(profile_id, payload)
        draft_only = launch.get("execution_mode") == "DRAFT"
        contracts = self.status()["contracts"]
        missing_contracts = [name for name, row in contracts.items()
                             if row.get("active") is not True or row.get("configured") is not True]
        creative_format = launch["creative"].get("format", "SINGLE")
        if creative_format not in self.status()["creative_formats"]:
            missing_contracts.append("CREATIVE_FORMAT_" + str(creative_format))
        for resource in self.owner._asset_selections(launch, bool(references)):
            if resource not in self.owner.reads.rows:
                missing_contracts.append("READ_" + resource.upper())
        return {"configuration_valid": True, "ready": False, "checked_live": False,
            "editor_state": "EDITOR_UNVERIFIED", "missing_fields": [],
            "objective_representation": {"selected": launch["campaign"].get("objective"),
                "private_campaign": {key: value for key, value in self.objective_wire.campaign(launch["campaign"]).items()
                    if key in {"objective", "is_odax_campaign_group"}},
                "editor_state": "EDITOR_UNVERIFIED"},
            "mutation_posts": 0, "target": target, "creative_format": creative_format,
            "delivery": launch.get("delivery", {key: "PAUSED" for key in ("campaign", "adset", "ad")}),
            "steps": [{"stage": stage, "label": label} for stage, label in
                (("MEDIA", "Медиа рекламы"), ("DRAFT", "Черновик рекламы"),
                 ("CAMPAIGN", "Кампания"), ("ADSET", "Группа объявлений"),
                 ("AD", "Объявление"), ("PUBLISH", "Публикация"), ("VERIFY", "Проверка объектов"))
                if not (draft_only and stage == "PUBLISH")],
            "execution_mode": "DRAFT" if draft_only else "PUBLISH",
            "local_media": references, "media_resolution_required": bool(references),
            "missing_contracts": missing_contracts, "protocol": "private_ads_manager_versioned",
            "launch": launch, "launch_fingerprint": fingerprint, "source_revision": SOURCE_REVISION}

    async def _preflight(self, client, web, payload, target, *, publication_required=False):
        from .private_launch import _payment_selector, _safe_payment_methods, _select_payment_method
        from .static_payment_read import inspect_methods, complete_methods
        from .private_launch import _effective_launch_payload
        # Status-only delivery requests need no creation configuration. An
        # activation always requires publication gates, even if its caller
        # includes a stale DRAFT label in the original Launch configuration.
        draft_only = not publication_required and _effective_launch_payload(payload.get("launch")).get("execution_mode") == "DRAFT"
        account = target["ad_account_id"]
        fields = ["id", "account_id", "account_status", "business"]
        if not draft_only:
            fields.append("ad_account_promotable_objects")
        row = await client.get("/act_" + account, {"fields": fields})
        business = row.get("business") if isinstance(row, dict) else None
        promotable = row.get("ad_account_promotable_objects") if isinstance(row, dict) else None
        pages = promotable.get("promotable_page_ids") if isinstance(promotable, dict) else None
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or row["id"].removeprefix("act_") != account
                or not isinstance(business, dict) or business.get("id") != target["business_id"]):
            raise fail("ACCOUNT_SCOPE_UNVERIFIED", "Live account read did not confirm the selected Business relation")
        if type(row.get("account_status")) not in {int, str} or str(row["account_status"]) != "1":
            raise fail("ACCOUNT_NOT_ACTIVE", "Selected advertising account is not active")
        if draft_only:
            from .private_draft_pages import verify_draft_page
            page_proof = await verify_draft_page(web, target=target, client=client)
            return {**target, "account_status": 1, "browser_started": False,
                "transport": "private_http_h2", "page_access_checked_live": False,
                "draft_page": page_proof, "draft_ready": True, "publication_ready": False,
                "payment_checked_live": False, "payment_selection_required": False}
        if not isinstance(pages, list) or target["page_id"] not in pages:
            raise fail("PAGE_UNVERIFIED", "Live account response did not confirm advertising access to this Page")
        funding = await inspect_methods(web, account=account, business_id=target["business_id"])
        if (funding.get("account_scope_verified") is True
                and funding.get("business_scope_verified") is True
                and funding.get("payment_account_relation_verified") is True
                and funding.get("methods_query_verified") is True
                and funding.get("inventory_complete") is not True):
            # The observed READ_METHODS query returns a filtered collection.
            # Prove the complete account-scoped credential inventory before
            # choosing a card; do not infer completeness from one visible card.
            funding = await complete_methods(web, funding, business_id=target["business_id"])
        if not all(funding.get(field) is True for field in (
                "account_scope_verified", "business_scope_verified",
                "payment_account_relation_verified", "methods_query_verified",
                "checked_live", "card_linked", "inventory_complete")):
            raise fail("PAYMENT_UNVERIFIED", "A complete fresh account-scoped payment read is required")
        if (funding.get("account_id") != account
                or funding.get("business_id") != target["business_id"]
                or not isinstance(funding.get("payment_account_id"), str)
                or not funding["payment_account_id"].isdigit()):
            raise fail("PAYMENT_SCOPE_UNVERIFIED", "Complete payment read belongs to another account or Business")
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
        if not required:
            matched = [method for method in funding.get("payment_methods", []) if selected
                       and method.get("last4") == selected["last4"]
                       and str(method.get("type", "")).casefold() == selected["type"].casefold()]
            if len(matched) != 1:
                raise fail("PAYMENT_UNVERIFIED", "Exactly one live payment method must match the selected card")
            method = matched[0]
            if method.get("is_expired") is True:
                raise fail("PAYMENT_EXPIRED", "The selected linked card has expired")
            if (method.get("needs_verification") is True or method.get("verification_tasks")
                    or method.get("card_confirmation_status") == "REQUIRED"):
                raise fail("PAYMENT_CONFIRMATION_REQUIRED", "Meta requires confirmation of the linked card")
            if (method.get("needs_verification") is not False
                    or method.get("verification_tasks_observed") is not True
                    or method.get("card_confirmation_status") != "CLEAR"):
                raise fail("PAYMENT_CONFIRMATION_UNVERIFIED",
                           "Selected card verification state was not conclusively cleared by Meta")
        return {**target, "account_status": 1, "browser_started": False, "transport": "private_http_h2",
            "page_access_checked_live": True, "payment_checked_live": True,
            "payment_selection_required": required, "payment_methods": methods,
            "selected_payment_method": selected, "payment_selection_source": source}

    async def _prepare_live(self, *, profile_id, context, session, payload, saved_media=None, expected_creative=None,
                            publication_required=False):
        target, launch, references, fingerprint = self.prepare(profile_id, payload)
        if self.owner._inventory_row(context, target["business_id"], target["ad_account_id"]) is None:
            raise fail("ACCOUNT_CONTEXT_UNAVAILABLE", "The selected account is absent from this profile inventory")
        actor = identity(str(context.cookies.get("c_user") or ""))
        from .private_launch_media import LaunchMediaResolver
        media = await LaunchMediaResolver().resolve(references) if references and saved_media is None and expected_creative is None else []
        if (saved_media is None and expected_creative is None and len(media) != len(references)) or any(row.id != reference["id"] or row.media_type not in {"image", "video"}
                for row, reference in zip(media, references)):
            raise fail("MEDIA_FORMAT_UNAVAILABLE", "Managed media type is unsupported")
        from .media_preflight import inspect_managed, compatibility
        media_checks = []
        for row in media:
            facts = row.inspection or await inspect_managed(row)
            media_checks.append({'id': row.id, 'sha256': row.sha256,
                **compatibility(facts, launch['adset'].get('targeting', {}),
                                creative_format=launch['creative'].get('format', 'SINGLE'))})
        web = await session.facebook_web()
        from fb_worker import RemoteRequestError
        try:
            client = await AdsManagerPrivateHttp.open(web, account_id=target["ad_account_id"],
                business_id=target["business_id"], actor_id=actor)
        except RemoteRequestError:
            raise fail("TRANSPORT_UNAVAILABLE", "Profile HTTP connection did not reach Ads Manager", True) from None
        if expected_creative is not None:
            client.read_only = True
            row = await client.get("/act_" + target["ad_account_id"], {"fields": ["id", "account_id", "business"]})
            if (not isinstance(row.get("id"), str) or row["id"].removeprefix("act_") != target["ad_account_id"]
                    or not isinstance(row.get("business"), dict) or row["business"].get("id") != target["business_id"]):
                raise fail("ACCOUNT_SCOPE_UNVERIFIED", "Publication recovery account/Business is unconfirmed")
            return target, launch, fingerprint, actor, client, [], {**target,
                "resume_read_only": True, "payment_selection_required": False,
                "payment_checked_live": False, "expected_creative": copy.deepcopy(expected_creative)}
        preflight = await self._preflight(client, web, payload, target, publication_required=publication_required)
        preflight['local_media'] = media_checks
        creative = launch['creative']
        if creative.get('existing_creative_id'):
            selected_id = identity(creative['existing_creative_id'])
            row = await client.get('/'+selected_id, {'fields':['id','account_id','object_story_spec','image_hash','video_id']})
            from .private_ads_verify import _scope
            _scope(row,target['ad_account_id'],selected_id)
            story = row.get('object_story_spec') or {}
            if not isinstance(story,dict):
                raise fail('CREATIVE_FORMAT_UNAVAILABLE','Existing creative story is unconfirmed')
            link = story.get('link_data') or {}
            video = story.get('video_data') or {}
            if not all(isinstance(part,dict) for part in (story,link,video)) or link.get('child_attachments'):
                raise fail('CREATIVE_FORMAT_UNAVAILABLE','Select the explicit carousel format for carousel media')
            image_id = row.get('image_hash') or link.get('image_hash')
            video_id = row.get('video_id') or video.get('video_id')
            if ((row.get('image_hash') and link.get('image_hash') and row['image_hash'] != link['image_hash'])
                    or (row.get('video_id') and video.get('video_id') and row['video_id'] != video['video_id'])):
                raise fail('MEDIA_UNVERIFIED','Existing creative media identities conflict')
            if bool(image_id) == bool(video_id):
                raise fail('MEDIA_UNVERIFIED','Existing creative did not confirm a single image or video')
            creative.pop('existing_creative_id')
            creative['existing_video_id' if video_id else 'existing_image_hash'] = video_id or image_id
            preflight['existing_creative'] = {'id':selected_id,'account_id':target['ad_account_id'],'verified':True}
        selections = self.owner._asset_selections(launch, bool(references))
        preflight["asset_checks"] = await self.owner._verify_assets(web,
            {name: wanted for name, wanted in selections.items() if name not in {"images", "videos", "instagram_media", "pixels"}}, target)
        if selections.get("pixels"):
            inventory = await read_pixels(client)
            found = {row["id"] for row in inventory["data"]}
            if not set(selections["pixels"]) <= found:
                raise fail("ASSET_ACCESS_UNCONFIRMED", "Selected pixel is not available to this ad account")
            preflight["asset_checks"]["pixels"] = {"verified": True,
                "selected": selections["pixels"], "checked_live": True}
        creative = launch["creative"]
        from .private_ads_creative import option_media
        extra_images, extra_videos = option_media(creative)
        for selected_hash in extra_images: await verify_image(client,selected_hash)
        for selected_video in extra_videos: await verify_video(client,selected_video)
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
        return {"ready": not proof["payment_selection_required"],
            "configuration_valid": True, "checked_live": True,
            "editor_state": "EDITOR_UNVERIFIED", "missing_fields": [], "missing_contracts": [],
            "api_preflight_ready": proof.get("draft_ready") is True if launch.get("execution_mode") == "DRAFT"
                else not proof["payment_selection_required"],
            **target, "launch": launch,
            "ready_for": launch.get("execution_mode", "PUBLISH"),
            "preflight": proof, "protocol": "private_ads_manager_versioned",
            "source_revision": SOURCE_REVISION, "contracts": self.status()["contracts"]}

    async def run(self, *, item_id, profile_id, context, session, payload, task_idempotency_key=None):
        promote = isinstance(payload, dict) and "publish_saved_draft" in payload
        if promote and (payload["publish_saved_draft"] is not True
                        or "repair_objective" in payload or "reconcile_only" in payload):
            raise fail("INVALID_INPUT", "Saved draft publication must be a separate explicit action")
        if isinstance(payload, dict) and "repair_objective" in payload:
            from .private_ads_objective_repair import repair_saved_objective
            return await repair_saved_objective(self, profile_id=profile_id, context=context, session=session, payload=payload)
        if isinstance(payload, dict) and "reconcile_only" in payload:
            from .private_ads_inspect import inspect_saved_draft
            return await inspect_saved_draft(self, profile_id=profile_id, context=context, session=session, payload=payload)
        target, launch, references, fingerprint = self.prepare(profile_id, payload)
        if promote and (launch.get("execution_mode") != "DRAFT" or launch.get("delivery")):
            raise fail("INVALID_INPUT", "Saved draft publication requires the original DRAFT intent and PAUSED delivery")
        key = str(payload.get("launch_key") or task_idempotency_key or
                  f"{profile_id}:{target['business_id']}:{target['ad_account_id']}").strip()
        if not key or len(key) > 200:
            raise fail("INVALID_INPUT", "Advertising launch key is missing or too long")
        await self.owner.state.init()
        saved_steps = await self.owner.state.all_steps(key)
        actor = identity(str(context.cookies.get("c_user") or ""))
        source_digest = self.source_digest
        saved_sources = [row.get("result", {}).get("evidence", {}).get("source_digest") for row in saved_steps]
        if any(not isinstance(value, str) for value in saved_sources):
            raise fail("SAVED_OPERATION_MISMATCH", "Saved publication source revision is malformed")
        saved_sources = set(saved_sources)
        historical_recovery = bool(saved_steps) and saved_sources != {source_digest}
        if historical_recovery:
            if len(saved_sources) != 1 or next(iter(saved_sources)) not in READ_ONLY_PUBLICATION_PREDECESSORS:
                raise fail("SAVED_OPERATION_MISMATCH", "Saved publication has an unreviewed source revision")
            source_digest = next(iter(saved_sources))
            saved = {row["step"]: row for row in saved_steps}
            hierarchy = {"ADS_MANAGER:DRAFT", "ADS_MANAGER:FRAGMENT_CAMPAIGN",
                         "ADS_MANAGER:FRAGMENT_AD_SET", "ADS_MANAGER:FRAGMENT_AD"}
            publication = saved.get("ADS_MANAGER:PUBLISH", {}).get("result", {}).get("evidence", {}).get("receipt", {})
            if (not hierarchy <= set(saved) or not publication.get("operation_id")
                    or any(value != "PAUSED" for value in launch.get("delivery", {}).values())):
                raise fail("SAVED_OPERATION_MISMATCH", "Historical recovery requires an accepted publication and PAUSED delivery")
            for name in hierarchy:
                row = saved[name]
                proof = row.get("result", {}).get("evidence", {}).get("verification", {})
                if (row.get("status") != "SUCCESS" or proof.get("verified") is not True
                        or proof.get("source") != "independent_private_ads_http"):
                    raise fail("SAVED_OPERATION_MISMATCH", "Historical publication hierarchy is not independently confirmed")
        for prior in saved_steps:
            evidence = prior.get("result", {}).get("evidence", {})
            if (not prior["step"].startswith("ADS_MANAGER:")
                    or any(str(prior.get(name)) != str(target[name]) for name in
                           ("profile_id", "business_id", "ad_account_id"))
                    or evidence.get("launch_fingerprint") != fingerprint
                    or evidence.get("source_digest") != source_digest
                    or evidence.get("actor_id") != actor):
                raise fail("SAVED_OPERATION_MISMATCH", "Launch key belongs to another protocol, target or configuration")
            if not evidence.get("receipt"):
                if isinstance(evidence.get("response_error"), dict):
                    from .private_ads_http import AdsManagerResponseError
                    raise AdsManagerResponseError(evidence["response_error"])
                raise fail("RECONCILE_REQUIRED", "An advertising submission has an unknown reply; replay is blocked")
        if promote:
            saved = {row["step"]: row for row in saved_steps}
            for operation in ("DRAFT", "FRAGMENT_CAMPAIGN", "FRAGMENT_AD_SET", "FRAGMENT_AD"):
                row = saved.get("ADS_MANAGER:" + operation)
                verified = (row or {}).get("result", {}).get("evidence", {}).get("verification", {})
                if (not row or row.get("status") != "SUCCESS" or verified.get("verified") is not True
                        or verified.get("source") != "independent_private_ads_http"
                        or (operation != "DRAFT" and verified.get("meta_validation_status") != "VALIDATED")):
                    raise fail("SAVED_OPERATION_MISMATCH", "Publication requires the complete independently validated saved draft")
        digest = hashlib.sha256((source_digest + actor).encode()).hexdigest()
        await self.owner.state.bind_plan(key, target, fingerprint, digest)
        published = await self.owner.state.step(key, OperationKey("ADS_MANAGER:PUBLISH"))
        if (not historical_recovery and launch.get("delivery") and published and published.get("status") == "SUCCESS"
                and published.get("result", {}).get("evidence", {}).get("verification", {}).get("verified") is True):
            return await self._finish_delivery(profile_id, context, session, payload, target, launch, key,
                {"status": "SUCCESS", "entities": published["result"]["evidence"]["verification"]["entities"],
                 "launch_key": key, **target, "verified": True, "browser_started": False})
        required = {"ADS_MANAGER:DRAFT", "ADS_MANAGER:FRAGMENT_CAMPAIGN", "ADS_MANAGER:FRAGMENT_AD_SET",
                    "ADS_MANAGER:FRAGMENT_AD", "ADS_MANAGER:PUBLISH"}
        saved = {row["step"]: row for row in saved_steps}
        expected_creative = None
        if required <= set(saved):
            candidate = saved["ADS_MANAGER:FRAGMENT_AD"].get("result", {}).get("evidence", {}).get("binding", {}).get("creative")
            if isinstance(candidate, dict):
                expected_creative = candidate
        if historical_recovery and expected_creative is None:
            raise fail("SAVED_OPERATION_MISMATCH", "Historical publication has no confirmed creative binding")
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
            saved_media=saved_media if references else None, expected_creative=expected_creative,
            publication_required=promote)
        if proof["payment_selection_required"]:
            raise fail("PAYMENT_SELECTION_REQUIRED", "Choose a currently linked card before creating advertising")
        sender = AdsManagerSubmitter(self.owner.state, launch_key=key, profile_id=profile_id,
            business_id=target["business_id"], account_id=target["ad_account_id"], actor_id=actor,
            launch_fingerprint=fingerprint, source_digest=source_digest)
        selection = launch["creative"]
        uploaded = []
        for row in media:
            uploaded.append(await upload_video(client, sender, row) if row.media_type == "video"
                            else {"image_hash": await upload_image(client, sender, row)})
        creative = (copy.deepcopy(proof["expected_creative"]) if proof.get("resume_read_only")
                    else resolved_creative(selection, target["page_id"], uploaded or proof.get("confirmed_media", []),
                                           proof.get("instagram_media")))
        # A historical publication resumes its exact saved requests, without
        # applying a new creation serializer to previously accepted mutations.
        if historical_recovery:
            levels = {}
            for level in ("campaign", "ad_set", "ad"):
                binding = saved["ADS_MANAGER:FRAGMENT_" + level.upper()].get("result", {}).get("evidence", {}).get("binding", {}).get("values")
                if not isinstance(binding, dict) or not binding:
                    raise fail("SAVED_OPERATION_MISMATCH", "Historical publication has no exact submitted settings")
                levels[level] = copy.deepcopy(binding)
            # _saved_publication checks each original request fingerprint
            # against these bindings before any publication/object read.
            # In particular, do not rebuild old native objective/bidding fields
            # from the selected (UI) objective or today's creation serializer.
        else:
            campaign_values, adset_values = (copy.deepcopy(launch["campaign"]), copy.deepcopy(launch["adset"]))
            campaign_values, adset_values, _ = native_bidding(campaign_values, adset_values)
            campaign_values = self.objective_wire.campaign(campaign_values)
            levels = {"campaign": campaign_values, "ad_set": adset_values,
                      "ad": {**copy.deepcopy(launch["ad"]), "creative": creative}}
        publisher = AdsManagerDraftPublisher(sender)
        draft_only = launch.get("execution_mode") == "DRAFT" and not promote
        result = await publisher.run(client, name=launch["campaign"]["name"],
                                     levels=levels, verify_objects=verify_published_objects, draft_only=draft_only)
        if draft_only:
            result.update({"launch_key": key, **target, "preflight": proof,
                "source_revision": SOURCE_REVISION, "execution_mode": "DRAFT",
                "configuration": self.objectives.validate(launch)})
            return result
        result.update({"launch_key": key, **target, "preflight": proof,
            "source_revision": SOURCE_REVISION, "execution_mode": "PUBLISH",
            "steps": [{"step": name, "status": "SUCCESS", "entity_id": result["entities"][field]}
                      for name, field in (("CAMPAIGN", "campaign_id"), ("ADSET", "adset_id"),
                                          ("CREATIVE", "creative_id"), ("AD", "ad_id"))]})
        if historical_recovery:
            # _prepare_live made this sender read-only. Never enter a separate
            # delivery session that could dispatch a new status mutation.
            result.update(recovery_read_only=True, recovered_source_digest=source_digest,
                          delivery={level:"PAUSED" for level in ("campaign", "adset", "ad")})
            return result
        return await self._finish_delivery(profile_id, context, session, payload, target, launch, key, result)

    async def _finish_delivery(self, profile_id, context, session, payload, target, launch, key, result):
        if launch.get("delivery"):
            delivery_key = "ads-delivery:" + hashlib.sha256(key.encode()).hexdigest()
            delivery = await self.run_delivery(profile_id=profile_id, context=context, session=session,
                payload={**payload, **target, "source_launch_key": key, "delivery_key": delivery_key,
                         "status": "ACTIVE", "delivery": launch["delivery"]})
            result.update(delivery=delivery["delivery"], delivery_key=delivery_key,
                          effective_statuses=delivery["effective_statuses"], delivery_running_verified=False)
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
