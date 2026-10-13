"""Private image upload through the profile pool and existing media journal."""
from __future__ import annotations
import asyncio
import hashlib
import re
from .private_ads_draft import fail

def _image_receipt(response, filename=None):
    rows = response.get("images") if isinstance(response, dict) else None
    if not isinstance(rows, dict) or len(rows) != 1:
        raise fail("IMAGE_RECEIPT_UNAVAILABLE", "Image upload did not return one exact image")
    key, row = next(iter(rows.items()))
    if filename is not None and str(key).rsplit("/", 1)[-1] != filename:
        raise fail("IMAGE_RECEIPT_UNAVAILABLE", "Image upload receipt belongs to another filename")
    image_hash = row.get("hash") if isinstance(row, dict) else None
    if not isinstance(image_hash, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,160}", image_hash):
        raise fail("IMAGE_RECEIPT_UNAVAILABLE", "Image upload returned no valid image hash")
    return {"image_hash": image_hash}

async def verify_image(client, image_hash):
    if not isinstance(image_hash, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,160}", image_hash):
        raise fail("IMAGE_UNVERIFIED", "Selected image hash is malformed")
    response = await client.get("/act_" + client.context.account_id + "/adimages",
        {"hashes": [image_hash], "fields": ["account_id", "hash"], "limit": 2})
    rows = response.get("data") if isinstance(response, dict) else None
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or rows[0].get("hash") != image_hash or not isinstance(rows[0].get("account_id"), str)
            or rows[0]["account_id"].removeprefix("act_") != client.context.account_id):
        raise fail("IMAGE_UNVERIFIED", "Independent read did not confirm the selected image/account", True)
    return {"verified": True, "image_hash": image_hash, "account_id": client.context.account_id}

async def upload_image(client, submitter, media):
    if media.media_type != "image" or not media.mime_type.startswith("image/"):
        raise fail("MEDIA_FORMAT_UNAVAILABLE", "This upload adapter supports a single image")
    handle = await asyncio.to_thread(media.path.open, "rb")
    try:
        def rehash():
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
                size += len(chunk)
            handle.seek(0)
            return digest.hexdigest(), size
        digest, size = await asyncio.to_thread(rehash)
        if digest != media.sha256 or size != media.size_bytes:
            raise fail("MEDIA_CHANGED", "Media bytes changed before upload")
        result = await submitter.submit(client, operation="IMAGE:" + media.sha256,
            path="/act_" + client.context.account_id + "/adimages", params={},
            files={media.path.name: (media.path.name, handle, media.mime_type)},
            media_digest=media.sha256, receipt=lambda response: _image_receipt(response, media.path.name))
    finally:
        handle.close()
    proof = await verify_image(client, result["image_hash"])
    from .private_ads_publication import AdsManagerDraftPublisher
    await AdsManagerDraftPublisher(submitter)._confirm("IMAGE:" + media.sha256, proof)
    return result["image_hash"]
