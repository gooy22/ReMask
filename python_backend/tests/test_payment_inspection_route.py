import ast
import asyncio
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app import payment_inspection


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code=status_code
        self.detail=detail


class ProfileContextError(Exception):
    pass


class BrowserBusinessError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code=code


class PaymentInspectionRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text())
        route=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef)
                   and node.name=='profile_payment_methods')
        route.decorator_list=[]
        self.inspect=AsyncMock(return_value={'profile_id':'Fixture','account_id':'123456789'})
        namespace={'HTTPException':HTTPException,'ProfileContextError':ProfileContextError,
            'BrowserBusinessError':BrowserBusinessError,'inspect_profile_payment_methods':self.inspect,
            'pool':SimpleNamespace(resolver=object(),provisioning_state=object()),'re':re,'asyncio':asyncio}
        exec(compile(ast.Module(body=[route],type_ignores=[]),'main.py','exec'),namespace)
        self.route=namespace['profile_payment_methods']

    async def test_valid_business_hint_reaches_payment_inspection(self):
        with patch.object(payment_inspection,'inspect_profile_payment_methods',self.inspect):
            result=await self.route('Fixture','123456789','987654321','','Fixture RK')
        self.assertEqual(result['account_id'],'123456789')
        self.assertEqual(self.inspect.await_args.kwargs['asset_hint'],
            {'business_id':'987654321','business_asset_id':'','name':'Fixture RK'})

    async def test_invalid_partial_hint_is_rejected_before_browser_access(self):
        with self.assertRaises(HTTPException) as raised:
            await self.route('Fixture','123456789','','','Fixture RK')
        self.assertEqual(raised.exception.detail,'PAYMENT_ACCOUNT_BINDING_MISSING')
        self.inspect.assert_not_awaited()
