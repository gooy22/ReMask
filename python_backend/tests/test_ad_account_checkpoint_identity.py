import unittest

from app.provisioning.ad_account_handler import _capture_success_id_from_checkpoint


class AdAccountCheckpointIdentityTests(unittest.TestCase):
    def extract(self, controls):
        return _capture_success_id_from_checkpoint({'browser_diagnostic':{'ui_state':{
            'dialogs':['Ad account created successfully. ReMask RK 8 20261002 has been created and added to the portfolio.'],
            'controls':controls,
        }}},business_id='1632909278268870',account_name='ReMask RK 8 20261002')

    def test_name_date_does_not_hide_the_explicit_created_id(self):
        self.assertEqual(self.extract([
            'ReMask RK 8 20261002 [tag=DIV role=heading x=693 y=173]',
            '123456789012345 [tag=A role=link x=714 y=194]',
        ]),'act_123456789012345')

    def test_success_message_without_an_explicit_id_cannot_promote_name_date(self):
        self.assertEqual(self.extract(['ReMask RK 8 20261002 [tag=DIV role=heading x=693 y=173]']), '')
