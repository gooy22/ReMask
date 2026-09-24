# python_backend/app/facebook_query_discovery.py

from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin


@dataclass(
    frozen=True,
    slots=True,
)
class PersistedQueryDiscovery:
    doc_id: str
    friendly_name: str
    source_url: str
    source_kind: str


_DISCOVERY_LOCKS: dict[
    str,
    asyncio.Lock,
] = {}

_DISCOVERY_CACHE: dict[
    str,
    tuple[
        float,
        PersistedQueryDiscovery,
    ],
] = {}


def _discovery_lock(
    key: str,
) -> asyncio.Lock:
    current = _DISCOVERY_LOCKS.get(
        key
    )

    if current is None:
        current = asyncio.Lock()
        _DISCOVERY_LOCKS[
            key
        ] = current

    return current


def _decode_ascii_unicode(
    match: re.Match[str],
) -> str:
    value = int(
        match.group(
            1
        ),
        16,
    )

    if (
        0 <= value <= 0x10FFFF
    ):
        try:
            return chr(
                value
            )
        except ValueError:
            pass

    return match.group(
        0
    )


def source_variants(
    source: str,
) -> list[str]:
    raw = str(
        source
        or ""
    )

    variants: list[
        str
    ] = []

    def append_unique(
        value: str,
    ) -> None:
        if (
            value
            and value not in variants
        ):
            variants.append(
                value
            )

    append_unique(
        raw
    )

    entity_decoded = html.unescape(
        raw
    )

    append_unique(
        entity_decoded
    )

    unicode_decoded = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        _decode_ascii_unicode,
        entity_decoded,
    )

    unicode_decoded = re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda match: chr(
            int(
                match.group(
                    1
                ),
                16,
            )
        ),
        unicode_decoded,
    )

    unicode_decoded = (
        unicode_decoded
        .replace(
            r"\/",
            "/",
        )
        .replace(
            r"\\/",
            "/",
        )
        .replace(
            r"\"",
            '"',
        )
        .replace(
            r'\\"',
            '"',
        )
        .replace(
            r"\'",
            "'",
        )
    )

    append_unique(
        unicode_decoded
    )

    twice_decoded = html.unescape(
        unicode_decoded
    )

    append_unique(
        twice_decoded
    )

    return variants


def _valid_dtsg_token(
    token: str,
) -> bool:
    value = str(
        token
        or ""
    ).strip()

    if not (
        8
        <= len(
            value
        )
        <= 512
    ):
        return False

    if re.search(
        r"[\s<>]",
        value,
    ):
        return False

    if value.lower() in {
        "null",
        "none",
        "undefined",
        "false",
        "true",
    }:
        return False

    return True


def _dtsg_context_valid(
    window: str,
) -> bool:
    lower = str(
        window
        or ""
    ).lower()

    direct_markers = (
        "dtsginitialdata",
        "dtsginitdata",
        "fb_dtsg",
    )

    relay_markers = (
        "__bbox",
        "relayprefetchedstreamcache",
        "requirelazy",
        "relay",
    )

    has_dtsg = any(
        marker in lower
        for marker in direct_markers
    )

    has_relay = any(
        marker in lower
        for marker in relay_markers
    )

    if (
        "dtsginitialdata"
        in lower
        or "dtsginitdata"
        in lower
    ):
        return True

    if (
        "fb_dtsg"
        in lower
        and has_relay
    ):
        return True

    return (
        has_dtsg
        and has_relay
    )


