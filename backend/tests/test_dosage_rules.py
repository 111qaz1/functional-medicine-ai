from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.append(str(BACKEND_ROOT))
sys.path.append(str(PROJECT_ROOT))

from app.domain.models import DosageOptionSummary, DraftRecommendationItem
from app.services.dosage_rules import select_dosage_option
from app.services.review_local import InvalidDosageOverrideError, ReviewService
from scripts.import_product_dosage_mapping import DosageImportError, extract_product_dosage_blocks


def context(
    *,
    symptoms: set[str] | None = None,
    conditions: set[str] | None = None,
    goals: set[str] | None = None,
    chief_concerns: set[str] | None = None,
    summary: str = "",
    markers: dict[str, list] | None = None,
    age: int | None = None,
):
    return SimpleNamespace(
        markers_by_code=markers or {},
        clinical_findings=[],
        symptoms=symptoms or set(),
        conditions=conditions or set(),
        goals=goals or set(),
        chief_concerns=chief_concerns or set(),
        lifestyle_tags=set(),
        clinical_summary_text=summary,
        age=age,
    )


def normalize(value: str) -> str:
    return "".join(value.lower().split())


class DosageRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        payload = json.loads(
            (BACKEND_ROOT / "app" / "data" / "product_dosage_mapping.json").read_text(encoding="utf-8")
        )
        cls.payload = payload
        cls.products = {item["sku_id"]: item for item in payload["products"]}

    def test_all_31_products_have_structured_dose_options_and_product_31_uses_merged_anchor(self) -> None:
        self.assertEqual(self.payload["schema_version"], 3)
        self.assertEqual(len(self.products), 31)
        self.assertTrue(all(item["dose_options"] for item in self.products.values()))
        product = self.products["sku_amino_acid_detox"]
        self.assertEqual(product["source_product_row"], 231)
        self.assertEqual(product["source_dosage_row"], 233)
        self.assertEqual(
            [option["label"].split("（", 1)[0] for option in product["dose_options"]],
            ["日常抗氧化 / 养护", "强化调理", "特殊场景"],
        )

    def test_magnesium_chooses_higher_strength_when_sleep_and_spasm_both_match(self) -> None:
        selection = select_dosage_option(
            self.products["sku_magnesium_glycinate"],
            context(symptoms={"睡眠不佳", "肌肉痉挛"}),
            normalize,
        )
        self.assertIn("肌肉紧张", selection.option["label"])
        self.assertEqual(selection.option["regimen"]["daily_max"], 4)

    def test_vitamin_d_requires_explicit_numeric_value_for_non_default_tiers(self) -> None:
        low = context(markers={"vitamin_d": [SimpleNamespace(normalized_value=18.0, value=18.0)]})
        middle = context(markers={"vitamin_d": [SimpleNamespace(normalized_value=25.0, value=25.0)]})
        unknown = context(conditions={"维生素D缺乏"})
        self.assertIn("缺乏纠正", select_dosage_option(self.products["sku_vitamin_d3_k"], low, normalize).option["label"])
        self.assertIn("20-30", select_dosage_option(self.products["sku_vitamin_d3_k"], middle, normalize).option["label"])
        self.assertTrue(
            select_dosage_option(self.products["sku_vitamin_d3_k"], unknown, normalize).option["is_default"]
        )

    def test_acute_tier_requires_explicit_acute_fact(self) -> None:
        ordinary = select_dosage_option(
            self.products["sku_super_anti_inflammatory"],
            context(symptoms={"关节疼痛"}),
            normalize,
        )
        acute = select_dosage_option(
            self.products["sku_super_anti_inflammatory"],
            context(symptoms={"急性关节疼痛"}),
            normalize,
        )
        self.assertNotIn("急性", ordinary.option["label"])
        self.assertIn("急性", acute.option["label"])

    def test_product_31_daily_strengthened_and_special_scenarios(self) -> None:
        product = self.products["sku_amino_acid_detox"]
        daily = select_dosage_option(product, context(symptoms={"普通疲劳"}), normalize)
        strengthened = select_dosage_option(product, context(summary="医生确认存在饮酒伤肝"), normalize)
        special = select_dosage_option(product, context(summary="医生确认今天酒后需要支持"), normalize)
        self.assertTrue(daily.option["is_default"])
        self.assertIn("强化", strengthened.option["label"])
        self.assertIn("特殊", special.option["label"])

    def test_product_block_import_finds_dose_below_sequence_row_and_rejects_conflicts(self) -> None:
        cells = {(row, 1): str(row) for row in range(1, 32)}
        cells.update({(row, 10): f"产品 {row} 每日1粒" for row in range(1, 31)})
        cells[(32, 10)] = "产品31 每日1粒"
        blocks = extract_product_dosage_blocks(cells, max_row=32)
        self.assertEqual(blocks[31]["source_row"], 32)

        cells[(31, 10)] = "产品31 每日2粒"
        with self.assertRaises(DosageImportError):
            extract_product_dosage_blocks(cells, max_row=32)

    def test_manual_non_system_tier_requires_note_and_updates_effective_dosage(self) -> None:
        product = self.products["sku_amino_acid_detox"]
        options = [
            DosageOptionSummary.model_validate(
                {
                    "option_id": option["option_id"],
                    "label": option["label"],
                    "display_text": option["display_text"],
                    "requires_review": option["requires_review"],
                    "regimen": option["regimen"],
                }
            )
            for option in product["dose_options"]
        ]
        system_option, override_option = options[0], options[1]
        item = DraftRecommendationItem(
            sku_id=product["sku_id"],
            display_name="谷胱甘肽",
            dosage=system_option.display_text,
            dosage_option_id=system_option.option_id,
            dosage_option_label=system_option.label,
            dosage_options=options,
            dosage_regimen=system_option.regimen,
            reason="测试",
        )
        service = object.__new__(ReviewService)
        with self.assertRaises(InvalidDosageOverrideError):
            service._recommendation_with_dosage_override(
                item,
                {"option_id": override_option.option_id, "note": ""},
            )
        effective = service._recommendation_with_dosage_override(
            item,
            {"option_id": override_option.option_id, "note": "结合饮酒史调整"},
        )
        self.assertEqual(effective.dosage_option_id, override_option.option_id)
        self.assertEqual(effective.dosage, override_option.display_text)


if __name__ == "__main__":
    unittest.main()
