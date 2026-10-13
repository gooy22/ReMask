"""Reviewed LWI AsyncRequest preview; no Ads SDK or server-side browser."""
import json
from urllib.parse import urlencode, urlsplit

from .private_ads_draft import fail, identity
from .private_ads_http import AdsManagerResponseError
from .private_ads_media import verify_image
from .private_ads_video import verify_video


async def read_preview(client, catalog, account, inputs):
    creative = inputs.get('creative')
    format_name = inputs.get('ad_format')
    if (not isinstance(creative, dict) or not creative
            or set(creative) - set(catalog.data['object_fields']['creative'])
            or format_name not in catalog.data['form_schema']['preview_formats']):
        raise fail('CREATIVE_INVALID', 'Preview requires a reviewed format and private creative specification')
    # A preview must not disclose or borrow assets from another advertising scope.
    story = creative.get('object_story_spec')
    promotable = account.get('ad_account_promotable_objects')
    pages = promotable.get('promotable_page_ids') if isinstance(promotable, dict) else None
    page = story.get('page_id') if isinstance(story, dict) else creative.get('object_id')
    if not isinstance(pages, list) or page not in [str(value) for value in pages]:
        raise fail('PAGE_PERMISSION_UNCONFIRMED', 'Preview Page is not promotable in the selected account')
    images, videos = set(), set()
    def media_refs(value, depth=0):
        if depth > 24:
            raise fail('CREATIVE_INVALID', 'Preview specification exceeds its nesting budget')
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'image_hash','hash'} and isinstance(child, str): images.add(child)
                if key == 'video_id': videos.add(identity(child))
                media_refs(child, depth+1)
        elif isinstance(value, list):
            for child in value: media_refs(child, depth+1)
    media_refs(creative)
    if len(images)+len(videos)>10:
        raise fail('CREATIVE_INVALID', 'Preview media exceeds its asset budget')
    for selected in images:
        await verify_image(client.http, selected)
    for selected in videos:
        await verify_video(client.http, selected)
    for key, resource in [('source_instagram_media_id','instagram_media'), ('instagram_actor_id','instagram_accounts')]:
        value = creative.get(key) or (story.get(key) if isinstance(story, dict) else None)
        if value:
            from .private_ads_reads import AdsManagerReadCatalog
            result = await AdsManagerReadCatalog(catalog).read(client.web, resource,
                {'ad_account_id':client.account,'business_id':client.business,'query':'','limit':500})
            if not any(row.get('id') == value for row in result['data']):
                raise fail('ASSET_ACCESS_UNCONFIRMED', 'Preview Instagram resource belongs to another account')
    web = client.web
    bootstrap = await web.bootstrap()
    if bootstrap.actor_id != client.actor or not bootstrap.fb_dtsg:
        raise fail('ACTOR_MISMATCH', 'Preview bootstrap differs from the selected actor')
    session = await web._ensure_session()
    if not client.http._profile_matches() or session.proxy != web.profile.proxy:
        raise fail('PROFILE_CONTEXT_UNAVAILABLE', 'Preview profile changed before dispatch', True)
    if not web.profile.proxy and getattr(session,'direct_test_until',0) != client.http._direct_deadline:
        raise fail('PROFILE_CONTEXT_UNAVAILABLE', 'Preview direct-test lease differs from the initialized route', True)
    route = catalog.data['native_reads']['CREATIVE_PREVIEW']
    if route['operation_kind'] != 'read' or route['method'] != 'POST':
        raise fail('MANIFEST_INVALID', 'Preview must use its reviewed read sender')
    query = {'ad_account_id':client.account,'ad_preview_format':format_name,
             'creative_spec':json.dumps(creative,separators=(',',':'),allow_nan=False),'is_sponsored':'1'}
    origin = client.http.document_origin
    if urlsplit(origin).hostname not in {'adsmanager.facebook.com','business.facebook.com'}:
        raise fail('BOOTSTRAP_ROUTE_UNREVIEWED', 'Preview document origin is unreviewed')
    fields = {key:str(value) for key,value in (bootstrap.request_context or {}).items()
        if key in {'__hs','__hsi','__rev','__spin_r','__spin_b','__spin_t','__dyn','__csr','__ccg','dpr'}
        and str(value or '').strip()}
    fields.update({'__a':'1','__user':client.actor,'fb_dtsg':bootstrap.fb_dtsg,'__req':web._next_graphql_req()})
    if bootstrap.lsd: fields['lsd'] = bootstrap.lsd
    if bootstrap.jazoest: fields['jazoest'] = bootstrap.jazoest
    headers = {'Accept':'*/*','Origin':origin,'Referer':origin+'/',
               'Content-Type':'application/x-www-form-urlencoded','X-Requested-With':'XMLHttpRequest'}
    try:
        async with session.request('POST',origin+route['path']+'?'+urlencode(query),
                data=fields,headers=headers,allow_redirects=False,request_timeout_seconds=60) as response:
            if response.http_version != 'HTTP/2':
                raise fail('PROTOCOL_UNAVAILABLE','Preview origin did not negotiate HTTP/2',True)
            raw = await response.content.read(1_000_001)
            if response.status != 200:
                raise AdsManagerResponseError({'http_status':response.status})
            if len(raw)>1_000_000:
                raise fail('RESPONSE_TOO_LARGE','Preview response exceeds its budget')
            text = raw.decode('utf-8').strip()
            if text.startswith('for (;;);'): text = text[len('for (;;);'):]
            result = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        raise fail('RESPONSE_INVALID','Preview response could not be decoded') from None
    except Exception as error:
        from .provisioning.models import ProvisioningError
        if isinstance(error, ProvisioningError): raise
        raise fail('HTTP_UNAVAILABLE','Preview HTTP request did not return a usable response',True) from None
    if not isinstance(result, dict) or result.get('error'):
        raise AdsManagerResponseError({'code':result.get('error') if isinstance(result,dict) else None})
    payload = result.get('payload') if result.get('__ar') == 1 else result
    markup = payload.get('markup') if isinstance(payload,dict) else None
    # Transport HTML markers are data only. Never execute jsmods/onload code.
    if isinstance(markup,dict) and set(markup)=={'__html'}: markup = markup['__html']
    if not isinstance(markup,str) or not markup.strip():
        raise fail('RESOURCE_READ_INCOMPLETE','Meta did not return preview markup',True)
    return {'source':'private_lwi_preview','ad_format':format_name,'body':markup}