def extract_csrf_token(
    document: str,
) -> str:
    best: tuple[
        int,
        str,
    ] | None = None

    anchor_patterns = (
        r"DTSGInitialData",
        r"DTSGInitData",
        r"fb_dtsg",
        r"RelayPrefetchedStreamCache",
        r"requireLazy",
        r"__bbox",
    )

    token_patterns = (
        (
            120,
            r'["\']token["\']\s*:\s*["\']([^"\']+)["\']',
        ),
        (
            140,
            r'["\']fb_dtsg["\']\s*:\s*["\']([^"\']+)["\']',
        ),
        (
            160,
            r'["\']name["\']\s*:\s*["\']fb_dtsg["\']'
            r'.{0,800}?'
            r'["\']value["\']\s*:\s*["\']([^"\']+)["\']',
        ),
        (
            180,
            r'name=["\']fb_dtsg["\']'
            r'[^>]{0,1000}?'
            r'value=["\']([^"\']+)["\']',
        ),
    )

    for source in source_variants(
        document
    ):
        anchors: list[
            int
        ] = []

        for pattern in anchor_patterns:
            for match in re.finditer(
                pattern,
                source,
                flags=re.IGNORECASE,
            ):
                anchors.append(
                    match.start()
                )

        if not anchors:
            continue

        for anchor in anchors:
            left = max(
                0,
                anchor - 5000,
            )

            right = min(
                len(
                    source
                ),
                anchor + 7000,
            )

            window = source[
                left:right
            ]

            if not _dtsg_context_valid(
                window
            ):
                continue

            for (
                base_score,
                pattern,
            ) in token_patterns:
                for match in re.finditer(
                    pattern,
                    window,
                    flags=(
                        re.IGNORECASE
                        | re.DOTALL
                    ),
                ):
                    token = html.unescape(
                        str(
                            match.group(
                                1
                            )
                            or ""
                        )
                    ).strip()

                    if not _valid_dtsg_token(
                        token
                    ):
                        continue

                    absolute = (
                        left
                        + match.start(
                            1
                        )
                    )

                    distance = abs(
                        absolute
                        - anchor
                    )

                    score = (
                        base_score
                        + distance
                    )

                    lower_window = (
                        window.lower()
                    )

                    if (
                        "dtsginitialdata"
                        in lower_window
                    ):
                        score -= 80

                    if (
                        "dtsginitdata"
                        in lower_window
                    ):
                        score -= 70

                    if (
                        "relayprefetchedstreamcache"
                        in lower_window
                    ):
                        score -= 30

                    if (
                        "requirelazy"
                        in lower_window
                    ):
                        score -= 20

                    if (
                        "__bbox"
                        in lower_window
                    ):
                        score -= 20

                    candidate = (
                        score,
                        token,
                    )

                    if (
                        best is None
                        or candidate[0]
                        < best[0]
                    ):
                        best = candidate

    return (
        best[1]
        if best is not None
        else ""
    )


_RELAY_CONTEXT_MARKERS = (
    "RelayPrefetchedStreamCache",
    "__bbox",
    "requireLazy",
)


def _balanced_segment(
    source: str,
    start: int,
    *,
    max_chars: int = 250_000,
) -> str:
    if start < 0 or start >= len(source):
        return ""

    opener = source[start]
    closer = {
        "{": "}",
        "[": "]",
        "(": ")",
    }.get(opener)

    if closer is None:
        return ""

    stack = [opener]
    quote = ""
    escaped = False
    limit = min(len(source), start + max_chars)

    for index in range(start + 1, limit):
        char = source[index]

        if quote:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = ""
            continue

        if char in {'"', "'"}:
            quote = char
            continue

        if char in "{[(":
            stack.append(char)
            continue

        if char in "}])":
            expected = {
                "}": "{",
                "]": "[",
                ")": "(",
            }[char]

            if not stack or stack[-1] != expected:
                return ""

            stack.pop()

            if not stack:
                return source[start:index + 1]

    return ""


