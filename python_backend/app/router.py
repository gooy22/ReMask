from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .session import ProfileSession


class RoutePolicyError(RuntimeError):
    pass


class TransparentPostRouter:
    """
    Contract-agnostic JSON POST router.

    Tasks supply a route alias plus an arbitrary nested JSON object. Aliases are
    resolved only from REMASK_TASK_ROUTES_JSON. Direct URLs are never accepted.
    Browser-session cookies are deliberately not forwarded.
    """

    def __init__(self, raw_config: str | None = None) -> None:
        raw = raw_config if raw_config is not None else os.getenv('REMASK_TASK_ROUTES_JSON', '{}')
        try:
            parsed = json.loads(raw or '{}')
        except json.JSONDecodeError as exc:
            raise RoutePolicyError('REMASK_TASK_ROUTES_JSON is invalid JSON') from exc
        if not isinstance(parsed, dict):
            raise RoutePolicyError('REMASK_TASK_ROUTES_JSON must be an object')
        self.routes: dict[str, dict[str, Any]] = {}
        for alias, config in parsed.items():
            if not isinstance(alias, str) or not alias.strip():
                continue
            if isinstance(config, str):
                config = {'url': config}
            if not isinstance(config, dict):
                continue
            url = str(config.get('url') or '').strip()
            if not url:
                continue
            parsed_url = urlparse(url)
            if parsed_url.scheme not in {'http','https'} or not parsed_url.hostname:
                raise RoutePolicyError(f'invalid route URL for alias {alias}')
            host = parsed_url.hostname.lower()
            # Private Facebook web surfaces are intentionally not routable.
            if host == 'facebook.com' or host.endswith('.facebook.com'):
                if host != 'graph.facebook.com':
                    raise RoutePolicyError(f'private Facebook web route is not allowed: {alias}')
            headers = config.get('headers') if isinstance(config.get('headers'), dict) else {}
            self.routes[alias] = {
                'url': url,
                'headers': {str(k): str(v) for k, v in headers.items()},
                'timeout': int(config.get('timeout') or 30),
            }

    async def execute(self, session: ProfileSession, payload: dict[str, Any]) -> dict[str, Any]:
        route = str(payload.get('route') or '').strip()
        if not route:
            raise RoutePolicyError('route is required')
        if '://' in route:
            raise RoutePolicyError('direct URLs are not allowed; use a configured route alias')
        config = self.routes.get(route)
        if not config:
            raise RoutePolicyError(f'route alias is not configured: {route}')

        variables = payload.get('variables', {})
        if not isinstance(variables, dict):
            raise RoutePolicyError('variables must be a JSON object')

        timeout = aiohttp.ClientTimeout(total=max(1, min(int(config['timeout']), 120)))
        headers = {
            'Accept': 'application/json',
            'User-Agent': session.context.user_agent,
            **config['headers'],
        }

        # Use the profile's network route, but do not forward its browser cookies.
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as client:
                async with client.post(
                    config['url'],
                    json=variables,
                    proxy=session.context.proxy,
                ) as response:
                    raw = await response.text()
                    content_type = response.headers.get('Content-Type', '')
                    parsed: Any = None
                    if 'json' in content_type.lower() or (raw and raw[:1] in '{['):
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError:
                            parsed = None

                    result = {
                        'route': route,
                        'status': response.status,
                        'response_json': parsed,
                        'response_text': None if parsed is not None else raw[:200000],
                    }
                    if response.status >= 400:
                        raise RuntimeError(f'route {route} returned HTTP {response.status}: {raw[:1000]}')
                    return result
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f'route {route} timeout') from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f'route {route} transport error: {exc.__class__.__name__}') from exc
