from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin
from typing import Any


@dataclass(frozen=True, slots=True)
class PersistedQueryDiscovery:
    doc_id: str
    friendly_name: str
    source_url: str
    source_kind: str


_DISCOVERY_LOCKS: dict[str, asyncio.Lock] = {}
_DISCOVERY_CACHE: dict[str, tuple[float, PersistedQueryDiscovery]] = {}


def _discovery_lock(key: str) -> asyncio.Lock:
    current = _DISCOVERY_LOCKS.get(key)
    if current is None:
        current = asyncio.Lock()
        _DISCOVERY_LOCKS[key] = current
    return current


def extract_script_urls(document: str, base_url: str) -> list[str]:
    normalized = html.unescape(document or "").replace("\\/", "/")
    found: list[str] = []
    seen: set[str] = set()

    patterns = (
        r'<script[^>]+src=["\']([^"\']+\.js[^"\']*)["\']',
        r'["\'](https://[^"\']+\.js[^"\']*)["\']',
    )

    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            raw = html.unescape(match.group(1)).replace("\\/", "/")
            url = urljoin(base_url, raw)
            if url in seen:
                continue
            if not (
                "facebook.com" in url
                or "fbcdn.net" in url
            ):
                continue
            seen.add(url)
            found.append(url)

    return found


def _source_variants(source: str) -> list[str]:
    raw = str(source or "")
    variants = [raw]

    entity_decoded = html.unescape(raw)
    if entity_decoded not in variants:
        variants.append(entity_decoded)

    def decode_ascii_unicode(match: re.Match[str]) -> str:
        value = int(match.group(1), 16)
        return chr(value) if value <= 0x7F else match.group(0)

    decoded = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        decode_ascii_unicode,
        entity_decoded,
    )
    decoded = re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda match: chr(int(match.group(1), 16)),
        decoded,
    )
    decoded = (
        decoded
        .replace(r"\\/", "/")
        .replace(r'\\"', '"')
        .replace(r"\\'", "'")
    )
    if decoded not in variants:
        variants.append(decoded)

    return variants


def extract_doc_id_near_friendly_name(
    source: str,
    friendly_name: str,
) -> str:
    clean_name = str(friendly_name or "").strip()
    if not source or not clean_name:
        return ""

    aliases = (
        clean_name,
        f"{clean_name}_facebookRelayOperation",
    )
    best: tuple[int, str] | None = None

    for candidate_source in _source_variants(source):
        for alias in aliases:
            start = 0
            while True:
                index = candidate_source.find(alias, start)
                if index < 0:
                    break

                left = max(0, index - 5000)
                right = min(
                    len(candidate_source),
                    index + len(alias) + 5000,
                )
                window = candidate_source[left:right]

                patterns = (
                    r'(?:"|\')?(?:doc_id|docID|queryID|query_id|id)'
                    r'(?:"|\')?\s*[:=]\s*(?:"|\')([0-9]{5,40})(?:"|\')',
                    r'params\s*:\s*\{.{0,2500}?id\s*:\s*["\']([0-9]{5,40})["\']',
                    r'["\'](?:doc_id|id)["\']\s*,\s*["\']([0-9]{5,40})["\']',
                )

                for pattern in patterns:
                    for match in re.finditer(
                        pattern,
                        window,
                        flags=re.IGNORECASE | re.DOTALL,
                    ):
                        value = match.group(1)
                        absolute = left + match.start(1)
                        distance = abs(absolute - index)
                        if best is None or distance < best[0]:
                            best = (distance, value)

                start = index + len(alias)

    return best[1] if best else ""


async def discover_persisted_query(
    session: Any,
    *,
    friendly_name: str,
    entry_urls: list[str],
    max_scripts_per_entry: int = 0,
    document_max_bytes: int = 3_000_000,
    script_max_bytes: int = 0,
    cache_ttl_seconds: int = 600,
) -> PersistedQueryDiscovery | None:
    """
    v14 lightweight persisted-query discovery.

    Only the initial Facebook HTML response and its response headers are
    inspected. No JavaScript bundle URLs are fetched or parsed. The legacy
    max_scripts/script_max_bytes arguments remain for call-site compatibility
    but are intentionally ignored.
    """
    clean_name = str(friendly_name or "").strip()
    if not clean_name:
        return None

    cache_key = clean_name
    now = time.monotonic()
    cached = _DISCOVERY_CACHE.get(cache_key)
    if cached and now - cached[0] <= max(30, int(cache_ttl_seconds)):
        return cached[1]

    async with _discovery_lock(cache_key):
        now = time.monotonic()
        cached = _DISCOVERY_CACHE.get(cache_key)
        if cached and now - cached[0] <= max(30, int(cache_ttl_seconds)):
            return cached[1]

        for entry_url in entry_urls:
            try:
                if hasattr(session, "fetch_text_with_headers"):
                    status, document, final_url, headers = (
                        await session.fetch_text_with_headers(
                            entry_url,
                            max_bytes=document_max_bytes,
                        )
                    )
                else:
                    status, document, final_url = await session.fetch_text(
                        entry_url,
                        max_bytes=document_max_bytes,
                    )
                    headers = {}
            except Exception:
                continue

            if status >= 400:
                continue

            direct = extract_doc_id_near_friendly_name(
                document,
                clean_name,
            )
            if direct:
                result = PersistedQueryDiscovery(
                    doc_id=direct,
                    friendly_name=clean_name,
                    source_url=final_url,
                    source_kind="html",
                )
                _DISCOVERY_CACHE[cache_key] = (time.monotonic(), result)
                return result

            header_blob = "\n".join(
                f"{key}: {value}"
                for key, value in dict(headers or {}).items()
            )
            header_doc_id = extract_doc_id_near_friendly_name(
                header_blob,
                clean_name,
            )
            if header_doc_id:
                result = PersistedQueryDiscovery(
                    doc_id=header_doc_id,
                    friendly_name=clean_name,
                    source_url=final_url,
                    source_kind="response_headers",
                )
                _DISCOVERY_CACHE[cache_key] = (time.monotonic(), result)
                return result

        return None


__all__ = [
    "PersistedQueryDiscovery",
    "discover_persisted_query",
    "extract_doc_id_near_friendly_name",
    "extract_script_urls",
]
