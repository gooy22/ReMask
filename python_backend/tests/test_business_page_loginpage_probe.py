import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.business_fan_page_contracts import command, execute
from fb_worker import FacebookWebSession, RemoteRequestError, WebProfile

BUSINESS = "1451903470239662"
ACTOR = "61594946647826"
CATEGORY_ID = "242822000000000"


def bindings_for(operation):
    if operation == "READ_FP":
        return {"business": BUSINESS, "cursor": None}
    if operation == "FP_PRECHECK":
        return {"business": BUSINESS}
    if operation == "FP_CATEGORY":
        return {"category": "Digital creator"}
    return {
        "business": BUSINESS,
        "name": "PrgssTeam",
        "bio": "",
        "categories": [CATEGORY_ID],
        "join": "fixture-join",
    }


class BusinessPageLoginpageProbeTests(unittest.TestCase):
    def setUp(self):
        self.web = FacebookWebSession(WebProfile(
            "15", {"c_user": ACTOR, "xs": "fixture-xs"}, None, "fixture-UA",
        ))

    def validate(self, operation, *, business=BUSINESS, before_submit=None,
                 read_timeout_seconds=None):
        row = command(operation, **bindings_for(operation))
        return self.web._validate_business_loginpage_probe(
            operation, row["doc_id"], row["variables"],
            friendly_name=row["friendly_name"],
            endpoint_url=row["endpoint_url"],
            request_envelope=None,
            before_submit=before_submit,
            business_context_id=business,
            multipart_files=None,
            read_timeout_seconds=read_timeout_seconds,
        )

    def test_exact_page_contracts_are_scoped_to_verified_business(self):
        for operation in ("READ_FP", "FP_PRECHECK", "FP_CATEGORY"):
            with self.subTest(operation=operation):
                self.assertEqual(
                    self.validate(operation, read_timeout_seconds=30),
                    operation,
                )
        self.assertEqual(
            self.validate("CREATE_FP", before_submit=AsyncMock()),
            "CREATE_FP",
        )

    def test_page_reads_reject_wrong_scope_or_unreviewed_budget(self):
        with self.assertRaises(RemoteRequestError):
            self.validate("READ_FP", business="1428816905866955",
                          read_timeout_seconds=30)
        with self.assertRaises(RemoteRequestError):
            self.validate("READ_FP", read_timeout_seconds=15)

    def test_page_create_requires_exact_business_and_durable_intent(self):
        with self.assertRaises(RemoteRequestError):
            self.validate("CREATE_FP")
        with self.assertRaises(RemoteRequestError):
            self.validate("CREATE_FP", business="1428816905866955",
                          before_submit=AsyncMock())

    def test_unknown_page_operation_cannot_use_the_probe(self):
        with self.assertRaises(RemoteRequestError):
            self.validate("UNREVIEWED_PAGE_MUTATION")


class BusinessPageContractExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_executor_marks_reads_and_durable_create(self):
        web = SimpleNamespace(graphql=AsyncMock(return_value={"data": {}}))
        await execute(web, "READ_FP", business=BUSINESS, cursor=None)
        read_kwargs = web.graphql.await_args.kwargs
        self.assertEqual(read_kwargs["business_loginpage_probe"], "READ_FP")
        self.assertEqual(read_kwargs["business_context_id"], BUSINESS)
        self.assertEqual(read_kwargs["read_timeout_seconds"], 30)

        web.graphql.reset_mock()
        intent = AsyncMock()
        await execute(
            web, "CREATE_FP", business=BUSINESS, name="PrgssTeam",
            bio="", categories=[CATEGORY_ID], join="fixture-join",
            before_submit=intent,
        )
        create_kwargs = web.graphql.await_args.kwargs
        self.assertEqual(create_kwargs["business_loginpage_probe"], "CREATE_FP")
        self.assertEqual(create_kwargs["business_context_id"], BUSINESS)
        self.assertIs(create_kwargs["before_submit"], intent)
        self.assertNotIn("read_timeout_seconds", create_kwargs)
