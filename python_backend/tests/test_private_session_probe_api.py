import unittest
from unittest.mock import AsyncMock, patch

import httpx
import main as api
from app.provisioning.models import ProvisioningError
from app.session import ProfileContextError


class SessionProbeApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_key_is_required_before_context_lookup(self):
        with patch.object(api, 'API_KEY', 'fixture-worker-key'), \
                patch.object(api, '_resolve_read_profile', new=AsyncMock()) as resolve:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://fixture') as client:
                response = await client.post('/api/v1/profiles/4/private-session-probe', json={})
            self.assertEqual(response.status_code, 401)
            resolve.assert_not_awaited()

    async def test_only_validated_probe_result_is_returned(self):
        context = object()
        result = {'response_received':True, 'persisted':False, 'objects_created':False}
        with patch.object(api, 'API_KEY', 'fixture-worker-key'), \
                patch.object(api, '_resolve_read_profile', new=AsyncMock(return_value=context)) as resolve, \
                patch('app.private_session_probe.probe_document', new=AsyncMock(return_value=result)) as probe:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://fixture',
                    headers={'X-Remask-Worker-Key':'fixture-worker-key'}) as client:
                response = await client.post('/api/v1/profiles/4/private-session-probe', json={'cookies':{'xs':'fixture'}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'ok':True, **result})
            resolve.assert_awaited_once_with('4')
            probe.assert_awaited_once_with(context, {'cookies':{'xs':'fixture'}})

    async def test_validation_error_is_fixed_and_cannot_queue_a_job(self):
        error = ProvisioningError('PRIVATE_SESSION_PROBE_ACTOR_MISMATCH', 'Fixed validation message', retryable=False)
        with patch.object(api, 'API_KEY', 'fixture-worker-key'), \
                patch.object(api, '_resolve_read_profile', new=AsyncMock(return_value=object())), \
                patch('app.private_session_probe.probe_document', new=AsyncMock(side_effect=error)), \
                patch.object(api.store, 'create_job', new=AsyncMock()) as create:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://fixture',
                    headers={'X-Remask-Worker-Key':'fixture-worker-key'}) as client:
                response = await client.post('/api/v1/profiles/4/private-session-probe', json={})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['detail']['code'], 'PRIVATE_SESSION_PROBE_ACTOR_MISMATCH')
            create.assert_not_awaited()

    async def test_page_access_preserves_explicit_meta_login_gate(self):
        error = ProfileContextError(
            "Saved Facebook session was explicitly invalidated by Meta.",
            retryable=False,
            category="business_login_gate",
        )
        with patch.object(api, 'API_KEY', 'fixture-worker-key'), \
                patch('app.private_launch_read.inspect_profile_pages_http', new=AsyncMock(side_effect=error)), \
                patch.object(api.pool.provisioning_state, 'latest_profile_fan_pages',
                             new=AsyncMock(return_value=[])):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://fixture',
                    headers={'X-Remask-Worker-Key':'fixture-worker-key'}) as client:
                response = await client.get('/api/v1/profiles/15/page-access',
                    params={'account_id':'120249392950240046'})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result['diagnostic']['code'], 'BUSINESS_LOGIN_GATE')
        self.assertFalse(result['checked_live'])

    async def test_payment_read_preserves_explicit_meta_login_gate(self):
        error = ProfileContextError(
            "Saved Facebook session was explicitly invalidated by Meta.",
            retryable=False,
            category="business_login_gate",
        )
        with patch.object(api, 'API_KEY', 'fixture-worker-key'), \
                patch('app.payment_inspection.inspect_profile_payment_methods',
                      new=AsyncMock(side_effect=error)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://fixture',
                    headers={'X-Remask-Worker-Key':'fixture-worker-key'}) as client:
                response = await client.get('/api/v1/profiles/15/payment-methods',
                    params={'account_id':'120249392950240046'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['detail'], 'BUSINESS_LOGIN_GATE')
