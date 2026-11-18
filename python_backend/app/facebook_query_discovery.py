# python_backend/app/facebook_query_discovery.py

from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit


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


_DISCOVERY_RESPONSE_CACHE_TTL_SECONDS = 30.0
_DISCOVERY_RESPONSE_CACHE_MAX_ENTRIES = 16
_DISCOVERY_RESPONSE_CACHE_MAX_BYTES = 16_000_000
_DISCOVERY_MAX_ENTRY_URLS = 4
_DISCOVERY_MAX_SCRIPTS_PER_SCAN = 4
_DISCOVERY_MAX_SCRIPT_BYTES = 3_000_000
_DISCOVERY_TRUSTED_HOSTS = {
    "facebook.com", "www.facebook.com", "business.facebook.com",
    "adsmanager.facebook.com",
}


def _session_actor(session: Any) -> str:
    profile = getattr(session, "profile", None)
    cookies = getattr(profile, "cookies", {})
    return str(cookies.get("c_user") or "")[:64] if isinstance(cookies, dict) else ""


def _trusted_discovery_host(url: str) -> bool:
    try:
        parts = urlsplit(str(url or ""))
    except Exception:
        return False
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and not (parts.port or parts.username or parts.password)
        and (
            host in _DISCOVERY_TRUSTED_HOSTS
            or host.endswith(".fbcdn.net")
        )
    )


