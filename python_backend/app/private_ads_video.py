"""Serial Ads Manager video upload on the existing durable submission journal.

Offsets acknowledge bytes only. A finished receipt becomes an advertising
asset only after exact-account membership, encoding and thumbnail reads.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from urllib.parse import urlsplit

from .private_ads_draft import fail, identity
from .private_ads_publication import AdsManagerDraftPublisher


def _offset(value, size):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,12}", str(value)):
        raise fail("VIDEO_OFFSET_INVALID", "Video sender returned an invalid byte offset")
    result = int(value)
    if not 0 <= result <= size:
        raise fail("VIDEO_OFFSET_INVALID", "Video offset is outside the managed file")
    return result


async def verify_video(client, video_id):
    video_id = identity(video_id)
    params, cursors = {"fields": ["id"], "limit": 100}, set()
    member = False
    for _ in range(5):
        response = await client.get("/act_" + client.context.account_id + "/advideos", params)
        rows = response.get("data")
        if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
            raise fail("VIDEO_READ_INCOMPLETE", "Account video list is incomplete", True)
        if any(row.get("id") == video_id for row in rows):
            member = True
            break
        paging = response.get("paging") or {}
        if not isinstance(paging, dict):
            raise fail("VIDEO_READ_INCOMPLETE", "Account video pagination is malformed", True)
        if not paging.get("next"):
            break
        cursor = (paging.get("cursors") or {}).get("after")
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096 or cursor in cursors:
            raise fail("VIDEO_READ_INCOMPLETE", "Account video cursor is incomplete", True)
        cursors.add(cursor)
        params["after"] = cursor
    if not member:
        raise fail("VIDEO_UNVERIFIED", "Video was not confirmed in the exact advertising account", True)
    row = await client.get("/" + video_id, {"fields": ["id", "status", "thumbnails"]})
    status = row.get("status")
    thumbnails = row.get("thumbnails")
    if (row.get("id") != video_id or not isinstance(status, dict) or status.get("video_status") != "ready"
            or not isinstance(thumbnails, dict) or not isinstance(thumbnails.get("data"), list)
            or not thumbnails["data"] or any(not isinstance(item, dict) for item in thumbnails["data"])):
        raise fail("VIDEO_NOT_READY", "Video encoding or its thumbnail is pending", True)
    thumb = next((item for item in thumbnails["data"] if item.get("is_preferred") is True), thumbnails["data"][0])
    uri = thumb.get("uri")
    if not isinstance(uri, str) or len(uri) > 4096:
        raise fail("VIDEO_NOT_READY", "Video has no usable thumbnail", True)
    try:
        parsed = urlsplit(uri)
        valid = parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise fail("VIDEO_NOT_READY", "Video thumbnail URL is malformed", True)
    return {"verified": True, "video_id": video_id, "account_id": client.context.account_id,
            "video_status": "ready", "thumbnail_uri": uri}


async def upload_video(client, submitter, media):
    if media.media_type != "video" or not media.mime_type.startswith("video/"):
        raise fail("MEDIA_FORMAT_UNAVAILABLE", "Video uploader requires managed video bytes")
    handle = await asyncio.to_thread(media.path.open, "rb")
    publisher = AdsManagerDraftPublisher(submitter)
    waterfall = hashlib.sha256((submitter.launch_key + media.sha256).encode()).hexdigest()[:32]
    path = "/act_" + client.context.account_id + "/advideos"

    def phase(row, expected):
        start, end = _offset(row.get("start_offset"), media.size_bytes), _offset(row.get("end_offset"), media.size_bytes)
        if start != expected or end < start or (end == start and start != media.size_bytes):
            raise fail("VIDEO_OFFSET_INVALID", "Video acknowledgement differs from the submitted range")
        return {"start_offset": str(start), "end_offset": str(end)}

    def start_receipt(row):
        return {"upload_session_id": identity(row.get("upload_session_id")),
                "video_id": identity(row.get("video_id")), **phase(row, 0)}

    try:
        def checksum():
            digest, size = hashlib.sha256(), 0
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
                size += len(chunk)
            handle.seek(0)
            return digest.hexdigest(), size
        if await asyncio.to_thread(checksum) != (media.sha256, media.size_bytes):
            raise fail("MEDIA_CHANGED", "Managed video bytes changed before upload")
        start_op = "VIDEO_START:" + media.sha256
        start = await submitter.submit(client, operation=start_op, path=path,
            params={"upload_phase": "start", "file_size": media.size_bytes,
                    "waterfall_id": waterfall, "creator_product": "LEGACY"},
            receipt=start_receipt, media_digest=media.sha256, multipart=True)
        await publisher._confirm(start_op, {"phase_receipt": True, "video_id": start["video_id"]})
        position = start
        for _ in range(1000):
            left, right = int(position["start_offset"]), int(position["end_offset"])
            if left == right == media.size_bytes:
                break
            if not left < right or right - left > 16 * 1024 * 1024:
                raise fail("VIDEO_OFFSET_INVALID", "Video requested an unbounded chunk")
            def read_chunk():
                handle.seek(left)
                return handle.read(right - left)
            raw = await asyncio.to_thread(read_chunk)
            if len(raw) != right - left:
                raise fail("MEDIA_CHANGED", "Managed video changed during transfer")
            operation = "VIDEO_TRANSFER_" + str(left) + ":" + media.sha256
            position = await submitter.submit(client, operation=operation, path=path,
                params={"upload_phase": "transfer", "upload_session_id": start["upload_session_id"],
                        "start_offset": left, "waterfall_id": waterfall},
                files={"video_file_chunk": ("chunk", raw, media.mime_type)},
                receipt=lambda row: phase(row, right), media_digest=hashlib.sha256(raw).hexdigest())
            await publisher._confirm(operation, {"phase_receipt": True, "acknowledged_offset": right})
        else:
            raise fail("VIDEO_UPLOAD_BUDGET", "Video exceeded the serial upload request budget")

        def finish_receipt(row):
            if row.get("video_id") != start["video_id"]:
                raise fail("VIDEO_RECEIPT_UNAVAILABLE", "Finish response did not confirm the uploaded video")
            return {"video_id": start["video_id"]}
        operation = "VIDEO_FINISH:" + media.sha256
        result = await submitter.submit(client, operation=operation, path=path,
            params={"upload_phase": "finish", "upload_session_id": start["upload_session_id"],
                    "video_id": start["video_id"], "target_id": client.context.account_id,
                    "waterfall_id": waterfall, "creator_product": "LEGACY", "source": "ads_interfaces",
                    "upload_source": "ads_interfaces", "has_file_been_replaced": False,
                    "supports_chunking": True, "title": media.path.stem, "thumbnail_type": "generated_default"},
            receipt=finish_receipt, media_digest=media.sha256, multipart=True)
    finally:
        handle.close()
    proof = await verify_video(client, result["video_id"])
    await publisher._confirm(operation, proof)
    return proof
