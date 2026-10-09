import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.contract_maintenance.payment_sources import (capture_payment_sources, public_payment_modules,
                                                    payment_deferred_script_urls, source_export)
from app.provisioning.models import ProvisioningError


class PaymentSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_maintenance_reads_missing_sources_after_legacy_script_128(self):
        # Regression: the original 128-script ceiling hid the card input builder
        # and PTT implementation in a later, public JS chunk.
        from app.contract_maintenance.payment_sources import MAX_SCRIPTS
        self.assertGreaterEqual(MAX_SCRIPTS, 387)
        entry = 'https://business.facebook.com/billing_hub/payment_settings/'
        html = ''.join(
            '<script src="https://static.xx.fbcdn.net/%03d.js"></script>' % i
            for i in range(170)
        )
        async def fetch(url, **kwargs):
            if url.startswith('https://business.facebook.com/'):
                return 200, html, entry
            index = int(url.rsplit('/', 1)[-1][:-3])
            name = ('BillingCreditCardUtils' if index == 168 else
                    'getPTTUtils' if index == 169 else
                    'BillingDecoy%03d' % index)
            return 200, '__d("' + name + '",[],function(){});', url
        web = SimpleNamespace(fetch_text=AsyncMock(side_effect=fetch))
        result = await capture_payment_sources(web, account_id='123456789', business_id='987654321')
        self.assertEqual(result['scripts_read'], 170)
        self.assertEqual(result['scripts_not_read'], 0)
        self.assertFalse(result['script_limit_reached'])
        self.assertFalse(result['byte_limit_reached'])
        self.assertNotIn('BillingCreditCardUtils', result['missing_required_sources'])
        self.assertNotIn('getPTTUtils', result['missing_required_sources'])

    def test_current_card_screen_and_input_builder_are_required_instead_of_legacy_page_query(self):
        from pathlib import Path
        from app.contract_maintenance.payment_sources import REQUIRED_SOURCE_MODULES
        source = Path(__file__).with_name('fixtures').joinpath('meta_payment_save_observed_20261009.js').read_text()
        exported = source_export(public_payment_modules(source))
        for name in ('BillingAddCreditCardScreenQuery.graphql', 'BillingAddCreditCardState',
                     'BillingSaveCardCredentialStateMutation.graphql', 'BillingSaveCardCredentialState'):
            self.assertIn(name, REQUIRED_SOURCE_MODULES)
            self.assertNotIn(name, exported['missing_required_sources'])
        self.assertIn('BillingCreditCardUtils', exported['missing_required_sources'])
        self.assertIn('getPTTUtils', exported['missing_required_sources'])
        self.assertNotIn('BillingAddCreditCardPageViewManagerQuery.graphql', REQUIRED_SOURCE_MODULES)

    def test_critical_input_and_tokenization_sources_survive_a_budget_full_of_unrelated_artifacts(self):
        critical = public_payment_modules('__d("BillingCreditCardUtils",[],function(){throw new Error("do not execute")});'
            '__d("getPTTUtils",[],function(){throw new Error("do not execute")});')
        others = public_payment_modules('__d("BillingUnrelatedQuery.graphql",[],function(){return "unrelated"});')
        budget = sum(len(row['source'].encode()) for row in critical)
        result = source_export(others + critical, max_bytes=budget)
        self.assertEqual({row['name'] for row in result['modules']}, {'BillingCreditCardUtils', 'getPTTUtils'})
        self.assertTrue(result['export_truncated'])

    async def test_actual_card_screen_read_supplies_lazy_dependencies_only_after_exact_rk_bm_proofs(self):
        from unittest.mock import patch
        from app.contract_maintenance.payment_sources import inspect_profile_payment_sources
        from tests.test_payment_static_methods import account_response, methods_response, ACCOUNT, BM, PAYMENT, NODE
        options = {'data': {'payment_account': {'payment_legacy_account_id': PAYMENT,
            'billable_account': {'__typename': 'AdAccount', 'id': ACCOUNT}}}}
        screen = {'data': {'payment_account': {'id': NODE,
            'billable_account': {'__typename': 'AdAccount', 'id': ACCOUNT},
            'billing_payment_method_options': [{'__typename': 'AdAccountNewCreditCardOption',
                'check_make_default': True, 'can_save_to_business': False, 'verify_tokenization_required': True}]},
            'viewer': {'primary_email': 'fixture-sensitive'}},
            'extensions': {'rsrcMap': {'builder': {'type': 'js', 'src': 'https://static.xx.fbcdn.net/builder.js'}},
                'compMap': {'BillingCreditCardUtils': {'r': ['builder']}}}}
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def facebook_web(self): return web
            async def facebook_business_browser(self): raise AssertionError('No browser')
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(cookies={'c_user': '111222333'})))
        for failure in ('none', 'business', 'payment', 'card_scope', 'card_query', 'card_options'):
            with self.subTest(failure=failure):
                import copy
                methods, card = methods_response(), copy.deepcopy(screen)
                if failure == 'business': methods['data']['billable_account_by_asset_id']['owning_business']['id'] = '111222333'
                if failure == 'payment': methods['data']['billable_account_by_asset_id']['billing_payment_account']['id'] = 'foreign'
                if failure == 'card_scope': card['data']['payment_account']['billable_account']['id'] = BM
                if failure == 'card_query': card['errors'] = [{'message': 'fixture-sensitive'}]
                if failure == 'card_options': card['data']['payment_account']['billing_payment_method_options'] = []
                web = SimpleNamespace(graphql=AsyncMock(return_value=card))
                async def execute(web, operation, **kwargs):
                    return {'READ_ACCOUNT': account_response(), 'READ_METHODS': methods, 'READ_OPTIONS': options}[operation]
                with patch('app.session.ProfileSession', return_value=Session()), \
                     patch('app.payment_inspection.resolve_payment_asset', AsyncMock(return_value={'business_id': BM})), \
                     patch('app.static_payment_read.execute', AsyncMock(side_effect=execute)), \
                     patch('app.contract_maintenance.payment_sources.capture_payment_sources', AsyncMock(return_value={'submitted': False})) as capture:
                    result = await inspect_profile_payment_sources(resolver, 'fixture', ACCOUNT, state=None)
                self.assertEqual(web.graphql.await_args.args, ('27759194723782263',
                    {'paymentAccountID': PAYMENT, 'country': None, 'currency': None, 'intent': None}))
                self.assertEqual(web.graphql.await_args.kwargs['business_context_id'], BM)
                documents = capture.await_args.kwargs['loader_documents']
                accepted = failure in ('none', 'card_options')
                self.assertEqual(bool(documents), accepted)
                self.assertEqual(result['payment_card_screen_probe']['loader_maps_accepted'], accepted)
                self.assertFalse(result['submitted']); self.assertNotIn('fixture-sensitive', json.dumps(result))
                if accepted:
                    self.assertEqual(payment_deferred_script_urls('\n'.join(documents)), ['https://static.xx.fbcdn.net/builder.js'])

    async def test_document_network_retry_is_bounded_and_preserves_target(self):
        import httpx
        from app.contract_maintenance.payment_sources import payment_source_document
        entry='https://business.facebook.com/billing_hub/payment_settings/'
        web=SimpleNamespace(fetch_text=AsyncMock(side_effect=[httpx.ReadTimeout('fixture-secret'),(200,'ok',entry)]))
        self.assertEqual(await payment_source_document(web,entry),(200,'ok',entry))
        self.assertEqual(web.fetch_text.await_count,2)
        self.assertTrue(all(call.args==(entry,) for call in web.fetch_text.await_args_list))
        web.fetch_text=AsyncMock(side_effect=httpx.ReadTimeout('fixture-secret'))
        with self.assertLogs('remask.payment_maintenance',level='INFO') as logs:
            with self.assertRaises(ProvisioningError) as raised:
                await payment_source_document(web,entry)
        self.assertEqual(web.fetch_text.await_count,2)
        self.assertNotIn('fixture-secret',str(raised.exception)+' '.join(logs.output))
        self.assertEqual(raised.exception.code,'PAYMENT_CONTRACT_SOURCE_TIMEOUT')

    async def test_document_auth_failure_is_not_retried_or_exposed(self):
        from fb_worker import AuthenticationError
        import httpx
        from app.contract_maintenance.payment_sources import payment_source_document
        error=AuthenticationError('fixture-secret')
        error.__cause__=httpx.ReadTimeout('fixture-secret')
        web=SimpleNamespace(fetch_text=AsyncMock(side_effect=error))
        with self.assertLogs('remask.payment_maintenance',level='WARNING') as logs:
            with self.assertRaises(ProvisioningError) as raised:
                await payment_source_document(web,'https://business.facebook.com/billing_hub/payment_settings/')
        self.assertEqual(web.fetch_text.await_count,1)
        self.assertEqual(raised.exception.code,'SESSION_EXPIRED')
        self.assertNotIn('fixture-secret',str(raised.exception)+' '.join(logs.output))

    async def test_only_public_modules_are_exported_never_authenticated_html(self):
        entry = 'https://business.facebook.com/billing_hub/payment_settings/?asset_id=123456789&business_id=987654321'
        html = '<script>__d("BillingInlineSecret",[],function(){var fb_dtsg="fixture-secret";});</script><script src="https://static.xx.fbcdn.net/payment.js"></script>'
        js = '__d("BillingCardMutation.graphql",[],function(){var artifact={params:{id:"123456789",name:"BillingCardMutation",operationKind:"mutation"}};});__d("OtherModule",[],function(){});'
        web = SimpleNamespace(profile=SimpleNamespace(name='fixture'), fetch_text=AsyncMock(side_effect=[(200, html, entry), (200, js, 'https://static.xx.fbcdn.net/payment.js')]))
        result = await capture_payment_sources(web, account_id='123456789', business_id='987654321')
        self.assertEqual(result['status'], 'SOURCE_EVIDENCE')
        self.assertEqual([r['name'] for r in result['modules']], ['BillingCardMutation.graphql'])
        self.assertNotIn('fixture-secret', json.dumps(result))
        self.assertFalse(result['contract_verified']); self.assertFalse(result['submitted'])
        self.assertFalse(result['browser_started']); self.assertEqual(web.fetch_text.await_count, 2)

    async def test_auth_gate_stops_before_reading_cdn(self):
        web = SimpleNamespace(fetch_text=AsyncMock(return_value=(200, '<form id="login_form"></form>', 'https://business.facebook.com/login/')))
        with self.assertRaises(ProvisioningError):
            await capture_payment_sources(web, account_id='123456789', business_id='987654321')
        self.assertEqual(web.fetch_text.await_count, 1)

    async def test_foreign_or_nonbilling_document_does_not_export(self):
        for final in ('https://business.facebook.com/latest/home', 'https://example.com/billing_hub/'):
            with self.subTest(final=final):
                web = SimpleNamespace(fetch_text=AsyncMock(return_value=(200, '<script src="https://static.xx.fbcdn.net/a.js"></script>', final)))
                with self.assertRaises(ProvisioningError):
                    await capture_payment_sources(web, account_id='123456789', business_id='987654321')
                self.assertEqual(web.fetch_text.await_count, 1)

    async def test_cdn_failure_or_foreign_redirect_is_inconclusive_not_a_verified_contract(self):
        entry = 'https://business.facebook.com/billing_hub/payment_settings/'
        html = '<script src="https://static.xx.fbcdn.net/payment.js"></script><script src="https://facebook.com.evil.test/x.js"></script>'
        web = SimpleNamespace(profile=SimpleNamespace(name='fixture'), fetch_text=AsyncMock(side_effect=[(200, html, entry), (200, '__d("PaymentMutation",[],function(){});', 'https://example.com/payment.js')]))
        result = await capture_payment_sources(web, account_id='123456789', business_id='987654321')
        self.assertEqual(result['status'], 'INCONCLUSIVE'); self.assertEqual(result['modules'], [])
        self.assertEqual(result['script_errors'], 1); self.assertEqual(web.fetch_text.await_count, 2)

    async def test_invalid_identity_does_not_dispatch(self):
        web = SimpleNamespace(fetch_text=AsyncMock())
        with self.assertRaises(ValueError):
            await capture_payment_sources(web, account_id='act_123456789', business_id='987654321')
        with self.assertRaises(ValueError):
            await capture_payment_sources(web, account_id='123456789', business_id='987654321', payment_account_id='invalid')
        web.fetch_text.assert_not_awaited()

    async def test_verified_payment_details_document_supplies_deferred_card_sources_without_inline_values(self):
        entry = 'https://business.facebook.com/billing_hub/payment_settings/?asset_id=123456789&business_id=987654321'
        details = 'https://adsmanager.facebook.com/adsmanager/billing_hub/accounts/details?asset_id=555666777&business_id=987654321'
        maps = {'rsrcMap': {'save': {'type': 'js', 'src': 'https://static.xx.fbcdn.net/save.js'}},
                'compMap': {'BillingAddCreditCardPage.react': {'r': [], 'rdfds': {'r': ['save']}}}}
        document = '<input name="card" value="4111111111111111"><script>var token="fixture-secret";</script>' + json.dumps(maps)
        web = SimpleNamespace(fetch_text=AsyncMock(side_effect=[
            (200, '', entry), (200, document, details),
            (200, '__d("BillingSaveCardCredentialStateMutation.graphql",[],function(){});', 'https://static.xx.fbcdn.net/save.js')]))
        result = await capture_payment_sources(web, account_id='123456789', business_id='987654321', payment_account_id='555666777')
        self.assertEqual(web.fetch_text.await_args_list[1].args, (details,))
        self.assertTrue(web.fetch_text.await_args_list[1].kwargs['document_navigation'])
        self.assertNotIn('document_navigation', web.fetch_text.await_args_list[2].kwargs)
        self.assertEqual([row['name'] for row in result['modules']], ['BillingSaveCardCredentialStateMutation.graphql'])
        self.assertEqual(result['document_audit'], [
            {'host': 'business.facebook.com', 'status': 'READ'}, {'host': 'adsmanager.facebook.com', 'status': 'READ'}])
        for secret in ('4111111111111111', 'fixture-secret', '<input', '?asset_id='):
            self.assertNotIn(secret, json.dumps(result))
        self.assertFalse(result['submitted']); self.assertFalse(result['contract_verified'])

    async def test_details_auth_or_foreign_scope_preserves_primary_evidence_and_rejects_secondary_sources(self):
        entry = 'https://business.facebook.com/billing_hub/payment_settings/'
        details = 'https://adsmanager.facebook.com/adsmanager/billing_hub/accounts/details?asset_id=555666777&business_id=987654321'
        for location in ('https://business.facebook.com/login/',
                         details.replace('555666777', '111222333'),
                         details.replace('987654321', '111222333'),
                         details + '&asset_id=111222333',
                         details.replace('adsmanager.facebook.com/', 'example.com/')):
            with self.subTest(location=location):
                web = SimpleNamespace(fetch_text=AsyncMock(side_effect=[
                    (200, '<script src="https://static.xx.fbcdn.net/base.js"></script>', entry),
                    (200, '<script src="https://static.xx.fbcdn.net/foreign.js"></script>', location),
                    (200, '__d("BillingBaseModule",[],function(){});', 'https://static.xx.fbcdn.net/base.js')]))
                result = await capture_payment_sources(web, account_id='123456789', business_id='987654321', payment_account_id='555666777')
                self.assertEqual([row['name'] for row in result['modules']], ['BillingBaseModule'])
                self.assertEqual(result['document_audit'][1]['status'], 'UNAVAILABLE')
                self.assertNotIn('https://static.xx.fbcdn.net/foreign.js', [call.args[0] for call in web.fetch_text.await_args_list])
                self.assertFalse(result['submitted'])

    async def test_details_timeout_preserves_budget_for_primary_cdn_capture(self):
        import asyncio
        from unittest.mock import patch
        entry = 'https://business.facebook.com/billing_hub/payment_settings/'
        async def fetch(url, **kwargs):
            if url.startswith('https://adsmanager.facebook.com/'):
                await asyncio.Event().wait()
            if url.startswith('https://business.facebook.com/'):
                return 200, '<script src="https://static.xx.fbcdn.net/base.js"></script>', entry
            return 200, '__d("BillingBaseModule",[],function(){});', url
        web = SimpleNamespace(fetch_text=AsyncMock(side_effect=fetch))
        with patch('app.contract_maintenance.payment_sources.OPTIONAL_DOCUMENT_TIMEOUT', 0.01), \
             self.assertLogs('remask.payment_maintenance', level='WARNING'):
            result = await capture_payment_sources(web, account_id='123456789', business_id='987654321', payment_account_id='555666777')
        self.assertEqual(result['status'], 'SOURCE_EVIDENCE')
        self.assertEqual(result['document_audit'][1]['code'], 'PAYMENT_CONTRACT_SOURCE_TIMEOUT')
        self.assertEqual(web.fetch_text.await_count, 3)

    def test_factories_are_parsed_as_data_and_conflicting_sources_kept(self):
        source = '__d("PaymentMutation",[],function(){throw new Error("must not execute")});__d("PaymentMutation",[],function(){return 1});'
        result = public_payment_modules(source)
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]['sha256'], result[1]['sha256'])

    def test_public_loader_runtime_is_retained_without_executing_or_exporting_unrelated_code(self):
        source='__d("BootloaderEndpoint",[],function(){throw new Error("must not execute")});__d("UnrelatedModule",[],function(){});'
        self.assertEqual([row['name'] for row in public_payment_modules(source)],['BootloaderEndpoint'])

    def test_observed_ptt_and_fbpay_sources_and_resource_maps_are_not_filtered_out(self):
        source='__d("modularGeneratePTT",[],function(){throw new Error("must not execute")});__d("FBPayAuthLibraryCommon",[],function(){});__d("PlatformTrustTokenUPLLogger",[],function(){});__d("UnrelatedModule",[],function(){});'
        self.assertEqual([row['name'] for row in public_payment_modules(source)],
                         ['modularGeneratePTT','FBPayAuthLibraryCommon','PlatformTrustTokenUPLLogger'])
        maps={'rsrcMap':{'ptt':{'type':'js','src':'https://static.xx.fbcdn.net/ptt.js'},
                         'fbpay':{'type':'js','src':'https://static.xx.fbcdn.net/fbpay.js'}},
              'compMap':{'modularGeneratePTT':{'r':['ptt']},'FBPayAuthLibraryCommon':{'r':['fbpay']}}}
        self.assertEqual(payment_deferred_script_urls(json.dumps(maps)),
                         ['https://static.xx.fbcdn.net/ptt.js','https://static.xx.fbcdn.net/fbpay.js'])
        exported=source_export(public_payment_modules(source))
        self.assertNotIn('modularGeneratePTT',exported['missing_required_sources'])
        self.assertNotIn('FBPayAuthLibraryCommon',exported['missing_required_sources'])

    def test_deferred_loader_uses_only_observed_public_js_resources_for_payment_components(self):
        maps = {'rsrcMap': {
            'card': {'type': 'js', 'src': 'https://static.xx.fbcdn.net/card.js'},
            'style': {'type': 'css', 'src': 'https://static.xx.fbcdn.net/style.css'},
            'foreign': {'type': 'js', 'src': 'https://facebook.com.evil.test/secret.js'},
            'port': {'type': 'js', 'src': 'https://static.xx.fbcdn.net:8443/secret.js'},
            'auth': {'type': 'js', 'src': 'https://secret@static.xx.fbcdn.net/card.js'},
            'other': {'type': 'js', 'src': 'https://static.xx.fbcdn.net/other.js'},
        }, 'compMap': {
            'BillingAddPaymentMethodRoot.react': {'r': ['card', 'style', 'foreign', 'port', 'auth']},
            'UnrelatedRoot.react': {'r': ['other']},
        }}
        result = payment_deferred_script_urls('<script type="application/json">' + json.dumps(maps) + '</script>')
        self.assertEqual(result, ['https://static.xx.fbcdn.net/card.js'])

    def test_conflicting_resource_or_component_mapping_is_rejected(self):
        first = {'rsrcMap': {'x': {'type': 'js', 'src': 'https://static.xx.fbcdn.net/a.js'}},
                 'compMap': {'BillingAddPaymentMethodRoot.react': {'r': ['x']}}}
        second = {'rsrcMap': {'x': {'type': 'js', 'src': 'https://static.xx.fbcdn.net/b.js'}}}
        self.assertEqual(payment_deferred_script_urls(json.dumps(first) + json.dumps(second)), [])
        second = {'compMap': {'BillingAddPaymentMethodRoot.react': {'r': []}}}
        self.assertEqual(payment_deferred_script_urls(json.dumps(first) + json.dumps(second)), [])

    def test_card_loader_follows_both_deferred_tiers_and_transitive_modules(self):
        maps = {'rsrcMap': {
            name: {'type': 'js', 'src': 'https://static.xx.fbcdn.net/' + name + '.js'}
            for name in ('ui', 'save', 'crypto', 'dependency', 'unused')
        }, 'compMap': {
            'BillingAddCreditCardPage.react': {'r': ['ui'], 'rdfds': {'r': ['save'], 'm': ['CardDependency']},
                                             'rds': {'r': ['crypto'], 'm': ['getPTTUtils']}},
            'CardDependency': {'r': ['dependency'], 'rds': {'m': ['BillingAddCreditCardPage.react']}},
            'getPTTUtils': {'r': ['crypto']},
            'UnrelatedRoot': {'r': ['unused']},
        }}
        self.assertEqual(payment_deferred_script_urls(json.dumps(maps)), [
            'https://static.xx.fbcdn.net/' + name + '.js' for name in ('save', 'crypto', 'ui', 'dependency')])
        bad = {'compMap': {'CardDependency': {'r': ['unused']}}}
        self.assertNotIn('https://static.xx.fbcdn.net/dependency.js',
                         payment_deferred_script_urls(json.dumps(maps) + json.dumps(bad)))

    async def test_save_contract_in_deferred_tier_is_captured_before_eager_budget(self):
        from unittest.mock import patch
        entry = 'https://business.facebook.com/billing_hub/payment_settings/'
        maps = {'rsrcMap': {name: {'type': 'js', 'src': 'https://static.xx.fbcdn.net/' + name + '.js'} for name in ('save', 'ui')},
                'compMap': {'BillingAddCreditCardPage.react': {'r': ['ui'], 'rds': {'r': ['save']}}}}
        document = json.dumps(maps) + '<script src="https://static.xx.fbcdn.net/eager.js"></script>'
        async def fetch(url, **kwargs):
            if url.startswith('https://business.facebook.com/'):
                return 200, document, entry
            return 200, '__d("BillingSaveCardCredentialStateMutation.graphql",[],function(){});' + \
                '__d("getPTTUtils",[],function(){});', url
        web = SimpleNamespace(profile=SimpleNamespace(name='fixture'), fetch_text=AsyncMock(side_effect=fetch))
        with patch('app.contract_maintenance.payment_sources.MAX_SCRIPTS', 1):
            result = await capture_payment_sources(web, account_id='123456789', business_id='987654321')
        self.assertEqual(web.fetch_text.await_args_list[1].args[0], 'https://static.xx.fbcdn.net/save.js')
        self.assertEqual({row['name'] for row in result['modules']},
                         {'BillingSaveCardCredentialStateMutation.graphql', 'getPTTUtils'})
        self.assertNotIn('getPTTUtils', result['missing_required_sources'])
        self.assertIn('BillingAddCreditCardScreenQuery.graphql', result['missing_required_sources'])
        self.assertTrue(result['script_limit_reached'])
        self.assertFalse(result['submitted']); self.assertFalse(result['contract_verified'])

    def test_truncation_is_explicit_and_artifact_wins_over_generic_ui_module(self):
        rows = [{'name': 'BillingGenericUI', 'source': 'x' * 25},
                {'name': 'BillingSaveCardCredentialStateMutation.graphql', 'source': 'y' * 25}]
        result = source_export(rows, max_bytes=30)
        self.assertEqual([m['name'] for m in result['modules']], ['BillingSaveCardCredentialStateMutation.graphql'])
        self.assertTrue(result['export_truncated']); self.assertEqual(result['module_count_total'], 2)
        self.assertIn('BillingHubPaymentSettingsPaymentMethodsListQuery.graphql', result['missing_required_sources'])

    async def test_deferred_card_script_is_read_before_eager_bundles(self):
        entry = 'https://business.facebook.com/billing_hub/payment_settings/'
        maps = {'rsrcMap': {'x': {'type': 'js', 'src': 'https://static.xx.fbcdn.net/card.js'}},
                'compMap': {'BillingAddPaymentMethodRoot.react': {'r': ['x']}}}
        html = '<script>' + json.dumps(maps) + '</script><script src="https://static.xx.fbcdn.net/generic.js"></script>'
        async def fetch(url, **kwargs):
            if url.startswith('https://business.facebook.com/'):
                return 200, html, entry
            return 200, '__d("BillingSaveCardCredentialStateMutation.graphql",[],function(){});', url
        web = SimpleNamespace(profile=SimpleNamespace(name='fixture'), fetch_text=AsyncMock(side_effect=fetch))
        result = await capture_payment_sources(web, account_id='123456789', business_id='987654321')
        self.assertEqual(web.fetch_text.await_args_list[1].args[0], 'https://static.xx.fbcdn.net/card.js')
        self.assertEqual(result['deferred_scripts_observed'], 1); self.assertEqual(result['scripts_not_read'], 0)
        self.assertFalse(result['export_truncated']); self.assertFalse(result['contract_verified'])

    def test_loader_extensions_extract_only_literal_maps_not_payment_data(self):
        from app.contract_maintenance.payment_sources import payment_loader_documents
        maps={'rsrcMap':{'x':{'type':'js','src':'https://static.xx.fbcdn.net/card.js'}},
              'compMap':{'BillingAddCreditCardPage.react':{'r':['x']}}}
        payload={'data':{'pan':'PRIVATE CARD',**maps},'extensions':{'sr_payload':json.dumps(maps)}}
        documents=payment_loader_documents(payload)
        self.assertEqual(payment_deferred_script_urls('\n'.join(documents)),['https://static.xx.fbcdn.net/card.js'])
        self.assertNotIn('PRIVATE CARD','\n'.join(documents))
        self.assertEqual(payment_loader_documents({'data':maps}),[])

    async def test_nested_public_loader_map_is_read_before_remaining_eager_scripts_and_cycle_is_bounded(self):
        entry='https://business.facebook.com/billing_hub/payment_settings/'
        maps={'rsrcMap':{'x':{'type':'js','src':'https://static.xx.fbcdn.net/card.js'}},
              'compMap':{'BillingAddCreditCardPage.react':{'r':['x']}}}
        html=''.join('<script src="https://static.xx.fbcdn.net/'+name+'.js"></script>'
                     for name in ('root', *(f'eager{i}' for i in range(1, 12)), 'remaining'))
        async def fetch(url,**kwargs):
            if url.startswith('https://business.facebook.com/'):return 200,html,entry
            if url.endswith('root.js'):return 200,json.dumps(maps),url
            return 200,json.dumps(maps)+'__d("BillingCardModule",[],function(){});',url
        web=SimpleNamespace(profile=SimpleNamespace(name='fixture'),fetch_text=AsyncMock(side_effect=fetch))
        result=await capture_payment_sources(web,account_id='123456789',business_id='987654321')
        urls=[c.args[0] for c in web.fetch_text.await_args_list]
        self.assertEqual(urls.count('https://static.xx.fbcdn.net/card.js'),1)
        self.assertLess(urls.index('https://static.xx.fbcdn.net/card.js'),urls.index('https://static.xx.fbcdn.net/remaining.js'))
        self.assertEqual(result['scripts_read'],14);self.assertEqual(result['scripts_not_read'],0)
        self.assertFalse(result['browser_started']);self.assertFalse(result['submitted'])

    async def test_script_budget_omissions_are_explicit(self):
        from unittest.mock import patch
        entry='https://business.facebook.com/billing_hub/payment_settings/'
        html=''.join('<script src="https://static.xx.fbcdn.net/'+name+'.js"></script>' for name in ('a','b','c'))
        async def fetch(url,**kwargs):
            if url.startswith('https://business.facebook.com/'):return 200,html,entry
            return 200,'__d("BillingModule",[],function(){});',url
        web=SimpleNamespace(profile=SimpleNamespace(name='fixture'),fetch_text=AsyncMock(side_effect=fetch))
        with patch('app.contract_maintenance.payment_sources.MAX_SCRIPTS',2):
            result=await capture_payment_sources(web,account_id='123456789',business_id='987654321')
        self.assertEqual(result['scripts_observed'],3);self.assertEqual(result['scripts_not_read'],1)
        self.assertTrue(result['script_limit_reached'])

    async def test_maintenance_uses_pinned_read_probes_and_passes_only_verified_loader_maps(self):
        from unittest.mock import patch
        from app.contract_maintenance.payment_sources import inspect_profile_payment_sources
        from tests.test_payment_static_methods import account_response,methods_response,ACCOUNT,BM,PAYMENT
        maps={'rsrcMap':{'x':{'type':'js','src':'https://static.xx.fbcdn.net/card.js'}},
              'compMap':{'BillingAddCreditCardPage.react':{'r':['x']}}}
        options={'data':{'payment_account':{'payment_legacy_account_id':PAYMENT,
                    'billable_account':{'__typename':'AdAccount','id':ACCOUNT}}},'extensions':maps}
        web=object()
        class Session:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return None
            async def facebook_web(self):return web
            async def facebook_business_browser(self):raise AssertionError('No browser')
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(cookies={'c_user':'111222333'})))
        async def execute(web,operation,**kwargs):
            return {'READ_ACCOUNT':account_response(),'READ_METHODS':methods_response(),'READ_OPTIONS':options}[operation]
        with patch('app.session.ProfileSession',return_value=Session()), \
             patch('app.payment_inspection.resolve_payment_asset',AsyncMock(return_value={'business_id':BM})), \
             patch('app.static_payment_read.execute',AsyncMock(side_effect=execute)) as query, \
             patch('app.contract_maintenance.payment_sources.capture_payment_sources',AsyncMock(return_value={'submitted':False})) as capture:
            result=await inspect_profile_payment_sources(resolver,'fixture',ACCOUNT,state=None)
        self.assertEqual({c.args[1] for c in query.await_args_list},{'READ_ACCOUNT','READ_METHODS','READ_OPTIONS'})
        self.assertTrue(result['payment_methods_probe']['card_linked'])
        self.assertTrue(result['payment_options_probe']['account_scope_verified'])
        self.assertFalse(result['submitted'])
        documents=capture.await_args.kwargs['loader_documents']
        self.assertEqual(payment_deferred_script_urls('\n'.join(documents)),['https://static.xx.fbcdn.net/card.js'])
        self.assertEqual(capture.await_args.kwargs['payment_account_id'], PAYMENT)

    async def test_details_route_requires_confirmed_business_and_payment_relation(self):
        from unittest.mock import patch
        from app.contract_maintenance.payment_sources import inspect_profile_payment_sources
        from tests.test_payment_static_methods import account_response, methods_response, ACCOUNT, BM
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def facebook_web(self): return object()
            async def facebook_business_browser(self): raise AssertionError('No browser')
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(cookies={'c_user': '111222333'})))
        for failure in ('business', 'payment', 'query'):
            payload = methods_response()
            if failure == 'business':
                payload['data']['billable_account_by_asset_id']['owning_business']['id'] = '111222333'
            elif failure == 'payment':
                payload['data']['billable_account_by_asset_id']['billing_payment_account']['id'] = 'foreign'
            else:
                payload['errors'] = [{'message': 'fixture-secret'}]
            async def execute(web, operation, **kwargs):
                return {'READ_ACCOUNT': account_response(), 'READ_METHODS': payload, 'READ_OPTIONS': {}}[operation]
            with self.subTest(failure=failure), \
                 patch('app.session.ProfileSession', return_value=Session()), \
                 patch('app.payment_inspection.resolve_payment_asset', AsyncMock(return_value={'business_id': BM})), \
                 patch('app.static_payment_read.execute', AsyncMock(side_effect=execute)), \
                 patch('app.contract_maintenance.payment_sources.capture_payment_sources', AsyncMock(return_value={'submitted': False})) as capture:
                result = await inspect_profile_payment_sources(resolver, 'fixture', ACCOUNT, state=None)
                self.assertIsNone(capture.await_args.kwargs['payment_account_id'])
                self.assertFalse(result['submitted'])
                self.assertNotIn('fixture-secret', json.dumps(result))
