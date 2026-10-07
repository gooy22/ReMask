from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, unquote_plus, urlsplit


SAFE_ENVELOPE_KEYS = frozenset({
    "__aaid", "__bid", "__hs", "__hblp", "__hsdp", "__rev", "__s",
    "__hsi", "__dyn", "__csr", "__comet_req", "__spin_r", "__spin_b",
    "__spin_t", "__jssesw", "__crn", "__req", "__ccg", "dpr",
    "server_timestamps", "fb_api_caller_class",
})


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _request_body(request: Any) -> tuple[str, bool]:
    try:
        raw_buffer = getattr(request, "post_data_buffer", None)
        if raw_buffer:
            if isinstance(raw_buffer, bytes):
                return raw_buffer.decode("utf-8"), True
            return str(raw_buffer), True
        return str(getattr(request, "post_data", "") or ""), True
    except (UnicodeDecodeError, UnicodeError):
        try:
            return str(getattr(request, "post_data", "") or ""), False
        except Exception:
            return "", False
    except Exception:
        try:
            return str(getattr(request, "post_data", "") or ""), False
        except Exception:
            return "", False


def graphql_request_meta(request: Any) -> dict[str, Any]:
    """Parse a Meta GraphQL request without returning cookies/auth tokens."""
    method = _clean(getattr(request, "method", "")).upper()
    url = _clean(getattr(request, "url", ""))
    raw, body_decodable = _request_body(request)
    parsed = parse_qs(raw, keep_blank_values=True) if raw else {}

    try:
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    except Exception:
        query = {}
    for key, values in query.items():
        if key not in parsed and isinstance(values, list):
            parsed[key] = values

    effective_method = method
    query_method = _clean((parsed.get("method") or [""])[0]).upper()
    if method == "GET" and query_method == "POST":
        effective_method = "POST"

    friendly = _clean(
        (parsed.get("fb_api_req_friendly_name") or [""])[0]
    )
    if not friendly:
        try:
            headers = getattr(request, "headers", {}) or {}
            friendly = _clean(
                headers.get("x-fb-friendly-name")
                or headers.get("X-FB-Friendly-Name")
            )
        except Exception:
            friendly = ""

    doc_id = _clean((parsed.get("doc_id") or [""])[0])
    variables: dict[str, Any] = {}
    raw_variables = _clean((parsed.get("variables") or [""])[0])
    if raw_variables:
        try:
            decoded = json.loads(raw_variables)
            if isinstance(decoded, dict):
                variables = decoded
        except (TypeError, ValueError, json.JSONDecodeError):
            variables = {}

    raw_input = variables.get("input")
    return {
        "method": effective_method,
        "browser_method": method,
        "url": url,
        "friendly_name": friendly,
        "doc_id": doc_id,
        "variables": variables,
        "input": raw_input if isinstance(raw_input, dict) else {},
        # This is intentionally retained only for in-process matchers. Never
        # expose it in safe summaries because request bodies can contain auth.
        "decoded_raw": unquote_plus(raw) if raw else "",
        "body_decodable": body_decodable,
    }


