import unittest

from app.facebook_business_browser import _extract_inventory_ad_account_rows, _inventory_response_business_scope


class RkInventoryEntityScopeTests(unittest.TestCase):
    def ids(self, payload, scoped=False):
        return [row['id'] for row in _extract_inventory_ad_account_rows(payload, request_scoped=scoped)]

    def test_scoped_rk_asset_does_not_turn_nested_business_page_or_user_into_accounts(self):
        payload={'data':{'business_assets':{'nodes':[
            {'asset_type':'AD_ACCOUNT','asset_id':'120251332838140263','account_id':'1569487661117197','name':'Rashed Chowdhury RK',
             'business':{'__typename':'Business','id':'1760742031708754','name':'Rashed Chowdhury'},
             'owner':{'__typename':'User','id':'61594897075733'}, 'page':{'__typename':'Page','id':'1354440067752617'}},
            {'__typename':'Business','id':'1632909278268870','name':'ReMask 8'},
            {'__typename':'Page','id':'1354440067752617'},
        ]}}}
        self.assertEqual(self.ids(payload,True),['act_1569487661117197'])

    def test_untyped_descendants_of_explicit_rk_collections_do_not_inherit_rk_type(self):
        payload={'data':{'business':{'ad_accounts':{'edges':[{'node':{'id':'1569487661117197','name':'RK',
            'business':{'id':'1760742031708754'},'owner':{'id':'61594897075733'},'primary_page':{'id':'1354440067752617'}}}]}}}}
        self.assertEqual(self.ids(payload),['act_1569487661117197'])

    def test_only_explicitly_scoped_generic_asset_rows_are_accepted(self):
        payload={'data':{'assets':{'nodes':[{'id':'1569487661117197','name':'RK','object':{'id':'1569487661117197'},'business':{'id':'1760742031708754'}}]}}}
        self.assertEqual(self.ids(payload),[])
        self.assertEqual(self.ids(payload,True),['act_1569487661117197'])

    def test_a_business_id_cannot_also_be_its_ad_account_id(self):
        self.assertEqual(self.ids({'ad_accounts':[{'id':'1760742031708754','business_id':'1760742031708754'}]}),[])

    def test_a_connection_id_is_not_an_ad_account(self):
        self.assertEqual(self.ids({'__typename':'AdAccountConnection','id':'987654321','edges':[]}),[])

    def test_background_payload_needs_business_relation_independent_of_page_url(self):
        self.assertFalse(_inventory_response_business_scope('1760742031708754',set(),set()))
        self.assertFalse(_inventory_response_business_scope('1760742031708754',{'1632909278268870'},{'1760742031708754'}))
        self.assertFalse(_inventory_response_business_scope('1760742031708754',set(),{'1632909278268870','1760742031708754'}))
        self.assertTrue(_inventory_response_business_scope('1760742031708754',{'1760742031708754'},set()))
        self.assertTrue(_inventory_response_business_scope('1760742031708754',set(),{'1760742031708754'}))
