"""Carousel lifecycle checks on synthetic HTTP responses; no Meta mutations."""
import copy
import hashlib
import unittest
from unittest.mock import AsyncMock, patch

from app.private_launch_media import LocalMedia
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as launch_fixture


class CarouselClient(launch_fixture.ImageClient):
    def __init__(self):
        super().__init__()
        self.images = {}
        self.uploaded_bytes = []
        self.image_reads = []
        self.fail_before = None
        self.lose_after = None
        self.foreign_hash = None
        self.reverse_creative = False

    async def post(self, path, params, *, before_submit, files):
        if not path.endswith('/adimages'):
            return await super().post(path, params, before_submit=before_submit, files=files)
        raw = next(iter(files.values()))[1].read()
        if raw == self.fail_before:
            self.fail_before = None
            raise ConnectionError('synthetic failure before claim')
        await before_submit()
        self.uploads += 1
        self.uploaded_bytes.append(raw)
        image_hash = hashlib.sha256(raw).hexdigest()[:32]
        self.images[image_hash] = '111111'
        if raw == self.lose_after:
            raise TimeoutError('synthetic lost reply')
        return {'images': {'card.png': {'hash': image_hash}}}

    async def get(self, path, params):
        if path.endswith('/adimages'):
            selected = params['hashes'][0]
            self.image_reads.append(selected)
            return {'data': [{'hash': selected, 'account_id':
                '999999' if selected == self.foreign_hash else self.images.get(selected, '111111')}]}
        row = await super().get(path, params)
        if path == '/777774' and self.reverse_creative:
            row['object_story_spec']['link_data']['child_attachments'].reverse()
        return row


class CarouselTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await launch_fixture.LaunchTests.asyncSetUp(self)
        data = b'synthetic second image bytes'
        sha = hashlib.sha256(data).hexdigest()
        path = self.media.path.parent / ('m-' + sha[:24] + '.png')
        path.write_bytes(data)
        self.second = LocalMedia('m-' + sha[:24], sha, len(data), 'image', 'image/png', path)
        self.client = CarouselClient()
        self.payload['launch_key'] = 'carousel-launch'
        creative = self.payload['launch']['creative']
        creative.pop('media_library_id')
        creative.update({'format': 'CAROUSEL',
            'carousel_media_library_ids': [self.media.id, self.second.id],
            'carousel_cards': [{'headline': 'first', 'link': 'https://example.com/first'},
                               {'headline': 'second', 'description': 'second description', 'link': ''}]})
        self.resolver = AsyncMock(return_value=[self.media, self.second])
        for context in (
            patch('app.private_ads_launch.AdsManagerPrivateHttp.open', new=AsyncMock(return_value=self.client)),
            patch('app.private_launch_media.LaunchMediaResolver.resolve', new=self.resolver)):
            context.start()
            self.addCleanup(context.stop)

    async def launch(self):
        return await launch_fixture.LaunchTests.launch(self)

    async def test_two_cards_keep_media_text_links_and_resume_without_posts(self):
        original = copy.deepcopy(self.payload)
        result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertEqual(self.client.uploaded_bytes, [self.media.path.read_bytes(), self.second.path.read_bytes()])
        story = self.client.expected_creative['object_story_spec']
        self.assertEqual(story['page_id'], '555555')
        link = story['link_data']
        self.assertFalse(link['multi_share_optimized'])
        self.assertFalse(link['multi_share_end_card'])
        cards = link['child_attachments']
        self.assertEqual([card['name'] for card in cards], ['first', 'second'])
        self.assertEqual([card['image_hash'] for card in cards], [row.sha256[:32] for row in (self.media, self.second)])
        self.assertEqual([card['link'] for card in cards], ['https://example.com/first', 'https://example.com'])
        self.assertEqual(self.payload, original)
        await self.launch()
        self.assertEqual(self.client.uploads, 2)
        self.assertEqual(len(self.client.posts), 5)

    async def test_image_receipt_survives_interrupt_before_next_claim(self):
        self.client.fail_before = self.second.path.read_bytes()
        with self.assertRaises(ConnectionError):
            await self.launch()
        self.assertEqual(self.client.uploads, 1)
        self.assertEqual(self.client.posts, [])
        result = await self.launch()
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(self.client.uploads, 2)
        self.assertEqual(len(self.client.posts), 5)

    async def test_lost_second_upload_reply_blocks_all_replay(self):
        self.client.lose_after = self.second.path.read_bytes()
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_RECONCILE_REQUIRED')
        self.assertEqual(self.client.uploads, 2)
        self.assertEqual(self.client.posts, [])

    async def test_same_file_two_cards_uploads_once_but_keeps_both_cards(self):
        self.payload['launch']['creative']['carousel_media_library_ids'] = [self.media.id, self.media.id]
        self.resolver.return_value = [self.media, self.media]
        result = await self.launch()
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(self.client.uploads, 1)
        cards = self.client.expected_creative['object_story_spec']['link_data']['child_attachments']
        self.assertEqual([card['name'] for card in cards], ['first', 'second'])
        self.assertEqual(cards[0]['image_hash'], cards[1]['image_hash'])

    async def test_existing_hashes_review_and_create_require_exact_account_reads(self):
        creative = self.payload['launch']['creative']
        creative.pop('carousel_media_library_ids')
        for index, card in enumerate(creative['carousel_cards']):
            card['image_hash'] = 'existing-card-' + str(index)
        result = await self.owner.review(profile_id='19', context=self.context, payload=self.payload,
                                        transport=type('Transport', (), {'session': self.session})())
        self.assertTrue(result['ready'])
        self.assertEqual(self.client.image_reads, ['existing-card-0', 'existing-card-1'])
        self.assertEqual(self.client.uploads, 0)
        self.assertEqual(self.client.posts, [])
        await self.launch()
        self.assertEqual(self.client.uploads, 0)
        self.assertEqual(len(self.client.posts), 5)

    async def test_foreign_existing_image_stops_before_creating_draft(self):
        creative = self.payload['launch']['creative']
        creative.pop('carousel_media_library_ids')
        for index, card in enumerate(creative['carousel_cards']):
            card['image_hash'] = 'existing-card-' + str(index)
        self.client.foreign_hash = 'existing-card-1'
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_IMAGE_UNVERIFIED')
        self.assertEqual(self.client.posts, [])

    async def test_changed_card_order_in_published_creative_is_not_success(self):
        self.client.reverse_creative = True
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_CREATIVE_SPEC_MISMATCH')
        self.client.reverse_creative = False
        result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertEqual(self.client.uploads, 2)
        self.assertEqual(len(self.client.posts), 5)

    async def test_bad_card_input_rejects_before_bootstrap_or_upload(self):
        for kind in ('unknown', 'video', 'url', 'cta', 'card_reference', 'too_many', 'ad_auth', 'ad_nonfinite'):
            payload = copy.deepcopy(self.payload)
            creative = payload['launch']['creative']
            card = creative['carousel_cards'][-1]
            if kind == 'unknown': card['unsupported_option'] = True
            if kind == 'video': card['video_id'] = '888888'
            if kind == 'url': card['link'] = 'https://example.com:invalid/path'
            if kind == 'cta': card['call_to_action'] = {'type': 'LEARN_MORE'}
            if kind == 'card_reference': card['media_library_id'] = self.media.id
            if kind == 'too_many': creative['carousel_cards'] *= 6
            if kind == 'ad_auth': payload['launch']['ad']['access_token'] = 'synthetic-secret'
            if kind == 'ad_nonfinite': payload['launch']['ad']['bid_amount'] = float('nan')
            with self.subTest(kind=kind), self.assertRaises(ProvisioningError):
                await self.owner.run(item_id='fixture', profile_id='19', context=self.context,
                                     session=self.session, payload=payload)
        self.session.facebook_web.assert_not_awaited()
        self.assertEqual(self.client.uploads, 0)
        self.assertEqual(self.client.posts, [])

    async def test_resolver_reorder_or_omission_stops_before_bootstrap(self):
        for rows in ([self.second, self.media], [self.media]):
            self.resolver.return_value = rows
            with self.assertRaises(ProvisioningError):
                await self.launch()
        self.session.facebook_web.assert_not_awaited()
        self.assertEqual(self.client.uploads, 0)

    async def test_offline_plan_reports_carousel_without_claiming_ready(self):
        result = self.owner.plan(profile_id='19', payload=self.payload)
        self.assertEqual(result['creative_format'], 'CAROUSEL')
        self.assertFalse(result['ready'])
        self.assertEqual(result['mutation_posts'], 0)
        self.resolver.assert_not_awaited()


