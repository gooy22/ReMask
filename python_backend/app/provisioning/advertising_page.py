"""One workspace advertising Page; identities and grants survive independent Jobs."""
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
    def __init__(self, state: Any):
        self.state = state

    def _read(self) -> dict:
        with sqlite3.connect(str(self.state.path),timeout=10) as db:
            db.execute('CREATE TABLE IF NOT EXISTS workspace_advertising_page (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            row=db.execute('SELECT value FROM workspace_advertising_page WHERE key=?',('primary',)).fetchone()
            return json.loads(row[0]) if row else {'name':DEFAULT_PAGE_NAME,'owner_profile_id':DEFAULT_OWNER_PROFILE}

    async def get(self) -> dict:
        return await asyncio.to_thread(self._read)

    def _write(self, value: dict) -> None:
        with sqlite3.connect(str(self.state.path),timeout=10) as db:
            db.execute('INSERT OR REPLACE INTO workspace_advertising_page(key,value) VALUES (?,?)',('primary',json.dumps(value)))

    async def patch(self, **changes) -> dict:
        value={**await self.get(),**changes}
        await asyncio.to_thread(self._write,value)
        return value


async def ensure_common_page(session: Any, params: dict, state: Any, resolver: Any) -> dict:
    from .fan_pages_handler import fan_pages_handler
    from ..session import MetaSession
    async with _PAGE_LOCK:
        config=AdvertisingPageStore(state)
        page=await config.get()
        owner=str(page['owner_profile_id'])
        if not page.get('page_id'):
            known=[p for p in await state.latest_profile_fan_pages(owner)
                if str(p.get('name') or '').casefold()==page['name'].casefold()]
            if len({p['id'] for p in known})>1:
                raise ProvisioningError('COMMON_PAGE_AMBIGUOUS','Several saved Pages have the workspace advertising name')
            if known and known[0].get('main_business_confirmed') is True:
                selected=known[0]
            else:
                if session.context.profile_id==owner:
                    owner_session=session
                elif resolver is not None:
                    owner_session=MetaSession(await resolver.resolve(owner))
                else:
                    raise ProvisioningError('COMMON_PAGE_OWNER_UNAVAILABLE','The advertising Page owner profile is unavailable')
                try:
                    from .models import ProvisioningStep
                    await state.set_running('workspace-common-page-'+owner,owner,'workspace-common-page',ProvisioningStep.FAN_PAGES)
                    creation={'names':[page['name']],'count':1,'category':params.get('category') or 'Digital creator',
                         'confirm_main_business':True,'require_policy_consent':True,
                         'policies_accepted':page.get('policies_accepted') is True and page.get('policies_name')==page['name']
                             and page.get('policies_owner_profile_id')==owner}
                    if known: creation.update(mode='confirm_existing',existing_page_id=known[0]['id'],page_name=known[0]['name'])
                    result=await fan_pages_handler(owner_session,creation, {},
                        provisioning_state=state,item_id='workspace-common-page-'+owner,
                        profile_id=owner,scope_key='workspace-common-page')
                    selected=result['pages'][0]
                    await state.complete('workspace-common-page-'+owner,owner,'workspace-common-page',ProvisioningStep.FAN_PAGES,result)
                finally:
                    if owner_session is not session: await owner_session.close()
            page=await config.patch(page_id=selected['id'],name=selected['name'],
                main_business_confirmed=selected.get('main_business_confirmed') is True,
                main_business_id=str(selected.get('main_business_id') or ''))
        return {'page_ids':[page['page_id']], 'pages':[{'id':page['page_id'],'name':page['name'],
            'shared':True,'owner_profile_id':owner,'attached':False,
            'main_business_confirmed':page.get('main_business_confirmed') is True,
            'main_business_id':page.get('main_business_id') or ''}], 'count':1,'common_page':True}
