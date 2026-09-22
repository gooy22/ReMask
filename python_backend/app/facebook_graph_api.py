from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp


class GraphApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        payload: dict[str, Any] | None = None,
        code: int | None = None,
        subcode: int | None = None,
        error_type: str = "",
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.payload = payload or {}
        self.code = code
        self.subcode = subcode
        self.error_type = error_type


class GraphMutationUncertain(RuntimeError):
    """POST may have reached Meta but no authoritative response was received."""


@dataclass(slots=True)
class GraphIdentity:
    user_id: str
    name: str = ""


class FacebookGraphApi:
    """
    Official Graph API transport bound to the same profile proxy/UA.

    It is intentionally separate from fb_worker.FacebookWebSession:
    - this class uses the saved access token against graph.facebook.com;
    - FacebookWebSession uses browser cookies + fb_dtsg/private GraphQL.
    """

    def __init__(
        self,
        *,
        access_token: str,
        proxy: str | None,
        user_agent: str,
        timeout_seconds: int = 25,
        api_version: str | None = None,
    ) -> None:
        self.access_token = str(access_token or "").strip()
        self.proxy = str(proxy or "").strip() or None
        self.user_agent = str(user_agent or "").strip()
        self.timeout_seconds = max(5, int(timeout_seconds))
        version = str(
            api_version
            or os.getenv("META_GRAPH_API_VERSION")
            or "v26.0"
        ).strip()
        if not version.startswith("v"):
            version = "v" + version
        self.api_version = version
        self.base_url = f"https://graph.facebook.com/{self.api_version}"
        self.timeout = aiohttp.ClientTimeout(
            total=self.timeout_seconds,
            connect=self.timeout_seconds,
            sock_connect=self.timeout_seconds,
            sock_read=self.timeout_seconds,
        )
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._identity: GraphIdentity | None = None

    async def _client(self) -> aiohttp.ClientSession:
        current = self._session
        if current is not None and not current.closed:
            return current

        async with self._lock:
            current = self._session
            if current is None or current.closed:
                self._session = aiohttp.ClientSession(
                    timeout=self.timeout,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "application/json",
                    },
                )
            return self._session

    async def close(self) -> None:
        async with self._lock:
            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None
        self._identity = None

    @staticmethod
    def _error_from_payload(
        payload: dict[str, Any],
        *,
        http_status: int,
    ) -> GraphApiError:
        error = payload.get("error")
        if not isinstance(error, dict):
            return GraphApiError(
                f"Meta Graph HTTP {http_status}",
                http_status=http_status,
                payload=payload,
            )

        def as_int(value: Any) -> int | None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        message = str(error.get("message") or "Meta Graph API error").strip()
        code = as_int(error.get("code"))
        subcode = as_int(error.get("error_subcode"))
        error_type = str(error.get("type") or "").strip()

        return GraphApiError(
            message,
            http_status=http_status,
            payload=payload,
            code=code,
            subcode=subcode,
            error_type=error_type,
        )

    async def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        mutation: bool = False,
    ) -> dict[str, Any]:
        if not self.access_token:
            raise GraphApiError("profile access token is missing")
        if not self.proxy:
            raise GraphApiError("profile proxy is missing")

        target = str(path_or_url or "").strip()
        if target.startswith("https://"):
            parts = urlsplit(target)
            if parts.hostname != "graph.facebook.com":
                raise GraphApiError(
                    f"blocked non-Graph pagination host: {parts.hostname or '<empty>'}"
                )
            url = target
        else:
            url = self.base_url + "/" + target.lstrip("/")

        query = dict(params or {})
        body = dict(data or {})

        if method.upper() == "GET":
            query["access_token"] = self.access_token
        else:
            body["access_token"] = self.access_token

        client = await self._client()

        try:
            async with client.request(
                method.upper(),
                url,
                params=query or None,
                data=body or None,
                proxy=self.proxy,
                allow_redirects=False,
            ) as response:
                raw = await response.text()

                try:
                    payload = json.loads(raw) if raw else {}
                except (json.JSONDecodeError, ValueError) as exc:
                    if mutation:
                        raise GraphMutationUncertain(
                            f"Meta mutation returned non-JSON HTTP {response.status}"
                        ) from exc
                    raise GraphApiError(
                        f"Meta Graph returned non-JSON HTTP {response.status}",
                        http_status=response.status,
                    ) from exc

                if not isinstance(payload, dict):
                    if mutation:
                        raise GraphMutationUncertain(
                            "Meta mutation returned unexpected response type"
                        )
                    raise GraphApiError(
                        "Meta Graph returned unexpected response type",
                        http_status=response.status,
                    )

                if response.status >= 400 or isinstance(payload.get("error"), dict):
                    raise self._error_from_payload(
                        payload,
                        http_status=response.status,
                    )

                return payload

        except (GraphApiError, GraphMutationUncertain):
            raise
        except asyncio.TimeoutError as exc:
            if mutation:
                raise GraphMutationUncertain(
                    "Meta create-business request timed out; result is unknown"
                ) from exc
            raise GraphApiError("Meta Graph request timeout") from exc
        except aiohttp.ClientError as exc:
            if mutation:
                raise GraphMutationUncertain(
                    "Meta create-business network failure; result is unknown"
                ) from exc
            raise GraphApiError(
                f"Meta Graph network failure: {exc.__class__.__name__}"
            ) from exc

    async def identity(self, *, force: bool = False) -> GraphIdentity:
        if self._identity is not None and not force:
            return self._identity

        payload = await self._request(
            "GET",
            "me",
            params={"fields": "id,name"},
        )
        user_id = str(payload.get("id") or "").strip()
        if not user_id:
            raise GraphApiError("Meta /me returned no user id", payload=payload)

        self._identity = GraphIdentity(
            user_id=user_id,
            name=str(payload.get("name") or "").strip(),
        )
        return self._identity

    async def list_pages(self, *, max_pages: int = 10) -> list[dict[str, Any]]:
        await self.identity()
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        next_url: str | None = None

        for _ in range(max(1, min(int(max_pages), 20))):
            if next_url:
                payload = await self._request("GET", next_url)
            else:
                payload = await self._request(
                    "GET",
                    "me/accounts",
                    params={
                        "fields": "id,name,category,tasks",
                        "limit": "100",
                    },
                )

            rows = payload.get("data")
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    page_id = str(row.get("id") or "").strip()
                    if not page_id or page_id in seen:
                        continue
                    seen.add(page_id)
                    result.append(
                        {
                            "id": page_id,
                            "name": str(row.get("name") or page_id).strip(),
                            "category": str(row.get("category") or "").strip(),
                            "tasks": [
                                str(x)
                                for x in (row.get("tasks") or [])
                                if isinstance(x, (str, int))
                            ],
                        }
                    )

            paging = payload.get("paging")
            if not isinstance(paging, dict):
                break
            candidate = str(paging.get("next") or "").strip()
            if not candidate:
                break
            parts = urlsplit(candidate)
            if parts.scheme != "https" or parts.hostname != "graph.facebook.com":
                break
            next_url = candidate

        return result

    async def list_businesses(self) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "me/businesses",
            params={
                "fields": "id,name,primary_page",
                "limit": "100",
            },
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            return []
        output: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            business_id = str(row.get("id") or "").strip()
            if not business_id:
                continue
            primary = row.get("primary_page")
            primary_page = primary if isinstance(primary, dict) else None
            primary_page_id = ""
            if isinstance(primary, dict):
                primary_page_id = str(primary.get("id") or "").strip()
            elif isinstance(primary, (str, int)):
                primary_page_id = str(primary).strip()

            output.append(
                {
                    "id": business_id,
                    "name": str(row.get("name") or business_id).strip(),
                    "primary_page": primary_page,
                    "primary_page_id": primary_page_id,
                }
            )
        return output

    async def create_business(
        self,
        *,
        name: str,
        primary_page_id: str,
        vertical: str = "ADVERTISING",
        email: str = "",
        timezone_id: int | None = None,
    ) -> str:
        identity = await self.identity()

        business_name = str(name or "").strip()
        page_id = str(primary_page_id or "").strip()
        vertical_value = str(vertical or "ADVERTISING").strip().upper()

        if not business_name:
            raise ValueError("Business name is required")
        if not page_id.isdigit():
            raise ValueError("A numeric primary Page ID is required")
        if not vertical_value:
            raise ValueError("Business vertical is required")

        body: dict[str, Any] = {
            "name": business_name,
            "vertical": vertical_value,
            "primary_page": page_id,
        }
        clean_email = str(email or "").strip()
        if clean_email:
            body["email"] = clean_email
        if timezone_id is not None:
            body["timezone_id"] = str(int(timezone_id))

        payload = await self._request(
            "POST",
            f"{identity.user_id}/businesses",
            data=body,
            mutation=True,
        )

        business_id = str(payload.get("id") or "").strip()
        if not business_id:
            raise GraphMutationUncertain(
                "Meta create-business returned no id; result is unknown"
            )
        return business_id


__all__ = [
    "FacebookGraphApi",
    "GraphApiError",
    "GraphMutationUncertain",
    "GraphIdentity",
]
