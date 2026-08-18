from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.bootstrap import build_container
from app.core.settings import AppSettings
from app.domain.models import (
    AbnormalFinding,
    AnalysisStatus,
    CaseAnalysis,
    CaseIndicator,
    IndicatorStatus,
    ProductRule,
    Questionnaire,
    SemanticEvidenceReference,
    SemanticEvidenceStrength,
    SemanticSupportNeed,
    SupportDirection,
    SourceSpan,
    StructuredSystemFinding,
    SupportEligibilityStatus,
    UploadedFile,
)
from app.providers.local import GroundedDraftComposer
from app.providers.base import DraftCompositionResult
from app.services.recommendation_local_engine import RecommendationContext


class StubLLMProvider:
    def compose(self, draft_input):
        return DraftCompositionResult(
            selected_sku_ids=["sku_not_in_catalog", "sku_vitamin_d3_k"],
            product_reason_overrides={
                "sku_not_in_catalog": "This should be ignored.",
                "sku_vitamin_d3_k": "结合骨骼支持与本地证据 product:sku_vitamin_d3_k 进入候选推荐",
            },
            rationale=["模型仅在本地候选和证据范围内辅助排序。"],
            lifestyle_actions=["Maintain a consistent sleep routine and follow the plan."],
            confidence=0.81,
        )


class CaptureLLMProvider:
    def __init__(self) -> None:
        self.last_input = None

    def compose(self, draft_input):
        self.last_input = draft_input
        selected = [draft_input.candidate_products[0].sku_id] if draft_input.candidate_products else []
        return DraftCompositionResult(
            selected_sku_ids=selected,
            product_reason_overrides={},
            rationale=["统一分析流程测试。"],
            lifestyle_actions=["保持基础生活方式干预。"],
            section_overrides={
                "总体健康画像": ["模型判断当前应先围绕代谢与恢复能力做整体支持。"],
                "系统功能深度分析": ["模型结合报告和问卷信息，提示当前代谢负担与生活方式因素相互叠加。"],
            },
            confidence=0.66,
        )


class RecommendationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "功能医学相关资料").mkdir(parents=True, exist_ok=True)
        self.settings = AppSettings(
            project_root=root,
            data_dir=Path(__file__).resolve().parents[1] / "app" / "data",
            runtime_dir=root / ".runtime",
            upload_dir=root / ".runtime" / "uploads",
            report_export_dir=root / ".runtime" / "reports",
            sqlite_path=root / ".runtime" / "test.sqlite3",
            knowledge_root=root / "功能医学相关资料",
            report_reference_path=root / "0316测试报告1.pdf",
        )
        self.container = build_container(self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _project_semantic_support(
        self,
        case,
        *,
        goal_code: str,
        system_id: str,
        text: str,
        evidence_ref: str = "clinical_summary:main",
        support_direction: SupportDirection = SupportDirection.unknown,
    ) -> CaseAnalysis:
        analysis = CaseAnalysis(
            id=f"analysis-{case.id}-{goal_code}",
            case_id=case.id,
            status=AnalysisStatus.reviewed,
            snapshot_hash="synthetic",
            model_version="synthetic",
            reviewed_case_summary=text,
            support_goal_version="support-goals-v1-2026-07-19",
            support_needs=[
                SemanticSupportNeed(
                    id=f"support-{case.id}-{goal_code}",
                    support_need_text=text,
                    support_goal_code=goal_code,
                    support_direction=support_direction,
                    system_id=system_id,
                    evidence_refs=[
                        SemanticEvidenceReference(
                            ref=evidence_ref,
                            evidence_strength=SemanticEvidenceStrength.contextual,
                        )
                    ],
                    evidence_strength=SemanticEvidenceStrength.contextual,
                    corroboration_count=1,
                    rationale=text,
                    model_confidence=0.62,
                    eligibility_status=SupportEligibilityStatus.eligible,
                )
            ],
            final_structured_system_findings=[
                StructuredSystemFinding(
                    system_id=system_id,
                    system_name=system_id,
                    priority_level="优先级高",
                    priority_score=45,
                    summary=text,
                )
            ],
        )
        self.container.repository.save_case_analysis(analysis)
        self.container.case_analysis_service._project_review_to_case(case, analysis)
        return analysis

    def test_dosage_matching_requires_review_for_high_risk_context(self) -> None:
        product = self.container.repository.get_product("sku_vitamin_d3_k")
        self.assertIsNotNone(product)
        context = RecommendationContext(
            markers_by_code={},
            clinical_findings=[],
            clinical_findings_by_code={},
            clinical_findings_by_system={},
            support_goal_findings={},
            goals=set(),
            chief_concerns=set(),
            family_history=set(),
            symptoms=set(),
            conditions={"肾功能异常"},
            medications={"warfarin"},
            allergies=set(),
            food_sensitivities=set(),
            pregnancy=False,
            age=16,
            lifestyle_tags=set(),
            msq_system_scores={},
            clinical_summary_text="",
            summary_nutrient_hints=[],
        )
        dosage = self.container.recommendation_service._resolve_dosage(product, context)
        warnings = self.container.recommendation_service._product_safety_warnings(product, context)
        self.assertIn("医生复核剂量", dosage)
        self.assertTrue(any("未成年人" in warning for warning in warnings))
        self.assertTrue(any("用药" in warning for warning in warnings))

    def _prepare_case(self, report_text: str, questionnaire: Questionnaire):
        case = self.container.case_service.create_case(
            customer_name="测试用户",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        uploaded_file = UploadedFile(
            id="file_demo",
            case_id=case.id,
            filename="report.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://report.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="report.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
        )
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": extraction.text, "missing_fields": []}],
            normalized_lab_items=lab_items,
            missing_fields=[],
            review_notes="unit-test",
        )
        self.container.case_service.submit_questionnaire(case.id, questionnaire)
        return case

    def test_generates_grounded_recommendations_with_catalog_only(self) -> None:
        case = self._prepare_case(
            "25-OH维生素D 18 ng/mL 30-100\n空腹血糖 6.2 mmol/L 3.9-5.6\nhs-CRP 4.2 mg/L 0-3",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳", "便秘"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["血糖平衡", "免疫支持", "睡眠恢复"],
                sleep_hours=5.5,
                sleep_quality="差",
                bowel_habits="便秘",
                stress_level="high",
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        catalog_ids = {product.sku_id for product in self.container.repository.list_products()}
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertFalse(draft.abstain_reason)
        self.assertTrue(recommended_ids)
        self.assertTrue(recommended_ids.issubset(catalog_ids))
        self.assertIn("sku_vitamin_d3_k", recommended_ids)
        self.assertTrue(all(item.warnings for item in draft.recommended_skus))
        self.assertTrue(
            any("注意/禁忌：" in item for item in draft.report_sections.get("首月营养素干预方案", []))
        )

    def test_bmi_low_hard_excludes_weight_support_even_when_glucose_is_high(self) -> None:
        case = self._prepare_case(
            "BMI 17.4 kg/m2 18.5-24\n空腹血糖 6.2 mmol/L 3.9-5.6",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["餐后困倦"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["血糖平衡"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertNotIn("sku_weight_support", {item.sku_id for item in draft.recommended_skus})
        decision = next(
            item for item in draft.safety_decisions if item.rule_id == "case_bmi_low_excludes_weight_support"
        )
        self.assertEqual(decision.action.value, "exclude")
        self.assertEqual(decision.sku_id, "sku_weight_support")

    def test_doctor_confirmed_underweight_excludes_weight_support_without_bmi_marker(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="体重安全事实测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        underweight = AbnormalFinding(
            id="finding-underweight-explicit",
            name="体重过轻",
            result_text="BMI 17.65 kg/m²",
            abnormal_flag="low",
            source_file_id="file-report",
            source_file_name="report.pdf",
            source_page=1,
            source_text="BMI 17.65 kg/m²，体重过轻",
            confidence=0.97,
            finding_code_candidate="underweight",
            system_id_candidates=["endocrine_metabolic"],
            support_goal_candidates=["weight_metabolism"],
            mapping_confidence=0.94,
        )
        standardized = self.container.case_analysis_service.standardization_service.standardize(
            underweight,
            doctor_confirmed=True,
        )
        analysis = CaseAnalysis(
            id="analysis-underweight-explicit",
            case_id=case.id,
            status=AnalysisStatus.reviewed,
            snapshot_hash="synthetic",
            model_version="synthetic",
            reviewed_abnormal_findings=[standardized],
            support_needs=[
                SemanticSupportNeed(
                    id="support-conflicting-weight-loss",
                    support_need_text="体重与脂肪代谢支持",
                    support_goal_code="weight_metabolism",
                    support_direction=SupportDirection.decrease,
                    system_id="endocrine_metabolic",
                    evidence_refs=[
                        SemanticEvidenceReference(
                            ref="finding:finding-underweight-explicit",
                            evidence_strength=SemanticEvidenceStrength.explicit_conclusion,
                        )
                    ],
                    evidence_strength=SemanticEvidenceStrength.explicit_conclusion,
                    corroboration_count=1,
                    rationale="模拟模型方向错误。",
                    model_confidence=0.91,
                    eligibility_status=SupportEligibilityStatus.eligible,
                )
            ],
        )
        self.container.repository.save_case_analysis(analysis)
        self.container.case_analysis_service._project_review_to_case(case, analysis)

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertNotIn("sku_weight_support", {item.sku_id for item in draft.recommended_skus})
        decision = next(
            item
            for item in draft.safety_decisions
            if item.rule_id == "case_bmi_low_excludes_weight_support"
        )
        self.assertEqual(decision.action.value, "exclude")

    def test_directionless_weight_goal_cannot_activate_weight_loss_product(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="体重方向测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self._project_semantic_support(
            case,
            goal_code="weight_metabolism",
            system_id="endocrine_metabolic",
            text="体重与脂肪代谢需要关注",
            support_direction=SupportDirection.unknown,
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertNotIn("sku_weight_support", {item.sku_id for item in draft.recommended_skus})

    def test_validated_directional_semantic_need_can_activate_weight_product(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="语义减重方向测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self._project_semantic_support(
            case,
            goal_code="weight_metabolism",
            system_id="endocrine_metabolic",
            text="报告明确提示体脂偏高，需要健康减脂支持",
            support_direction=SupportDirection.decrease,
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertIn("sku_weight_support", {item.sku_id for item in draft.recommended_skus})

    def test_safety_rules_are_local_and_complete_under_one_hundred_milliseconds(self) -> None:
        case = self._prepare_case(
            "BMI 17.4 kg/m2 18.5-24",
            Questionnaire(age=34, sex="female", medications=[], allergies=[]),
        )
        context = self.container.recommendation_service._build_context(
            self.container.case_service.get_case(case.id)
        )

        decisions, elapsed = self.container.recommendation_service.evaluate_safety_rules(context)

        self.assertLess(elapsed, 0.1)
        self.assertTrue(any(item.action.value == "exclude" for item in decisions["sku_weight_support"]))

    def test_positive_fatigue_symptom_and_msq_score_generate_energy_support(self) -> None:
        case = self._prepare_case(
            "白细胞 5.5 10^9/L 3.5-9.5",
            Questionnaire(
                symptoms=["容易疲劳虚弱，没精神（中等）"],
                msq_system_scores={"能量/活动": 3},
            ),
        )

        context = self.container.recommendation_service._build_context(
            self.container.case_service.get_case(case.id)
        )

        self.assertIn("energy_support", context.lifestyle_tags)
        self.assertEqual(context.msq_system_scores.get("能量/活动"), 3)

    def test_product_rows_follow_body_system_priority(self) -> None:
        case = self._prepare_case(
            "空腹血糖 6.2 mmol/L 3.9-5.6\nLDL-C 3.49 mmol/L 0-3.37\nhs-CRP 4.2 mg/L 0-3",
            Questionnaire(
                age=40,
                sex="female",
                symptoms=["疲劳"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["代谢支持", "免疫支持"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        ranks = [item.system_priority_rank for item in draft.recommended_skus if item.system_priority_rank is not None]

        self.assertEqual(ranks, sorted(ranks))
        fixed_names = {
            "消化系统/肠道", "肝脏/解毒系统", "免疫/炎症系统", "内分泌/代谢系统", "心血管系统",
            "呼吸系统", "神经/认知/睡眠系统", "骨骼/肌肉系统", "泌尿/肾脏系统", "生殖/妇科/乳腺系统", "皮肤/黏膜系统",
        }
        self.assertTrue({item.system_name for item in draft.structured_system_findings}.issubset(fixed_names))
        self.assertTrue(
            {item.priority_level for item in draft.structured_system_findings}.issubset({"最高优先级", "优先级高", "中度关注"})
        )
        for item in draft.structured_system_findings:
            for label in ("发现：", "含义：", "优先原因：", "干预方向："):
                self.assertIn(label, item.summary)

    def test_liposomal_vitamin_c_legacy_sku_is_removed_after_migration(self) -> None:
        enabled_ids = {product.sku_id for product in self.container.repository.list_products()}
        canonical = self.container.repository.get_product("sku_liposomal_vitamin_c_500")
        legacy = self.container.repository.get_product("sku_liposomal_vitamin_c_300")
        probiotic = self.container.repository.get_product("sku_probiotic_complex")
        legacy_probiotic = self.container.repository.get_product("sku_probiotics")

        self.assertIn("sku_liposomal_vitamin_c_500", enabled_ids)
        self.assertNotIn("sku_liposomal_vitamin_c_300", enabled_ids)
        self.assertIn("sku_probiotic_complex", enabled_ids)
        self.assertNotIn("sku_probiotics", enabled_ids)
        self.assertEqual(canonical.display_name, "脂质体维生素C")
        self.assertEqual(probiotic.display_name, "复合益生菌")
        self.assertIsNone(legacy)
        self.assertIsNone(legacy_probiotic)

    def test_product_sku_migration_rewrites_existing_draft_references(self) -> None:
        case = self._prepare_case(
            "hs-CRP 4.2 mg/L 0-3",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["恢复慢"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["免疫支持"],
            ),
        )
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        legacy_item = draft.recommended_skus[0].model_copy(
            update={
                "sku_id": "sku_liposomal_vitamin_c_300",
                "display_name": "脂质体维生素C",
            }
        )
        self.container.repository.save_draft(
            draft.model_copy(
                update={
                    "recommended_skus": [legacy_item],
                    "evidence_ids": ["product:sku_liposomal_vitamin_c_300"],
                }
            )
        )

        changed = self.container.repository.migrate_product_sku(
            "sku_liposomal_vitamin_c_300",
            "sku_liposomal_vitamin_c_500",
        )
        migrated = self.container.repository.get_draft(draft.id)

        self.assertGreaterEqual(changed, 1)
        self.assertEqual(migrated.recommended_skus[0].sku_id, "sku_liposomal_vitamin_c_500")
        self.assertEqual(migrated.evidence_ids, ["product:sku_liposomal_vitamin_c_500"])

    def test_product_safety_matrix_blocks_clear_contraindications(self) -> None:
        case = self._prepare_case(
            "hs-CRP 5.2 mg/L 0-3",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["关节疼痛", "炎症"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["抗炎支持"],
                pregnant_or_lactating=True,
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        contraindication_text = " ".join(draft.contraindications)

        self.assertNotIn("sku_super_anti_inflammatory", recommended_ids)
        self.assertIn("超级抗炎 被排除", contraindication_text)
        self.assertIn("pregnancy", contraindication_text)

    def test_product_safety_matrix_keeps_caution_items_with_warning(self) -> None:
        case = self._prepare_case(
            "空腹血糖 6.8 mmol/L 3.9-5.6\n糖化血红蛋白 6.1 % 4-6",
            Questionnaire(
                age=45,
                sex="male",
                symptoms=["餐后困倦", "嗜甜"],
                known_conditions=["胰岛素抵抗"],
                medications=["二甲双胍"],
                allergies=[],
                goals=["血糖平衡"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        blood_sugar_item = next(
            item for item in draft.recommended_skus if item.sku_id == "sku_blood_sugar_complex"
        )
        warnings_text = " ".join(blood_sugar_item.warnings)

        self.assertIn("sku_blood_sugar_complex", {item.sku_id for item in draft.recommended_skus})
        self.assertIn("降糖药物", warnings_text)
        self.assertIn("监测血糖", warnings_text)

    def test_client_metformin_rule_recalls_b_complex(self) -> None:
        case = self._prepare_case(
            "基础体检未见明显急性异常。",
            Questionnaire(
                age=45,
                sex="male",
                medications=["二甲双胍"],
                allergies=[],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        b_complex = next(item for item in draft.recommended_skus if item.sku_id == "sku_b_complex")

        self.assertTrue(
            any(evidence_id == "signal:client_recall_rcl_metformin_b12" for evidence_id in b_complex.evidence_ids)
        )
        self.assertIn("二甲双胍", b_complex.reason)

    def test_client_coronary_rule_recalls_fish_oil_without_forcing_coq10(self) -> None:
        case = self._prepare_case(
            "基础体检未见明显急性异常。",
            Questionnaire(
                age=58,
                sex="male",
                known_conditions=["冠心病", "支架术后"],
                medications=[],
                allergies=[],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        by_sku = {item.sku_id: item for item in draft.recommended_skus}

        self.assertIn("sku_fish_oil_rtg", by_sku)
        self.assertTrue(
            any(
                evidence_id == "signal:client_recall_rcl_coronary_cardiovascular_fish_oil"
                for evidence_id in by_sku["sku_fish_oil_rtg"].evidence_ids
            )
        )
        self.assertNotIn("sku_coq10", by_sku)
        self.assertTrue(any("不能替代" in warning for warning in by_sku["sku_fish_oil_rtg"].warnings))

    def test_client_statin_use_alone_does_not_recall_fish_oil_or_coq10(self) -> None:
        case = self._prepare_case(
            "基础体检未见明显急性异常。",
            Questionnaire(
                age=58,
                sex="male",
                medications=["阿托伐他汀"],
                allergies=[],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        by_sku = {item.sku_id: item for item in draft.recommended_skus}

        self.assertNotIn("sku_fish_oil_rtg", by_sku)
        self.assertNotIn("sku_coq10", by_sku)

    def test_client_statin_muscle_symptoms_add_nonmandatory_coq10_guidance(self) -> None:
        case = self._prepare_case(
            "基础体检未见明显急性异常。",
            Questionnaire(
                age=58,
                sex="male",
                symptoms=["近期肌肉酸痛"],
                medications=["阿托伐他汀"],
                allergies=[],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        guidance = " ".join(draft.report_sections.get("风险提示", []))
        coq10 = next(
            (item for item in draft.recommended_skus if item.sku_id == "sku_coq10"),
            None,
        )

        self.assertIn("排除其他病因后", guidance)
        self.assertIn("不属于常规或强制推荐", guidance)
        if coq10 is not None:
            self.assertFalse(
                any(
                    evidence_id.startswith("signal:client_recall_")
                    for evidence_id in coq10.evidence_ids
                )
            )

    def test_client_gut_dysbiosis_rule_recalls_probiotic_with_evidence_warning(self) -> None:
        case = self._prepare_case(
            "基础体检未见明显急性异常。",
            Questionnaire(
                age=38,
                sex="female",
                known_conditions=["菌群多样性低"],
                medications=[],
                allergies=[],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        probiotic = next(item for item in draft.recommended_skus if item.sku_id == "sku_probiotic_complex")

        self.assertTrue(any("菌群多样性低" in item for item in probiotic.warnings))
        self.assertTrue(
            any(
                evidence_id == "signal:client_recall_rcl_gut_dysbiosis_probiotic"
                for evidence_id in probiotic.evidence_ids
            )
        )

    def test_client_hashimoto_rule_recalls_selenium_with_warning(self) -> None:
        case = self._prepare_case(
            "基础体检未见明显急性异常。",
            Questionnaire(
                age=41,
                sex="female",
                known_conditions=["桥本氏甲状腺炎"],
                medications=[],
                allergies=[],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        selenium = next(item for item in draft.recommended_skus if item.sku_id == "sku_selenium_vitamin_e")

        self.assertTrue(any("不能替代甲状腺治疗" in warning for warning in selenium.warnings))

    def test_client_safety_exclusion_overrides_recall(self) -> None:
        case = self._prepare_case(
            "基础体检未见明显急性异常。",
            Questionnaire(
                age=67,
                sex="female",
                known_conditions=["骨质疏松"],
                medications=["华法林"],
                allergies=[],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertNotIn("sku_vitamin_d3_k", {item.sku_id for item in draft.recommended_skus})
        self.assertTrue(
            any(
                decision.rule_id == "client_h9_warfarin_vitamin_k_excludes"
                and decision.action.value == "exclude"
                for decision in draft.safety_decisions
            )
        )
        self.assertNotIn("VD3+K 被排除", " ".join(draft.report_sections.get("风险提示", [])))

    def test_client_infant_rule_excludes_all_adult_formulas(self) -> None:
        case = self._prepare_case(
            "基础体检未见明显急性异常。",
            Questionnaire(age=2, sex="male", medications=[], allergies=[]),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertFalse(draft.recommended_skus)
        infant_decisions = [
            decision
            for decision in draft.safety_decisions
            if decision.rule_id == "client_h8_infant_adult_formula_excludes"
        ]
        self.assertEqual(len(infant_decisions), 31)

    def test_validated_symptom_support_need_promotes_directly_relevant_nutrients(self) -> None:
        case = self._prepare_case(
            "基础体检未见明显急性异常。",
            Questionnaire(
                age=38,
                sex="female",
                symptoms=["入睡困难", "夜醒", "紧张"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["睡眠恢复"],
                sleep_quality="差",
                stress_level="high",
            ),
        )
        self._project_semantic_support(
            case,
            goal_code="sleep_stress",
            system_id="neuro_sleep",
            text="持续入睡困难、夜醒与紧张提示睡眠压力恢复需求。",
            evidence_ref="questionnaire:symptoms",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = [item.sku_id for item in draft.recommended_skus]
        evidence_details = " ".join(
            detail
            for item in draft.recommended_skus
            for detail in item.evidence_details
        )

        self.assertIn("sku_sleep_support", recommended_ids[:4])
        self.assertIn("支持需求", evidence_details)

    def test_gut_semantic_need_does_not_activate_glucose_or_weight_products(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="肠道语义支持测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.update_clinical_summary(
            case.id,
            clinical_summary_text="β-葡萄糖醛酸酶升高，报告解释提示肠道菌群代谢需要关注。",
            actor_id="unit-test",
        )
        case = self.container.case_service.get_case(case.id)
        self._project_semantic_support(
            case,
            goal_code="gut_microbiome",
            system_id="digestive_gut",
            text="肠道菌群生态与相关代谢支持需求。",
            support_direction=SupportDirection.decrease,
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertIn("sku_herbal_antimicrobial", recommended_ids)
        self.assertNotIn("sku_blood_sugar_complex", recommended_ids)
        self.assertNotIn("sku_weight_support", recommended_ids)

    def test_gut_microbiome_direction_selects_probiotic_instead_of_antimicrobial(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="菌群恢复方向测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(age=36, sex="female", medications=[], allergies=[]),
        )
        case = self.container.case_service.get_case(case.id)
        self._project_semantic_support(
            case,
            goal_code="gut_microbiome",
            system_id="digestive_gut",
            text="菌群结构失衡并存在恢复与平衡肠道生态的支持需求。",
            support_direction=SupportDirection.restore,
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertIn("sku_probiotic_complex", recommended_ids)
        self.assertNotIn("sku_herbal_antimicrobial", recommended_ids)

    def test_digestive_enzyme_goal_does_not_activate_stomach_acid_product(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="消化酶方向测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(age=40, sex="male", medications=[], allergies=[]),
        )
        case = self.container.case_service.get_case(case.id)
        self._project_semantic_support(
            case,
            goal_code="digestive_enzyme",
            system_id="digestive_gut",
            text="餐后食物分解负担明显，需要消化酶与营养吸收支持。",
            support_direction=SupportDirection.increase,
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertIn("sku_digestive_enzymes", recommended_ids)
        self.assertNotIn("sku_stomach_acid_support", recommended_ids)

    def test_gastric_acid_goal_does_not_activate_digestive_enzyme_product(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="胃酸方向测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(age=40, sex="male", medications=[], allergies=[]),
        )
        case = self.container.case_service.get_case(case.id)
        self._project_semantic_support(
            case,
            goal_code="gastric_acid",
            system_id="digestive_gut",
            text="存在明确的低胃酸表现，需要恢复胃酸分泌支持。",
            support_direction=SupportDirection.restore,
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertIn("sku_stomach_acid_support", recommended_ids)
        self.assertNotIn("sku_digestive_enzymes", recommended_ids)

    def test_unknown_gut_microbiome_direction_does_not_activate_direction_bound_products(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="菌群未知方向测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(age=36, sex="female", medications=[], allergies=[]),
        )
        case = self.container.case_service.get_case(case.id)
        self._project_semantic_support(
            case,
            goal_code="gut_microbiome",
            system_id="digestive_gut",
            text="报告仅提示菌群相关变化，未明确需要补充、恢复或抑制。",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertNotIn("sku_probiotic_complex", recommended_ids)
        self.assertNotIn("sku_herbal_antimicrobial", recommended_ids)

    def test_independent_enzyme_and_microbiome_needs_can_recommend_both_products(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="消化酶与菌群联合测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(age=38, sex="female", medications=[], allergies=[]),
        )
        needs = [
            SemanticSupportNeed(
                id=f"support-{case.id}-digestive-enzyme",
                support_need_text="餐后食物分解负担明显，需要消化酶支持。",
                support_goal_code="digestive_enzyme",
                support_direction=SupportDirection.increase,
                system_id="digestive_gut",
                evidence_refs=[
                    SemanticEvidenceReference(
                        ref="clinical_summary:digestive_enzyme",
                        evidence_strength=SemanticEvidenceStrength.contextual,
                    )
                ],
                evidence_strength=SemanticEvidenceStrength.contextual,
                corroboration_count=1,
                rationale="消化酶支持需求",
                model_confidence=0.7,
                eligibility_status=SupportEligibilityStatus.eligible,
            ),
            SemanticSupportNeed(
                id=f"support-{case.id}-microbiome",
                support_need_text="菌群结构失衡，需要恢复肠道生态。",
                support_goal_code="gut_microbiome",
                support_direction=SupportDirection.restore,
                system_id="digestive_gut",
                evidence_refs=[
                    SemanticEvidenceReference(
                        ref="clinical_summary:microbiome",
                        evidence_strength=SemanticEvidenceStrength.contextual,
                    )
                ],
                evidence_strength=SemanticEvidenceStrength.contextual,
                corroboration_count=1,
                rationale="菌群恢复支持需求",
                model_confidence=0.7,
                eligibility_status=SupportEligibilityStatus.eligible,
            ),
        ]
        analysis = CaseAnalysis(
            id=f"analysis-{case.id}-digestive-combination",
            case_id=case.id,
            status=AnalysisStatus.reviewed,
            snapshot_hash="synthetic",
            model_version="synthetic",
            reviewed_case_summary="同时存在消化酶和菌群恢复支持需求。",
            support_goal_version="support-goals-v5-digestive-products-2026-07-22",
            support_needs=needs,
            final_structured_system_findings=[
                StructuredSystemFinding(
                    system_id="digestive_gut",
                    system_name="digestive_gut",
                    priority_level="最高优先级",
                    priority_score=70,
                    summary="消化系统存在两项相互独立的支持需求。",
                )
            ],
        )
        self.container.repository.save_case_analysis(analysis)
        self.container.case_analysis_service._project_review_to_case(case, analysis)

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertIn("sku_digestive_enzymes", recommended_ids)
        self.assertIn("sku_probiotic_complex", recommended_ids)

    def test_probiotic_allergy_excludes_only_probiotic_product(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="益生菌过敏安全测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(age=36, sex="female", medications=[], allergies=["布拉酵母"]),
        )
        case = self.container.case_service.get_case(case.id)
        self._project_semantic_support(
            case,
            goal_code="gut_microbiome",
            system_id="digestive_gut",
            text="菌群结构失衡并存在恢复肠道生态的支持需求。",
            support_direction=SupportDirection.restore,
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(age=36, sex="female", medications=[], allergies=["布拉酵母"]),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        decisions = [item for item in draft.safety_decisions if item.sku_id == "sku_probiotic_complex"]

        self.assertNotIn("sku_probiotic_complex", recommended_ids)
        self.assertTrue(any(item.rule_id == "probiotic_complex_component_allergy_excludes" for item in decisions))

    def test_doubao_config_does_not_enable_draft_composer_by_default(self) -> None:
        root = Path(self.temp_dir.name)
        settings = AppSettings(
            project_root=root,
            data_dir=Path(__file__).resolve().parents[1] / "app" / "data",
            runtime_dir=root / ".runtime",
            upload_dir=root / ".runtime" / "uploads",
            report_export_dir=root / ".runtime" / "reports",
            sqlite_path=root / ".runtime" / "llm-disabled.sqlite3",
            knowledge_root=root / "功能医学相关资料",
            report_reference_path=root / "0316测试报告1.pdf",
            llm_base_url="https://ark.cn-beijing.volces.com/api/v3",
            llm_api_key="test-key",
            llm_model="doubao-seed-2-0-lite-260215",
            llm_api_style="responses",
            rag_llm_fusion_enabled=True,
        )

        container = build_container(settings)

        self.assertIsInstance(container.recommendation_service.llm_provider, GroundedDraftComposer)
        self.assertEqual(container.recommendation_service.model_version, "local-structured-v1")
        self.assertIsNotNone(container.review_service.rag_fusion_provider)

    def test_generates_recommendations_without_questionnaire_when_report_is_reviewed(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="甲状腺案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        report_text = "甲状腺球蛋白抗体 7.5701 IU/mL 0.3-4.5\n甲状腺过氧化物酶抗体 329.001 IU/mL 0-95\n促甲状腺激素 2.64 mIU/L 0.27-4.2"
        uploaded_file = UploadedFile(
            id="file_thyroid",
            case_id=case.id,
            filename="thyroid.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://thyroid.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="thyroid.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
        )
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": extraction.text, "missing_fields": []}],
            normalized_lab_items=lab_items,
            missing_fields=[],
            review_notes="unit-test",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = [item.sku_id for item in draft.recommended_skus]
        first_system_heading = next(
            item for item in draft.report_sections["功能医学系统失衡分析"] if item.startswith("### 1.")
        )

        self.assertFalse(draft.abstain_reason)
        self.assertIn("未填写问卷，当前草案仅依据已上传报告和人工校对结果生成。", draft.missing_info)
        self.assertTrue({"sku_thyroid_support", "sku_selenium_vitamin_e"} & set(recommended_ids))
        self.assertEqual(recommended_ids[0], "sku_thyroid_support")
        self.assertIn("免疫/炎症系统", first_system_heading)
        self.assertNotIn("免疫系统/甲状腺", first_system_heading)

    def test_uses_case_customer_name_even_when_upload_contains_name_candidate(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="建档客户甲",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        uploaded_file = UploadedFile(
            id="file_named_questionnaire",
            case_id=case.id,
            filename="MSQ--上传姓名乙.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=128,
            storage_uri="memory://MSQ--上传姓名乙.docx",
            raw_extracted_text="姓名：上传姓名乙 性别：男 年龄：30",
        )
        case = self.container.case_service.add_uploaded_file(case.id, uploaded_file)

        self.assertEqual(self.container.recommendation_service._resolve_customer_name(case), "建档客户甲")
        self.assertEqual(self.container.review_service._customer_display_name(case), "建档客户甲")

    def test_falls_back_to_case_customer_name_when_report_text_has_no_valid_name(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="建档客户乙",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        uploaded_file = UploadedFile(
            id="file_report_intro",
            case_id=case.id,
            filename="体检报告2024.pdf",
            content_type="application/pdf",
            size_bytes=256,
            storage_uri="memory://体检报告2024.pdf",
            raw_extracted_text="现将您的健康体检报告呈上，请您着重关注本次体检的异常情况。",
        )
        case = self.container.case_service.add_uploaded_file(case.id, uploaded_file)

        self.assertEqual(self.container.recommendation_service._resolve_customer_name(case), "建档客户乙")
        self.assertEqual(self.container.review_service._customer_display_name(case), "建档客户乙")

    def test_keeps_internal_candidate_products_before_manual_parse_review(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="待校对甲状腺案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        report_text = "促甲状腺激素 TSH 7.57 uIU/mL 0.3-4.5\n甲状腺过氧化物酶抗体 anti-TPO 854 IU/mL 0-30"
        uploaded_file = UploadedFile(
            id="file_unreviewed_thyroid",
            case_id=case.id,
            filename="unreviewed-thyroid.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://unreviewed-thyroid.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="unreviewed-thyroid.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(
                age=33,
                sex="female",
                symptoms=["疲劳"],
                known_conditions=["甲状腺功能异常"],
                medications=[],
                allergies=[],
                goals=["免疫支持"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertFalse(draft.abstain_reason)
        self.assertIn("尚未完成人工解析校对。", draft.missing_info)
        self.assertTrue(draft.manual_review_required)
        self.assertTrue(draft.recommended_skus)
        self.assertNotIn("当前暂无明确可发布的首月营养素组合", " ".join(draft.report_sections["首月营养素干预方案"]))

    def test_parse_warnings_stay_out_of_customer_report(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="解析提醒隔离",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        report_text = "25-OH维生素D 18 ng/mL 30-100"
        uploaded_file = UploadedFile(
            id="file_parse_warning",
            case_id=case.id,
            filename="report.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://report.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="report.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
            parse_warnings=["疑似未识别指标：示例指标A 88.8 example-unit 1-2"],
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(
                age=33,
                sex="female",
                symptoms=["疲劳"],
                medications=[],
                allergies=[],
                goals=["免疫支持"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        report = self.container.review_service._render_report(
            draft.model_copy(update={"missing_info": draft.missing_info + ["疑似未识别指标：示例指标A 88.8 example-unit 1-2"]}),
            self.container.case_service.get_case(case.id),
        )

        self.assertIn("疑似未识别指标", " ".join(draft.missing_info))
        self.assertNotIn("疑似未识别指标", report)
        self.assertIn("尚未完成人工解析校对", report)

    def test_stale_normal_parse_warnings_are_rechecked_before_draft_display(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="解析提醒复核",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        report_text = "25-OH维生素D 18 ng/mL 30-100"
        uploaded_file = UploadedFile(
            id="file_stale_parse_warning",
            case_id=case.id,
            filename="report.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://report.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="report.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
            parse_warnings=[
                "疑似未识别指标：服务热线:010-00000000",
                "疑似未识别指标：体重 12.34 kg 0-999",
                "疑似未识别指标：血小板 PLT 123.45 0-999",
                "疑似未识别指标：中性粒细胞 NEUT 12.34 0-999",
            ],
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(
                age=33,
                sex="female",
                symptoms=["疲劳"],
                medications=[],
                allergies=[],
                goals=["免疫支持"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        joined_missing = " ".join(draft.missing_info)

        self.assertNotIn("服务热线", joined_missing)
        self.assertNotIn("体重", joined_missing)
        self.assertNotIn("血小板", joined_missing)
        self.assertNotIn("NEUT", joined_missing)

    def test_parse_warning_is_hidden_when_same_row_is_already_key_indicator(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="关键指标去重",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        report_text = "25-OH维生素D 18 ng/mL 30-100"
        uploaded_file = UploadedFile(
            id="file_key_indicator_warning",
            case_id=case.id,
            filename="report.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://report.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="report.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
            parse_warnings=["疑似未识别指标：示例肠道指标 ABCX 12 xunit 0-10"],
        )
        case = self.container.case_service.get_case(case.id)
        case.manual_indicators = [
            CaseIndicator(
                indicator_name="示例肠道指标",
                result_text="12 xunit",
                status=IndicatorStatus.attention,
                category="lab",
                source_span=SourceSpan(
                    file_name="report.txt",
                    page=1,
                    line_number=1,
                    snippet="示例肠道指标 ABCX 12 xunit 0-10",
                ),
            )
        ]
        self.container.repository.save_case(case)
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(
                age=33,
                sex="female",
                symptoms=["疲劳"],
                medications=[],
                allergies=[],
                goals=["免疫支持"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertNotIn("示例肠道指标", " ".join(draft.missing_info))

    def test_thyroid_condition_in_questionnaire_does_not_break_profile_matching(self) -> None:
        case = self._prepare_case(
            "空腹血糖 5.1 mmol/L 3.9-6.1",
            Questionnaire(
                age=34,
                sex="female",
                known_conditions=["桥本氏甲状腺炎", "甲减"],
                symptoms=["疲劳"],
                medications=[],
                allergies=[],
                goals=["睡眠恢复"],
            ),
        )

        matched_rules = self.container.assistant_rule_service.match_rules_for_case(
            self.container.case_service.get_case(case.id)
        )

        self.assertIsInstance(matched_rules, list)
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        self.assertIsNotNone(draft)

    def test_generates_lipid_pattern_recommendations_from_report_only(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="血脂案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        report_text = "非高密度脂蛋白胆固醇 5.84 mmol/L 0-4.1\n甘油三酯 1.45 mmol/L 0.56-1.71\n载脂蛋白A1 1.59 g/L 1.2-1.6"
        uploaded_file = UploadedFile(
            id="file_lipid",
            case_id=case.id,
            filename="lipid.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://lipid.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="lipid.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
        )
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": extraction.text, "missing_fields": []}],
            normalized_lab_items=lab_items,
            missing_fields=[],
            review_notes="unit-test",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertFalse(draft.abstain_reason)
        self.assertTrue({"sku_fish_oil_rtg", "sku_cardiac_support"} & recommended_ids)

    def test_generates_iron_pattern_recommendations_from_report_only(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="缺铁案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        report_text = "铁蛋白 11 ng/mL 12-150\n血清铁 6.2 umol/L 9-27\n血红蛋白 102 g/L 110-150\n平均红细胞体积 75 fL 80-100"
        uploaded_file = UploadedFile(
            id="file_iron",
            case_id=case.id,
            filename="iron.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://iron.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="iron.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
        )
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": extraction.text, "missing_fields": []}],
            normalized_lab_items=lab_items,
            missing_fields=[],
            review_notes="unit-test",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertFalse(draft.abstain_reason)
        self.assertTrue({"sku_plant_multi_mineral", "sku_liposomal_vitamin_c_500"} & recommended_ids)

    def test_generates_report_from_manual_clinical_summary_without_questionnaire(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="总结诊断案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.update_clinical_summary(
            case.id,
            clinical_summary_text=(
                "脂肪酸代谢不佳：细胞无法有效的将脂肪燃烧生成能量。\n"
                "碳水化合物代谢不佳：细胞无法有效的将碳水化合物燃烧成能量。\n"
                "细胞能量生成反应不佳：将营养成分转换成能量的代谢过程效率不佳。"
            ),
            actor_id="unit-test",
        )
        case = self.container.case_service.get_case(case.id)
        self._project_semantic_support(
            case,
            goal_code="energy_mitochondria",
            system_id="neuro_sleep",
            text="医生总结明确记录细胞能量生成与疲劳恢复支持需求。",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        guidance_section = draft.report_sections.get("原报告小结与建议") or []
        guidance_text = " ".join(guidance_section if isinstance(guidance_section, list) else [str(guidance_section)])

        self.assertFalse(draft.abstain_reason)
        self.assertTrue(recommended_ids)
        self.assertTrue(
            {"sku_mitochondrial_support", "sku_coq10"}
            & recommended_ids
        )
        self.assertIn("人工录入评估结论", guidance_text)
        self.assertIn("病例总结诊断", " ".join(draft.case_summary))

    def test_manual_anti_aging_summary_is_integrated_into_clinical_sections(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="抗衰摘要案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.update_clinical_summary(
            case.id,
            clinical_summary_text=(
                "DNA甲基化年龄 31.0 岁，免疫系统年龄 31.5 岁，"
                "心血管系统年龄 33.1 岁，内分泌系统年龄 35.8 岁；"
                "代表基因 KLF14、ELOVL2、TRIM59。"
            ),
            actor_id="unit-test",
        )
        case = self.container.case_service.get_case(case.id)
        self._project_semantic_support(
            case,
            goal_code="foundational",
            system_id="endocrine_metabolic",
            text="医生总结明确列出多种基础维生素、矿物质与氨基酸营养支持需求。",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        portrait_text = " ".join(draft.report_sections.get("核心结论与健康画像") or [])
        analysis_text = " ".join(draft.report_sections.get("功能医学系统失衡分析") or [])

        self.assertIn("一句话健康画像", portrait_text)
        self.assertNotIn("抗衰系统整合", analysis_text)
        self.assertTrue(any(name in analysis_text for name in ("免疫/炎症系统", "心血管系统", "内分泌/代谢系统")))

    def test_manual_summary_nutrient_list_influences_report_and_candidates(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="营养素清单案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.update_clinical_summary(
            case.id,
            clinical_summary_text=(
                "细胞能量生成反应不佳：将营养成分转换成能量的代谢过程效率不佳。\n"
                "所需要的营养素\n"
                "分类\n"
                "蛋白质(Protein)\n"
                "肉碱(Carnitine)\n"
                "谷氨酰胺(Glutamine)\n"
                "维生素(Vitamin)\n"
                "B1(硫胺素, Thiamine)\n"
                "B2(核黄素, Riboflavin)\n"
                "B6(吡哆醇, Pyridoxine)\n"
                "B12(Cobalamins)\n"
                "叶酸(Folic Acid)\n"
                "生物素(Biotin)\n"
                "矿物质(Mineral)\n"
            ),
            actor_id="unit-test",
        )
        case = self.container.case_service.get_case(case.id)
        self._project_semantic_support(
            case,
            goal_code="foundational",
            system_id="endocrine_metabolic",
            text="医生总结明确列出多种基础维生素、矿物质与氨基酸营养支持需求。",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        guidance_text = " ".join(draft.report_sections.get("原报告小结与建议") or [])
        analysis_text = " ".join(draft.report_sections.get("功能医学系统失衡分析") or [])
        summary_text = " ".join(draft.case_summary)

        self.assertFalse(draft.abstain_reason)
        self.assertTrue(
            {"sku_plant_multi_mineral"} & recommended_ids
        )
        self.assertIn("病例总结提示的所需营养素", guidance_text)
        self.assertIn("蛋白质", guidance_text)
        self.assertIn("所需营养素提示", summary_text)
        self.assertIn("内分泌/代谢系统", analysis_text)
        self.assertNotIn("排序", analysis_text)
        self.assertNotIn("候选", analysis_text)
        self.assertNotIn("挤掉", analysis_text)

    def test_customer_system_analysis_hides_internal_ranking_language(self) -> None:
        case = self._prepare_case(
            "25-OH维生素D 18 ng/mL 30-100\n"
            "空腹血糖 6.2 mmol/L 3.9-5.6\n"
            "总胆固醇 5.83 mmol/L 0-5.18",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["免疫支持", "代谢支持"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        analysis_text = " ".join(draft.report_sections.get("功能医学系统失衡分析") or [])

        self.assertIn("免疫/炎症系统", analysis_text)
        self.assertIn("维生素D", analysis_text)
        self.assertNotIn("维生素D/骨代谢与免疫支持", analysis_text)
        for internal_phrase in ("排序", "候选", "挤掉", "泛化", "产品优先级", "不应被"):
            self.assertNotIn(internal_phrase, analysis_text)

    def test_system_analysis_shows_each_core_system_with_any_problem_signal(self) -> None:
        case = self._prepare_case(
            "LDL-C 3.49 mmol/L 0.00-3.37 ↑",
            Questionnaire(
                age=40,
                sex="female",
                symptoms=[],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=[],
                sitting_hours_per_day=7,
                sleep_hours=7,
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        analysis_text = " ".join(draft.report_sections.get("功能医学系统失衡分析") or [])

        self.assertIn("内分泌/代谢系统", analysis_text)
        self.assertIn("心血管系统", analysis_text)
        self.assertNotIn("免疫系统/甲状腺", analysis_text)
        self.assertNotIn("消化系统/肠道", analysis_text)

    def test_first_month_dosage_uses_single_structured_default_tier(self) -> None:
        fish_oil = self.container.repository.get_product("sku_fish_oil_rtg")
        glutathione_support = self.container.repository.get_product("sku_amino_acid_detox")

        fish_oil_dosage = self.container.recommendation_service._first_month_dosage(fish_oil)
        glutathione_dosage = self.container.recommendation_service._first_month_dosage(glutathione_support)
        self.assertIn("每日1 粒", fish_oil_dosage)
        self.assertNotIn("血脂偏高", fish_oil_dosage)
        self.assertIn("每日1 粒", glutathione_dosage)
        self.assertNotIn("强化调理", glutathione_dosage)

    def test_product_tag_matrix_prioritizes_precise_liver_detox_products(self) -> None:
        case = self._prepare_case(
            "血清尿酸 458.3 umol/L 208-428\n"
            "甘油三酯 2.3 mmol/L 0.56-1.7\n"
            "总胆固醇 5.83 mmol/L 0-5.18\n"
            "ALT 45 U/L 0-40",
            Questionnaire(
                age=45,
                sex="male",
                symptoms=["疲劳", "腹胀"],
                known_conditions=["脂肪肝"],
                medications=[],
                allergies=[],
                goals=["肝脏支持", "代谢支持"],
                bowel_habits="便秘",
                dining_out_frequency="经常",
                stress_level="high",
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = [item.sku_id for item in draft.recommended_skus]
        reason_by_id = {item.sku_id: item.reason for item in draft.recommended_skus}

        self.assertFalse(draft.abstain_reason)
        self.assertGreaterEqual(len(recommended_ids), 3)
        self.assertEqual(recommended_ids[0], "sku_liver_detox_support")
        self.assertIn("sku_amino_acid_detox", recommended_ids[:3])
        self.assertIn("sku_bile_flow_support", recommended_ids)
        self.assertIn("关联度约", reason_by_id["sku_liver_detox_support"])
        self.assertIn("肝脏/解毒系统", reason_by_id["sku_liver_detox_support"])
        self.assertNotIn("sku_digestive_enzymes", recommended_ids)
        self.assertNotIn("sku_probiotic_complex", recommended_ids)
        first_system_heading = next(
            item for item in draft.report_sections["功能医学系统失衡分析"] if item.startswith("### 1.")
        )
        self.assertIn("肝脏/解毒系统", first_system_heading)
        self.assertIn("首月原则：先围绕肝脏/解毒", draft.report_sections["首月营养素干预方案"][0])

    def test_liver_report_guidance_prioritizes_liver_products_over_fish_oil(self) -> None:
        case = self._prepare_case(
            "总检结论 脂肪肝，尿酸偏高，建议减少饮酒和油脂摄入\n"
            "低密度脂蛋白胆固醇 LDL-C 3.49 mmol/L 0.00-3.37 ↑\n"
            "甘油三酯 TG 1.97 mmol/L 0.56-1.7 ↑\n"
            "血清尿酸 458.3 umol/L 208-428 ↑",
            Questionnaire(
                age=42,
                sex="male",
                symptoms=["疲劳"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["心血管支持"],
                dining_out_frequency="经常",
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = [item.sku_id for item in draft.recommended_skus]

        self.assertFalse(draft.abstain_reason)
        first_system_heading = next(
            item for item in draft.report_sections["功能医学系统失衡分析"] if item.startswith("### 1.")
        )
        self.assertIn("肝脏/解毒系统", first_system_heading)
        self.assertIn("sku_fish_oil_rtg", recommended_ids)
        self.assertIn("sku_liver_detox_support", recommended_ids)
        self.assertLess(recommended_ids.index("sku_liver_detox_support"), recommended_ids.index("sku_fish_oil_rtg"))
        self.assertIn(recommended_ids[0], {"sku_liver_detox_support", "sku_amino_acid_detox"})

    def test_recommendation_output_tops_up_reasonable_candidates_when_core_items_are_few(self) -> None:
        case = self._prepare_case(
            "总胆固醇 5.83 mmol/L 0-5.18\n"
            "体质指数 24.5 18.5-23.9\n"
            "空腹血糖 6.2 mmol/L 3.9-5.6\n"
            "高密度胆固醇 1.77 mmol/L 1.03-1.55",
            Questionnaire(
                age=38,
                sex="female",
                symptoms=["精神压力大"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["代谢支持", "心血管支持"],
                sitting_hours_per_day=7,
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = [item.sku_id for item in draft.recommended_skus]

        self.assertFalse(draft.abstain_reason)
        self.assertGreaterEqual(len(recommended_ids), 4)
        self.assertIn("sku_fish_oil_rtg", recommended_ids)
        self.assertIn("sku_blood_sugar_complex", recommended_ids)
        self.assertTrue({"sku_cardiac_support", "sku_weight_support"} & set(recommended_ids))

    def test_product_tag_matrix_has_customer_sequences_and_digestive_products(self) -> None:
        profiles = self.container.recommendation_service.product_tag_profiles

        self.assertEqual(profiles["sku_liver_detox_support"].sequence, "27")
        self.assertEqual(profiles["sku_amino_acid_detox"].sequence, "31")
        self.assertEqual(profiles["sku_bile_flow_support"].sequence, "21")
        self.assertEqual(profiles["sku_immune_support"].product_name, "槲皮素复合物")
        self.assertEqual(profiles["sku_digestive_enzymes"].sequence, "25")
        self.assertEqual(profiles["sku_probiotic_complex"].sequence, "26")
        self.assertEqual(profiles["sku_digestive_enzymes"].primary_axes, ("digestive_enzyme",))
        self.assertEqual(profiles["sku_probiotic_complex"].primary_axes, ("gut_microbiome",))

    def test_signal_scoring_still_recommends_for_lipid_case_without_explicit_pattern_rule(self) -> None:
        fish_oil = self.container.repository.get_product("sku_fish_oil_rtg")
        cardiac = self.container.repository.get_product("sku_cardiac_support")
        self.container.repository.save_product(
            fish_oil.model_copy(
                update={
                    "indications": ["goal:抗炎支持", "goal:心血管支持"],
                }
            )
        )
        self.container.repository.save_product(
            cardiac.model_copy(
                update={
                    "indications": ["goal:心血管支持", "symptom:心悸"],
                }
            )
        )

        case = self.container.case_service.create_case(
            customer_name="高血脂智能推荐",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        report_text = "非高密度脂蛋白胆固醇 5.84 mmol/L 0-4.1\n甘油三酯 1.45 mmol/L 0.56-1.71\n高密度胆固醇 1.39 mmol/L 0.91-1.55"
        uploaded_file = UploadedFile(
            id="file_lipid_signal",
            case_id=case.id,
            filename="lipid-signal.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://lipid-signal.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="lipid-signal.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
        )
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": extraction.text, "missing_fields": []}],
            normalized_lab_items=lab_items,
            missing_fields=[],
            review_notes="unit-test",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertFalse(draft.abstain_reason)
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        self.assertTrue({"sku_fish_oil_rtg", "sku_cardiac_support"} & recommended_ids)
        self.assertTrue(any("signal:" in evidence_id for evidence_id in draft.evidence_ids))

    def test_signal_scoring_still_recommends_for_iron_case_without_explicit_marker_rule(self) -> None:
        multi = self.container.repository.get_product("sku_plant_multi_mineral")
        vitamin_c = self.container.repository.get_product("sku_liposomal_vitamin_c_500")
        self.container.repository.save_product(
            multi.model_copy(
                update={
                    "indications": ["goal:基础营养", "goal:免疫支持"],
                }
            )
        )
        self.container.repository.save_product(
            vitamin_c.model_copy(
                update={
                    "indications": ["goal:基础抗氧化", "goal:恢复支持"],
                }
            )
        )

        case = self.container.case_service.create_case(
            customer_name="缺铁智能推荐",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        report_text = "铁蛋白 11 ng/mL 12-150\n血清铁 6.2 umol/L 9-27\n血红蛋白 102 g/L 110-150\n平均红细胞体积 75 fL 80-100"
        uploaded_file = UploadedFile(
            id="file_iron_signal",
            case_id=case.id,
            filename="iron-signal.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://iron-signal.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="iron-signal.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
        )
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": extraction.text, "missing_fields": []}],
            normalized_lab_items=lab_items,
            missing_fields=[],
            review_notes="unit-test",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertFalse(draft.abstain_reason)
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        self.assertTrue({"sku_plant_multi_mineral", "sku_liposomal_vitamin_c_500"} & recommended_ids)
        self.assertTrue(any("signal:" in evidence_id for evidence_id in draft.evidence_ids))

    def test_display_only_lipid_strings_do_not_reconstruct_standard_markers(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="血脂展示层案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        corrected_text = "lipid indicator placeholder"
        uploaded_file = UploadedFile(
            id="file_lipid_indicator_only",
            case_id=case.id,
            filename="lipid-case.txt",
            content_type="text/plain",
            size_bytes=len(corrected_text.encode("utf-8")),
            storage_uri="memory://lipid-case.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": corrected_text, "missing_fields": []}],
            normalized_lab_items=[],
            missing_fields=[],
            review_notes="unit-test",
        )
        self.container.recommendation_service.indicator_service = SimpleNamespace(
            build=lambda _: [
                SimpleNamespace(
                    indicator_name="非高密度脂蛋白胆固醇",
                    result_text="5.84 mmol/L",
                    status=IndicatorStatus.attention,
                    source_span=SourceSpan(file_name="lipid-case.txt", page=1, line_number=1, snippet="非高密度脂蛋白胆固醇 5.84"),
                )
            ]
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertTrue(draft.abstain_reason)
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        self.assertFalse({"sku_fish_oil_rtg", "sku_cardiac_support"} & recommended_ids)

    def test_display_only_iron_strings_do_not_reconstruct_standard_markers(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="缺铁展示层案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        corrected_text = "iron indicator placeholder"
        uploaded_file = UploadedFile(
            id="file_iron_indicator_only",
            case_id=case.id,
            filename="iron-case.txt",
            content_type="text/plain",
            size_bytes=len(corrected_text.encode("utf-8")),
            storage_uri="memory://iron-case.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": corrected_text, "missing_fields": []}],
            normalized_lab_items=[],
            missing_fields=[],
            review_notes="unit-test",
        )
        self.container.recommendation_service.indicator_service = SimpleNamespace(
            build=lambda _: [
                SimpleNamespace(
                    indicator_name="铁蛋白",
                    result_text="11 ng/mL",
                    status=IndicatorStatus.attention,
                    source_span=SourceSpan(file_name="iron-case.txt", page=1, line_number=1, snippet="铁蛋白 11 12-150"),
                ),
                SimpleNamespace(
                    indicator_name="血清铁",
                    result_text="6.2 umol/L",
                    status=IndicatorStatus.attention,
                    source_span=SourceSpan(file_name="iron-case.txt", page=1, line_number=2, snippet="血清铁 6.2 9-27"),
                ),
            ]
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertTrue(draft.abstain_reason)
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        self.assertFalse({"sku_plant_multi_mineral", "sku_liposomal_vitamin_c_500"} & recommended_ids)

    def test_risk_notices_do_not_block_nutrition_recommendations(self) -> None:
        case = self._prepare_case(
            "空腹血糖 7.8 mmol/L 3.9-5.6\nALT 132 U/L 0-40",
            Questionnaire(
                age=16,
                sex="male",
                symptoms=["疲劳"],
                known_conditions=[],
                medications=["insulin"],
                allergies=[],
                goals=["血糖平衡"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertFalse(draft.abstain_reason)
        self.assertTrue(draft.recommended_skus)
        self.assertEqual(draft.red_flags, [])
        self.assertTrue(draft.report_sections["风险提示"])

    def test_remote_llm_output_is_filtered_to_local_catalog(self) -> None:
        self.container.recommendation_service.llm_provider = StubLLMProvider()
        self.container.recommendation_service.model_version = "remote:test-model"
        self.container.recommendation_service.prompt_version = "grounded-remote-report-v1"

        case = self._prepare_case(
            "25-OH维生素D 18 ng/mL 30-100\n空腹血糖 6.2 mmol/L 3.9-5.6",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["骨骼支持", "免疫支持"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertFalse(draft.abstain_reason)
        recommended_ids = [item.sku_id for item in draft.recommended_skus]
        self.assertEqual(set(recommended_ids), {"sku_blood_sugar_complex", "sku_vitamin_d3_k"})
        self.assertNotIn("sku_weight_support", recommended_ids)
        self.assertTrue(all("结合" in item.reason for item in draft.recommended_skus))
        self.assertTrue(all("product:sku_" not in item.reason for item in draft.recommended_skus))
        self.assertTrue(draft.evidence_details)
        self.assertTrue(any("VD3+K" in item for item in draft.evidence_details))
        self.assertTrue(all(any("\u4e00" <= ch <= "\u9fff" for ch in item) for item in draft.lifestyle_actions))
        self.assertEqual(draft.model_version, "remote:test-model")
        self.assertIn("核心结论与健康画像", draft.report_sections)
        self.assertIn("功能医学系统失衡分析", draft.report_sections)
        self.assertIn("后续检查建议", draft.report_sections)
        self.assertIn("90天健康路线图", draft.report_sections)
        self.assertNotIn("证据来源", draft.report_sections)

    def test_doctor_confirmed_llm_vitamin_d_finding_reaches_recommendation_engine(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="标准化链路测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        finding = AbnormalFinding(
            id="finding-vitamin-d-llm",
            name="维生素D减少",
            result_text="偏低",
            abnormal_flag="low",
            source_file_id="file-report",
            source_file_name="report.pdf",
            source_page=1,
            source_text="维生素D减少",
            confidence=0.95,
            marker_code_candidate="vitamin_d",
        )
        analysis = CaseAnalysis(
            id="analysis-vitamin-d-llm",
            case_id=case.id,
            snapshot_hash="synthetic",
            model_version="synthetic",
            reviewed_abnormal_findings=[finding],
        )
        self.container.case_analysis_service._project_review_to_case(case, analysis)
        projected = self.container.case_service.get_case(case.id)
        self.assertEqual([item.marker_code for item in projected.extracted_lab_items], ["vitamin_d"])

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        self.assertIn("sku_vitamin_d3_k", {item.sku_id for item in draft.recommended_skus})

    def test_validated_model_support_goal_can_select_product_without_sku_output(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="支持目标兜底测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        finding = AbnormalFinding(
            id="finding-model-bone-support",
            name="骨代谢支持需求",
            result_text="需要关注",
            abnormal_flag="positive",
            source_file_id="file-report",
            source_file_name="report.pdf",
            source_page=1,
            source_text="骨代谢支持需求",
            confidence=0.95,
            system_id_candidates=["bone_muscle"],
            support_goal_candidates=["vitamin_d_repletion"],
            mapping_confidence=0.92,
        )
        analysis = CaseAnalysis(
            id="analysis-model-bone-support",
            case_id=case.id,
            status=AnalysisStatus.reviewed,
            snapshot_hash="synthetic",
            model_version="synthetic",
            reviewed_abnormal_findings=[finding],
            support_goal_version="support-goals-v1-2026-07-19",
            support_needs=[
                SemanticSupportNeed(
                    id="support-model-bone",
                    support_need_text="维生素D与骨代谢支持",
                    support_goal_code="vitamin_d_repletion",
                    system_id="bone_muscle",
                    evidence_refs=[
                        SemanticEvidenceReference(
                            ref="finding:finding-model-bone-support",
                            evidence_strength=SemanticEvidenceStrength.explicit_conclusion,
                        )
                    ],
                    evidence_strength=SemanticEvidenceStrength.explicit_conclusion,
                    corroboration_count=1,
                    rationale="医生确认骨代谢支持需求。",
                    model_confidence=0.92,
                    eligibility_status=SupportEligibilityStatus.eligible,
                )
            ],
        )
        self.container.repository.save_case_analysis(analysis)
        self.container.case_analysis_service._project_review_to_case(case, analysis)
        projected = self.container.case_service.get_case(case.id)
        self.assertEqual(
            projected.confirmed_clinical_findings[0].standardization_status.value,
            "support_mapped",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        vitamin_d = next(item for item in draft.recommended_skus if item.sku_id == "sku_vitamin_d3_k")
        self.assertIn("finding-model-bone-support", vitamin_d.matched_finding_ids)
        self.assertIn("support-model-bone", vitamin_d.matched_support_need_ids)
        self.assertTrue(
            any("model_support_goal_vitamin_d_repletion" in item for item in vitamin_d.evidence_ids)
        )

    def test_product_selection_does_not_expose_catalog_to_draft_model(self) -> None:
        capture = CaptureLLMProvider()
        self.container.recommendation_service.llm_provider = capture
        self.container.recommendation_service.model_version = "remote:test-model"
        self.container.recommendation_service.prompt_version = "grounded-remote-report-v1"

        case = self.container.case_service.create_case(
            customer_name="统一分析流程案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        report_text = "非高密度脂蛋白胆固醇 5.84 mmol/L 0-4.1\n甘油三酯 1.45 mmol/L 0.56-1.71\n高密度胆固醇 1.39 mmol/L 0.91-1.55"
        uploaded_file = UploadedFile(
            id="file_model_primary",
            case_id=case.id,
            filename="model-primary.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://model-primary.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="model-primary.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
        )
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": extraction.text, "missing_fields": []}],
            normalized_lab_items=lab_items,
            missing_fields=[],
            review_notes="unit-test",
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        self.assertFalse(draft.abstain_reason)
        self.assertIsNone(capture.last_input)
        self.assertTrue(draft.recommended_skus)
        self.assertTrue(all(item.sku_id.startswith("sku_") for item in draft.recommended_skus))

    def test_clinician_rule_cannot_bypass_versioned_product_capability(self) -> None:
        self.container.repository.save_product(
            ProductRule(
                sku_id="sku_doctor_custom_lipid_support",
                display_name="医生定制脂代谢支持",
                category="general_support",
                source_refs=["manual:test"],
                formula_summary="用于验证医生智慧规则能把新增经验带入后续相似病例推荐。",
                core_ingredients=["测试成分A"],
                candidate_use_cases=["医生经验加权"],
                contraindications=[],
                enabled=True,
                merge_status=None,
                indications=[],
                exclusions=[],
                dosage_rule="每日 1 粒。",
                interaction_rule=[],
                warning_text=[],
                lifestyle_tags=[],
                priority=80,
            )
        )

        source_case = self._prepare_case(
            "一般健康记录，已完成人工校对。",
            Questionnaire(
                age=45,
                sex="male",
                symptoms=["fatigue_case"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["custom_cardio_goal"],
            ),
        )

        baseline_draft = self.container.recommendation_service.generate(source_case.id, requested_by="unit-test")
        baseline_ids = {item.sku_id for item in baseline_draft.recommended_skus}
        self.assertNotIn("sku_doctor_custom_lipid_support", baseline_ids)

        rule = self.container.assistant_rule_service.create_from_case(
            case_id=source_case.id,
            author_id="reviewer-01",
            instruction_text="以后遇到类似病例，优先加入 sku_doctor_custom_lipid_support。",
        )
        self.assertIn("sku_doctor_custom_lipid_support", rule.target_sku_ids)

        future_case = self._prepare_case(
            "一般健康记录，已完成人工校对。",
            Questionnaire(
                age=46,
                sex="male",
                symptoms=["fatigue_case"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["custom_cardio_goal"],
            ),
        )

        future_draft = self.container.recommendation_service.generate(future_case.id, requested_by="unit-test")
        future_ids = {item.sku_id for item in future_draft.recommended_skus}

        self.assertNotIn("sku_doctor_custom_lipid_support", future_ids)

    def test_dynamically_added_product_waits_for_versioned_capability_mapping(self) -> None:
        self.container.repository.save_product(
            ProductRule(
                sku_id="sku_custom_focus_support",
                display_name="专注支持配方",
                category="cognitive_support",
                source_refs=["manual:test"],
                formula_summary="用于验证新增产品能否被后续推荐引擎直接读取。",
                core_ingredients=["乙酰左旋肉碱", "磷脂酰丝氨酸"],
                candidate_use_cases=["专注支持", "脑力恢复"],
                contraindications=[],
                enabled=True,
                merge_status=None,
                indications=["goal:自定义专注支持"],
                exclusions=[],
                dosage_rule="每日 1 粒，早餐后使用。",
                interaction_rule=[],
                warning_text=[],
                lifestyle_tags=["focus_support"],
                priority=10,
            )
        )

        case = self._prepare_case(
            "一般健康记录，已完成人工校对。",
            Questionnaire(
                age=29,
                sex="female",
                symptoms=["注意力下降"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["自定义专注支持"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")

        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        self.assertTrue(draft.abstain_reason)
        self.assertNotIn("sku_custom_focus_support", recommended_ids)

    def test_approve_generates_pdf_report(self) -> None:
        case = self._prepare_case(
            "25-OH维生素D 18 ng/mL 30-100\n白细胞计数 5.5 10^9/L 3.5-9.5\n铁蛋白 8 ng/mL 15-150",
            Questionnaire(
                age=30,
                sex="female",
                symptoms=["疲劳"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["免疫支持", "睡眠恢复"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=None,
            edits={},
        )

        self.assertIsNotNone(review.pdf_report_path)
        self.assertTrue(Path(review.pdf_report_path).exists())
        self.assertTrue(Path(review.pdf_report_path).read_bytes().startswith(b"%PDF"))
        self.assertNotIn("## 病例摘要", review.publishable_report)
        self.assertNotIn("## 证据来源", review.publishable_report)
        self.assertNotIn("## 审计信息", review.publishable_report)
        self.assertIn("## 一、核心结论与健康画像", review.publishable_report)
        self.assertIn("## 二、异常指标汇总", review.publishable_report)
        self.assertIn("## 四、生活方式干预处方", review.publishable_report)
        self.assertIn("## 五、首月营养素干预方案", review.publishable_report)
        self.assertNotIn("总医嘱说明", review.publishable_report)
        self.assertNotIn("对症治疗", review.publishable_report)
        self.assertIn("### 1.", review.publishable_report)
        self.assertIn("### 1. 饮食建议", review.publishable_report)
        self.assertIn("### 2. 运动建议", review.publishable_report)
        self.assertIn("### 3. 监测与复查", review.publishable_report)
        self.assertIn("至少300克不同颜色蔬菜", review.publishable_report)
        self.assertIn("注意/禁忌：", review.publishable_report)
        self.assertNotIn("白细胞计数：5.5", review.publishable_report)
        formatted_indicator = self.container.review_service.pdf_exporter._format_item(
            "异常指标汇总",
            "25-OH维生素D：18 ng/mL（偏低）。说明：用于验证 PDF 列表符号。",
        )
        self.assertNotIn("•", formatted_indicator)
        formatted_lifestyle = self.container.review_service.pdf_exporter._format_item(
            "生活方式干预处方",
            "压力管理：每天安排2次5分钟呼吸练习或冥想。",
        )
        self.assertIn("2次", formatted_lifestyle)
        self.assertIn("5分钟", formatted_lifestyle)
        self.assertNotIn("2 次", formatted_lifestyle)
        self.assertNotIn("5 分钟", formatted_lifestyle)

    def test_approve_excludes_removed_recommended_skus_from_pdf_payload(self) -> None:
        case = self._prepare_case(
            "25-OH维生素D 18 ng/mL 30-100\nhs-CRP 4.2 mg/L 0-3\n铁蛋白 8 ng/mL 15-150",
            Questionnaire(
                age=30,
                sex="female",
                symptoms=["疲劳"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["免疫支持", "恢复支持"],
            ),
        )
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        self.assertGreaterEqual(len(draft.recommended_skus), 2)
        removed = draft.recommended_skus[0]
        captured: dict[str, list[str]] = {}
        original_export = self.container.review_service.pdf_exporter.export

        def capture_export(*args, **kwargs):
            captured["sku_ids"] = [item.sku_id for item in kwargs.get("recommended_skus", [])]
            return original_export(*args, **kwargs)

        self.container.review_service.pdf_exporter.export = capture_export

        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=None,
            edits={"excluded_sku_ids": [removed.sku_id]},
        )

        self.assertNotIn(removed.sku_id, captured["sku_ids"])
        self.assertNotIn(removed.display_name, review.publishable_report)
        self.assertTrue(Path(review.pdf_report_path).exists())

    def test_approval_adds_safety_to_manual_publishable_nutrition_lines(self) -> None:
        case = self._prepare_case(
            "25-OH维生素D 18 ng/mL 30-100\nhs-CRP 4.2 mg/L 0-3",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["免疫支持"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended = draft.recommended_skus[0]
        manual_report = (
            "# 功能医学营养与生活方式建议\n\n"
            "## 首月营养素干预方案\n"
            f"- {recommended.display_name}：{recommended.dosage}。目的：用于测试手动报告安全提示兜底。\n"
        )

        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=manual_report,
            edits={},
        )

        self.assertIn("注意/禁忌：", review.publishable_report)
        self.assertIn(recommended.display_name, review.publishable_report)
        self.assertNotIn("总医嘱说明", review.publishable_report)
        self.assertNotIn("对症治疗", review.publishable_report)

    def test_approval_rerenders_legacy_auto_customer_report(self) -> None:
        case = self._prepare_case(
            "促甲状腺激素 7.57 mIU/L 0.27-4.2\n甲状腺过氧化物酶抗体 854 IU/mL 0-30",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳"],
                known_conditions=["桥本氏甲状腺炎"],
                medications=[],
                allergies=[],
                goals=["甲状腺支持"],
            ),
        )
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        legacy_report = (
            "# 功能医学营养与生活方式建议\n\n"
            "## 总体健康画像\n"
            "- 旧版画像。\n\n"
            "## 关键指标\n"
            "- 旧版关键指标。\n\n"
            "## 个性化营养素方案\n"
            "- 旧版营养素。\n\n"
            "## 生活方式干预重点\n"
            "- 旧版生活方式。\n\n"
            "## 复查与跟进建议\n"
            "- 旧版复查。\n"
        )

        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=legacy_report,
            edits={},
        )

        self.assertIn("# 功能医学综合分析与首月干预方案", review.publishable_report)
        self.assertIn("## 核心结论与健康画像", review.publishable_report)
        self.assertIn("## 异常指标汇总", review.publishable_report)
        self.assertIn("## 首月营养素干预方案", review.publishable_report)
        self.assertNotIn("旧版画像", review.publishable_report)

    def test_approval_rejects_question_mark_corrupted_publishable_summary(self) -> None:
        case = self._prepare_case(
            "25-OH维生素D 18 ng/mL 30-100\nhs-CRP 4.2 mg/L 0-3",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["免疫支持"],
            ),
        )
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        corrupted_report = (
            "# ?????????????\n\n"
            "## ??????\n"
            "- ??????????????????????????????????????????????????????\n\n"
            "## ????????\n"
            "- ???????? 1 ???????????????????????RAG???\n"
        )

        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=corrupted_report,
            edits={},
        )

        self.assertNotIn("????", review.publishable_report)
        self.assertNotIn("RAG", review.publishable_report)
        self.assertIn("# 功能医学综合分析与首月干预方案", review.publishable_report)
        self.assertIn("## 核心结论与健康画像", review.publishable_report)

    def test_delete_case_cleans_associated_files_and_records(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="删除测试",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        stored_path = Path(self.container.recommendation_service.object_store.save("delete-case.txt", b"ferritin 8"))
        uploaded_file = UploadedFile(
            id="file_delete",
            case_id=case.id,
            filename="delete-case.txt",
            content_type="text/plain",
            size_bytes=10,
            storage_uri=str(stored_path),
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        report_text = "25-OH维生素D 18 ng/mL 30-100\n铁蛋白 8 ng/mL 15-150"
        extraction, lab_items = self.container.parsing_service.parse(
            filename="delete-case.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
        )
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": extraction.text, "missing_fields": []}],
            normalized_lab_items=lab_items,
            missing_fields=[],
            review_notes="unit-test",
        )
        self.container.case_service.submit_questionnaire(
            case.id,
            Questionnaire(
                age=30,
                sex="female",
                symptoms=["疲劳"],
                known_conditions=[],
                medications=[],
                allergies=[],
                goals=["免疫支持"],
            ),
        )
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=None,
            edits={},
        )

        self.assertTrue(stored_path.exists())
        self.assertTrue(Path(review.pdf_report_path).exists())

        self.container.case_service.delete_case(case.id)

        self.assertIsNone(self.container.repository.get_case(case.id))
        self.assertIsNone(self.container.repository.get_draft(draft.id))
        self.assertIsNone(self.container.repository.get_review_decision(draft.id))
        self.assertFalse(stored_path.exists())
        self.assertFalse(Path(review.pdf_report_path).exists())


if __name__ == "__main__":
    unittest.main()
