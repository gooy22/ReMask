import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.contract_maintenance.payment_sources import (capture_payment_sources, public_payment_modules,
                                                    payment_deferred_script_urls, source_export)
from app.provisioning.models import ProvisioningError


class PaymentSourceTests(unittest.IsolatedAsyncioTestCase):
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
        web.fetch_text.assert_not_awaited()

    def test_factories_are_parsed_as_data_and_conflicting_sources_kept(self):
        source = '__d("PaymentMutation",[],function(){throw new Error("must not execute")});__d("PaymentMutation",[],function(){return 1});'
        result = public_payment_modules(source)
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]['sha256'], result[1]['sha256'])

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
