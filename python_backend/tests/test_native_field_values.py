"""Reviewed UI enums are executable constraints, not arbitrary sender strings."""
import copy
import unittest
from unittest.mock import AsyncMock, patch

from app.private_ads_catalog import AdsManagerRouteCatalog
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixtures


class NativeFieldValueTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchTests.asyncSetUp
    launch = fixtures.LaunchTests.launch

    async def test_unknown_placement_and_invalid_primitive_values_stop_before_media_or_meta(self):
        base = copy.deepcopy(self.payload)
        cases = [('targeting','publisher_platforms',['not_a_platform']),
                 ('targeting','publisher_platforms','facebook'),
                 ('targeting','instagram_positions',['not_a_position']),
                 ('targeting','age_min',True),('targeting','age_max','65'),
                 ('campaign','buying_type','UNKNOWN_BUYING'),
                 ('campaign','special_ad_categories',['MADE_UP_CATEGORY']),
                 ('adset','is_dynamic_creative','false'),('ad','priority',True)]
        with patch('app.private_launch_media.LaunchMediaResolver.resolve',new=AsyncMock()) as media:
            for group,field,value in cases:
                self.payload=copy.deepcopy(base)
                section=self.payload['launch']['adset']['targeting'] if group=='targeting' else self.payload['launch'][group]
                section[field]=value
                with self.subTest(group=group,field=field,value=value), self.assertRaises(ProvisioningError):
                    await self.launch()
            media.assert_not_awaited()
        self.session.facebook_web.assert_not_awaited()
        self.assertEqual(self.client.posts,[])

    async def test_reviewed_values_and_nested_selections_are_never_coerced_or_replaced(self):
        launch = self.payload['launch']
        launch['adset']['targeting'].update(publisher_platforms=['facebook','instagram'],
            facebook_positions=['feed','story'],instagram_positions=['stream','story'],
            device_platforms=['mobile'],age_min=23,age_max=33,
            exclusions={'interests':[{'id':'123456','name':'Preserve'}]},
            flexible_spec=[{'interests':[{'id':'234567'}],'behaviors':[{'id':'345678'}]}],
            locales=[52,1000])
        launch['adset']['is_dynamic_creative']=False
        launch['ad']['priority']=2
        before = copy.deepcopy(launch)
        AdsManagerRouteCatalog().validate_values(launch)
        self.assertEqual(launch,before)
        result=await self.launch()
        self.assertTrue(result['verified'])
        values=next(row for row in self.client.fragments.values() if row['ad_object_type']=='ad_set')['values']
        targeting=next(row['new_value'] for row in values if row['field']=='targeting')
        self.assertEqual(targeting, before['adset']['targeting'])
