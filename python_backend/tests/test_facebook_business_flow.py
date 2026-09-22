from __future__ import annotations

import unittest

from app.facebook_business_create import (
    _prefer_page_backed_candidates,
    extract_business_id,
)
from app.facebook_docids import DocIdCandidate
from app.facebook_page_discovery import (
    _extract_known_page_lists,
    _extract_pages_from_html,
)


class FanPageDiscoveryTests(unittest.TestCase):
    def test_old_pages_can_administer_list(self) -> None:
        payload = {
            "data": {
                "userData": {
                    "pages_can_administer": [
                        {
                            "id": "123456789012345",
                            "name": "Golden Fruits",
                            "category": "Product/service",
                        }
                    ]
                }
            }
        }
        pages = _extract_known_page_lists(payload)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["id"], "123456789012345")
        self.assertEqual(pages[0]["name"], "Golden Fruits")

    def test_relay_edges_and_nodes_are_supported(self) -> None:
        payload = {
            "data": {
                "viewer": {
                    "pages_can_administer": {
                        "edges": [
                            {
                                "node": {
                                    "__typename": "Page",
                                    "id": "111111111111111",
                                    "name": "Page A",
                                    "tasks": ["ADVERTISE", "MANAGE"],
                                }
                            }
                        ],
                        "nodes": [
                            {
                                "__typename": "Page",
                                "id": "222222222222222",
                                "name": "Page B",
                                "category": "Community",
                            }
                        ],
                    }
                }
            }
        }
        pages = _extract_known_page_lists(payload)
        self.assertEqual(
            {page["id"] for page in pages},
            {"111111111111111", "222222222222222"},
        )

    def test_generic_scan_does_not_accept_plain_business_id_name(self) -> None:
        payload = {
            "data": {
                "business": {
                    "__typename": "Business",
                    "id": "333333333333333",
                    "name": "Not a Page",
                }
            }
        }
        self.assertEqual(_extract_known_page_lists(payload), [])

    def test_html_bootstrap_page_object(self) -> None:
        html = """
        <html><body>
        <script type="application/json">
        {
          "payload": {
            "viewer": {
              "pages": {
                "nodes": [
                  {
                    "__typename": "Page",
                    "id": "444444444444444",
                    "name": "Fan Page From HTML",
                    "category": "Brand"
                  }
                ]
              }
            }
          }
        }
        </script>
        </body></html>
        """
        pages = _extract_pages_from_html(html)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["id"], "444444444444444")

    def test_business_id_extraction_known_shape(self) -> None:
        business_id, path = extract_business_id(
            {
                "data": {
                    "business_create": {
                        "business": {
                            "id": "555555555555555",
                            "name": "Created Business",
                        }
                    }
                }
            }
        )
        self.assertEqual(business_id, "555555555555555")
        self.assertEqual(path, "data.business_create.business.id")


class BusinessCandidateOrderingTests(unittest.TestCase):
    def test_selected_page_prefers_page_backed_contract(self) -> None:
        modern = DocIdCandidate(
            operation="CREATE_BM",
            doc_id="10024830640911292",
            friendly_name="useBusinessCreationMutationMutation",
            endpoint_url="https://business.facebook.com/api/graphql/",
            variables_mode="scope_selector_business_creation_v1",
            source="test",
            priority=999,
        )
        page_backed = DocIdCandidate(
            operation="CREATE_BM",
            doc_id="739201948201938",
            friendly_name="BusinessManagerCreateMutation",
            endpoint_url="https://business.facebook.com/api/graphql/",
            variables_mode="legacy_primary_page_v1",
            source="test",
            priority=1,
        )

        ordered = _prefer_page_backed_candidates(
            [modern, page_backed],
            page_id="123456789012345",
        )
        self.assertEqual(ordered[0].variables_mode, "legacy_primary_page_v1")

    def test_no_page_preserves_registry_order(self) -> None:
        first = DocIdCandidate(
            operation="CREATE_BM",
            doc_id="111111",
            friendly_name="first",
            endpoint_url="https://business.facebook.com/api/graphql/",
            variables_mode="scope_selector_business_creation_v1",
            source="test",
            priority=10,
        )
        second = DocIdCandidate(
            operation="CREATE_BM",
            doc_id="222222",
            friendly_name="second",
            endpoint_url="https://business.facebook.com/api/graphql/",
            variables_mode="legacy_primary_page_v1",
            source="test",
            priority=1,
        )
        self.assertEqual(
            _prefer_page_backed_candidates(
                [first, second],
                page_id="",
            ),
            [first, second],
        )


if __name__ == "__main__":
    unittest.main()
