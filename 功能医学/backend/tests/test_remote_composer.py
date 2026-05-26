from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.domain.models import ProductRule
from app.providers.base import DraftCompositionInput, DraftCompositionResult
from app.providers.remote import OpenAICompatibleGroundedComposer, _RemoteCompositionPayload


class FallbackComposer:
    def compose(self, draft_input: DraftCompositionInput) -> DraftCompositionResult:
        return DraftCompositionResult(
            selected_sku_ids=[product.sku_id for product in draft_input.candidate_products[:1]],
            rationale=["本地 fallback 推荐。"],
            confidence=0.6,
        )


class RemoteComposerSanitizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.product = ProductRule(
            sku_id="sku_vitamin_d3_k",
            display_name="VD3+K",
            category="fat_soluble_support",
            formula_summary="以维生素 D3、K1 和 K2 组成的脂溶性营养支持配方。",
            core_ingredients=["维生素D3", "维生素K1", "维生素K2"],
            candidate_use_cases=["维生素D支持", "骨骼支持", "免疫支持"],
            indications=["marker:vitamin_d:low"],
            dosage_rule="遵医嘱或按产品标签使用。",
            priority=5,
        )
        self.input = DraftCompositionInput(
            customer_name="杨25",
            analysis_mode="llm_primary",
            case_summary=["25-羟维生素D偏低"],
            key_lab_highlights=["25-羟维生素D: 22.75 ng/mL（需关注）"],
            candidate_products=[self.product],
            knowledge_hits=[],
            product_evidence_map={"sku_vitamin_d3_k": ["product:sku_vitamin_d3_k", "signal:vitamin_d_repletion"]},
            red_flags=[],
            contraindications=[],
            missing_info=["尚未确认当前用药。", "尚未确认过敏史。"],
        )
        self.composer = OpenAICompatibleGroundedComposer(
            base_url="https://example.test/v1",
            api_key="test",
            model="test-model",
            fallback=FallbackComposer(),
        )

    def test_ignores_positive_text_in_abstain_reason(self) -> None:
        payload = _RemoteCompositionPayload(
            selected_sku_ids=["sku_vitamin_d3_k"],
            rationale=["有足够临床证据支持维生素D补充。"],
            confidence=0.75,
            abstain_reason="无明确禁忌证，有足够临床证据支持推荐",
        )

        result = self.composer._sanitize_response(payload, self.input)

        self.assertIsNone(result.abstain_reason)
        self.assertEqual(result.selected_sku_ids, ["sku_vitamin_d3_k"])

    def test_keeps_true_blocking_abstain_reason(self) -> None:
        payload = _RemoteCompositionPayload(
            selected_sku_ids=["sku_vitamin_d3_k"],
            rationale=["需要先人工复核。"],
            confidence=0.4,
            abstain_reason="证据不足，等待人工复核后再推荐",
        )

        result = self.composer._sanitize_response(payload, self.input)

        self.assertEqual(result.abstain_reason, "证据不足，等待人工复核后再推荐")
        self.assertEqual(result.selected_sku_ids, [])


if __name__ == "__main__":
    unittest.main()
