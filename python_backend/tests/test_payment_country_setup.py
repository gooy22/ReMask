import copy
import hashlib
from pathlib import Path
import unittest

from app.payment_country_setup import configure_country, DECISION_DOC, UPDATE_DOC, INITIALIZE_DOC
from app.static_payment_read import account_proof
from tests.test_payment_card_http import FakeHTTP, read_account, read_screen
from tests.test_static_payment_card import ACCOUNT, BUSINESS, PAYMENT, NODE


class CountryHTTP(FakeHTTP):
    def __init__(self):
        super().__init__()
        self.setup=read_screen()
        p=self.setup['data']['payment_account'];p['payment_legacy_account_id']=PAYMENT
        a=p['billable_account'];a['currency']='USD';a['timezone_info']={'timezone':'America/Los_Angeles'}
        a['supported_country_options']=[{'value':'US'},{'value':'UA'}]
        self.editable=True;self.decision_override=None;self.reject=False;self.foreign_reply=False;self.readback_wrong=False
    async def graphql(self,doc,variables,**kwargs):
        if doc=='28388533884149241':
            self.calls.append((doc,copy.deepcopy(variables),kwargs))
            p=copy.deepcopy(self.setup)
            if self.readback_wrong:p['data']['payment_account']['billable_account']['billable_account_tax_info']['business_country_code']='US'
            return p
        if doc==DECISION_DOC:
            self.calls.append((doc,copy.deepcopy(variables),kwargs))
            p=copy.deepcopy(self.setup)
            p['data']['payment_account']['billable_account']['billable_account_tax_info']['can_update_tax_country']=self.editable
            return self.decision_override if self.decision_override is not None else p
        if doc in {UPDATE_DOC, INITIALIZE_DOC}:
            self.calls.append((doc,copy.deepcopy(variables),kwargs))
            country=variables['input']['country_code']
            tax=self.setup['data']['payment_account']['billable_account']['billable_account_tax_info']
            tax.update(business_country_code=country,predicated_business_country_code=country)
            self.screen=copy.deepcopy(self.setup)
            p=copy.deepcopy(self.setup['data']['payment_account'])
            if self.foreign_reply:p['payment_legacy_account_id']='999888777'
            return {'data':{'billable_account_set_country_currency':{
                'client_result':{'__typename':'Failure' if self.reject else 'XFBBillableAccountSetCountryCurrencyTimezoneSuccess'},
                'payment_account':p}}}
        return await super().graphql(doc,variables,**kwargs)


