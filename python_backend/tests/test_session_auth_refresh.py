import asyncio
import ast
from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.provisioning.models import ProvisioningError
from app.session import ProfileContextError
from app.session_auth_refresh import refresh_saved_auth_context, read_with_saved_auth_refresh


class SavedAuthRefreshTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_meta_login_gate_has_a_distinct_machine_code(self):
        error = ProfileContextError(
            "Saved Facebook session was explicitly invalidated by Meta.",
            retryable=False,
            category="business_login_gate",
        )
        self.assertEqual(error.code, "BUSINESS_LOGIN_GATE")

    def test_other_profile_context_failures_keep_the_generic_code(self):
        error = ProfileContextError("resolver unavailable", category="resolver_http")
        self.assertEqual(error.code, "PROFILE_CONTEXT_ERROR")

    def test_unclassified_auth_gate_has_a_specific_machine_code(self):
        error = ProfileContextError(
            "Meta returned authentication gate (SOME_GATE) during read-only revalidation.",
            category="auth_gate_unclassified",
        )
        self.assertEqual(error.code, "META_AUTH_GATE_UNCLASSIFIED")
        self.assertIn("(SOME_GATE)", str(error))

    def context(self, *, uid="123456789", xs="fixture-old", profile="14"):
        return SimpleNamespace(profile_id=profile, cookies={"c_user": uid, "xs": xs},
                               proxy="http://fixture-proxy:80", user_agent="Fixture")

    async def test_reloads_a_new_saved_session_for_the_same_facebook_identity(self):
        context=self.context()
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=self.context(xs="fixture-fresh")))
        self.assertTrue(await refresh_saved_auth_context(resolver,context))
        self.assertEqual(context.cookies["xs"],"fixture-fresh")
        resolver.resolve.assert_awaited_once_with("14")

    async def test_same_expired_session_restores_verified_previous_session(self):
        context = self.context()
        fresh = self.context()
        resolver = SimpleNamespace(
            resolve=AsyncMock(return_value=fresh),
            restore_previous_profile_session_after_gate=AsyncMock(),
        )
        async def restore(candidate):
            candidate.cookies = {"c_user": "123456789", "xs": "fixture-previous"}
            candidate._loaded_cookies = dict(candidate.cookies)
            return True
        resolver.restore_previous_profile_session_after_gate.side_effect = restore
        self.assertTrue(await refresh_saved_auth_context(resolver, context))
        self.assertEqual(context.cookies["xs"], "fixture-previous")
        self.assertEqual(context._loaded_cookies, context.cookies)
        resolver.restore_previous_profile_session_after_gate.assert_awaited_once_with(fresh)

    async def test_resolver_that_already_probed_backup_is_not_probed_twice(self):
        context = self.context()
        fresh = self.context()
        fresh._previous_profile_restore_attempted = True
        resolver = SimpleNamespace(
            resolve=AsyncMock(return_value=fresh),
            restore_previous_profile_session_after_gate=AsyncMock(return_value=True),
        )
        self.assertFalse(await refresh_saved_auth_context(resolver, context))
        resolver.restore_previous_profile_session_after_gate.assert_not_awaited()

    async def test_same_expired_session_is_not_reported_restored(self):
        context=self.context()
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=self.context()))
        self.assertFalse(await refresh_saved_auth_context(resolver,context))

    async def test_unrelated_cookie_change_is_not_new_authentication(self):
        context = self.context()
        fresh = self.context()
        fresh.cookies['locale'] = 'en_US'
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=fresh))
        self.assertFalse(await refresh_saved_auth_context(resolver, context))
        self.assertNotIn('locale', context.cookies)

    async def test_refreshed_cookie_baseline_is_the_saved_session(self):
        context = self.context()
        context._loaded_cookies = dict(context.cookies)
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=self.context(xs='fixture-fresh')))
        self.assertTrue(await refresh_saved_auth_context(resolver, context))
        self.assertEqual(context._loaded_cookies, context.cookies)
        self.assertIsNot(context._loaded_cookies, context.cookies)

    async def test_another_profile_or_another_facebook_identity_cannot_replace_the_session(self):
        for fresh in (self.context(profile="15",xs="fixture-fresh"),
                      self.context(uid="999999999",xs="fixture-fresh")):
            context=self.context()
            resolver=SimpleNamespace(resolve=AsyncMock(return_value=fresh))
            self.assertFalse(await refresh_saved_auth_context(resolver,context))
            self.assertEqual(context.cookies["c_user"],"123456789")
            self.assertEqual(context.cookies["xs"],"fixture-old")

    async def test_incomplete_session_does_not_erase_current_authentication(self):
        context=self.context()
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=self.context(xs="")))
        self.assertFalse(await refresh_saved_auth_context(resolver,context))
        self.assertEqual(context.cookies["xs"],"fixture-old")


