"""Authenticated production ASGI handlers + real resolver/processor/local bytes.

The storage HTTP peer is an offline stand-in for PHP, not live Meta proof.
"""
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
import httpx
from PIL import Image

import main as api
from app.media_preflight import inspect_media
from app.private_launch_media import LaunchMediaResolver


class ManagedMediaApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'files').mkdir()
        self.records = {}; self.uploads = 0
        self.resolver = LaunchMediaResolver(); self.resolver.root = self.root; self.resolver.key = 'storage-fixture-only'
        app = web.Application()
        app.router.add_post('/ajax/pythonWorkerJobs.php', self.storage)
        self.server = web.AppRunner(app); await self.server.setup()
        site = web.TCPSite(self.server, '127.0.0.1', 0); await site.start()
        self.addAsyncCleanup(self.server.cleanup)
        self.resolver.url = 'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/ajax/pythonWorkerJobs.php'
        patcher = patch('app.private_launch_media.LaunchMediaResolver', return_value=self.resolver)
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(api, 'API_KEY', 'worker-fixture-only')
        patcher.start(); self.addCleanup(patcher.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://worker.fixture',
            headers={'X-Remask-Worker-Key':'worker-fixture-only'})
        self.addAsyncCleanup(self.client.aclose)

    def register(self, raw, mime='image/png'):
        sha = hashlib.sha256(raw).hexdigest(); identity = 'm-'+sha[:24]
        path = self.root/'files'/(identity+'.bin'); path.write_bytes(raw)
        self.records[identity] = {'id':identity,'sha256':sha,'path':str(path),'size_bytes':len(raw),
            'media_type':'video' if mime.startswith('video/') else 'image','mime_type':mime}
        return self.records[identity]

    async def storage(self, request):
        if request.headers.get('X-Remask-Internal-Key') != 'storage-fixture-only':
            return web.json_response({'ok':False}, status=403)
        if request.content_type == 'application/json':
            data = await request.json()
            self.assertEqual(data['action'], 'private_launch_media_resolve')
            return web.json_response({'ok':True,'media':[self.records[x] for x in data['ids']]})
        fields = await request.post()
        self.assertEqual(fields['action'], 'private_launch_media')
        self.uploads += 1
        raw = fields['media'].file.read()
        provisional = self.root/'temporary.image'; provisional.write_bytes(raw)
        mime = inspect_media(provisional)['mime_type']; provisional.unlink()
        record = self.register(raw, mime)
        return web.json_response({'ok':True,'media':{k:v for k,v in record.items() if k!='path'}})

    def image(self):
        path = self.root/'source.png'; Image.new('RGB',(80,40),(90,30,120)).save(path,'PNG')
        return self.register(path.read_bytes())

    async def test_authentication_and_corrupt_content_fail_before_preparation(self):
        bad = self.register(b'fake image bytes')
        body = {'creative':{'format':'SINGLE','media_library_id':bad['id']}}
        response = await self.client.post('/api/v1/private-launch/media-inspect', json=body,
            headers={'X-Remask-Worker-Key':'wrong'})
        self.assertEqual(response.status_code,401)
        response = await self.client.post('/api/v1/private-launch/media-inspect', json=body)
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['detail']['code'],'MEDIA_CONTENT_INVALID')
        self.assertEqual(self.uploads,0)

    async def test_inspect_and_prepare_keep_original_and_return_verified_new_managed_identity(self):
        original = self.image(); before = Path(original['path']).read_bytes()
        response = await self.client.post('/api/v1/private-launch/media-inspect',json={
            'creative':{'format':'SINGLE','media_library_id':original['id']},
            'targeting':{'publisher_platforms':['instagram'],'instagram_positions':['story']}})
        self.assertEqual(response.status_code,200,response.text)
        check = response.json()['media'][0]
        self.assertEqual(check['inspection']['width'],80)
        self.assertFalse(check['compatibility']['warnings'][0]['meta_rejection_established'])
        response = await self.client.post('/api/v1/private-launch/media-prepare', json={
            'reference':{'id':original['id'],'sha256':original['sha256']},
            'options':{'encoding':'PNG','mode':'pad','width':80,'height':80}})
        self.assertEqual(response.status_code,200,response.text)
        prepared = response.json()
        self.assertNotEqual(prepared['media']['id'],original['id'])
        self.assertEqual(prepared['media']['id'],'m-'+prepared['media']['sha256'][:24])
        self.assertEqual((prepared['inspection']['width'],prepared['inspection']['height']),(80,80))
        self.assertEqual(Path(original['path']).read_bytes(),before)
        self.assertTrue(prepared['preparation']['original_preserved'])
        self.assertEqual(self.uploads,1)
        self.assertNotIn('path',str(prepared))

    async def test_arbitrary_filters_paths_or_network_inputs_cannot_reach_transform(self):
        original = self.image()
        for payload in [
            {'reference':{'id':'http://private.invalid/video'},'options':{}},
            {'reference':{'id':original['id']},'options':{'filter':'movie=http://private.invalid'}},
            {'reference':{'id':original['id'],'sha256':'0'*64},'options':{}}]:
            response = await self.client.post('/api/v1/private-launch/media-prepare',json=payload)
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.uploads,0)
