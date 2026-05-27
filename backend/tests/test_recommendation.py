from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.bootstrap import build_container
from app.core.settings import AppSettings
from app.domain.models import AnalysisMode, IndicatorStatus, ProductRule, Questionnaire, SourceSpan, UploadedFile
from app.providers.base import DraftCompositionResult


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
            rationale=["大模型优先模式测试。"],
            lifestyle_actions=["保持基础生活方式干预。"],
            section_overrides={
                "总体健康画像": ["模型优先判断当前应先围绕代谢与恢复能力做整体支持。"],
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
            any("注意/禁忌：" in item for item in draft.report_sections.get("个性化营养素方案", []))
        )

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
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertFalse(draft.abstain_reason)
        self.assertIn("未填写问卷，当前草案仅依据已上传报告和人工校对结果生成。", draft.missing_info)
        self.assertTrue({"sku_thyroid_support", "sku_selenium_vitamin_e"} & recommended_ids)

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
        self.assertNotIn("当前暂无明确可发布的营养素组合", " ".join(draft.report_sections["个性化营养素方案"]))

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
        self.assertTrue({"sku_plant_multi_mineral", "sku_liposomal_vitamin_c_300"} & recommended_ids)

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

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        guidance_section = draft.report_sections.get("原报告小结与建议") or []
        guidance_text = " ".join(guidance_section if isinstance(guidance_section, list) else [str(guidance_section)])

        self.assertFalse(draft.abstain_reason)
        self.assertTrue(recommended_ids)
        self.assertTrue(
            {"sku_blood_sugar_complex", "sku_mitochondrial_support", "sku_fish_oil_rtg", "sku_coq10"}
            & recommended_ids
        )
        self.assertIn("人工录入评估结论", guidance_text)
        self.assertIn("病例总结诊断", " ".join(draft.case_summary))

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

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        guidance_text = " ".join(draft.report_sections.get("原报告小结与建议") or [])
        analysis_text = " ".join(draft.report_sections.get("系统功能深度分析") or [])
        summary_text = " ".join(draft.case_summary)

        self.assertFalse(draft.abstain_reason)
        self.assertTrue(
            {"sku_plant_multi_mineral", "sku_b_complex", "sku_hcy_methylation", "sku_amino_acid_detox"} & recommended_ids
        )
        self.assertIn("病例总结提示的所需营养素", guidance_text)
        self.assertIn("蛋白质", guidance_text)
        self.assertIn("所需营养素提示", summary_text)
        self.assertIn("营养素方向", analysis_text)

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
        vitamin_c = self.container.repository.get_product("sku_liposomal_vitamin_c_300")
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
        self.assertTrue({"sku_plant_multi_mineral", "sku_liposomal_vitamin_c_300"} & recommended_ids)
        self.assertTrue(any("signal:" in evidence_id for evidence_id in draft.evidence_ids))

    def test_generates_lipid_recommendations_from_case_indicators_when_lab_items_missing(self) -> None:
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

        self.assertFalse(draft.abstain_reason)
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        self.assertTrue({"sku_fish_oil_rtg", "sku_cardiac_support"} & recommended_ids)

    def test_generates_iron_recommendations_from_case_indicators_when_lab_items_missing(self) -> None:
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

        self.assertFalse(draft.abstain_reason)
        recommended_ids = {item.sku_id for item in draft.recommended_skus}
        self.assertTrue({"sku_plant_multi_mineral", "sku_liposomal_vitamin_c_300"} & recommended_ids)

    def test_abstains_when_red_flags_are_triggered(self) -> None:
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

        self.assertTrue(draft.abstain_reason)
        self.assertEqual(draft.recommended_skus, [])
        self.assertTrue(draft.red_flags)

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
        self.assertEqual([item.sku_id for item in draft.recommended_skus], ["sku_vitamin_d3_k"])
        self.assertIn("结合", draft.recommended_skus[0].reason)
        self.assertNotIn("product:sku_", draft.recommended_skus[0].reason)
        self.assertTrue(draft.evidence_details)
        self.assertTrue(any("VD3+K" in item for item in draft.evidence_details))
        self.assertTrue(all(any("\u4e00" <= ch <= "\u9fff" for ch in item) for item in draft.lifestyle_actions))
        self.assertEqual(draft.model_version, "remote:test-model")
        self.assertIn("总体健康画像", draft.report_sections)
        self.assertIn("系统功能深度分析", draft.report_sections)
        self.assertIn("功能医学检测建议", draft.report_sections)
        self.assertIn("90天健康路线图", draft.report_sections)
        self.assertNotIn("证据来源", draft.report_sections)

    def test_llm_primary_mode_passes_broader_case_context_to_model(self) -> None:
        capture = CaptureLLMProvider()
        self.container.recommendation_service.llm_provider = capture
        self.container.recommendation_service.model_version = "remote:test-model"
        self.container.recommendation_service.prompt_version = "grounded-remote-report-v1"

        case = self.container.case_service.create_case(
            customer_name="LLM优先案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
            analysis_mode=AnalysisMode.llm_primary,
        )
        report_text = "非高密度脂蛋白胆固醇 5.84 mmol/L 0-4.1\n甘油三酯 1.45 mmol/L 0.56-1.71\n高密度胆固醇 1.39 mmol/L 0.91-1.55"
        uploaded_file = UploadedFile(
            id="file_llm_primary",
            case_id=case.id,
            filename="llm-primary.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://llm-primary.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="llm-primary.txt",
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
        self.assertIsNotNone(capture.last_input)
        self.assertEqual(capture.last_input.analysis_mode, "llm_primary")
        self.assertIn("非高密度脂蛋白胆固醇", capture.last_input.reviewed_report_text)
        self.assertIn("support_profiles", capture.last_input.structured_case_context)
        self.assertGreaterEqual(len(capture.last_input.candidate_products), 1)
        self.assertIn("模型优先判断当前应先围绕代谢与恢复能力做整体支持", draft.report_sections["总体健康画像"])
        self.assertIn("模型结合报告和问卷信息，提示当前代谢负担与生活方式因素相互叠加", draft.report_sections["系统功能深度分析"])

    def test_clinician_rule_from_case_biases_future_similar_cases(self) -> None:
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

        self.assertIn("sku_doctor_custom_lipid_support", future_ids)
        self.assertTrue(any(evidence_id.startswith("clinician_rule:") for evidence_id in future_draft.evidence_ids))

    def test_dynamically_added_product_enters_future_recommendations(self) -> None:
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
        self.assertFalse(draft.abstain_reason)
        self.assertIn("sku_custom_focus_support", recommended_ids)

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
        self.assertIn("## 总体健康画像", review.publishable_report)
        self.assertIn("## 关键指标", review.publishable_report)
        self.assertIn("说明：", review.publishable_report)
        self.assertIn("## 生活方式干预重点", review.publishable_report)
        self.assertIn("抗炎餐盘", review.publishable_report)
        self.assertIn("注意/禁忌：", review.publishable_report)
        self.assertNotIn("白细胞计数：5.5", review.publishable_report)
        formatted_indicator = self.container.review_service.pdf_exporter._format_item(
            "关键指标",
            "25-OH维生素D：18 ng/mL（偏低）。说明：用于验证 PDF 列表符号。",
        )
        self.assertTrue(formatted_indicator.startswith("- "))
        self.assertNotIn("•", formatted_indicator)

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
            "## 个性化营养素方案\n"
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
        self.assertIn("# 功能医学营养与生活方式建议", review.publishable_report)
        self.assertIn("## 总体健康画像", review.publishable_report)

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
