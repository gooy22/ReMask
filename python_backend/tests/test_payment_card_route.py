"""Exercise the deployed route's error path without starting workers or browsers."""
import ast
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from app.facebook_business_browser import BrowserBusinessError


class ContextFailure(RuntimeError):
    pass


def payment_route():
    tree=ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text())
    route=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='profile_payment_card_action')
    route.decorator_list=[]
    namespace={'Body':lambda *args:None,'HTTPException':HTTPException,'pool':SimpleNamespace(resolver=None),
        're':re,'BrowserBusinessError':BrowserBusinessError,'ProfileContextError':ContextFailure}
    exec(compile(ast.Module(body=[route],type_ignores=[]),'main.py','exec'),namespace)
    return namespace[route.name]


class PaymentRouteErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_canonical_target_keeps_actual_failure_and_no_secret_message(self):
        route=payment_route()
        for account in ('123456789','act_123456789'):
            for error,code in [(RuntimeError('sensitive fixture message'),'CARD_BROWSER_INTERRUPTED'),(ContextFailure('sensitive fixture message'),'PROFILE_CONTEXT_ERROR')]:
                with self.subTest(account=account,code=code),patch('app.payment_card_binding.profile_payment_card',AsyncMock(side_effect=error)):
                    result=await route('Fixture',{'account_id':account,'operation':'prepare'})
                self.assertEqual(result['account_id'],'123456789');self.assertEqual(result['code'],code)
                self.assertEqual(result['status'],'BLOCKED');self.assertFalse(result['submitted']);self.assertFalse(result['funding_verified'])
                self.assertNotIn('sensitive fixture message',json.dumps(result))

    async def test_invalid_target_stays_bad_request_after_worker_failure(self):
        route=payment_route()
        for account in ('not-an-account',r'\dddddd','1234','123456789 extra'):
            with self.subTest(account=account),patch('app.payment_card_binding.profile_payment_card',AsyncMock(side_effect=RuntimeError('fixture'))):
                with self.assertRaises(HTTPException) as caught:await route('Fixture',{'account_id':account})
            self.assertEqual(caught.exception.status_code,400);self.assertEqual(caught.exception.detail,'INVALID_PAYMENT_TARGET')

    async def test_bind_failure_requires_reconciliation_when_submission_is_unknown(self):
        route=payment_route()
        with patch('app.payment_card_binding.profile_payment_card',AsyncMock(side_effect=RuntimeError('fixture cleanup failure'))) as worker:
            result=await route('Fixture',{'account_id':'act_123456789','operation':'bind'})
        self.assertEqual(result['status'],'SUBMITTED_UNVERIFIED');self.assertIsNone(result['submitted'])
        self.assertFalse(result['funding_verified']);worker.assert_awaited_once()
