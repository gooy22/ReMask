from __future__ import annotations

import asyncio
import re
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit


FAN_PAGE_CREATE_NAMES = (
    "Create Page",
    "Create page",
    "Create",
    "Создать Страницу",
    "Создать страницу",
    "Создать",
    "Створити сторінку",
    "Створити",
    "Créer une Page",
    "Créer la Page",
    "Seite erstellen",
    "পৃষ্ঠা তৈরি করুন",
    "Tạo Trang",
    "Tạo trang",
    "पेज बनाएँ",
    "पेज बनाएं",
)

def confirmed_created_page(
    request_meta: dict[str, Any], payload: Any, *, actor_id: str,
    page_name: str, before_ids: set[str],
) -> dict[str, str] | None:
    """Accept only a scoped successful Page CREATE response, never nearby IDs."""
    actor = str(actor_id or "").strip()
    if not actor.isdigit():
        return None
    try:
        route = urlsplit(str(request_meta.get("url") or ""))
        host = (route.hostname or "").lower()
    except ValueError:
        return None
    operation = str(request_meta.get("friendly_name") or "").lower()
    if not (
        (host == "facebook.com" or host.endswith(".facebook.com"))
        and "graphql" in route.path.lower()
        and request_meta.get("method") == "POST"
        and all(word in operation for word in ("page", "create", "mutation"))
        and not any(word in operation for word in ("draft", "preview", "suggest", "delete", "update"))
        and request_meta.get("body_decodable") is True
    ):
        return None
    input_data = request_meta.get("input")
    if not isinstance(input_data, dict):
        return None
    names = [str(input_data[key]).strip() for key in ("name", "page_name")
             if input_data.get(key) is not None]
    if not names or any(name != page_name for name in names):
        return None
    request_actors = {str(input_data[key]).strip() for key in ("actor_id", "actorID")
                      if input_data.get(key) is not None}
    request_actors.update(str(value).strip() for value in request_meta.get("actor_ids", []))
    if request_actors != {actor}:
        return None

    chunks = payload if isinstance(payload, list) else [payload]
    pages: dict[str, str] = {}

    def has_error(value: Any) -> bool:
        if isinstance(value, list):
            return any(has_error(child) for child in value)
        if not isinstance(value, dict):
            return False
        if value.get("errors") or value.get("error"):
            return True
        if any(value.get(key) is False for key in ("success", "is_success")):
            return True
        return any(has_error(child) for child in value.values())

    def collect(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, dict):
            page_id = str(value.get("id") or "").strip()
            name = str(value.get("name") or "").strip()
            if (value.get("__typename") == "Page" and page_id.isdigit()
                    and page_id not in before_ids and name == page_name):
                pages[page_id] = name
            for child in value.values():
                collect(child)

    if not chunks or any(not isinstance(chunk, dict) or has_error(chunk) for chunk in chunks):
        return None
    for chunk in chunks:
        data = chunk.get("data")
        if not isinstance(data, dict):
            return None
        for key, result in data.items():
            # A viewer/profile/list branch inside a mutation is not CREATE
            # evidence. Require an explicit Page-create result envelope.
            if not re.fullmatch(r"page_?create|create_?page", str(key), re.I):
                continue
            primary = result.get("page") if isinstance(result, dict) else None
            if not isinstance(primary, dict) or not (
                primary.get("__typename") == "Page"
                and str(primary.get("name") or "").strip() == page_name
                and str(primary.get("id") or "").strip().isdigit()
                and str(primary["id"]).strip() not in before_ids
            ):
                continue
            collect(result)
    if len(pages) != 1:
        return None
    page_id, name = next(iter(pages.items()))
    return {"id": page_id, "name": name}


def fan_page_click_never_resolved(click_meta: Any, *, allowed_names: tuple[str, ...]) -> bool:
    """Prove no click from a complete Playwright locator-resolution timeout."""
    if not isinstance(click_meta, dict) or not (
        click_meta.get("found") is True
        and click_meta.get("attempted") is True
        and click_meta.get("clicked") is False
        and click_meta.get("role") == "button"
        and click_meta.get("name") in allowed_names
    ):
        return False
    error = str(click_meta.get("error") or "")
    # The old recorder capped errors at 500 chars. Never interpret a possibly
    # truncated call log: later lines could contain a dispatched click.
    if not error or len(error) >= 500:
        return False
    lines = [line.strip() for line in error.splitlines() if line.strip()]
    if len(lines) != 3 or not re.fullmatch(
        r"TimeoutError: Locator\.click: Timeout \d+ms exceeded\.", lines[0],
    ) or lines[1] != "Call log:":
        return False
    return bool(re.fullmatch(
        r"""- waiting for get_by_role\(["']button["'],.*\)(?:\.first|\.nth\(\d+\))""",
        lines[2],
    ))


