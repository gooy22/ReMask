"""Resolve reusable media inside ReMask; never download client-supplied URLs."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .provisioning.models import ProvisioningError


@dataclass(frozen=True, slots=True)
class LocalMedia:
    id: str
    sha256: str
    size_bytes: int
    media_type: str
    mime_type: str
    path: Path

    def public(self):
        return {"id": self.id, "sha256": self.sha256, "size_bytes": self.size_bytes,
                "media_type": self.media_type, "mime_type": self.mime_type, "filename": self.path.name}


def invalid(message):
    return ProvisioningError("PRIVATE_LAUNCH_MEDIA_INVALID", message, retryable=False)


class LaunchMediaResolver:
    """Use the existing PHP store factory, including its PostgreSQL materializer."""
    def __init__(self):
        source = urlsplit(os.getenv("REMASK_PROFILE_RESOLVER_URL") or
                          "http://127.0.0.1:80/ajax/pythonProfileContext.php")
        # The worker and media volume are colocated. No authenticated request
        # or arbitrary filesystem path may be routed by a browser payload.
        if (source.scheme != "http" or source.hostname not in {"127.0.0.1", "localhost", "::1"}
                or source.username or source.password):
            raise invalid("Media resolution requires the local ReMask bridge")
        self.url = urlunsplit((source.scheme, source.netloc,
                             "/ajax/pythonWorkerJobs.php", "", ""))
        self.key = os.getenv("REMASK_INTERNAL_KEY") or ""
        self.root = Path(os.getenv("REMASK_MEDIA_LIBRARY_DIR") or
                         str(Path(os.getenv("REMASK_DATA_DIR") or "/var/lib/remask") / "media-library"))
        self.max_bytes = 157_286_400

    async def resolve(self, references):
        if not self.key:
            raise invalid("Internal media bridge authentication is unavailable")
        if not isinstance(references, list) or not 1 <= len(references) <= 10:
            raise invalid("Select between one and ten local media items")
        ids = []
        for row in references:
            if not isinstance(row, dict) or not re.fullmatch(r"m-[a-f0-9]{24}", str(row.get("id", ""))):
                raise invalid("Invalid Media Library reference")
            ids.append(row["id"])
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), trust_env=False) as client:
                async with client.post(self.url, headers={"X-Remask-Internal-Key": self.key},
                                       json={"action": "private_launch_media_resolve", "ids": list(dict.fromkeys(ids))},
                                       allow_redirects=False) as response:
                    if response.status != 200:
                        raise invalid("Media Library resolution did not complete")
                    payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise ProvisioningError("PRIVATE_LAUNCH_MEDIA_UNAVAILABLE",
                                   "Media Library is temporarily unavailable", retryable=True) from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("media"), list):
            raise invalid("Media Library returned an invalid result")
        if any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
               or not re.fullmatch(r"m-[a-f0-9]{24}", row["id"]) for row in payload["media"]):
            raise invalid("Media Library returned an invalid reference")
        rows = {row["id"]: row for row in payload["media"]}
        if set(rows) != set(ids) or len(rows) != len(payload["media"]):
            raise invalid("Media Library returned another media selection")
        output = []
        for reference in references:
            try:
                item = await asyncio.to_thread(self._validate, rows[reference["id"]])
            except (OSError, ValueError) as exc:
                raise invalid("The saved media file is unavailable") from exc
            if reference.get("sha256") and reference["sha256"] != item.sha256:
                raise invalid("Saved media checksum changed")
            output.append(item)
        return output

    def _validate(self, row):
        path = Path(str(row.get("path") or "")).resolve(strict=True)
        root = self.root.resolve(strict=True)
        if (path.parent not in {root / "files", root / "pg-cache"}
                or not path.is_file() or path.name.split(".")[0] != row.get("id")):
            raise invalid("Media file is outside the managed library")
        size = path.stat().st_size
        if not 0 < size <= self.max_bytes or size != row.get("size_bytes"):
            raise invalid("Media size does not match the saved library record")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
        kind, mime = row.get("media_type"), row.get("mime_type")
        if (not 0 < size <= self.max_bytes or size != row.get("size_bytes") or sha != row.get("sha256")
                or row.get("id") != "m-" + sha[:24] or kind not in {"image", "video"}
                or not isinstance(mime, str) or not re.fullmatch(r"(?:image|video)/[a-zA-Z0-9.+-]+", mime)
                or not mime.startswith(kind + "/")):
            raise invalid("Media bytes, type or checksum do not match the saved library record")
        return LocalMedia(row["id"], sha, size, kind, mime, path)


def local_references(creative):
    """Preserve carousel order and reject competing local media sources."""
    sources = [key for key in ("staged_media", "media_library_id", "carousel_media_library_ids") if creative.get(key)]
    if len(sources) > 1:
        raise invalid("Select one media source for this creative")
    if not sources:
        return []
    source = sources[0]
    if source == "staged_media":
        values = creative[source]
        if not isinstance(values, list):
            raise invalid("Staged media must be a list")
        if any(not isinstance(row, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("sha256", ""))) for row in values):
            raise invalid("Staged media requires its saved checksum")
        references = [{"id": row.get("id"), "sha256": row["sha256"]} for row in values]
    elif source == "media_library_id":
        references = [{"id": creative[source]}]
    else:
        values = creative[source]
        if not isinstance(values, list):
            raise invalid("Carousel media must be an ordered list")
        references = [{"id": value} for value in values]
    if not 1 <= len(references) <= 10 or any(not re.fullmatch(r"m-[a-f0-9]{24}", str(row["id"])) for row in references):
        raise invalid("Invalid Media Library selection")
    return references


def creative_with_media(creative, media):
    """Produce sender input, not a guessed Meta variables envelope."""
    output = {key: value for key, value in creative.items() if key not in
              {"staged_media", "media_library_id", "carousel_media_library_ids"}}
    format_name = output.get("format", "SINGLE")
    if format_name == "CAROUSEL":
        cards = output.get("carousel_cards")
        if not isinstance(cards, list) or not 2 <= len(cards) <= 10:
            raise invalid("Carousel requires between two and ten cards")
        if media and len(media) != len(cards):
            raise invalid("Each carousel card requires its own media reference")
        output["carousel_cards"] = []
        for index, card in enumerate(cards):
            if not isinstance(card, dict):
                raise invalid("Carousel card must be an object")
            clean = {key: value for key, value in card.items() if key != "media_library_id"}
            if media:
                clean.pop("image_hash", None); clean.pop("video_id", None)
                clean.update(media[index])
            if bool(clean.get("image_hash")) == bool(clean.get("video_id")):
                raise invalid("Each carousel card requires exactly one image or video")
            output["carousel_cards"].append(clean)
    elif media:
        if format_name != "SINGLE" or len(media) != 1:
            raise invalid("Single creative requires exactly one local media item")
        for key in ("existing_image_hash", "existing_video_id", "existing_creative_id", "source_instagram_media_id"):
            output.pop(key, None)
        for key, value in media[0].items():
            output[{"image_hash": "existing_image_hash", "video_id": "existing_video_id"}[key]] = value
    return output
