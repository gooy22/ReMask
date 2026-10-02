import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.provisioning.funding_handler import funding_handler
from app.provisioning.fan_pages_handler import fan_pages_handler
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.service import ProvisioningService
from app.provisioning.state import ProvisioningStateStore


class FundingConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_verified_assignment_succeeds(self):
        transport = SimpleNamespace(funding=AsyncMock(return_value={
            'ad_account_id': 'act_333333333', 'funding_source_id': '444444444',
            'funding_verified': True}))
        result = await funding_handler(SimpleNamespace(), {'funding_source_id': '444444444'},
            {'profile_id': '7', 'ad_account_id': '333333333'}, transport=transport)
        self.assertTrue(result['funding_verified'])
        self.assertEqual(result['ad_account_id'], 'act_333333333')

    async def test_missing_or_wrong_proof_cannot_be_success(self):
        for result in [
            {'funding_source_id': '444444444'},
            {'ad_account_id': '333333333', 'funding_source_id': '444444444'},
            {'ad_account_id': '333333333', 'funding_source_id': '444444444', 'funding_verified': 'true'},
            {'ad_account_id': '999999999', 'funding_source_id': '444444444', 'funding_verified': True},
            {'ad_account_id': '333333333', 'funding_source_id': '999999999', 'funding_verified': True},
        ]:
            with self.subTest(result=result), self.assertRaises(ProvisioningError) as caught:
                await funding_handler(SimpleNamespace(), {'funding_source_id': '444444444'},
                    {'profile_id': '7', 'ad_account_id': 'act_333333333'},
                    transport=SimpleNamespace(funding=AsyncMock(return_value=result)))
            self.assertEqual(caught.exception.code, 'FUNDING_RESULT_UNVERIFIED')
            self.assertFalse(caught.exception.retryable)

    async def test_saved_scope_source_still_requires_new_job_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ProvisioningStateStore(str(Path(tmp) / 'state.sqlite'))
            await state.init()
            await state.complete('old', '7', 'scope', ProvisioningStep.AD_ACCOUNT, {'ad_account_id':'333333333'})
            await state.complete('old', '7', 'scope', ProvisioningStep.FUNDING, {'funding_source_id':'444444444'})
            transport = SimpleNamespace(funding=AsyncMock(return_value={'funding_source_id':'444444444'}))
            with patch('app.provisioning.service.get_handler', return_value=funding_handler):
                with self.assertRaises(ProvisioningError) as caught:
                    await ProvisioningService(state, transport).run(item_id='new', profile_id='7',
                        context=SimpleNamespace(), session=SimpleNamespace(),
                        payload={'scope_key':'scope', 'steps':['FUNDING'],
                                 'parameters':{'FUNDING':{'funding_source_id':'444444444'}}})
            self.assertEqual(caught.exception.code, 'FUNDING_RESULT_UNVERIFIED')
            transport.funding.assert_awaited_once()
            self.assertEqual((await state.step('new', ProvisioningStep.FUNDING))['status'], 'FAILED')

    async def test_business_page_attach_does_not_confirm_rk_advertising_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ProvisioningStateStore(str(Path(tmp) / 'state.sqlite'))
            await state.init()
            session = SimpleNamespace(context=SimpleNamespace(profile_id='7'))
            await state.set_running('page', '7', 'scope', ProvisioningStep.FAN_PAGES)
            with patch('app.provisioning.fan_pages_handler._attach_page_to_business',
                new=AsyncMock(return_value={'attached':True,'transport':'business_settings'})) as attach:
                result = await fan_pages_handler(session,
                    {'mode':'attach_existing','existing_page_id':'222222222','page_name':'Existing Page',
                     'business_id':'111111111','ad_account_id':'333333333'}, {},
                    provisioning_state=state,item_id='page',profile_id='7',scope_key='scope')
            attach.assert_awaited_once()
            self.assertEqual(result['page_ids'], ['222222222'])
            self.assertEqual(result['created_count'], 0)
            self.assertTrue(result['page_business_attached'])
            self.assertFalse(result['ad_account_page_access_verified'])
            self.assertEqual(result['attachment_scope'], 'business')

    async def test_old_same_job_success_without_proof_cannot_skip_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ProvisioningStateStore(str(Path(tmp)/'state.sqlite'))
            await state.init()
            await state.complete('old','7','scope',ProvisioningStep.AD_ACCOUNT,{'ad_account_id':'333333333'})
            await state.complete('old','7','scope',ProvisioningStep.FUNDING,{'funding_source_id':'444444444'})
            transport = SimpleNamespace(funding=AsyncMock())
            with self.assertRaises(ProvisioningError) as caught:
                await ProvisioningService(state,transport).run(item_id='old',profile_id='7',
                    context=SimpleNamespace(),session=SimpleNamespace(),payload={
                        'scope_key':'scope','steps':['FUNDING'],
                        'parameters':{'FUNDING':{'funding_source_id':'444444444'}}})
            self.assertEqual(caught.exception.code,'FUNDING_RESULT_UNVERIFIED')
            transport.funding.assert_not_awaited()

    async def test_verified_same_job_result_keeps_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ProvisioningStateStore(str(Path(tmp)/'state.sqlite'))
            await state.init()
            await state.complete('old','7','scope',ProvisioningStep.AD_ACCOUNT,{'ad_account_id':'333333333'})
            await state.complete('old','7','scope',ProvisioningStep.FUNDING,{
                'funding_source_id':'444444444','ad_account_id':'act_333333333','funding_verified':True})
            transport = SimpleNamespace(funding=AsyncMock())
            result = await ProvisioningService(state,transport).run(item_id='old',profile_id='7',
                context=SimpleNamespace(),session=SimpleNamespace(),payload={
                    'scope_key':'scope','steps':['FUNDING'],
                    'parameters':{'FUNDING':{'funding_source_id':'444444444'}}})
            self.assertTrue(result['steps'][0]['skipped'])
            transport.funding.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
