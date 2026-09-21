from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class JobBridgeError(RuntimeError):
    pass


class JobBridgeClient:
    def __init__(self, url: str | None, internal_key: str | None, timeout: int = 20) -> None:
        self.url=(url or '').strip()
        self.key=(internal_key or '').strip()
        self.timeout=aiohttp.ClientTimeout(total=timeout)

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def _headers(self) -> dict[str,str]:
        headers={'Accept':'application/json','Content-Type':'application/json'}
        if self.key:
            headers['X-Remask-Internal-Key']=self.key
        return headers

    async def _post(self, payload: dict[str,Any]) -> dict[str,Any]:
        if not self.enabled:
            raise JobBridgeError('REMASK_JOB_BRIDGE_URL is not configured')
        try:
            async with aiohttp.ClientSession(timeout=self.timeout,headers=self._headers()) as session:
                async with session.post(self.url,json=payload) as response:
                    body=await response.json(content_type=None)
                    if response.status>=400 or not isinstance(body,dict) or not body.get('ok'):
                        detail=''
                        if isinstance(body,dict):
                            detail=str(body.get('message') or body.get('error') or '')
                        raise JobBridgeError(f'bridge HTTP {response.status}: {detail}'.strip())
                    return body
        except asyncio.TimeoutError as exc:
            raise JobBridgeError('bridge timeout') from exc
        except aiohttp.ClientError as exc:
            raise JobBridgeError(f'bridge network error: {exc.__class__.__name__}') from exc
        except ValueError as exc:
            raise JobBridgeError('bridge returned invalid JSON') from exc

    async def create_proxy_check_job(self, profile_id: str, idempotency_key: str) -> str:
        body=await self._post({
            'action':'create',
            'idempotency_key':idempotency_key,
            'profiles':[{
                'profile_id':profile_id,
                'tasks':[{'action':'proxy_check','payload':{}}],
            }],
        })
        job=body.get('job') or {}
        job_id=str(job.get('job_id') or '').strip()
        if not job_id:
            raise JobBridgeError('bridge create response contains no job_id')
        return job_id

    async def get_job(self, job_id: str) -> dict[str,Any]:
        body=await self._post({'action':'status','job_id':job_id})
        job=body.get('job')
        if not isinstance(job,dict):
            raise JobBridgeError('bridge status response contains no job')
        return job