def _response_cache(session: Any) -> dict | None:
    cache = getattr(session, "_meta_query_discovery_response_cache", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    try:
        setattr(session, "_meta_query_discovery_response_cache", cache)
    except (AttributeError, TypeError):
        return None
    return cache


def _response_lock(session: Any):
    lock = getattr(session, "_meta_query_discovery_response_lock", None)
    if lock is not None and hasattr(lock, "__aenter__"):
        return lock
    lock = asyncio.Lock()
    try:
        setattr(session, "_meta_query_discovery_response_lock", lock)
    except (AttributeError, TypeError):
        return None
    return lock


async def _fetch_discovery_response(
    session: Any,
    url: str,
    *,
    max_bytes: int,
    referer: str = "",
) -> tuple[int, str, str, dict[str, str]]:
    """Fetch a discovery source once per URL/session for a short in-memory window."""
    size_limit = max(1, int(max_bytes))
    actor = _session_actor(session)
    # JavaScript bundle bytes are content-addressed by URL; omit the
    # referring settings page so the same bundle is not downloaded once per BM.
    cache_referer = str(referer or "")
    if urlsplit(str(url or "")).path.lower().endswith(".js"):
        cache_referer = ""
    cache_key = (actor, str(url), cache_referer, size_limit)
    cache = _response_cache(session)

    async def fetch_once():
        now = time.monotonic()
        if cache is not None:
            for key, (stamp, _size, _response) in list(cache.items()):
                if now - float(stamp) > _DISCOVERY_RESPONSE_CACHE_TTL_SECONDS:
                    cache.pop(key, None)
            cached = cache.get(cache_key)
            if cached is not None and now - float(cached[0]) <= _DISCOVERY_RESPONSE_CACHE_TTL_SECONDS:
                return cached[2]

        with_headers = getattr(session, "fetch_text_with_headers", None)
        if callable(with_headers):
            kwargs = {"max_bytes": size_limit}
            if referer:
                kwargs["referer"] = referer
            response = await with_headers(url, **kwargs)
            status, body, final_url, headers = response
        else:
            kwargs = {"max_bytes": size_limit}
            if referer:
                kwargs["referer"] = referer
            status, body, final_url = await session.fetch_text(url, **kwargs)
            headers = {}

        normalized_headers = {
            str(key): str(value)
            for key, value in dict(headers or {}).items()
            if str(key).casefold() not in {"cookie", "set-cookie", "authorization"}
        }
        normalized = (
            int(status),
            str(body or ""),
            str(final_url or url),
            normalized_headers,
        )
        body_size = len(normalized[1].encode("utf-8"))
        if cache is not None and normalized[0] == 200 and body_size <= size_limit:
            while cache:
                total = sum(int(value[1]) for value in cache.values())
                if (
                    len(cache) < _DISCOVERY_RESPONSE_CACHE_MAX_ENTRIES
                    and total + body_size <= _DISCOVERY_RESPONSE_CACHE_MAX_BYTES
                ):
                    break
                oldest = min(cache, key=lambda key: float(cache[key][0]))
                cache.pop(oldest, None)
            if body_size <= _DISCOVERY_RESPONSE_CACHE_MAX_BYTES:
                cache[cache_key] = (time.monotonic(), body_size, normalized)
        return normalized

    lock = _response_lock(session)
    if lock is None:
        return await fetch_once()
    async with lock:
        return await fetch_once()


def _validate_discovery_response(
    status: int,
    body: str,
    final_url: str,
    *,
    missing_is_skippable: bool = True,
) -> bool:
    from .meta_auth_document import auth_gate
    from .provisioning.meta_errors import classify_meta_payload_error
    from .provisioning.models import ProvisioningError

    gate = auth_gate(final_url, body)
    if gate:
        raise ProvisioningError(
            gate,
            "Meta returned an authentication gate during persisted-query discovery; further profile requests stopped.",
            retryable=False,
        )
    if not _trusted_discovery_host(final_url):
        raise ProvisioningError(
            "PRIVATE_QUERY_DISCOVERY_UNTRUSTED_RESPONSE",
            "Persisted-query discovery stopped after an untrusted response URL.",
            retryable=False,
        )
    try:
        status = int(status)
    except (TypeError, ValueError):
        raise ProvisioningError(
            "PRIVATE_QUERY_DISCOVERY_INVALID_STATUS",
            "Persisted-query discovery stopped after an invalid HTTP status.",
            retryable=False,
        )
    if status in {404, 410} and missing_is_skippable:
        return False
    if status != 200:
        code, retryable, _diagnostic = classify_meta_payload_error(
            {},
            entity="BUSINESS",
            http_status=status,
            fallback_message=f"HTTP {status}",
        )
        if code == "META_UNKNOWN_ERROR":
            code = "REMOTE_TIMEOUT" if status >= 500 else "PRIVATE_QUERY_DISCOVERY_HTTP_ERROR"
            retryable = status >= 500
        raise ProvisioningError(
            code,
            "Persisted-query discovery stopped after a non-success response; no further source requests were issued.",
            retryable=bool(retryable),
        )
    return True


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
    requested_scripts = max(0, int(max_scripts_per_entry or 0))
    max_scripts = min(requested_scripts, _DISCOVERY_MAX_SCRIPTS_PER_SCAN)
    max_script_bytes = min(
        max(0, int(script_max_bytes or 0)),
        _DISCOVERY_MAX_SCRIPT_BYTES,
    )
    max_document_bytes = min(
        max(1, int(document_max_bytes or 0)),
        3_000_000,
    )

    clean_name = str(friendly_name or "").strip()
    if not clean_name:
        return None

    bounded_entries = list(dict.fromkeys(
        str(value or "").strip() for value in entry_urls if str(value or "").strip()
    ))[:_DISCOVERY_MAX_ENTRY_URLS]
    if not bounded_entries:
        return None
    if any(not _trusted_discovery_host(url) for url in bounded_entries):
        from .provisioning.models import ProvisioningError
        raise ProvisioningError(
            "PRIVATE_QUERY_DISCOVERY_UNTRUSTED_ENTRY",
            "Persisted-query discovery rejected an untrusted entry URL.",
            retryable=False,
        )

    ttl = max(0, int(cache_ttl_seconds or 0))
    cache_key = clean_name + "|" + "|".join(bounded_entries)
    if ttl > 0:
        cached = _DISCOVERY_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] <= ttl:
            return cached[1]

    async with _discovery_lock(cache_key):
        if ttl > 0:
            cached = _DISCOVERY_CACHE.get(cache_key)
            if cached and time.monotonic() - cached[0] <= ttl:
                return cached[1]

        scripts_scanned = 0
        for entry_url in bounded_entries:
            status, document, final_url, headers = await _fetch_discovery_response(
                session,
                entry_url,
                max_bytes=max_document_bytes,
            )
            if not _validate_discovery_response(status, document, final_url):
                continue

            doc_id = extract_doc_id_from_relay_context(document, clean_name)
            if doc_id:
                result = PersistedQueryDiscovery(
                    doc_id=doc_id,
                    friendly_name=clean_name,
                    source_url=final_url,
                    source_kind="html",
                )
                if ttl > 0:
                    _DISCOVERY_CACHE[cache_key] = (time.monotonic(), result)
                return result

            header_blob = "\n".join(f"{key}: {value}" for key, value in headers.items())
            doc_id = extract_doc_id_near_friendly_name(header_blob, clean_name)
            if doc_id:
                result = PersistedQueryDiscovery(
                    doc_id=doc_id,
                    friendly_name=clean_name,
                    source_url=final_url,
                    source_kind="response_headers",
                )
                if ttl > 0:
                    _DISCOVERY_CACHE[cache_key] = (time.monotonic(), result)
                return result

            if not max_scripts or not max_script_bytes or scripts_scanned >= max_scripts:
                continue

            script_urls: list[str] = []
            seen_script_urls: set[str] = set()
            script_patterns = (
                r'<script[^>]+src=["\']([^"\']+)["\']',
                r'["\']src["\']\s*:\s*["\']([^"\']+\.js(?:\?[^"\']*)?)["\']',
            )
            for source in source_variants(document):
                for pattern in script_patterns:
                    for match in re.finditer(pattern, source, flags=re.IGNORECASE):
                        raw_url = html.unescape(str(match.group(1) or "")).strip()
                        if not raw_url:
                            continue
                        script_url = urljoin(final_url, raw_url)
                        if script_url in seen_script_urls:
                            continue
                        seen_script_urls.add(script_url)
                        script_urls.append(script_url)
                        if len(script_urls) >= max_scripts - scripts_scanned:
                            break
                    if len(script_urls) >= max_scripts - scripts_scanned:
                        break
                if len(script_urls) >= max_scripts - scripts_scanned:
                    break

            for script_url in script_urls:
                if not _trusted_discovery_host(script_url):
                    from .provisioning.models import ProvisioningError
                    raise ProvisioningError(
                        "PRIVATE_QUERY_DISCOVERY_UNTRUSTED_SCRIPT",
                        "Persisted-query discovery stopped before fetching an untrusted script URL.",
                        retryable=False,
                    )
                scripts_scanned += 1
                script_status, script_body, script_final_url, script_headers = await _fetch_discovery_response(
                    session,
                    script_url,
                    max_bytes=max_script_bytes,
                    referer=final_url,
                )
                if not _validate_discovery_response(
                    script_status, script_body, script_final_url,
                ):
                    continue

                doc_id = extract_doc_id_near_friendly_name(script_body, clean_name)
                if not doc_id:
                    doc_id = extract_doc_id_from_relay_context(script_body, clean_name)
                if not doc_id:
                    header_blob = "\n".join(
                        f"{key}: {value}" for key, value in script_headers.items()
                    )
                    doc_id = extract_doc_id_near_friendly_name(header_blob, clean_name)
                if doc_id:
                    result = PersistedQueryDiscovery(
                        doc_id=doc_id,
                        friendly_name=clean_name,
                        source_url=script_final_url,
                        source_kind="script" if script_body else "response_headers",
                    )
                    if ttl > 0:
                        _DISCOVERY_CACHE[cache_key] = (time.monotonic(), result)
                    return result

            if scripts_scanned >= max_scripts:
                break

    return None


__all__ = [
    "PersistedQueryDiscovery",
    "discover_persisted_query",
    "extract_csrf_token",
    "extract_doc_id_from_relay_context",
    "extract_doc_id_near_friendly_name",
    "source_variants",
]
