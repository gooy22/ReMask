import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.provisioning.ad_account_handler import ad_account_handler
from app.provisioning.models import ProvisioningError


class AdAccountUnverifiedResumeTests(unittest.IsolatedAsyncioTestCase):
    async def assert_uncertain_resume_does_not_start_creation(self, checkpoint):
        saved = dict(checkpoint)
        async def save(*args):
            saved.update(args[-1])
            return dict(saved)
        store = SimpleNamespace(
            remember_entity=AsyncMock(), forget_entity=AsyncMock(),
            checkpoint=AsyncMock(side_effect=save),
            latest_ad_account_resume_for_business=AsyncMock(return_value=None),
        )
        session = SimpleNamespace(context=SimpleNamespace(profile_id='8'))
        with patch('app.provisioning.ad_account_handler._verify_expected_ad_account_in_business',
                   AsyncMock(return_value=(False, []))), \
             patch('app.provisioning.ad_account_handler._prove_empty_after_uncertainty',
                   AsyncMock(return_value=('',False,{}))) as proof, \
             patch('app.provisioning.ad_account_handler._reconcile_existing',
                   AsyncMock(side_effect=AssertionError('creation preflight was reached'))) as preflight:
            with self.assertRaises(ProvisioningError) as error:
                await ad_account_handler(session,
                    {'business_id':'1632909278268870','name':'ReMask RK 8 20261002','currency':'USD','timezone_id':1},
                    {}, profile_id='8', item_id='fixture', provisioning_state=store,
                    step_state={'result':checkpoint})
            self.assertEqual(error.exception.code, 'AD_ACCOUNT_CREATE_RESULT_UNKNOWN')
            proof.assert_awaited_once()
            preflight.assert_not_awaited()

    async def test_rejected_create_response_still_requires_independent_absence_proof(self):
        await self.assert_uncertain_resume_does_not_start_creation({
            'phase':'CREATE_RESULT_UNKNOWN','business_id':'1632909278268870',
            'create_response_ad_account_id':'act_20261002',
        })

    async def test_previously_unverified_candidate_does_not_skip_uncertainty_guard(self):
        await self.assert_uncertain_resume_does_not_start_creation({
            'phase':'CREATE_RESULT_UNVERIFIED','business_id':'1632909278268870',
            'candidate_ad_account_id':'act_20261002',
        })


if __name__ == '__main__':
    unittest.main()
