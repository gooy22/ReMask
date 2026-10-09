import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.contract_maintenance.payment_sources import capture_payment_sources, public_payment_modules
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