def fan_page_pending_never_submitted(checkpoint: Any) -> bool:
    """Accept original, name-scoped evidence only; copied history uses the same rule."""
    if not isinstance(checkpoint, dict):
        return False
    name = str(checkpoint.get("active_page_name") or "").strip()
    diagnostic = checkpoint.get("browser_diagnostic")
    return bool(
        str(checkpoint.get("phase") or "").upper() in {
            "PAGE_CREATE_CLICK_INTENT", "PAGE_CREATE_RESULT_UNKNOWN",
        }
        and name
        and isinstance(diagnostic, dict)
        and str(diagnostic.get("page_name") or "").strip() == name
        and diagnostic.get("stage") == "fan_page_final_click_unknown"
        and fan_page_click_never_resolved(
            diagnostic.get("click_meta"), allowed_names=FAN_PAGE_CREATE_NAMES,
        )
    )


class FanPageCreateCapture:
    """Observe Meta's own UI request; never send or replay a mutation."""

    def __init__(self, page: Any, *, actor_id: str, page_name: str, before_ids: set[str],
                 request_meta: Callable[[Any], dict[str, Any]], decode: Callable[[str], Any]):
        self.page = page
        self.actor_id = actor_id
        self.page_name = page_name
        self.before_ids = before_ids
        self.request_meta = request_meta
        self.decode = decode
        self.armed = False
        self.tasks: set[asyncio.Task[Any]] = set()
        self.results: dict[str, dict[str, str]] = {}
        self.diagnostics: list[dict[str, str]] = []
        self.listening = False

    async def __aenter__(self):
        if callable(getattr(self.page, "on", None)):
            self.page.on("response", self.on_response)
            self.listening = True
        return self

    async def __aexit__(self, *args):
        if self.listening:
            try:
                self.page.remove_listener("response", self.on_response)
            except Exception:
                pass
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def on_response(self, response: Any) -> None:
        if not self.armed:
            return
        task = asyncio.create_task(self.inspect(response))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def inspect(self, response: Any) -> None:
        try:
            request = response.request
            meta = self.request_meta(request)
            operation = str(meta.get("friendly_name") or "").lower()
            if not all(word in operation for word in ("page", "create", "mutation")):
                return
            # Auth values stay local. Only actor identifiers enter the matcher.
            fields = parse_qs(str(getattr(request, "post_data", "") or ""))
            meta["actor_ids"] = [value for key in ("av", "__user")
                                 for value in fields.get(key, [])]
            if not (200 <= int(response.status) < 300):
                self.diagnostics.append({"result": "http_error"})
                return
            raw = await asyncio.wait_for(response.text(), timeout=3.0)
            if len(raw) > 2_000_000:
                self.diagnostics.append({"result": "response_too_large"})
                return
            payload = self.decode(raw)
            page = confirmed_created_page(
                meta, payload, actor_id=self.actor_id,
                page_name=self.page_name, before_ids=self.before_ids,
            )
            if page:
                self.results[page["id"]] = page
                self.diagnostics.append({"result": "confirmed", "page_id": page["id"]})
            else:
                chunks = payload if isinstance(payload, list) else [payload]
                roots = sorted({str(key) for chunk in chunks if isinstance(chunk, dict)
                                and isinstance(chunk.get("data"), dict) for key in chunk["data"]})
                self.diagnostics.append({"result": "unconfirmed",
                    "operation": str(meta.get("friendly_name") or "")[:120],
                    "response_roots": ",".join(roots[:12])[:300]})
        except Exception as exc:
            self.diagnostics.append({"result": "unavailable", "code": exc.__class__.__name__})

    async def drain(self) -> None:
        if self.tasks:
            await asyncio.wait(tuple(self.tasks), timeout=3.2)

    @property
    def page_result(self) -> dict[str, str] | None:
        return next(iter(self.results.values())) if len(self.results) == 1 and not self.tasks else None
