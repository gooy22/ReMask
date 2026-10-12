"""Image story specifications using the captured Ads Manager field mappings.

Carousel cards preserve the Launch/Media Library order. Automatic reordering
and the generated end card are disabled so independent reads can prove it.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .private_ads_draft import fail


def destination(value):
    if not isinstance(value, str) or not value.strip():
        raise fail("CREATIVE_INVALID", "Advertising destination URL is required")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and not parsed.username and not parsed.password)
        parsed.port  # Reject malformed ports before any upload.
    except ValueError:
        valid = False
    if not valid:
        raise fail("CREATIVE_INVALID", "Advertising destination URL is malformed")


def image_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value):
        raise fail("IMAGE_UNVERIFIED", "Selected image hash is malformed")


def _optional_text(row):
    for field in ("headline", "description", "call_to_action"):
        value = row.get(field)
        if value is not None and not isinstance(value, str):
            raise fail("CREATIVE_INVALID", "Creative text and call-to-action fields must be strings")
    if row.get("call_to_action") and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", row["call_to_action"].upper()):
        raise fail("CREATIVE_INVALID", "Advertising call-to-action type is malformed")


def validate_creative(creative, references):
    kind = creative.get("format", "SINGLE")
    if kind not in {"SINGLE", "CAROUSEL", "INSTAGRAM_POST"} or creative.get("existing_creative_id"):
        raise fail("CREATIVE_FORMAT_UNAVAILABLE", "Select a supported private advertising creative")
    if kind == "INSTAGRAM_POST":
        from .private_ads_draft import identity
        identity(creative.get("source_instagram_media_id"))
        if creative.get("instagram_actor_id"):
            identity(creative["instagram_actor_id"])
        if (references or any(creative.get(key) for key in ("existing_image_hash", "existing_video_id", "carousel_cards"))
                or not isinstance(creative.get("name"), str) or not creative["name"].strip()):
            raise fail("CREATIVE_INVALID", "Instagram post requires its name and exact existing post without competing media")
        return
    if creative.get("source_instagram_media_id") or creative.get("instagram_actor_id"):
        raise fail("CREATIVE_FORMAT_UNAVAILABLE", "Instagram actor/post requires INSTAGRAM_POST format")
    for field in ("name", "message", "link"):
        if not isinstance(creative.get(field), str) or not creative[field].strip():
            raise fail("CREATIVE_INVALID", "Creative name, text and destination URL are required")
    destination(creative["link"])
    _optional_text(creative)
    if creative.get("url_tags") is not None and not isinstance(creative["url_tags"], str):
        raise fail("CREATIVE_INVALID", "Advertising URL tags must be a string")
    if kind == "SINGLE":
        if references and (creative.get("existing_image_hash") or creative.get("existing_video_id")):
            raise fail("CREATIVE_INVALID", "Local media and existing assets cannot compete")
        if len(references) > 1 or creative.get("carousel_cards"):
            raise fail("CREATIVE_FORMAT_UNAVAILABLE", "A single image requires exactly one media item")
        if not references:
            if bool(creative.get("existing_image_hash")) == bool(creative.get("existing_video_id")):
                raise fail("MEDIA_REQUIRED", "Select exactly one existing image or video")
            if creative.get("existing_image_hash"):
                image_hash(creative["existing_image_hash"])
            else:
                from .private_ads_draft import identity
                identity(creative["existing_video_id"])
        return
    if creative.get("existing_image_hash") or creative.get("existing_video_id") or creative.get("media_library_id"):
        raise fail("CREATIVE_INVALID", "Select ordered carousel media instead of a single image")
    cards = creative.get("carousel_cards")
    if not isinstance(cards, list) or not 2 <= len(cards) <= 10:
        raise fail("CREATIVE_INVALID", "An image carousel requires between two and ten cards")
    if references and len(references) != len(cards):
        raise fail("CREATIVE_INVALID", "Each carousel card requires its ordered media reference")
    allowed = {"headline", "description", "link", "call_to_action", "image_hash", "video_id", "media_library_id"}
    for index, card in enumerate(cards):
        if not isinstance(card, dict) or set(card) - allowed:
            raise fail("CREATIVE_FIELDS_UNAVAILABLE", "Selected carousel card options require their private adapter")
        if references and (card.get("video_id") or card.get("image_hash")):
            raise fail("CREATIVE_INVALID", "Local carousel references cannot compete with existing assets")
        if card.get("video_id"):
            from .private_ads_draft import identity
            identity(card["video_id"])
        _optional_text(card)
        if card.get("link") is not None and not isinstance(card["link"], str):
            raise fail("CREATIVE_INVALID", "Carousel destinations must be strings")
        destination(card.get("link") or creative["link"])
        if card.get("media_library_id") and (not references or card["media_library_id"] != references[index]["id"]):
            raise fail("CREATIVE_INVALID", "Carousel card and ordered Media Library reference differ")
        if not references:
            if bool(card.get("image_hash")) == bool(card.get("video_id")):
                raise fail("CREATIVE_INVALID", "Each carousel card requires exactly one image or video")
            if card.get("image_hash"):
                image_hash(card["image_hash"])


def _link_data(creative):
    link = {"link": creative["link"], "message": creative["message"],
            "call_to_action": {"type": str(creative.get("call_to_action") or "LEARN_MORE").upper()}}
    if creative.get("headline"):
        link["name"] = creative["headline"]
    if creative.get("description"):
        link["description"] = creative["description"]
    return link


def _story(creative, page_id, link):
    result = {"name": creative["name"], "object_story_spec": {"page_id": page_id, "link_data": link}}
    if creative.get("url_tags"):
        result["url_tags"] = creative["url_tags"]
    return result


def image_creative(creative, page_id, selected_hash):
    return _story(creative, page_id, {**_link_data(creative), "image_hash": selected_hash})


def carousel_creative(creative, page_id, hashes):
    cards = creative["carousel_cards"]
    if len(hashes) != len(cards):
        raise fail("CREATIVE_INVALID", "Carousel media resolution did not preserve every card")
    attachments = []
    for card, selected_hash in zip(cards, hashes):
        row = {"link": card.get("link") or creative["link"], "image_hash": selected_hash,
               "call_to_action": {"type": str(card.get("call_to_action") or
                     creative.get("call_to_action") or "LEARN_MORE").upper()}}
        if card.get("headline"):
            row["name"] = card["headline"]
        if card.get("description"):
            row["description"] = card["description"]
        attachments.append(row)
    return _story(creative, page_id, {**_link_data(creative), "child_attachments": attachments,
        "multi_share_optimized": False, "multi_share_end_card": False})


def video_creative(creative, page_id, media):
    video = {"video_id": media["video_id"], "image_url": media["thumbnail_uri"],
             "message": creative["message"],
             "call_to_action": {"type": str(creative.get("call_to_action") or "LEARN_MORE").upper(),
                                "value": {"link": creative["link"]}}}
    if creative.get("headline"):
        video["title"] = creative["headline"]
    if creative.get("description"):
        video["link_description"] = creative["description"]
    result = {"name": creative["name"], "object_story_spec": {"page_id": page_id, "video_data": video}}
    if creative.get("url_tags"):
        result["url_tags"] = creative["url_tags"]
    return result


def resolved_creative(creative, page_id, media, instagram=None):
    """Use account-confirmed assets; preserve source-defined carousel ordering."""
    kind = creative.get("format", "SINGLE")
    if kind == "INSTAGRAM_POST":
        if not isinstance(instagram, dict) or instagram.get("id") != creative["source_instagram_media_id"]:
            raise fail("INSTAGRAM_UNVERIFIED", "Existing Instagram post is not confirmed for this account")
        result = {"name": creative["name"], "object_id": page_id,
                  "source_instagram_media_id": instagram["id"]}
        if isinstance(instagram.get("caption"), str):
            result["body"] = instagram["caption"]
        if creative.get("instagram_actor_id"):
            result["instagram_actor_id"] = creative["instagram_actor_id"]
        if creative.get("url_tags"):
            result["url_tags"] = creative["url_tags"]
        return result
    if kind == "SINGLE":
        if references and (creative.get("existing_image_hash") or creative.get("existing_video_id")):
            raise fail("CREATIVE_INVALID", "Local media and existing assets cannot compete")
        if len(media) != 1:
            raise fail("CREATIVE_INVALID", "Single creative requires one confirmed media item")
        return (video_creative(creative, page_id, media[0]) if media[0].get("video_id")
                else image_creative(creative, page_id, media[0]["image_hash"]))
    if len(media) != len(creative["carousel_cards"]):
        raise fail("CREATIVE_INVALID", "Carousel media order is incomplete")
    result = carousel_creative(creative, page_id, [row.get("image_hash", "planned-video") for row in media])
    for attachment, row in zip(result["object_story_spec"]["link_data"]["child_attachments"], media):
        if row.get("video_id"):
            attachment.pop("image_hash")
            attachment.update(video_id=row["video_id"], picture=row["thumbnail_uri"])
    return result
