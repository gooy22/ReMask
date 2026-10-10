import copy
import unittest
from unittest.mock import AsyncMock

from app.payment_card_business_wallet import (
    DOC_ID, FRIENDLY_NAME, business_wallet_card_stage, inspect_business_wallet_card,
)

ACCOUNT='120251439661740682'
BUSINESS='1653201116239354'
SAVED={'payment_account_id':'551199228800', 'payment_account_node_id':'child-payment-node',
       'credential':{'id':'save-card-relay-node','credential_id':'save-card-credential',
                     'type':'Visa','last4':'0574'}}


def wallet(*, linked=False):
    card={'__typename':'ExternalCreditCard','id':'save-card-relay-node',
          'credential_id':'save-card-credential','card_association_name':'VISA',
          'last_four_digits':'0574'}
    if linked:
        card['linked_ad_accounts']={'nodes':[{'id':ACCOUNT}]}
    return {'data':{'business':{'id':BUSINESS,'billing_payment_account':{
        'id':'parent-business-payment-node',
        'billing_payment_methods':[{'credential':card}]}}}}


class BusinessWalletCardTests(unittest.IsolatedAsyncioTestCase):
    def stage(self, payload, saved=None):
        return business_wallet_card_stage(payload, account=ACCOUNT, business_id=BUSINESS,
                                          saved=saved or SAVED)

    def test_exact_business_wallet_card_is_not_declared_linked(self):
        self.assertEqual(self.stage(wallet()),'business_wallet_card_saved_not_attached_to_rk')

    def test_business_parent_reports_child_relationship_but_rk_not_verified(self):
        self.assertEqual(self.stage(wallet(linked=True)),
                         'business_wallet_reports_rk_link_but_rk_methods_missing')

    def test_foreign_business_never_confirms_card(self):
        p=wallet();p['data']['business']['id']='foreign'
        self.assertEqual(self.stage(p),'business_wallet_scope_unverified')
        self.assertEqual(self.stage({'errors':[{'message':'sensitive'}]}),
                         'business_wallet_scope_unverified')

    def test_parent_and_child_must_be_distinct_payment_nodes(self):
        p=wallet();p['data']['business']['billing_payment_account']['id']='child-payment-node'
        self.assertEqual(self.stage(p),'business_wallet_same_as_rk')

    def test_wrong_card_brand_or_last_four_never_confirmed(self):
        for k,value in (('card_association_name','Mastercard'),('last_four_digits','9112'),
                        ('__typename','PaymentPaypalBillingAgreement')):
            with self.subTest(field=k):
                p=wallet();p['data']['business']['billing_payment_account']['billing_payment_methods'][0]['credential'][k]=value
                self.assertEqual(self.stage(p),'business_wallet_card_metadata_unverified')

    def test_different_credential_same_mask_is_not_the_saved_card(self):
        p=wallet()
        card=p['data']['business']['billing_payment_account']['billing_payment_methods'][0]['credential']
        card['id']='other-card';card['credential_id']='other-credential'
        self.assertEqual(self.stage(p),'business_wallet_card_not_observed')

    def test_duplicate_matches_are_ambiguous(self):
        p=wallet()
        rows=p['data']['business']['billing_payment_account']['billing_payment_methods']
        rows.append(copy.deepcopy(rows[0]))
        self.assertEqual(self.stage(p),'business_wallet_card_ambiguous')

    def test_missing_or_unbounded_list_is_unverified(self):
        for rows in (None, {}, [{}]*501):
            with self.subTest(rows=type(rows).__name__):
                p=wallet();p['data']['business']['billing_payment_account']['billing_payment_methods']=rows
                self.assertEqual(self.stage(p),'business_wallet_methods_unavailable')

    async def test_query_scoped_to_exact_business_and_no_card_save(self):
        class Web:
            private_only=False
            graphql=AsyncMock(return_value=wallet())
        web=Web()
        stage=await inspect_business_wallet_card(web,account=ACCOUNT,business_id=BUSINESS,saved=SAVED)
        self.assertEqual(stage,'business_wallet_card_saved_not_attached_to_rk')
        self.assertTrue(web.private_only)
        web.graphql.assert_awaited_once()
        args,kwargs=web.graphql.await_args
        self.assertEqual(args[0],DOC_ID)
        self.assertEqual(kwargs['friendly_name'],FRIENDLY_NAME)
        self.assertEqual(kwargs['business_context_id'],BUSINESS)
        self.assertEqual(args[1]['businessID'],BUSINESS)
        self.assertEqual(args[1]['assetID'],ACCOUNT)
        self.assertEqual(args[1]['paymentAccountID'],SAVED['payment_account_id'])
        self.assertTrue(args[1]['preloadPaymentAccount'])
        self.assertEqual(args[1]['billable_account_types'],['FB_ADS'])
        self.assertNotIn('save-card-credential',str(web.graphql.await_args))
