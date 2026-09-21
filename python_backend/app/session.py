from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp

class ProfileContextError(RuntimeError): pass
class ProxyCheckError(RuntimeError): pass

@dataclass(slots=True)
class ProfileContext:
    profile_id: str
    cookies: dict[str, str]
    proxy: str | None
    user_agent: str

class ProfileResolver:
    def __init__(self, resolver_url: str | None, internal_key: str | None, timeout: int = 15) -> None:
        self.url=resolver_url
        self.key=internal_key
        self.timeout=aiohttp.ClientTimeout(total=timeout)

    async def resolve(self, profile_id: str) -> ProfileContext:
        if not self.url:
            raise ProfileContextError('REMASK_PROFILE_RESOLVER_URL is not configured')
        headers={'Accept':'application/json'}
        if self.key:
            headers['X-Remask-Internal-Key']=self.key
        try:
            async with aiohttp.ClientSession(timeout=self.timeout, headers=headers) as session:
                async with session.get(self.url, params={'profile_id':profile_id}) as response:
                    payload=await response.json(content_type=None)
                    if response.status >= 400 or not isinstance(payload, dict):
                        raise ProfileContextError(f'profile resolver HTTP {response.status}')
        except asyncio.TimeoutError as exc:
            raise ProfileContextError('profile resolver timeout') from exc
        except aiohttp.ClientError as exc:
            raise ProfileContextError(f'profile resolver network error: {exc.__class__.__name__}') from exc
        cookies=payload.get('cookies') or {}
        ua=str(payload.get('user_agent') or '').strip()
        if not isinstance(cookies, dict) or not ua:
            raise ProfileContextError('profile resolver returned incomplete context')
        return ProfileContext(
            profile_id=profile_id,
            cookies={str(k):str(v) for k,v in cookies.items()},
            proxy=payload.get('proxy'),
            user_agent=ua,
        )

class ProfileSession:
    def __init__(self, context: ProfileContext, timeout_seconds: int = 15, pool_size: int = 20) -> None:
        self.context=context
        self.timeout=aiohttp.ClientTimeout(total=timeout_seconds, connect=timeout_seconds, sock_read=timeout_seconds)
        self.connector=aiohttp.TCPConnector(limit=pool_size, limit_per_host=pool_size, enable_cleanup_closed=True)
        self.session=aiohttp.ClientSession(
            timeout=self.timeout,
            connector=self.connector,
            cookies=context.cookies,
            headers={'User-Agent':context.user_agent,'Accept':'application/json,text/html;q=0.9,*/*;q=0.8'},
        )

    async def __aenter__(self) -> 'ProfileSession':
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.session.close()

    async def proxy_check(self) -> dict[str, Any]:
        if not self.context.proxy:
            raise ProxyCheckError('proxy is not configured')
        try:
            async with self.session.get(
                'https://api.ipify.org',
                params={'format':'json'},
                proxy=self.context.proxy,
            ) as response:
                payload=await response.json(content_type=None)
                if response.status != 200 or not isinstance(payload,dict) or not payload.get('ip'):
                    raise ProxyCheckError(f'proxy check HTTP {response.status}')
                return {'exit_ip':str(payload['ip'])}
        except asyncio.TimeoutError as exc:
            raise ProxyCheckError('proxy timeout') from exc
        except aiohttp.ClientError as exc:
            raise ProxyCheckError(f'proxy network error: {exc.__class__.__name__}') from exc
