"""Offline rejection cases for changing delivery of saved advertising objects."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from app.private_ads_delivery import _chain, run_delivery
from app.provisioning.models import ProvisioningError


class DeliveryCases(unittest.IsolatedAsyncioTestCase):
    async def test_unverified_source_cannot_open_session_or_submit_delivery(self):
        state = SimpleNamespace(init=AsyncMock(), step=AsyncMock(return_value=None))
        launch = SimpleNamespace(owner=SimpleNamespace(state=state), source_digest='synthetic-source')
        session = SimpleNamespace(facebook_web=AsyncMock())
        with self.assertRaises(ProvisioningError):
            await run_delivery(launch, profile_id='19', context=SimpleNamespace(cookies={'c_user': '123456'}),
                session=session, payload={'source_launch_key': 'source', 'delivery_key': 'request', 'status': 'ACTIVE',
                    'business_id': '222222', 'ad_account_id': '111111', 'page_id': '555555'})
        session.facebook_web.assert_not_awaited()

    async def test_reparented_or_foreign_creative_cannot_become_owned_delivery(self):
        entities = {'campaign_id': '777771', 'adset_id': '777772', 'ad_id': '777773', 'creative_id': '777774'}
        rows = {'777771': {'id': '777771', 'account_id': '111111', 'status': 'PAUSED'},
            '777772': {'id': '777772', 'account_id': '111111', 'status': 'PAUSED', 'campaign_id': '777771'},
            '777773': {'id': '777773', 'account_id': '111111', 'status': 'PAUSED', 'campaign_id': '777771',
                       'adset_id': '777772', 'creative': {'id': '777774'}},
            '777774': {'id': '777774', 'account_id': '111111', 'object_story_spec': {'page_id': '555555'}}}
        active_rows = copy.deepcopy(rows)
        async def get(path, params):
            return copy.deepcopy(active_rows[path[1:]])
        client = SimpleNamespace(context=SimpleNamespace(account_id='111111'), get=get)
        await _chain(client, entities, '555555')
        for entity, field, value in [('777772', 'campaign_id', '999999'),
                                      ('777774', 'account_id', '999999'),
                                      ('777774', 'object_story_spec', {'page_id': '999999'})]:
            active_rows = copy.deepcopy(rows)
            active_rows[entity][field] = value
            with self.subTest(field=field), self.assertRaises(ProvisioningError):
                await _chain(client, entities, '555555')
