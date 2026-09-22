from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urljoin
from typing import Any


@dataclass(frozen=True, slots=True)
class PersistedQueryDiscovery:
    doc_id: str
    friendly_name: str
    source_url: str
    source_kind: str


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


def extract_doc_id_near_friendly_name(
    source: str,
    friendly_name: str,
) -> str:
    if not source or not friendly_name or friendly_name not in source:
        return ""

    best: tuple[int, str] | None = None
    start = 0

    while True:
        index = source.find(friendly_name, start)
        if index < 0:
            break

        left = max(0, index - 3000)
        right = min(
            len(source),
            index + len(friendly_name) + 3000,
        )
        window = source[left:right]

        patterns = (
            r'(?:"|\')?(?:doc_id|docID|id)(?:"|\')?\s*[:=]\s*(?:"|\')([0-9]{5,40})(?:"|\')',
            r'params\s*:\s*\{.{0,1500}?id\s*:\s*["\']([0-9]{5,40})["\']',
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

        start = index + len(friendly_name)

    return best[1] if best else ""


async def discover_persisted_query(
    session: Any,
    *,
    friendly_name: str,
    entry_urls: list[str],
    max_scripts_per_entry: int = 18,
    document_max_bytes: int = 2_000_000,
    script_max_bytes: int = 1_500_000,
) -> PersistedQueryDiscovery | None:
    for entry_url in entry_urls:
        try:
            status, document, final_url = await session.fetch_text(
                entry_url,
                max_bytes=document_max_bytes,
            )
        except Exception:
            continue

        if status >= 400:
            continue

        direct = extract_doc_id_near_friendly_name(
            document,
            friendly_name,
        )
        if direct:
            return PersistedQueryDiscovery(
                doc_id=direct,
                friendly_name=friendly_name,
                source_url=final_url,
                source_kind="html",
            )

        script_urls = extract_script_urls(
            document,
            final_url,
        )

        for script_url in script_urls[:max(1, max_scripts_per_entry)]:
            try:
                script_status, body, resolved_url = await session.fetch_text(
                    script_url,
                    max_bytes=script_max_bytes,
                    referer=final_url,
                )
            except Exception:
                continue

            if script_status >= 400 or friendly_name not in body:
                continue

            doc_id = extract_doc_id_near_friendly_name(
                body,
                friendly_name,
            )
            if not doc_id:
                continue

            return PersistedQueryDiscovery(
                doc_id=doc_id,
                friendly_name=friendly_name,
                source_url=resolved_url,
                source_kind="javascript_bundle",
            )

    return None


__all__ = [
    "PersistedQueryDiscovery",
    "discover_persisted_query",
    "extract_doc_id_near_friendly_name",
    "extract_script_urls",
]
