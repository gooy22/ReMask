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
    if kind not in {"SINGLE", "CAROUSEL"} or any(creative.get(key) for key in
            ("existing_video_id", "existing_creative_id", "source_instagram_media_id", "instagram_actor_id")):
        raise fail("CREATIVE_FORMAT_UNAVAILABLE", "This private draft adapter supports images and image carousels")
    for field in ("name", "message", "link"):
        if not isinstance(creative.get(field), str) or not creative[field].strip():
            raise fail("CREATIVE_INVALID", "Creative name, text and destination URL are required")
    destination(creative["link"])
    _optional_text(creative)
    if creative.get("url_tags") is not None and not isinstance(creative["url_tags"], str):
        raise fail("CREATIVE_INVALID", "Advertising URL tags must be a string")
    if kind == "SINGLE":
        if len(references) > 1 or creative.get("carousel_cards"):
            raise fail("CREATIVE_FORMAT_UNAVAILABLE", "A single image requires exactly one media item")
        if not references:
            if not creative.get("existing_image_hash"):
                raise fail("MEDIA_REQUIRED", "Select an existing image or local Media Library image")
            image_hash(creative["existing_image_hash"])
        return
    if creative.get("existing_image_hash") or creative.get("media_library_id"):
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
        if card.get("video_id"):
            raise fail("CREATIVE_FORMAT_UNAVAILABLE", "Video carousel cards are not enabled")
        _optional_text(card)
        if card.get("link") is not None and not isinstance(card["link"], str):
            raise fail("CREATIVE_INVALID", "Carousel destinations must be strings")
        destination(card.get("link") or creative["link"])
        if card.get("media_library_id") and (not references or card["media_library_id"] != references[index]["id"]):
            raise fail("CREATIVE_INVALID", "Carousel card and ordered Media Library reference differ")
        if not references:
            image_hash(card.get("image_hash"))


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