class CountrySetupTests(unittest.IsolatedAsyncioTestCase):
    def fresh(self):
        web = CountryHTTP()
        account = web.setup['data']['payment_account']['billable_account']
        account['billable_account_tax_info']['business_country_code'] = None
        account['billing_page_configs'] = {'country_currency_timezone':{'can_select_tax_country':True}}
        account['supported_currency_options'] = [{'value':'USD'}]
        account['supported_timezone_options'] = [{'value':'America/Los_Angeles'}]
        account['billing_country_currency_restrictions'] = {'country_currency':[], 'currency_country':[]}
        return web

    async def test_fresh_country_uses_initial_sender_after_complete_empty_and_preserves_currency_timezone(self):
        web = self.fresh()
        result = await self.call(web)
        self.assertEqual(result['code'], 'CARD_COUNTRY_INITIALIZED_CONFIRMED')
        docs = [c[0] for c in web.calls]
        self.assertLess(docs.index('24871928132404465'), docs.index(INITIALIZE_DOC))
        self.assertNotIn(UPDATE_DOC, docs)
        mutation = next(c for c in web.calls if c[0] == INITIALIZE_DOC)
        self.assertEqual(mutation[1], {'input':{'billable_account_payment_legacy_account_id':PAYMENT,
            'country_code':'UA','currency':'USD','timezone':'America/Los_Angeles'},'paymentAccountID':PAYMENT,
            'completedTasks':['set_country_currency_timezone'],'userIntent':'ADD_PAYMENT_METHOD',
            'boostDurationInDays':None,'dailyBudget':None,'skipDeferredFragments':True})
        self.assertEqual(docs[-2:], ['28797973873175785','28388533884149241'])

    async def test_fresh_country_missing_permission_options_restrictions_or_foreign_business_never_mutates(self):
        for mode in ['permission','currency','timezone','restriction','foreign_business','existing_card']:
            web = self.fresh()
            a = web.setup['data']['payment_account']['billable_account']
            if mode == 'permission': a['billing_page_configs']['country_currency_timezone']['can_select_tax_country'] = False
            if mode == 'currency': a['supported_currency_options'] = []
            if mode == 'timezone': a['supported_timezone_options'] = []
            if mode == 'restriction': a['billing_country_currency_restrictions']['country_currency'] = [{'country':'UA','currency':'UAH'}]
            if mode == 'foreign_business': web.foreign_business = True
            if mode == 'existing_card': web.saved = True
            result = await self.call(web)
            self.assertNotIn('setup_payload', result, mode)
            self.assertNotIn(INITIALIZE_DOC, [c[0] for c in web.calls], mode)

    async def test_initial_setup_uses_explicit_supported_currency_when_meta_has_not_selected_it(self):
        web=self.fresh()
        account=web.setup['data']['payment_account']['billable_account']
        account['currency']=None
        original=web.graphql
        async def fill_currency(doc,variables,**kwargs):
            if doc==INITIALIZE_DOC:
                account['currency']=variables['input']['currency']
            return await original(doc,variables,**kwargs)
        web.graphql=fill_currency
        result=await self.call(web)
        self.assertEqual(result['code'], 'CARD_COUNTRY_INITIALIZED_CONFIRMED')
        saved=next(row for row in web.calls if row[0]==INITIALIZE_DOC)
        self.assertEqual(saved[1]['input']['currency'],'USD')

    async def test_fresh_existing_meta_currency_overrides_ui_default_without_changing_currency(self):
        # The workspace bootstraps new accounts with USD, but a real RK may
        # already have a different currency before its tax country is set.
        web=self.fresh()
        account=web.setup['data']['payment_account']['billable_account']
        account['currency']='EUR'
        account['supported_currency_options']=[{'value':'EUR'},{'value':'USD'}]
        result=await self.call(web)
        self.assertEqual(result['code'],'CARD_COUNTRY_INITIALIZED_CONFIRMED')
        mutation=next(row for row in web.calls if row[0]==INITIALIZE_DOC)
        self.assertEqual(mutation[1]['input']['currency'],'EUR')
        self.assertEqual(result['setup_payload']['data']['payment_account']['billable_account']['currency'],'EUR')

    async def test_fresh_existing_currency_restricted_by_meta_stays_blocked(self):
        web=self.fresh()
        account=web.setup['data']['payment_account']['billable_account']
        account['currency']='EUR'
        account['supported_currency_options']=[{'value':'EUR'},{'value':'USD'}]
        account['billing_country_currency_restrictions']['country_currency']=[
            {'country':'UA','currency':'USD'}]
        result=await self.call(web)
        self.assertEqual(result['code'],'CARD_BILLING_COUNTRY_MISMATCH')
        self.assertNotIn(INITIALIZE_DOC,[row[0] for row in web.calls])

    async def test_fresh_existing_currency_must_still_appear_in_meta_supported_options(self):
        web=self.fresh()
        account=web.setup['data']['payment_account']['billable_account']
        account['currency']='EUR'
        result=await self.call(web)
        self.assertEqual(result['code'],'CARD_COUNTRY_UPDATE_OPTIONS_UNCONFIRMED')
        self.assertEqual(result['setup_stage'],'currency_not_in_options')
        self.assertNotIn(INITIALIZE_DOC,[row[0] for row in web.calls])

    async def test_initial_setup_resolves_existing_timezone_from_unique_live_option_label(self):
        web=self.fresh()
        a=web.setup['data']['payment_account']['billable_account']
        a['timezone_info']={'display_name':'Kyiv time (UTC+3)'}
        a['supported_timezone_options']=[{'label':'Kyiv time (UTC+3)','value':'Europe/Kyiv'}]
        result=await self.call(web)
        self.assertEqual(result['code'],'CARD_COUNTRY_INITIALIZED_CONFIRMED')
        sent=next(row for row in web.calls if row[0]==INITIALIZE_DOC)
        self.assertEqual(sent[1]['input']['timezone'],'Europe/Kyiv')

    async def test_existing_unknown_timezone_label_stops_with_exact_safe_reason(self):
        web=self.fresh()
        a=web.setup['data']['payment_account']['billable_account']
        a['timezone_info']={'display_name':'Unidentified configured time zone'}
        result=await self.call(web)
        self.assertEqual(result['code'],'CARD_COUNTRY_UPDATE_OPTIONS_UNCONFIRMED')
        self.assertEqual(result['setup_stage'],'timezone_display_unmatched')
        self.assertNotIn(INITIALIZE_DOC,[row[0] for row in web.calls])

    async def test_initial_setup_accepts_live_kyiv_kiev_alias_without_guessing_other_zone(self):
        web=self.fresh()
        a=web.setup['data']['payment_account']['billable_account']
        a['timezone_info']={'timezone':'Europe/Kyiv'}
        a['supported_timezone_options']=[{'value':'Europe/Kiev'}]
        result=await self.call(web)
        self.assertEqual(result['code'],'CARD_COUNTRY_INITIALIZED_CONFIRMED')
        sent=next(row for row in web.calls if row[0]==INITIALIZE_DOC)
        self.assertEqual(sent[1]['input']['timezone'],'Europe/Kiev')

    async def test_fresh_country_reply_never_replaces_independent_persisted_readback(self):
        web = self.fresh(); web.readback_wrong = True
        result = await self.call(web)
        self.assertEqual(result['code'], 'CARD_COUNTRY_UPDATE_VERIFY_PENDING')
        self.assertNotIn('setup_payload', result)

    def test_current_country_documents_and_update_sender_are_pinned(self):
        hashes={
            'useBillingSetCountryCurrencyMutation':'16d3e22587a4ac4e6fcfe5ee697e0f3cd725fe40ab580363e421fa0c16419b8b',
            'useBillingSetCountryCurrencyMutation.graphql':'dca44bd1e653d5ca7df971343f128074f513ac747bed9be682f2d13b31e9033a',
            'useBillingSetCountryCurrencyMutation_facebookRelayOperation':'b2d67135b4ff171dcb7ae9e043fa855491e7ce25b2ee3f5022aff534a9028c00',
            'BillingCountryCurrencyDecisionStateQuery_facebookRelayOperation':'aff628e18b369a7a1868a4b8c73f44d08d7afd05e5b8bb3800b4f51c9135838a',
            'BillingCountryCurrencyDecisionStateQuery.graphql':'ea721a2401976c33c5133c2440b9b36f7070c9c73b55e5f2983aa9e6968895b3',
            'BillingCountryCurrencyDecisionStateSetCountryCurrencyTimezoneMutation_facebookRelayOperation':'147f5a0dead3401e5fd1d92cfdd3cf4ca33bc0d2873e5ab361f4bbd08ae1e82e',
            'BillingCountryCurrencyDecisionStateSetCountryCurrencyTimezoneMutation.graphql':'f5fc75ce7643d209e0a3eb18d92f9465828151278da475f333f1524c70524880',
            'BillingCountryCurrencyDecisionState':'4a90f79ffcc9af311986edb169e5ead4887b8a6e4ded81ac185a5e7d3514293f'}
        root=Path(__file__).parent/'fixtures'/'payment_reference'
        for name,digest in hashes.items():
            source=(root/(name+'.current.js')).read_text().rstrip('\n')
            self.assertEqual(hashlib.sha256(source.encode()).hexdigest(),digest)
        self.assertIn(DECISION_DOC,(root/'BillingCountryCurrencyDecisionStateQuery_facebookRelayOperation.current.js').read_text())
        self.assertIn(UPDATE_DOC,(root/'BillingCountryCurrencyDecisionStateSetCountryCurrencyTimezoneMutation_facebookRelayOperation.current.js').read_text())
        self.assertIn(INITIALIZE_DOC,(root/'useBillingSetCountryCurrencyMutation_facebookRelayOperation.current.js').read_text())

    async def call(self,web,setup=None):
        return await configure_country(web,target=ACCOUNT,business_id=BUSINESS,
            evidence=account_proof(read_account(),ACCOUNT),current_payload=copy.deepcopy(web.setup),
            setup=setup or {'country':'UA','country_mode':'prefer_ua','currency':'USD','timezone':'Europe/Kyiv'})

    async def test_update_requires_permission_and_independent_same_account_readback(self):
        web=CountryHTTP();result=await self.call(web)
        self.assertEqual(result['code'],'CARD_COUNTRY_UPDATED_CONFIRMED')
        docs=[c[0] for c in web.calls]
        self.assertEqual(docs,['28814526004898205',DECISION_DOC,UPDATE_DOC,'28797973873175785','28388533884149241'])
        mutation=next(c for c in web.calls if c[0]==UPDATE_DOC)
        self.assertEqual(mutation[1],{'input':{'billable_account_payment_legacy_account_id':PAYMENT,
            'country_code':'UA','currency':'USD','timezone':'America/Los_Angeles'}})
        self.assertEqual(mutation[2]['business_context_id'],BUSINESS)
        self.assertNotIn('card',str(mutation[1]));self.assertNotIn('create',str(mutation[1]))
        # A repeated action reads the already changed country, never replays the mutation.
        web.calls=[];again=await self.call(web)
        self.assertEqual(again['code'],'CARD_COUNTRY_CURRENT_PRESERVED');self.assertEqual(web.calls,[])

    async def test_locked_country_is_only_valid_prefer_ua_fallback(self):
        for mode,code in [('prefer_ua','CARD_COUNTRY_CURRENT_PRESERVED'),('strict','PAYMENT_COUNTRY_LOCKED')]:
            web=CountryHTTP();web.editable=False
            result=await self.call(web,{'country':'UA','country_mode':mode})
            self.assertEqual(result['code'],code);self.assertNotIn(UPDATE_DOC,[c[0] for c in web.calls])

    async def test_foreign_business_stops_before_country_mutation(self):
        web=CountryHTTP();web.foreign_business=True
        self.assertEqual((await self.call(web))['code'],'CARD_COUNTRY_UPDATE_SCOPE_UNVERIFIED')
        self.assertNotIn(UPDATE_DOC,[c[0] for c in web.calls])

    async def test_missing_permission_foreign_decision_or_currency_cannot_fallback(self):
        for mode in ['missing','foreign_payment','foreign_account','currency','errors']:
            web=CountryHTTP();p=copy.deepcopy(web.setup);node=p['data']['payment_account']['billable_account']
            if mode=='missing':node['billable_account_tax_info'].pop('can_update_tax_country')
            if mode=='foreign_payment':p['data']['payment_account']['id']='foreign-node'
            if mode=='foreign_account':node['id']='999888777'
            if mode=='currency':node['currency']='EUR'
            if mode=='errors':p['errors']=[{'message':'private server detail'}]
            web.decision_override=p
            result=await self.call(web)
            self.assertEqual(result['code'],'CARD_COUNTRY_UPDATE_SCOPE_UNVERIFIED')
            self.assertNotIn('setup_payload',result);self.assertNotIn(UPDATE_DOC,[c[0] for c in web.calls])

    async def test_missing_options_or_timezone_never_mutates(self):
        for mode in ['options','timezone']:
            web=CountryHTTP();node=web.setup['data']['payment_account']['billable_account']
            node.pop('supported_country_options' if mode=='options' else 'timezone_info')
            self.assertEqual((await self.call(web))['code'],'CARD_COUNTRY_UPDATE_OPTIONS_UNCONFIRMED')
            self.assertNotIn(UPDATE_DOC,[c[0] for c in web.calls])

    async def test_rejected_or_foreign_update_never_authorizes_save(self):
        for mode in ['reject','foreign_reply']:
            web=CountryHTTP();setattr(web,mode,True)
            result=await self.call(web)
            self.assertEqual(result['code'],'CARD_COUNTRY_UPDATE_RESULT_UNCONFIRMED')
            self.assertNotIn('setup_payload',result)

    async def test_update_response_alone_is_not_persisted_country_evidence(self):
        web=CountryHTTP();web.readback_wrong=True
        result=await self.call(web)
        self.assertEqual(result['code'],'CARD_COUNTRY_UPDATE_VERIFY_PENDING');self.assertNotIn('setup_payload',result)

    async def test_current_mode_and_invalid_preference_never_mutate(self):
        web=CountryHTTP()
        self.assertIn('setup_payload',await self.call(web,{'country_mode':'current'}));self.assertEqual(web.calls,[])
        result=await self.call(web,{'country':'US','country_mode':'prefer_ua'})
        self.assertEqual(result['code'],'PAYMENT_SETUP_INVALID');self.assertEqual(web.calls,[])