def safe_graphql_request_summary(
    request: Any = None,
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = meta if isinstance(meta, dict) else graphql_request_meta(request)
    variables = (
        meta.get("variables")
        if isinstance(meta.get("variables"), dict)
        else {}
    )
    raw_input = variables.get("input")
    input_keys = (
        sorted(str(key) for key in raw_input)
        if isinstance(raw_input, dict)
        else []
    )
    recursive_keys: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                recursive_keys.add(str(key))
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(variables)
    return {
        "url": _clean(meta.get("url")),
        "method": _clean(meta.get("method")),
        "friendly_name": _clean(meta.get("friendly_name")),
        "doc_id": _clean(meta.get("doc_id")),
        "variable_keys": sorted(str(key) for key in variables),
        "input_keys": input_keys,
        "recursive_keys": sorted(recursive_keys)[:80],
        "body_decodable": bool(meta.get("body_decodable")),
    }


def safe_request_envelope(request: Any) -> dict[str, str]:
    """Keep only non-auth request envelope fields allowed for replay."""
    raw, _ = _request_body(request)
    parsed = parse_qs(raw, keep_blank_values=True) if raw else {}
    if not parsed:
        try:
            parsed = parse_qs(
                urlsplit(_clean(getattr(request, "url", ""))).query,
                keep_blank_values=True,
            )
        except Exception:
            parsed = {}
    return {
        str(key): _clean(values[0])
        for key, values in parsed.items()
        if (
            key in SAFE_ENVELOPE_KEYS
            and isinstance(values, list)
            and values
            and _clean(values[0])
        )
    }


Matcher = Callable[[Any, dict[str, Any]], bool]


class GraphqlMutationCapture:
    """Reusable exactly-once browser GraphQL interception primitive.

    Definitive matches are aborted before reaching Meta and returned as a safe
    replay contract. An optional conservative matcher may abort an unclassified
    mutation after the caller arms the final-submit gate; such a request is
    never returned as replayable.
    """

    def __init__(
        self,
        page: Any,
        *,
        matcher: Matcher,
        plausible_matcher: Matcher | None = None,
        max_candidates: int = 24,
    ) -> None:
        self.page = page
        self.matcher = matcher
        self.plausible_matcher = plausible_matcher
        self.max_candidates = max(4, min(int(max_candidates), 100))
        self.candidates: list[dict[str, Any]] = []
        self.blocked_unclassified = False
        self.armed = False
        self._future: asyncio.Future[dict[str, Any]] | None = None
        self._installed = False

    async def __aenter__(self) -> "GraphqlMutationCapture":
        loop = asyncio.get_running_loop()
        self._future = loop.create_future()
        await self.page.route("**/*graphql*", self._intercept)
        self._installed = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._installed:
            try:
                await self.page.unroute("**/*graphql*", self._intercept)
            except Exception:
                pass
            self._installed = False
        if self._future is not None and not self._future.done():
            self._future.cancel()

    def arm(self) -> None:
        self.armed = True

    def disarm(self) -> None:
        self.armed = False

    @property
    def done(self) -> bool:
        return bool(self._future is not None and self._future.done())

    def result_nowait(self) -> dict[str, Any]:
        if self._future is None:
            raise RuntimeError("capture is not active")
        return self._future.result()

    async def wait(self, timeout: float) -> dict[str, Any]:
        if self._future is None:
            raise RuntimeError("capture is not active")
        return await asyncio.wait_for(
            asyncio.shield(self._future),
            timeout=max(0.01, float(timeout)),
        )

    async def _intercept(self, route: Any, request: Any) -> None:
        meta = graphql_request_meta(request)
        method = _clean(meta.get("method")).upper()
        url = _clean(meta.get("url")).lower()
        is_graphql_post = method == "POST" and "graphql" in url

        definitive = False
        plausible = False
        if is_graphql_post:
            try:
                definitive = bool(self.matcher(request, meta))
            except Exception:
                definitive = False
            if self.armed and not definitive and self.plausible_matcher:
                try:
                    plausible = bool(
                        self.plausible_matcher(request, meta)
                    )
                except Exception:
                    plausible = False

            summary = safe_graphql_request_summary(meta=meta)
            summary["matched_mutation"] = definitive
            summary["plausible_unclassified_mutation"] = plausible
            summary["final_gate_armed"] = self.armed
            self.candidates.append(summary)
            if len(self.candidates) > self.max_candidates:
                del self.candidates[:-self.max_candidates]

        if definitive:
            row = {
                "doc_id": _clean(meta.get("doc_id")),
                "friendly_name": _clean(meta.get("friendly_name")),
                "endpoint_url": _clean(meta.get("url")),
                "variables": (
                    meta.get("variables")
                    if isinstance(meta.get("variables"), dict)
                    else {}
                ),
                "request_envelope": safe_request_envelope(request),
            }
            await route.abort()
            if self._future is not None and not self._future.done():
                self._future.set_result(row)
            return

        if plausible:
            self.blocked_unclassified = True
            await route.abort()
            return

        await route.continue_()


__all__ = [
    "GraphqlMutationCapture",
    "SAFE_ENVELOPE_KEYS",
    "graphql_request_meta",
    "safe_graphql_request_summary",
    "safe_request_envelope",
]