class SavedAuthReadRetryTests(unittest.IsolatedAsyncioTestCase):
    context = SavedAuthRefreshTests.context
    async def test_successful_read_does_not_resolve_authentication_again(self):
        read = AsyncMock(return_value={'data': []})
        resolver = SimpleNamespace(resolve=AsyncMock())
        self.assertEqual(await read_with_saved_auth_refresh(resolver, self.context(), read), {'data': []})
        resolver.resolve.assert_not_awaited()
        read.assert_awaited_once_with()

    async def test_fresh_same_actor_authentication_retries_once(self):
        context = self.context()
        gate = ProvisioningError('PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE', 'fixture gate')
        read = AsyncMock(side_effect=[gate, {'draft_id': '123456'}])
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=self.context(xs='fixture-fresh')))
        result = await read_with_saved_auth_refresh(resolver, context, read)
        self.assertEqual(result, {'draft_id': '123456'})
        self.assertEqual(read.await_count, 2)
        resolver.resolve.assert_awaited_once_with('14')
        self.assertEqual(context.cookies['xs'], 'fixture-fresh')

    async def test_exact_auth_gate_recovers_from_previous_session_and_retries_once(self):
        context = self.context()
        fresh = self.context()
        gate = ProvisioningError('PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE', 'fixture gate')
        async def restore(candidate):
            candidate.cookies = {"c_user": "123456789", "xs": "fixture-previous"}
            return True
        resolver = SimpleNamespace(
            resolve=AsyncMock(return_value=fresh),
            restore_previous_profile_session_after_gate=AsyncMock(side_effect=restore),
        )
        read = AsyncMock(side_effect=[gate, {'draft_id': '123456'}])
        result = await read_with_saved_auth_refresh(resolver, context, read)
        self.assertEqual(result, {'draft_id': '123456'})
        self.assertEqual(read.await_count, 2)
        self.assertEqual(context.cookies['xs'], 'fixture-previous')
        resolver.restore_previous_profile_session_after_gate.assert_awaited_once_with(fresh)

    async def test_failed_previous_session_recovery_preserves_original_gate(self):
        context = self.context()
        gate = ProvisioningError('BUSINESS_LOGIN_GATE', 'fixture gate')
        resolver = SimpleNamespace(
            resolve=AsyncMock(return_value=self.context()),
            restore_previous_profile_session_after_gate=AsyncMock(return_value=False),
        )
        read = AsyncMock(side_effect=gate)
        with self.assertRaises(ProvisioningError) as caught:
            await read_with_saved_auth_refresh(resolver, context, read)
        self.assertIs(caught.exception, gate)
        read.assert_awaited_once_with()
        resolver.restore_previous_profile_session_after_gate.assert_awaited_once()

    async def test_missing_fresh_authentication_preserves_the_original_gate(self):
        for fresh in (self.context(), self.context(uid='999999999', xs='fixture-fresh')):
            context = self.context()
            gate = ProvisioningError('BUSINESS_LOGIN_GATE', 'fixture gate')
            read = AsyncMock(side_effect=gate)
            resolver = SimpleNamespace(resolve=AsyncMock(return_value=fresh))
            with self.assertRaises(ProvisioningError) as caught:
                await read_with_saved_auth_refresh(resolver, context, read)
            self.assertIs(caught.exception, gate)
            read.assert_awaited_once_with()

    async def test_a_second_gate_cannot_start_a_retry_loop(self):
        first = ProvisioningError('SESSION_EXPIRED', 'first gate')
        second = ProvisioningError('SESSION_EXPIRED', 'second gate')
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=self.context(xs='fixture-fresh')))
        read = AsyncMock(side_effect=[first, second])
        with self.assertRaises(ProvisioningError) as caught:
            await read_with_saved_auth_refresh(resolver, self.context(), read)
        self.assertIs(caught.exception, second)
        self.assertEqual(read.await_count, 2)
        self.assertEqual(resolver.resolve.await_count, 1)

    async def test_non_auth_failure_and_checkpoint_do_not_reload_a_session(self):
        for code in ('PRIVATE_ADS_DRAFT_READ_SPEC_MISMATCH', 'CHECKPOINT_REQUIRED',
                     'PRIVATE_ADS_DRAFT_CHECKPOINT_REQUIRED', 'PRIVATE_HTTP_READ_TIMEOUT'):
            resolver = SimpleNamespace(resolve=AsyncMock())
            error = ProvisioningError(code, 'fixture')
            read = AsyncMock(side_effect=error)
            with self.assertRaises(ProvisioningError) as caught:
                await read_with_saved_auth_refresh(resolver, self.context(), read)
            self.assertIs(caught.exception, error)
            resolver.resolve.assert_not_awaited()
            read.assert_awaited_once_with()

    async def test_resolver_failure_preserves_the_gate_without_logging_credentials(self):
        gate = ProvisioningError('SESSION_EXPIRED', 'fixture gate')
        resolver = SimpleNamespace(resolve=AsyncMock(side_effect=RuntimeError('fixture-sensitive-value')))
        with self.assertLogs('app.session_auth_refresh', level='INFO') as logs:
            with self.assertRaises(ProvisioningError) as caught:
                await read_with_saved_auth_refresh(resolver, self.context(), AsyncMock(side_effect=gate))
        self.assertIs(caught.exception, gate)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertNotIn('fixture-sensitive-value', '\n'.join(logs.output))

    async def test_cancellation_is_not_converted_into_authentication_recovery(self):
        resolver = SimpleNamespace(resolve=AsyncMock())
        with self.assertRaises(asyncio.CancelledError):
            await read_with_saved_auth_refresh(resolver, self.context(), AsyncMock(side_effect=asyncio.CancelledError()))
        resolver.resolve.assert_not_awaited()


