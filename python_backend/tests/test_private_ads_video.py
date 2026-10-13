"""Serial upload/resume cases on synthetic bytes and local SQLite only."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from app.private_ads_state import AdsManagerSubmitter
from app.private_ads_video import upload_video
from app.private_launch import PrivateLaunchStateStore
from app.private_launch_media import LocalMedia
from app.provisioning.models import ProvisioningError


class VideoClient:
    context = SimpleNamespace(account_id='111111')

    def __init__(self):
        self.posts, self.chunks = [], []
        self.ready = True
        self.lose_transfer = False

    async def post(self, path, params, *, before_submit, files=None, multipart=False):
        await before_submit()
        self.posts.append((path, dict(params), multipart))
        phase = params['upload_phase']
        if phase == 'start':
            return {'upload_session_id': '777771', 'video_id': '777772', 'start_offset': '0', 'end_offset': '4'}
        if phase == 'transfer':
            self.chunks.append(files['video_file_chunk'][1])
            if self.lose_transfer:
                self.lose_transfer = False
                raise ConnectionError('synthetic lost acknowledgement')
            end = int(params['start_offset']) + 4
            return {'start_offset': str(end), 'end_offset': str(min(end + 4, 8))}
        return {'video_id': '777772'}

    async def get(self, path, params):
        if path == '/act_111111/advideos':
            return {'data': [{'id': '777772'}]}
        return {'id': '777772', 'status': {'video_status': 'ready' if self.ready else 'processing'},
                'thumbnails': {'data': [{'uri': 'https://example.invalid/video.jpg', 'is_preferred': True}]}}


class VideoCases(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        path = root / 'synthetic.mp4'
        raw = b'abcdefgh'
        path.write_bytes(raw)
        self.media = LocalMedia('m-' + hashlib.sha256(raw).hexdigest()[:24], hashlib.sha256(raw).hexdigest(),
                                len(raw), 'video', 'video/mp4', path)
        state = PrivateLaunchStateStore(str(root / 'journal.sqlite'))
        await state.init()
        self.sender = AdsManagerSubmitter(state, launch_key='video-fixture', profile_id='19',
            business_id='222222', account_id='111111', actor_id='123456',
            launch_fingerprint='synthetic-fingerprint', source_digest='synthetic-source')
        self.client = VideoClient()

    async def test_encoding_pending_resumes_saved_finish_without_reupload(self):
        self.client.ready = False
        with self.assertRaises(ProvisioningError) as caught:
            await upload_video(self.client, self.sender, self.media)
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VIDEO_NOT_READY')
        self.assertEqual(b''.join(self.client.chunks), b'abcdefgh')
        self.assertEqual(len(self.client.posts), 4)
        self.client.ready = True
        proof = await upload_video(self.client, self.sender, self.media)
        self.assertTrue(proof['verified'])
        self.assertEqual(len(self.client.posts), 4)
        self.assertTrue(self.client.posts[0][2])
        self.assertTrue(self.client.posts[-1][2])

    async def test_lost_chunk_acknowledgement_cannot_be_replayed(self):
        self.client.lose_transfer = True
        with self.assertRaises(ProvisioningError):
            await upload_video(self.client, self.sender, self.media)
        with self.assertRaises(ProvisioningError) as caught:
            await upload_video(self.client, self.sender, self.media)
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_RECONCILE_REQUIRED')
        self.assertEqual(len(self.client.posts), 2)
