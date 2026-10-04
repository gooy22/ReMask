import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import main as api
from app.facebook_business_browser import BrowserBusinessError, BROWSER_TERMINAL_ACCESS_CODES


class ProfileFpAuthGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_auth_failure_overrides_missing_bm_create_surface(self):
        for code in BROWSER_TERMINAL_ACCESS_CODES:
            with self.subTest(code=code):
                browser = SimpleNamespace(
                    preflight=AsyncMock(side_effect=BrowserBusinessError(
                        'BUSINESS_CREATE_UI_UNAVAILABLE', 'No BM create entry', retryable=True,
                    )),
                    discover_managed_pages=AsyncMock(side_effect=BrowserBusinessError(
                        code, 'Page session blocked', retryable=False,
                    )),
                )
                context = SimpleNamespace(pages=[], email='', first_name='', last_name='', display_name='', access_token='')
                pool = SimpleNamespace(resolver=SimpleNamespace(resolve=AsyncMock(return_value=context)))
                session = SimpleNamespace(
                    proxy_check=AsyncMock(return_value={}),
                    facebook_business_browser=AsyncMock(return_value=browser),
                )
                factory = MagicMock()
                factory.return_value.__aenter__ = AsyncMock(return_value=session)
                factory.return_value.__aexit__ = AsyncMock(return_value=False)
                with patch.object(api, 'pool', pool), patch.object(api, 'ProfileSession', factory), patch.dict(api._FP_AUTH_GATE_CACHE, {}, clear=True):
                    result = await api.profile_preflight('9')
                    self.assertTrue(result['auth_blocked'])
                    self.assertFalse(result['facebook_session_ready'])
                    self.assertEqual(result['auth_error_code'], code)
                    with self.assertRaises(api.HTTPException) as failure:
                        await api._require_fp_auth_ready(['9'])
                    self.assertEqual(failure.exception.status_code, 409)
                    self.assertIn('9:' + code, failure.exception.detail)
