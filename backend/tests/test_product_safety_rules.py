from __future__ import annotations

import json
import unittest
from pathlib import Path


PRODUCT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "product_catalog.json"


class ProductSafetyRulesTests(unittest.TestCase):
    def test_every_enabled_product_has_customer_safe_safety_rules(self) -> None:
        products = json.loads(PRODUCT_CATALOG_PATH.read_text(encoding="utf-8-sig"))

        for product in products:
            if not product.get("enabled", True):
                continue
            safety_items = (
                product.get("contraindications", [])
                + product.get("interaction_rule", [])
                + product.get("warning_text", [])
            )
            with self.subTest(product=product["sku_id"]):
                self.assertTrue(safety_items)
                for item in safety_items:
                    self.assertNotIn("SKU", item)
                    self.assertNotIn("规格", item)


if __name__ == "__main__":
    unittest.main()