class _RouteHTTPError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


class _RawAuthError(Exception):
    pass


class ReadRouteAuthRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """Exercise route control flow without starting the app or contacting Meta."""
    def fixture(self, status='FAILED'):
        events = []
        context = SimpleNamespace(profile_id='4', cookies={'c_user':'123456789','xs':'fixture-old'},
            proxy='http://fixture-proxy:80', user_agent='Fixture',
            ad_accounts=[{'id':'111111','business_id':'222222'}])
        fresh = SimpleNamespace(profile_id='4', cookies={'c_user':'123456789','xs':'fixture-fresh'},
            proxy='http://fixture-proxy:80', user_agent='Fixture')
        async def resolve(profile):
            events.append('resolve-fresh')
            self.assertEqual(events[-2], 'close-fixture-old')
            return fresh
        class Scope:
            def __init__(self, ctx):
                self.cookies = dict(ctx.cookies)
            async def __aenter__(self):
                events.append('open-' + self.cookies['xs'])
                return self
            async def __aexit__(self, *args):
                events.append('close-' + self.cookies['xs'])
            async def facebook_web(self):
                return SimpleNamespace(private_only=False)
        payload = {'launch_key':'saved-key','business_id':'222222'}
        view = {'status':status,'items':[{'profile_id':'4','tasks':[
            {'action':'private_launch','payload':payload}]}]}
        resources = SimpleNamespace(get=AsyncMock())
        pool = SimpleNamespace(resolver=SimpleNamespace(resolve=AsyncMock(side_effect=resolve)),
            private_launch=SimpleNamespace(ads_manager=object(), resources=resources,
                _id=lambda value, field: str(value).removeprefix('act_')))
        namespace = {'asyncio':asyncio, 'HTTPException':_RouteHTTPError,
            'ProvisioningError':ProvisioningError, 'ProfileContextError':type('ProfileContextError',(Exception,),{}),
            'read_with_saved_auth_refresh':read_with_saved_auth_refresh, 'pool':pool,
            'store':SimpleNamespace(job_view=AsyncMock(return_value=view)),
            '_resolve_read_profile':AsyncMock(return_value=context), 'ProfileSession':Scope,
            'measure_phase':lambda _name: nullcontext(), 'Body':lambda value: value}
        read = AsyncMock()
        modules = {'app.private_draft_read_diagnostic':SimpleNamespace(read_diagnostic=read),
            'app.session':SimpleNamespace(ProfileSession=Scope),
            'fb_worker':SimpleNamespace(AuthenticationError=_RawAuthError,
                RemoteRequestError=type('RemoteRequestError',(Exception,),{}),
                ProxyError=type('ProxyError',(Exception,),{})),
            'app.private_auth':SimpleNamespace(private_auth_error=lambda _error:
                ProvisioningError('BUSINESS_LOGIN_GATE','fixture gate')),
            'app.private_advertising_reads':SimpleNamespace(RESOURCE_FIELDS={'pages':{'id','name'}}, TARGETING_TYPES=set()),
            'app.private_transport_error':SimpleNamespace(private_transport_error=lambda _error:(502,{}))}
        return context, fresh, events, payload, read, resources, namespace, modules

    def route(self, name, namespace):
        source = Path(__file__).resolve().parents[1].joinpath('main.py').read_text()
        node = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
        node.decorator_list = []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),
            'read-route-fixture','exec'), namespace)
        return namespace[name]

    async def test_draft_read_reopens_session_and_preserves_original_payload(self):
        context, _, events, payload, read, _, ns, modules = self.fixture(status='PARTIAL')
        read.side_effect = [ProvisioningError('PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE','gate'),
                            {'persisted':False,'advertising_mutation_posts':0}]
        route = self.route('private_draft_read', ns)
        with patch.dict(sys.modules, modules):
            result = await route('4', {'job_id':'a'*32})
        self.assertTrue(result['ok'])
        self.assertEqual(events, ['open-fixture-old','close-fixture-old','resolve-fresh',
                                  'open-fixture-fresh','close-fixture-fresh'])
        self.assertEqual(read.await_count, 2)
        for call in read.await_args_list:
            self.assertIs(call.kwargs['payload'], payload)
            self.assertIs(call.kwargs['context'], context)
        self.assertEqual(ns['store'].job_view.await_count, 1)

    async def test_draft_read_does_not_retry_an_unchanged_session(self):
        _, fresh, _, _, read, _, ns, modules = self.fixture()
        fresh.cookies['xs'] = 'fixture-old'
        read.side_effect = ProvisioningError('PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE','gate')
        with patch.dict(sys.modules, modules), self.assertRaises(_RouteHTTPError) as caught:
            await self.route('private_draft_read', ns)('4', {'job_id':'a'*32})
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail['code'], 'PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE')
        self.assertEqual(read.await_count, 1)

    async def test_pages_read_recovers_raw_auth_error_with_same_account_scope(self):
        _, _, events, _, _, resources, ns, modules = self.fixture()
        resources.get.side_effect = [_RawAuthError('fixture gate'), {'data':[{'id':'333333'}]}]
        with patch.dict(sys.modules, modules):
            result = await self.route('private_advertising_resources', ns)(
                '4', 'pages', '111111', force=True, execution_mode='DRAFT')
        self.assertEqual(result['data'], [{'id':'333333'}])
        self.assertEqual(events, ['open-fixture-old','close-fixture-old','resolve-fresh',
                                  'open-fixture-fresh','close-fixture-fresh'])
        self.assertEqual(resources.get.await_count, 2)
        for call in resources.get.await_args_list:
            self.assertEqual(call.kwargs['values']['ad_account_id'], '111111')
            self.assertEqual(call.kwargs['values']['business_id'], '222222')
            self.assertEqual(call.kwargs['actor_id'], '123456789')
            self.assertTrue(call.kwargs['web'].private_only)

    async def test_failed_pages_recovery_retains_cached_data_as_unverified(self):
        _, fresh, _, _, _, resources, ns, modules = self.fixture()
        fresh.cookies['xs'] = 'fixture-old'
        cached = {'availability':'AVAILABLE','data':[{'id':'333333'}], '_cache':{'stale':True}}
        resources.get.side_effect = [cached, _RawAuthError('fixture gate')]
        with patch.dict(sys.modules, modules):
            result = await self.route('private_advertising_resources', ns)(
                '4', 'pages', '111111', force=False, execution_mode='DRAFT')
        self.assertEqual(result['data'], cached['data'])
        self.assertFalse(result['checked_live'])
        self.assertFalse(result['account_scope_verified'])
        self.assertTrue(result['_cache']['refresh_failed'])
        self.assertEqual(resources.get.await_count, 2)

