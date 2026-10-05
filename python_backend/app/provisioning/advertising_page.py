"""One advertising Page per Facebook account, reused by its BM/RK jobs."""
from __future__ import annotations
import asyncio
import json
import sqlite3
from typing import Any

from .models import ProvisioningError

DEFAULT_PAGE_NAME = 'PrgssTeam'
DEFAULT_OWNER_PROFILE = '9'
_PAGE_LOCK = asyncio.Lock()


class AdvertisingPageStore:
    def __init__(self, state: Any, profile_id: str | None = None, facebook_uid: str = ''):
        self.state = state
        self.profile_id = str(profile_id or DEFAULT_OWNER_PROFILE)
        self.facebook_uid = str(facebook_uid or '')
        self.key = ('facebook:'+self.facebook_uid) if self.facebook_uid else (
            'primary' if self.profile_id==DEFAULT_OWNER_PROFILE else 'profile:'+self.profile_id)

    @classmethod
    def for_context(cls, state, context, profile_id=None):
        return cls(state,profile_id or str(context.profile_id),
            str((getattr(context,'cookies',{}) or {}).get('c_user') or ''))

    def _read(self) -> dict:
        with sqlite3.connect(str(self.state.path),timeout=10) as db:
            db.execute('CREATE TABLE IF NOT EXISTS workspace_advertising_page (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            row=db.execute('SELECT value FROM workspace_advertising_page WHERE key=?',(self.key,)).fetchone()
            if row: return json.loads(row[0])
            # Preserve the already-created profile 9 Page and its pending
            # submissions. Never migrate it into a different Facebook account.
            legacy=db.execute('SELECT value FROM workspace_advertising_page WHERE key=?',('primary',)).fetchone()
            if legacy:
                value=json.loads(legacy[0])
                if str(value.get('owner_profile_id') or DEFAULT_OWNER_PROFILE)==self.profile_id:
                    return {**value,'owner_facebook_uid':self.facebook_uid}
            return {'name':DEFAULT_PAGE_NAME,'owner_profile_id':self.profile_id,
                'owner_facebook_uid':self.facebook_uid}

    async def get(self) -> dict:
        return await asyncio.to_thread(self._read)

    def _write(self, value: dict) -> None:
        with sqlite3.connect(str(self.state.path),timeout=10) as db:
            db.execute('INSERT OR REPLACE INTO workspace_advertising_page(key,value) VALUES (?,?)',(self.key,json.dumps(value)))

    async def patch(self, **changes) -> dict:
        value={**await self.get(),**changes}
        await asyncio.to_thread(self._write,value)
        return value


async def ensure_common_page(session: Any, params: dict, state: Any, resolver: Any) -> dict:
    from .fan_pages_handler import fan_pages_handler
    async with _PAGE_LOCK:
        config=AdvertisingPageStore.for_context(state,session.context)
        page=await config.get()
        owner=str(session.context.profile_id)
        if params.get('policies_accepted') is True:
            page=await config.patch(policies_accepted=True,policies_name=page['name'],
                policies_owner_profile_id=owner,policies_owner_facebook_uid=config.facebook_uid)
        known={str(row['id']):row for row in [
            *await state.latest_profile_fan_pages(owner),*(getattr(session.context,'pages',None) or [])]
            if isinstance(row,dict) and str(row.get('id') or '').isdigit()}
        selected_id=str(params.get('page_id') or '').strip()
        if selected_id:
            selected=known.get(selected_id)
            if not selected:
                raise ProvisioningError('PROFILE_PAGE_UNAVAILABLE','Selected Page is absent from this Facebook profile inventory',retryable=True)
            if page.get('page_id')!=selected_id:
                page=await config.patch(page_id=selected_id,name=str(selected.get('name') or selected_id),grants={},
                    owner_profile_id=owner,owner_business_id='',ownership_phase='',owner_business_confirmed=False)
        if not page.get('page_id'):
            named=[row for row in known.values() if str(row.get('name') or '').casefold()==page['name'].casefold()]
            if named:
                # All candidates come from this profile's managed inventory.
                # Pick once deterministically and persist the identity so bulk
                # work never asks the user to choose between same-name Pages.
                selected=min(named,key=lambda row:int(row['id']))
            else:
                if params.get('reuse_only') is True:
                    raise ProvisioningError('PROFILE_PAGE_REQUIRED','Select an existing Page for this profile',retryable=True)
                from .models import ProvisioningStep
                item='workspace-common-page-'+owner
                await state.set_running(item,owner,'workspace-common-page',ProvisioningStep.FAN_PAGES)
                creation={'names':[page['name']],'count':1,'category':params.get('category') or 'Digital creator',
                    'confirm_main_business':False,'require_policy_consent':True,
                    'policies_accepted':page.get('policies_accepted') is True and page.get('policies_name')==page['name']
                        and (page.get('policies_owner_profile_id')==owner or bool(config.facebook_uid
                            and page.get('policies_owner_facebook_uid')==config.facebook_uid))}
                result=await fan_pages_handler(session,creation,{},provisioning_state=state,
                    item_id=item,profile_id=owner,scope_key='workspace-common-page')
                selected=result['pages'][0]
                await state.complete(item,owner,'workspace-common-page',ProvisioningStep.FAN_PAGES,result)
            page=await config.patch(page_id=selected['id'],name=selected['name'],
                main_business_confirmed=selected.get('main_business_confirmed') is True,
                main_business_id=str(selected.get('main_business_id') or ''))
        return {'page_ids':[page['page_id']], 'pages':[{'id':page['page_id'],'name':page['name'],
            'shared':True,'owner_profile_id':owner,'attached':False,
            'main_business_confirmed':page.get('main_business_confirmed') is True,
            'main_business_id':page.get('main_business_id') or ''}], 'count':1,'common_page':True}
