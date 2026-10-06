import tempfile
import unittest
from pathlib import Path

from app.provisioning.state import ProvisioningStateStore
from app.provisioning.models import ProvisioningStep


class AdAccountCheckpointNameHintsTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_and_not_submitted_names_remain_hints_without_confirmed_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ProvisioningStateStore(str(Path(directory)/"state.sqlite3"))
            await store.init()
            for item,profile,business,name in (
                ("old","fixture-new","1109354271000001","Old RK name"),
                ("new","fixture-new","1109354271000001","Amber Studio fixture Ads"),
                ("other","fixture-other","1109354271000001","Other profile RK"),
                ("bad","fixture-new","not-a-business","Ignored"),
            ):
                await store.set_running(item,profile,item,ProvisioningStep.AD_ACCOUNT)
                await store.checkpoint(item,profile,item,ProvisioningStep.AD_ACCOUNT,{
                    "phase":"CREATE_NOT_SUBMITTED","business_id":business,"account_name":name})
            self.assertEqual(await store.ad_account_name_hints_for_profile("fixture-new"),
                             {"1109354271000001":"Amber Studio fixture Ads"})
            self.assertEqual(await store.confirmed_ad_account_bindings_for_profile("fixture-new"),[])
            self.assertEqual(await store.ad_account_name_hints_for_profile("missing"),{})
