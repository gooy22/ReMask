import unittest

from app.facebook_business_browser import _extract_business_inventory_rows


class BusinessInventoryScopeTests(unittest.TestCase):
    def test_nested_assets_are_not_businesses(self):
        payload = {"data": {"bizkit": {"businesses": {"nodes": [{
            "__typename": "Business", "id": "111111111", "name": "Portfolio",
            "pages": {"nodes": [{"__typename": "Page", "id": "222222222", "name": "Page"}]},
            "users": [{"__typename": "BusinessUser", "id": "333333333", "name": "Person"}],
            "owned_ad_accounts": {"nodes": [{"__typename": "AdAccount", "id": "444444444", "name": "RK"}]},
            "unrelated": {"id": "555555555", "name": "Unrelated"},
        }]}}}}
        self.assertEqual(_extract_business_inventory_rows(payload), [{"id": "111111111", "name": "Portfolio"}])

    def test_untyped_business_collection_and_explicit_business_node_remain_supported(self):
        payload = {"data": {
            "businesses": {"edges": [{"node": {"id": "111111111", "name": "Portfolio", "page": {"id": "222222222", "name": "Page"}}}]},
            "viewer": {"portfolio_edge": {"node": {"business_id": "333333333", "name": "Other portfolio"}}},
        }}
        self.assertEqual(_extract_business_inventory_rows(payload), [
            {"id": "111111111", "name": "Portfolio"}, {"id": "333333333", "name": "Other portfolio"},
        ])

    def test_asset_owner_reference_cannot_supply_portfolio_name(self):
        for asset in [
            {"__typename": "AdAccount", "id": "222222222"},
            {"asset_type": "AD_ACCOUNT", "account_id": "222222222"},
            {"__typename": "Page", "id": "222222222"},
        ]:
            with self.subTest(asset=asset):
                payload = {"bizkit": {"assets": [{**asset, "business_id": "111111111", "name": "Asset name"}]}}
                self.assertEqual(_extract_business_inventory_rows(payload), [{"id": "111111111", "name": ""}])

    def test_typed_business_name_overrides_an_earlier_asset_reference(self):
        payload = {"bizkit": {
            "asset": {"__typename": "AdAccount", "business_id": "111111111", "name": "RK name"},
            "portfolio": {"__typename": "BusinessPortfolio", "id": "111111111", "name": "Portfolio"},
        }}
        self.assertEqual(_extract_business_inventory_rows(payload), [{"id": "111111111", "name": "Portfolio"}])

    def test_generic_bizkit_ids_do_not_prove_business_identity(self):
        payload = {"bizkit": {"viewer": {"id": "111111111", "name": "Person"}, "page": {"id": "222222222", "name": "Page"}}}
        self.assertEqual(_extract_business_inventory_rows(payload), [])


if __name__ == "__main__":
    unittest.main()
