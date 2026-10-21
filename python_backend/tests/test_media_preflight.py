"""Genuine local files; external Meta is simulated only in sender tests."""
import asyncio
from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from app.media_preflight import compatibility, inspect_media, inspect_managed
from app.private_launch_media import LaunchMediaResolver
from app.media_preparation import prepare_file
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as launch_fixture


class MediaInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def image(self, encoding='PNG', size=(64, 48), name='wrong-extension.bin'):
        path = self.root / name
        Image.new('RGBA' if encoding == 'PNG' else 'RGB', size, (60, 120, 180)).save(path, encoding)
        return path

    def test_png_and_jpeg_decode_actual_bytes_despite_filename(self):
        for encoding, mime in [('PNG', 'image/png'), ('JPEG', 'image/jpeg')]:
            facts = inspect_media(self.image(encoding), expected_kind='image', declared_mime=mime)
            self.assertEqual((facts['width'], facts['height']), (64, 48))
            self.assertTrue(facts['integrity_verified'])
            self.assertFalse(facts['meta_acceptance_verified'])
            self.assertEqual(facts['has_transparency'], encoding == 'PNG')

    def test_mime_and_kind_mismatch_are_distinct_from_corruption(self):
        path = self.image()
        for kwargs, code in [({'declared_mime':'image/jpeg'}, 'MEDIA_MIME_MISMATCH'),
                             ({'expected_kind':'video'}, 'MEDIA_TYPE_MISMATCH')]:
            with self.assertRaises(ProvisioningError) as caught:
                inspect_media(path, **kwargs)
            self.assertEqual(caught.exception.code, code)
        path.write_bytes(b'not a PNG')
        with self.assertRaises(ProvisioningError) as caught:
            inspect_media(path, declared_mime='image/png')
        self.assertEqual(caught.exception.code, 'MEDIA_CONTENT_INVALID')

    def test_truncated_image_and_excessive_dimensions_do_not_decode(self):
        path = self.image()
        path.write_bytes(path.read_bytes()[:40])
        with self.assertRaises(ProvisioningError): inspect_media(path)
        path = self.image(size=(64,48))
        with patch('app.media_preflight.MAX_IMAGE_PIXELS', 64):
            with self.assertRaises(ProvisioningError) as caught: inspect_media(path)
        self.assertEqual(caught.exception.code, 'MEDIA_LOCAL_DIMENSION_LIMIT')

    def test_orientation_and_conversion_requirements(self):
        path = self.root/'rotated.jpg'
        exif = Image.Exif(); exif[274] = 6
        Image.new('RGB', (80, 40)).save(path, 'JPEG', exif=exif)
        facts = inspect_media(path)
        self.assertEqual((facts['width'], facts['height'], facts['orientation']), (40,80,6))
        facts = inspect_media(self.image('WEBP'))
        self.assertTrue(facts['requires_conversion'])
        with self.assertRaises(ProvisioningError) as caught: compatibility(facts, {})
        self.assertEqual(caught.exception.code, 'MEDIA_PREPARATION_REQUIRED')

    def test_placement_warning_preserves_every_selection_and_is_not_rejection(self):
        facts = inspect_media(self.image())
        targeting = {'publisher_platforms':['facebook','instagram'],
                     'facebook_positions':['feed','story','facebook_reels'],
                     'instagram_positions':['stream','story','reels']}
        before = repr(targeting)
        result = compatibility(facts, targeting)
        self.assertTrue(result['locally_valid'])
        self.assertEqual(len(result['warnings']), 6)
        self.assertTrue(all(not w['meta_rejection_established'] for w in result['warnings']))
        self.assertEqual(repr(targeting), before)

    def video(self, codec='libx264', container='mp4'):
        path = self.root/('actual.'+container)
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
            'color=c=blue:s=64x48:r=10:d=1','-c:v',codec,'-pix_fmt','yuv420p',
            '-threads','1','-y',str(path)], check=True, timeout=20)
        return path

    def test_real_mp4_container_decoding_and_stream_metadata(self):
        facts = inspect_media(self.video(), expected_kind='video', declared_mime='video/mp4')
        self.assertEqual((facts['video_codec'],facts['width'],facts['height']), ('h264',64,48))
        self.assertEqual(facts['duration_seconds'], 1)
        self.assertEqual(facts['frame_rate'], 10)
        self.assertIsNone(facts['audio_codec'])
        self.assertFalse(facts['requires_conversion'])

    def test_unreviewed_codec_requires_preparation_and_corrupt_video_fails(self):
        path = self.video('mpeg4')
        facts = inspect_media(path)
        self.assertTrue(facts['requires_conversion'])
        with self.assertRaises(ProvisioningError): compatibility(facts, {})
        path.write_bytes(path.read_bytes()[:40])
        with self.assertRaises(ProvisioningError) as caught: inspect_media(path)
        self.assertEqual(caught.exception.code, 'MEDIA_CONTENT_INVALID')

    def test_missing_analyzer_fails_closed_before_upload(self):
        path = self.video()
        with patch('app.media_preflight.shutil.which', return_value=None):
            with self.assertRaises(ProvisioningError) as caught: inspect_media(path)
        self.assertEqual(caught.exception.code, 'MEDIA_INSPECTION_UNAVAILABLE')

    def test_managed_resolver_checks_bytes_and_not_only_trusted_metadata(self):
        files = self.root/'files'; files.mkdir()
        raw = b'pretend image bytes'
        sha = hashlib.sha256(raw).hexdigest(); identity = 'm-'+sha[:24]
        path = files/(identity+'.png'); path.write_bytes(raw)
        resolver = LaunchMediaResolver(); resolver.root = self.root
        row = {'id':identity,'path':str(path),'sha256':sha,'size_bytes':len(raw),
               'media_type':'image','mime_type':'image/png'}
        with self.assertRaises(ProvisioningError) as caught: resolver._validate(row)
        self.assertEqual(caught.exception.code, 'MEDIA_CONTENT_INVALID')

    def test_explicit_image_resize_crop_pad_and_checksums_preserve_original(self):
        source = self.image(size=(120,80))
        original = source.read_bytes()
        for mode, size in [('fit',(60,40)),('crop',(60,60)),('pad',(60,60))]:
            output = self.root/(mode+'.png')
            facts = prepare_file(source, output, {'width':60,'height':60,'mode':mode,'encoding':'PNG'})
            self.assertEqual((facts['width'],facts['height']), size)
            self.assertEqual(source.read_bytes(), original)
            self.assertNotEqual(hashlib.sha256(output.read_bytes()).hexdigest(), hashlib.sha256(original).hexdigest())
        with Image.open(self.root/'pad.png') as image:
            self.assertEqual(image.getpixel((0,0)), (255,255,255,255))

    def test_fit_never_upscales_and_crop_requires_explicit_valid_dimensions(self):
        source = self.image(size=(64,48)); output = self.root/'prepared.png'
        facts = prepare_file(source,output,{'width':1080,'height':1080})
        self.assertEqual((facts['width'],facts['height']), (64,48))
        with self.assertRaises(ProvisioningError) as caught:
            prepare_file(source,output,{'width':1080,'height':1080,'mode':'crop'})
        self.assertEqual(caught.exception.code, 'MEDIA_UPSCALING_REQUIRED')
        with self.assertRaises(ProvisioningError) as caught: prepare_file(source,source,{})
        self.assertEqual(caught.exception.code, 'MEDIA_ORIGINAL_PROTECTED')

    def test_webp_conversion_and_jpeg_output_are_decodable_without_original_exif(self):
        source = self.image('WEBP')
        output = self.root/'converted.jpg'
        facts = prepare_file(source,output,{'encoding':'JPEG','width':32,'height':32,'quality':80})
        self.assertEqual(facts['mime_type'],'image/jpeg')
        self.assertFalse(facts['requires_conversion'])
        with Image.open(output) as image: self.assertEqual(dict(image.getexif()), {})

    def test_actual_video_codec_conversion_retains_duration_without_overwriting_original(self):
        source = self.video('mpeg4'); before = source.read_bytes(); output = self.root/'prepared.mp4'
        facts = prepare_file(source,output,{'encoding':'MP4','width':32,'height':32,'mode':'fit'})
        self.assertEqual(facts['video_codec'], 'h264')
        self.assertEqual((facts['width'],facts['height']), (32,24))
        self.assertAlmostEqual(facts['duration_seconds'],1, places=1)
        self.assertEqual(source.read_bytes(),before)
        self.assertFalse(facts['requires_conversion'])


class NativeMediaPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = launch_fixture.LaunchTests('test_existing_launch_entrypoint_uploads_image_publishes_and_verifies')
        self.case.addCleanup = self.addCleanup
        await self.case.asyncSetUp()
        # Restore the actual production inspector in these genuine-file tests.
        patcher = patch('app.media_preflight.inspect_managed', side_effect=inspect_managed)
        patcher.start(); self.addCleanup(patcher.stop)

    async def test_corrupt_file_stops_before_session_bootstrap_or_any_remote_post(self):
        with self.assertRaises(ProvisioningError) as caught: await self.case.launch()
        self.assertEqual(caught.exception.code, 'MEDIA_CONTENT_INVALID')
        self.case.session.facebook_web.assert_not_awaited()
        self.assertEqual(self.case.client.posts, [])

    async def test_valid_png_passes_existing_native_draft_and_same_identity_on_resume(self):
        case = self.case
        Image.new('RGB', (64,48), (20,40,60)).save(case.media.path, 'PNG')
        raw = case.media.path.read_bytes(); sha = hashlib.sha256(raw).hexdigest()
        case.media = replace(case.media, id='m-'+sha[:24], sha256=sha, size_bytes=len(raw))
        case.payload['launch']['creative']['media_library_id'] = case.media.id
        case.payload['launch']['execution_mode'] = 'DRAFT'
        with patch('app.private_launch_media.LaunchMediaResolver.resolve', new=AsyncMock(return_value=[case.media])):
            result = await case.launch()
            self.assertTrue(result['draft_verified'])
            self.assertEqual(case.client.uploads, 1)
            await case.launch()
            self.assertEqual(case.client.uploads, 1)
