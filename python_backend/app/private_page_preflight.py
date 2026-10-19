"""Read-only Page readiness through the selected cookie/proxy HTTP/2 session."""
from __future__ import annotations
import asyncio
import time
from .facebook_page_discovery import list_pages_via_private_graphql
from .private_auth import private_auth_error

async def private_page_preflight(session, context, profile_id):
    started=time.monotonic()
    state={'session_ready':False,'complete':False,'pages':[],'error_code':''}
    async def inspect():
        try:
            async with asyncio.timeout(25):
                web=await session.facebook_web();web.private_only=True
                bootstrap=await web.bootstrap()
                current=str((getattr(context,'cookies',{}) or {}).get('c_user') or '')
                if not current.isdigit() or str(getattr(bootstrap,'actor_id',''))!=current or not getattr(bootstrap,'fb_dtsg',''):
                    state['error_code']='SESSION_EXPIRED';return
                state['session_ready']=True
                result=await list_pages_via_private_graphql(web)
                state.update(complete=bool(result.inventory_complete),pages=list(result.pages or []))
                if not result.inventory_complete:state['error_code']='PRIVATE_PAGE_INVENTORY_INCONCLUSIVE'
        except Exception as exc:
            auth=private_auth_error(exc)
            code=getattr(auth,'code','') or getattr(exc,'code','')
            if code in {'SESSION_EXPIRED','CHECKPOINT_REQUIRED','BUSINESS_LOGIN_GATE','TWO_FACTOR_REQUIRED','FACEBOOK_TEMPORARILY_BLOCKED'}:
                state['session_ready']=False
            state['error_code']=code or ('PRIVATE_PAGE_PREFLIGHT_TIMEOUT' if isinstance(exc,TimeoutError) else 'PRIVATE_PAGE_PREFLIGHT_INCONCLUSIVE')
    proxy,inspection=await asyncio.gather(session.proxy_check(),inspect(),return_exceptions=True)
    if isinstance(proxy,BaseException):raise proxy
    if isinstance(inspection,BaseException):raise inspection
    code=state['error_code'];blocked=code in {'SESSION_EXPIRED','CHECKPOINT_REQUIRED','BUSINESS_LOGIN_GATE','TWO_FACTOR_REQUIRED','FACEBOOK_TEMPORARILY_BLOCKED'}
    pages=state['pages'] if state['complete'] else list(getattr(context,'pages',[]) or [])
    return {'ok':True,'profile_id':str(profile_id),'purpose':'fan_pages','profile_context':'ok',
        'proxy':'direct_test' if proxy.get('mode')=='DIRECT_TEST' else 'ok','network_mode':proxy.get('mode','PROXY'),
        'direct_test_until':proxy.get('expires_at',0),'proxy_exit_ip':str(proxy.get('exit_ip','')),
        'proxy_latency_ms':int(proxy.get('latency_ms',0)),'total_ms':int((time.monotonic()-started)*1000),
        'facebook_session':'private_http','facebook_session_ready':state['session_ready'],
        'auth_blocked':blocked,'auth_error_code':code if blocked else '',
        'private_pages':{'ready':state['complete'],'pages':state['pages'],'source':'private_http' if state['complete'] else '',
            'error':code,'inventory_complete':state['complete']},
        'pages':pages,'pages_count':len(pages),'saved_pages_count':len(getattr(context,'pages',[]) or []),
        'pages_source':'private_http' if state['complete'] else 'last_confirmed',
        'browser_business':{'ready':False,'browser_started':False,'session_ready':state['session_ready'],
            'transport':'private_http','create_surface_ready':False,'error_code':''},
        'bm_route_ready':False,'browser_started':False,'transport':'private_http','web_error':code,
        'graph_api':{'ready':False,'token_present':False}}