def _relay_context_blocks(source: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for marker in _RELAY_CONTEXT_MARKERS:
        start = 0

        while True:
            index = source.find(marker, start)
            if index < 0:
                break

            candidate_starts: list[int] = []

            backward_floor = max(0, index - 12_000)
            for opener in ("{", "[", "("):
                pos = source.rfind(opener, backward_floor, index + 1)
                if pos >= 0:
                    candidate_starts.append(pos)

            forward_ceiling = min(len(source), index + 2_000)
            for opener in ("{", "[", "("):
                pos = source.find(opener, index, forward_ceiling)
                if pos >= 0:
                    candidate_starts.append(pos)

            for block_start in sorted(
                set(candidate_starts),
                key=lambda value: abs(value - index),
            ):
                block = _balanced_segment(source, block_start)
                if not block:
                    continue
                if marker not in block:
                    continue
                if len(block) > 250_000:
                    continue

                identity = (marker, block)
                if identity in seen:
                    continue

                seen.add(identity)
                blocks.append(identity)

            start = index + len(marker)

    return blocks


def _enclosing_object(
    source: str,
    position: int,
    *,
    max_backtrack: int = 24_000,
) -> str:
    floor = max(0, position - max_backtrack)

    openings = [
        index
        for index in range(position, floor - 1, -1)
        if source[index] == "{"
    ]

    for start in openings:
        block = _balanced_segment(
            source,
            start,
            max_chars=120_000,
        )
        if not block:
            continue

        end = start + len(block)

        if start <= position < end:
            return block

    return ""


def _doc_id_matches(source: str) -> list[tuple[int, str, int]]:
    patterns = (
        (
            0,
            r'["\'](?:doc_id|docID|queryID|query_id)["\']\s*:\s*["\']([0-9]{5,40})["\']',
        ),
        (
            10,
            r'(?<![A-Za-z0-9_])(?:doc_id|docID|queryID|query_id)\s*[:=]\s*["\']([0-9]{5,40})["\']',
        ),
        (
            20,
            r'["\']params["\']\s*:\s*\{.{0,2500}?["\']id["\']\s*:\s*["\']([0-9]{5,40})["\']',
        ),
        (
            40,
            r'["\']id["\']\s*:\s*["\']([0-9]{5,40})["\']',
        ),
    )

    matches: list[tuple[int, str, int]] = []

    for penalty, pattern in patterns:
        for match in re.finditer(
            pattern,
            source,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            value = str(match.group(1) or "").strip()

            if not re.fullmatch(r"\d{5,40}", value):
                continue

            matches.append(
                (
                    match.start(1),
                    value,
                    penalty,
                )
            )

    return matches


def extract_doc_id_from_relay_context(
    source: str,
    friendly_name: str,
) -> str:
    clean_name = str(friendly_name or "").strip()

    if not source or not clean_name:
        return ""

    aliases = (
        clean_name,
        clean_name + "_facebookRelayOperation",
    )

    best: tuple[int, str] | None = None

    for variant in source_variants(source):
        for marker, block in _relay_context_blocks(variant):
            lower_block = block.lower()

            if clean_name.lower() not in lower_block:
                continue

            for alias in aliases:
                alias_start = 0

                while True:
                    alias_index = block.find(alias, alias_start)
                    if alias_index < 0:
                        break

                    same_object = _enclosing_object(
                        block,
                        alias_index,
                    )

                    candidate_sources: list[tuple[str, int]] = []

                    if same_object and alias in same_object:
                        candidate_sources.append(
                            (
                                same_object,
                                100_000,
                            )
                        )

                    candidate_sources.append(
                        (
                            block,
                            20_000,
                        )
                    )

                    for candidate_source, base_score in candidate_sources:
                        for doc_pos, doc_id, pattern_penalty in _doc_id_matches(
                            candidate_source
                        ):
                            alias_pos = candidate_source.find(alias)
                            if alias_pos < 0:
                                continue

                            distance = abs(doc_pos - alias_pos)

                            score = (
                                base_score
                                - min(distance, 15_000)
                                - pattern_penalty
                            )

                            if alias.endswith(
                                "_facebookRelayOperation"
                            ):
                                score += 5_000

                            if marker == "RelayPrefetchedStreamCache":
                                score += 500
                            elif marker == "__bbox":
                                score += 350
                            elif marker == "requireLazy":
                                score += 250

                            candidate = (
                                score,
                                doc_id,
                            )

                            if (
                                best is None
                                or candidate[0] > best[0]
                            ):
                                best = candidate

                    alias_start = (
                        alias_index
                        + len(alias)
                    )

    return best[1] if best is not None else ""


def extract_doc_id_near_friendly_name(
    source: str,
    friendly_name: str,
) -> str:
    clean_name = str(
        friendly_name
        or ""
    ).strip()

    if (
        not source
        or not clean_name
    ):
        return ""

    aliases = (
        clean_name,
        (
            clean_name
            + "_facebookRelayOperation"
        ),
    )

    best: tuple[
        int,
        str,
    ] | None = None

    patterns = (
        r'(?:"|\')?(?:doc_id|docID|queryID|query_id|id)'
        r'(?:"|\')?\s*[:=]\s*(?:"|\')([0-9]{5,40})(?:"|\')',
        r'params\s*:\s*\{.{0,2500}?'
        r'id\s*:\s*["\']([0-9]{5,40})["\']',
        r'["\'](?:doc_id|id)["\']'
        r'\s*,\s*["\']([0-9]{5,40})["\']',
    )

    for candidate_source in source_variants(
        source
    ):
        for alias in aliases:
            start = 0

            while True:
                index = candidate_source.find(
                    alias,
                    start,
                )

                if index < 0:
                    break

                left = max(
                    0,
                    index - 5000,
                )

                right = min(
                    len(
                        candidate_source
                    ),
                    index
                    + len(
                        alias
                    )
                    + 5000,
                )

                window = candidate_source[
                    left:right
                ]

                for pattern in patterns:
                    for match in re.finditer(
                        pattern,
                        window,
                        flags=(
                            re.IGNORECASE
                            | re.DOTALL
                        ),
                    ):
                        value = str(
                            match.group(
                                1
                            )
                            or ""
                        ).strip()

                        if not re.fullmatch(
                            r"\d{5,40}",
                            value,
                        ):
                            continue

                        absolute = (
                            left
                            + match.start(
                                1
                            )
                        )

                        distance = abs(
                            absolute
                            - index
                        )

                        score = distance

                        if (
                            alias.endswith(
                                "_facebookRelayOperation"
                            )
                        ):
                            score -= 1000

                        candidate = (
                            score,
                            value,
                        )

                        if (
                            best is None
                            or candidate[0]
                            < best[0]
                        ):
                            best = candidate

                start = (
                    index
                    + len(
                        alias
                    )
                )

    return (
        best[1]
        if best is not None
        else ""
    )


async def discover_persisted_query(
    session: Any,
    *,
    friendly_name: str,
    entry_urls: list[str],
    max_scripts_per_entry: int = 0,
    document_max_bytes: int = 3_000_000,
    script_max_bytes: int = 0,
    cache_ttl_seconds: int = 0,
) -> PersistedQueryDiscovery | None:
    max_scripts = max(0, int(max_scripts_per_entry or 0))
    max_script_bytes = max(0, int(script_max_bytes or 0))

    clean_name = str(
        friendly_name
        or ""
    ).strip()

    if not clean_name:
        return None

    ttl = max(
        0,
        int(
            cache_ttl_seconds
        ),
    )

    cache_key = (
        clean_name
        + "|"
        + "|".join(
            str(
                value
                or ""
            ).strip()
            for value in entry_urls
        )
    )

    if ttl > 0:
        cached = _DISCOVERY_CACHE.get(
            cache_key
        )

        if (
            cached
            and time.monotonic()
            - cached[0]
            <= ttl
        ):
            return cached[1]

    async with _discovery_lock(
        cache_key
    ):
        if ttl > 0:
            cached = (
                _DISCOVERY_CACHE.get(
                    cache_key
                )
            )

            if (
                cached
                and time.monotonic()
                - cached[0]
                <= ttl
            ):
                return cached[1]

        for entry_url in entry_urls:
            try:
                if hasattr(
                    session,
                    "fetch_text_with_headers",
                ):
                    (
                        status,
                        document,
                        final_url,
                        headers,
                    ) = (
                        await session.fetch_text_with_headers(
                            entry_url,
                            max_bytes=(
                                document_max_bytes
                            ),
                        )
                    )

                else:
                    (
                        status,
                        document,
                        final_url,
                    ) = await session.fetch_text(
                        entry_url,
                        max_bytes=(
                            document_max_bytes
                        ),
                    )

                    headers = {}

            except Exception:
                continue

            if (
                int(
                    status
                )
                >= 400
            ):
                continue

            doc_id = (
                extract_doc_id_from_relay_context(
                    document,
                    clean_name,
                )
            )

            if doc_id:
                result = (
                    PersistedQueryDiscovery(
                        doc_id=doc_id,
                        friendly_name=(
                            clean_name
                        ),
                        source_url=str(
                            final_url
                            or entry_url
                        ),
                        source_kind="html",
                    )
                )

                if ttl > 0:
                    _DISCOVERY_CACHE[
                        cache_key
                    ] = (
                        time.monotonic(),
                        result,
                    )

                return result

            header_blob = "\n".join(
                (
                    f"{key}: {value}"
                )
                for (
                    key,
                    value,
                ) in dict(
                    headers
                    or {}
                ).items()
            )

            doc_id = (
                extract_doc_id_near_friendly_name(
                    header_blob,
                    clean_name,
                )
            )

            if doc_id:
                result = (
                    PersistedQueryDiscovery(
                        doc_id=doc_id,
                        friendly_name=(
                            clean_name
                        ),
                        source_url=str(
                            final_url
                            or entry_url
                        ),
                        source_kind=(
                            "response_headers"
                        ),
                    )
                )

                if ttl > 0:
                    _DISCOVERY_CACHE[
                        cache_key
                    ] = (
                        time.monotonic(),
                        result,
                    )

                return result

            # Meta increasingly keeps Relay persisted-query metadata in JS
            # bundles instead of the initial HTML. The old implementation
            # accepted script-scan parameters but discarded them, which meant
            # CREATE_BM discovery could never succeed unless the exact
            # operation happened to be embedded in the first document.
            if max_scripts > 0 and max_script_bytes > 0:
                script_urls: list[str] = []
                seen_script_urls: set[str] = set()
                script_patterns = (
                    r'<script[^>]+src=["\']([^"\']+)["\']',
                    r'["\']src["\']\s*:\s*["\']([^"\']+\.js(?:\?[^"\']*)?)["\']',
                )

                for source in source_variants(document):
                    for pattern in script_patterns:
                        for match in re.finditer(
                            pattern,
                            source,
                            flags=re.IGNORECASE,
                        ):
                            raw_url = html.unescape(
                                str(match.group(1) or "")
                            ).strip()
                            if not raw_url:
                                continue

                            script_url = urljoin(
                                str(final_url or entry_url),
                                raw_url,
                            )
                            if script_url in seen_script_urls:
                                continue

                            seen_script_urls.add(script_url)
                            script_urls.append(script_url)

                            if len(script_urls) >= max_scripts:
                                break
                        if len(script_urls) >= max_scripts:
                            break
                    if len(script_urls) >= max_scripts:
                        break

                for script_url in script_urls[:max_scripts]:
                    try:
                        if hasattr(
                            session,
                            "fetch_text_with_headers",
                        ):
                            (
                                script_status,
                                script_body,
                                script_final_url,
                                script_headers,
                            ) = await session.fetch_text_with_headers(
                                script_url,
                                max_bytes=max_script_bytes,
                                referer=str(final_url or entry_url),
                            )
                        else:
                            (
                                script_status,
                                script_body,
                                script_final_url,
                            ) = await session.fetch_text(
                                script_url,
                                max_bytes=max_script_bytes,
                                referer=str(final_url or entry_url),
                            )
                            script_headers = {}
                    except Exception:
                        continue

                    if int(script_status) >= 400:
                        continue

                    doc_id = extract_doc_id_near_friendly_name(
                        script_body,
                        clean_name,
                    )
                    if not doc_id:
                        doc_id = extract_doc_id_from_relay_context(
                            script_body,
                            clean_name,
                        )

                    if not doc_id and script_headers:
                        script_header_blob = "\n".join(
                            f"{key}: {value}"
                            for key, value in dict(
                                script_headers or {}
                            ).items()
                        )
                        doc_id = extract_doc_id_near_friendly_name(
                            script_header_blob,
                            clean_name,
                        )

                    if doc_id:
                        result = PersistedQueryDiscovery(
                            doc_id=doc_id,
                            friendly_name=clean_name,
                            source_url=str(
                                script_final_url
                                or script_url
                            ),
                            source_kind="script",
                        )

                        if ttl > 0:
                            _DISCOVERY_CACHE[
                                cache_key
                            ] = (
                                time.monotonic(),
                                result,
                            )

                        return result

    return None


__all__ = [
    "PersistedQueryDiscovery",
    "discover_persisted_query",
    "extract_csrf_token",
    "extract_doc_id_from_relay_context",
    "extract_doc_id_near_friendly_name",
    "source_variants",
]
