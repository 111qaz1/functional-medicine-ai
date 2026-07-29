from __future__ import annotations

import json
import unittest
from pathlib import Path


PRODUCT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "product_catalog.json"
PRODUCT_SAFETY_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "product_safety_matrix.json"
PRODUCT_RECALL_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "product_recall_matrix.json"


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

    def test_client_rule_matrices_reference_current_31_sku_catalog(self) -> None:
        products = json.loads(PRODUCT_CATALOG_PATH.read_text(encoding="utf-8-sig"))
        safety = json.loads(PRODUCT_SAFETY_PATH.read_text(encoding="utf-8-sig"))
        recall = json.loads(PRODUCT_RECALL_PATH.read_text(encoding="utf-8-sig"))
        catalog_ids = {product["sku_id"] for product in products}

        self.assertEqual(len(catalog_ids), 31)
        self.assertIn("sku_liposomal_vitamin_c_500", catalog_ids)
        self.assertNotIn("sku_liposomal_vitamin_c_300", catalog_ids)
        self.assertIn("sku_digestive_enzymes", catalog_ids)
        self.assertIn("sku_probiotic_complex", catalog_ids)

        for matrix_name, rules in (("safety", safety["rules"]), ("recall", recall["rules"])):
            for rule in rules:
                with self.subTest(matrix=matrix_name, rule=rule["rule_id"]):
                    self.assertTrue(rule.get("source_ref"))
                    self.assertTrue(rule.get("version"))
                    for sku_id in rule.get("sku_ids", []):
                        if sku_id != "*":
                            self.assertIn(sku_id, catalog_ids)

    def test_client_recall_rules_have_unique_ids_and_match_conditions(self) -> None:
        recall = json.loads(PRODUCT_RECALL_PATH.read_text(encoding="utf-8-sig"))
        rules = recall["rules"]
        rule_ids = [rule["rule_id"] for rule in rules]

        self.assertEqual(len(rule_ids), len(set(rule_ids)))
        for rule in rules:
            with self.subTest(rule=rule["rule_id"]):
                self.assertTrue(rule.get("all_conditions") or rule.get("any_conditions"))
                self.assertTrue(rule.get("message"))

    def test_safety_rules_have_unique_ids_and_supported_actions(self) -> None:
        safety = json.loads(PRODUCT_SAFETY_PATH.read_text(encoding="utf-8-sig"))
        rules = safety["rules"]
        rule_ids = [rule["rule_id"] for rule in rules]

        self.assertEqual(len(rule_ids), len(set(rule_ids)))
        for rule in rules:
            with self.subTest(rule=rule["rule_id"]):
                self.assertIn(rule["action"], {"exclude", "requires_review", "warn"})
                self.assertTrue(rule.get("all_conditions") or rule.get("any_conditions"))
                self.assertTrue(rule.get("message"))


if __name__ == "__main__":
    unittest.main()
