from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

class MirrorError(RuntimeError):
    pass

class SnapshotMirror:
    def __init__(self, url: str | None, internal_key: str | None, timeout: int = 15) -> None:
        self.url=(url or '').strip()
        self.key=(internal_key or '').strip()
        self.timeout=aiohttp.ClientTimeout(total=timeout)

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def _headers(self) -> dict[str,str]:
        headers={'Accept':'application/json'}
        if self.key:
            headers['X-Remask-Internal-Key']=self.key
        return headers

    async def health(self) -> bool:
        if not self.enabled:
            return False
        try:
            async with aiohttp.ClientSession(timeout=self.timeout,headers=self._headers()) as session:
                async with session.get(self.url,params={'action':'health'}) as response:
                    payload=await response.json(content_type=None)
                    return response.status==200 and isinstance(payload,dict) and bool(payload.get('ok'))
        except (asyncio.TimeoutError,aiohttp.ClientError,ValueError):
            return False

    async def load_jobs(self) -> list[dict[str,Any]]:
        if not self.enabled:
            return []
        try:
            async with aiohttp.ClientSession(timeout=self.timeout,headers=self._headers()) as session:
                async with session.get(self.url,params={'action':'list'}) as response:
                    payload=await response.json(content_type=None)
                    if response.status>=400 or not isinstance(payload,dict):
                        raise MirrorError(f'mirror list HTTP {response.status}')
                    jobs=payload.get('jobs') or []
                    if not isinstance(jobs,list):
                        raise MirrorError('mirror list returned invalid jobs')
                    return [job for job in jobs if isinstance(job,dict)]
        except asyncio.TimeoutError as exc:
            raise MirrorError('mirror list timeout') from exc
        except aiohttp.ClientError as exc:
            raise MirrorError(f'mirror list network error: {exc.__class__.__name__}') from exc

    async def save_job(self, job: dict[str,Any]) -> None:
        if not self.enabled:
            return
        try:
            async with aiohttp.ClientSession(timeout=self.timeout,headers=self._headers()) as session:
                async with session.post(self.url,json={'action':'save','job':job}) as response:
                    payload=await response.json(content_type=None)
                    if response.status>=400 or not isinstance(payload,dict) or not payload.get('ok'):
                        raise MirrorError(f'mirror save HTTP {response.status}')
        except asyncio.TimeoutError as exc:
            raise MirrorError('mirror save timeout') from exc
        except aiohttp.ClientError as exc:
            raise MirrorError(f'mirror save network error: {exc.__class__.__name__}') from exc
