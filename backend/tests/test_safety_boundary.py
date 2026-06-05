from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.bootstrap import build_container
from app.core.settings import AppSettings
from app.domain.models import (
    ClinicianRule,
    ClinicianRuleAction,
    ProductRule,
    Questionnaire,
    RuleScope,
    UploadedFile,
)
from app.providers.base import DraftCompositionResult
from app.services.rag_retriever import RagHit
from app.services.rag_safety import CUSTOMER_RAG_PREFIX


class CaptureComposer:
    def __init__(self) -> None:
        self.last_input = None

    def compose(self, draft_input):
        self.last_input = draft_input
        selected = [product.sku_id for product in draft_input.candidate_products[:4]]
        return DraftCompositionResult(
            selected_sku_ids=selected,
            rationale=["安全边界测试。"],
            lifestyle_actions=["保持基础生活方式干预。"],
            confidence=0.7,
        )


class FakeRagRetriever:
    def __init__(self, hits: list[RagHit]) -> None:
        self.hits = hits
        self.queries: list[str] = []

    def hybrid_search(self, query: str, top_k: int = 5) -> list[RagHit]:
        self.queries.append(query)
        return self.hits[:top_k]


class FakeRagFusionProvider:
    def __init__(
        self,
        sections: dict[str, list[str]] | None = None,
        used_rag_refs: dict[str, list[str]] | None = None,
        section_patches: dict[str, list[dict]] | None = None,
    ) -> None:
        self.sections = sections
        self.section_patches = section_patches or {}
        self.used_rag_refs = used_rag_refs or {}
        self.last_payload = None

    def fuse_report_sections(self, **kwargs):
        self.last_payload = kwargs
        return type(
            "FakeRagFusionResult",
            (),
            {
                "sections": self.sections or {},
                "section_patches": self.section_patches,
                "used_rag_refs": self.used_rag_refs,
            },
        )()


def rag_hit(text: str, *, chunk_id: str = "rag_test", topic_tags: list[str] | None = None) -> RagHit:
    return RagHit(
        chunk_id=chunk_id,
        text=text,
        score=0.91,
        source_kind="unit_test",
        topic_tags=topic_tags or ["代谢", "生活方式"],
        metadata={"source_title": "unit-test", "section": "internal"},
    )


class RagSafetyBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "功能医学相关资料").mkdir(parents=True, exist_ok=True)
        settings = AppSettings(
            project_root=root,
            data_dir=Path(__file__).resolve().parents[1] / "app" / "data",
            runtime_dir=root / ".runtime",
            upload_dir=root / ".runtime" / "uploads",
            report_export_dir=root / ".runtime" / "reports",
            sqlite_path=root / ".runtime" / "test.sqlite3",
            knowledge_root=root / "功能医学相关资料",
            report_reference_path=root / "0316测试报告1.pdf",
            rag_enabled=False,
        )
        self.container = build_container(settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _prepare_case(self, report_text: str, questionnaire: Questionnaire):
        case = self.container.case_service.create_case(
            customer_name="RAG安全测试用户",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        uploaded_file = UploadedFile(
            id=f"file_{case.id}",
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

    def test_rag_cannot_introduce_outside_catalog_supplements_or_drug_dosing(self) -> None:
        composer = CaptureComposer()
        self.container.recommendation_service.llm_provider = composer
        self.container.recommendation_service.rag_retriever = FakeRagRetriever(
            [
                rag_hit("建议补充虫草素，并考虑氟康唑 100mg 口服疗程。", chunk_id="unsafe_drug"),
                rag_hit(
                    "代谢综合征常与胰岛素抵抗、血糖波动和慢性炎症负担相互叠加，需要结合饮食、睡眠和活动量综合观察。",
                    chunk_id="safe_metabolic",
                ),
                rag_hit(
                    "空腹血糖和胰岛素相关指标可帮助观察血糖稳定性、餐后反应和代谢压力，需要结合复查趋势解释。",
                    chunk_id="safe_indicator",
                    topic_tags=["代谢"],
                ),
                rag_hit(
                    "维生素D状态与免疫调节、炎症反应和整体恢复有关，适合放在营养支持背景中理解。",
                    chunk_id="safe_nutrition",
                    topic_tags=["营养", "免疫"],
                ),
                rag_hit(
                    "睡眠不足、久坐和压力负荷会影响血糖稳定性与炎症恢复，生活方式干预应结合可执行节奏推进。",
                    chunk_id="safe_lifestyle",
                    topic_tags=["生活方式", "睡眠/疲劳"],
                ),
            ]
        )
        case = self._prepare_case(
            "25-OH维生素D 18 ng/mL 30-100\n空腹血糖 6.2 mmol/L 3.9-5.6\nhs-CRP 4.2 mg/L 0-3",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳"],
                medications=[],
                allergies=[],
                goals=["血糖平衡"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        serialized_sections = json.dumps(draft.report_sections, ensure_ascii=False)
        catalog_ids = {product.sku_id for product in self.container.repository.list_products()}
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertTrue(recommended_ids.issubset(catalog_ids))
        self.assertNotIn("虫草素", serialized_sections)
        self.assertNotIn("氟康唑", serialized_sections)
        self.assertNotIn("100mg", serialized_sections)
        self.assertIn(CUSTOMER_RAG_PREFIX, serialized_sections)
        self.assertIn("RAG总体健康画像", draft.report_sections)
        self.assertIn("RAG异常指标解释", draft.report_sections)
        self.assertIn("RAG生活方式干预", draft.report_sections)
        self.assertIn("RAG复查建议", draft.report_sections)
        self.assertEqual(len(composer.last_input.rag_hits), 4)
        self.assertIn("rag_rejected:unsafe_drug", serialized_sections)

        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=None,
            edits={},
        )
        self.assertNotIn(CUSTOMER_RAG_PREFIX, review.publishable_report)
        self.assertIn("胰岛素抵抗", review.publishable_report)
        self.assertIn("睡眠节律和压力恢复", review.publishable_report)
        self.assertIn("## 总体健康画像", review.publishable_report)
        self.assertIn("## 关键指标", review.publishable_report)
        self.assertIn("## 个性化营养素方案", review.publishable_report)
        self.assertIn("## 生活方式干预重点", review.publishable_report)
        self.assertIn("## 复查与跟进建议", review.publishable_report)
        nutrition_block = review.publishable_report.split("## 个性化营养素方案", 1)[1].split("\n## ", 1)[0]
        self.assertNotIn(CUSTOMER_RAG_PREFIX, nutrition_block)
        self.assertNotIn("胰岛素抵抗", nutrition_block)

    def test_red_flags_and_contraindications_still_take_priority_over_rag(self) -> None:
        self.container.recommendation_service.rag_retriever = FakeRagRetriever(
            [
                rag_hit(
                    "孕期可考虑主动排毒和间歇性禁食来改善代谢。",
                    chunk_id="unsafe_pregnancy",
                    topic_tags=["解毒", "生活方式"],
                )
            ]
        )
        case = self._prepare_case(
            "空腹血糖 6.2 mmol/L 3.9-5.6",
            Questionnaire(
                age=32,
                sex="female",
                pregnant_or_lactating=True,
                symptoms=["疲劳"],
                medications=[],
                allergies=[],
                goals=["代谢支持"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        serialized_sections = json.dumps(draft.report_sections, ensure_ascii=False)

        self.assertTrue(draft.abstain_reason)
        self.assertEqual(draft.recommended_skus, [])
        self.assertIn("孕期或哺乳期需要人工审核", " ".join(draft.red_flags))
        self.assertNotIn("主动排毒", serialized_sections)
        self.assertNotIn("间歇性禁食", serialized_sections)
        self.assertIn("skipped_due_to_red_flags", serialized_sections)

    def test_publishable_summary_still_receives_naturalized_rag_enhancement(self) -> None:
        self.container.recommendation_service.rag_retriever = FakeRagRetriever(
            [
                rag_hit(
                    "代谢综合征常与胰岛素抵抗、血糖波动和慢性炎症负担相互叠加，需要结合饮食、睡眠和活动量综合观察。",
                    chunk_id="safe_metabolic",
                    topic_tags=["代谢", "炎症"],
                ),
                rag_hit(
                    "空腹血糖和胰岛素相关指标可帮助观察血糖稳定性、餐后反应和代谢压力，需要结合复查趋势解释。",
                    chunk_id="safe_indicator",
                    topic_tags=["代谢"],
                ),
                rag_hit(
                    "睡眠不足、久坐和压力负荷会影响血糖稳定性与炎症恢复，生活方式干预应结合可执行节奏推进。",
                    chunk_id="safe_lifestyle",
                    topic_tags=["生活方式", "睡眠/疲劳"],
                ),
            ]
        )
        case = self._prepare_case(
            "空腹血糖 6.2 mmol/L 3.9-5.6\nhs-CRP 4.2 mg/L 0-3",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳"],
                medications=[],
                allergies=[],
                goals=["血糖平衡"],
            ),
        )
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        draft.report_sections["RAG总体健康画像"] = [
            f"{CUSTOMER_RAG_PREFIX}代谢压力需要结合血糖、炎症、睡眠和压力恢复一起观察。"
        ]
        draft.report_sections["RAG异常指标解释"] = [
            f"{CUSTOMER_RAG_PREFIX}空腹血糖和胰岛素相关指标可帮助观察血糖稳定性、餐后反应和代谢压力，需要结合复查趋势解释。"
        ]
        draft.report_sections["RAG生活方式干预"] = [
            f"{CUSTOMER_RAG_PREFIX}睡眠不足、久坐和压力负荷会影响血糖稳定性与炎症恢复，生活方式干预应结合可执行节奏推进。"
        ]
        draft.report_sections["RAG复查建议"] = [
            f"{CUSTOMER_RAG_PREFIX}复查时建议把血糖、炎症、睡眠压力和执行记录放在同一趋势里观察。"
        ]
        self.container.repository.save_draft(draft)
        manual_report = (
            "# 功能医学营养与生活方式建议\n\n"
            "## 总体健康画像\n"
            "- 从这次报告看，当前更值得优先关注的是空腹血糖和炎症负担。\n\n"
            "## 关键指标\n"
            "- 空腹血糖：6.2 mmol/L（需关注）。说明：提示血糖稳定性需要关注。\n\n"
            "## 生活方式干预重点\n"
            "- 睡眠修复：固定起床时间，并逐步减少久坐。\n\n"
            "## 复查与跟进建议\n"
            "- 8-12周后复查关键指标，并根据症状变化调整方案。\n"
        )

        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=manual_report,
            edits={},
        )

        self.assertNotIn(CUSTOMER_RAG_PREFIX, review.publishable_report)
        self.assertNotIn("功能医学知识库", review.publishable_report)
        self.assertIn("胰岛素抵抗", review.publishable_report)
        self.assertIn("睡眠节律和压力恢复", review.publishable_report)

    def test_optional_remote_rag_fusion_can_rewrite_only_allowed_report_sections(self) -> None:
        self.container.recommendation_service.rag_retriever = FakeRagRetriever(
            [
                rag_hit(
                    "空腹血糖和胰岛素相关指标可帮助观察血糖稳定性、餐后反应和代谢压力，需要结合复查趋势解释。",
                    chunk_id="safe_indicator",
                    topic_tags=["代谢"],
                ),
                rag_hit(
                    "睡眠不足、久坐和压力负荷会影响血糖稳定性与炎症恢复，生活方式干预应结合可执行节奏推进。",
                    chunk_id="safe_lifestyle",
                    topic_tags=["生活方式", "睡眠/疲劳"],
                ),
            ]
        )
        fusion_provider = FakeRagFusionProvider(
            sections={
                "总体健康画像": ["从这次报告看，当前重点是血糖和炎症负担；模型融合后把睡眠、压力和代谢恢复放在同一条主线观察。"],
                "关键指标": ["空腹血糖：6.2 mmol/L（需关注）。说明：模型融合后提示需结合餐后波动、睡眠压力和复查趋势理解。"],
                "生活方式干预重点": ["睡眠修复：模型融合后建议固定起床时间、减少久坐，并用饭后散步帮助血糖稳定。"],
                "复查与跟进建议": ["8-12周后复查关键指标，模型融合后建议同步记录睡眠、压力、餐后反应和执行情况。"],
                "个性化营养素方案": ["不允许模型改写这个区块。"],
            },
            used_rag_refs={"关键指标": ["indicator_1"], "生活方式干预重点": ["lifestyle_1"]},
        )
        self.container.review_service.rag_fusion_provider = fusion_provider
        case = self._prepare_case(
            "空腹血糖 6.2 mmol/L 3.9-5.6\nhs-CRP 4.2 mg/L 0-3",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳"],
                medications=[],
                allergies=[],
                goals=["血糖平衡"],
            ),
        )
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        manual_report = (
            "# 功能医学营养与生活方式建议\n\n"
            "## 总体健康画像\n"
            "- 从这次报告看，当前更值得优先关注的是空腹血糖和炎症负担。\n\n"
            "## 关键指标\n"
            "- 空腹血糖：6.2 mmol/L（需关注）。说明：提示血糖稳定性需要关注。\n\n"
            "## 个性化营养素方案\n"
            "- 原营养素方案必须保持不由RAG融合模型改写。\n\n"
            "## 生活方式干预重点\n"
            "- 睡眠修复：固定起床时间，并逐步减少久坐。\n\n"
            "## 复查与跟进建议\n"
            "- 8-12周后复查关键指标，并根据症状变化调整方案。\n"
        )

        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=manual_report,
            edits={},
        )

        self.assertIn("模型融合后把睡眠、压力和代谢恢复放在同一条主线观察", review.publishable_report)
        self.assertIn("模型融合后提示需结合餐后波动", review.publishable_report)
        self.assertIn("原营养素方案必须保持不由融合模型改写", review.publishable_report)
        self.assertNotIn("不允许模型改写这个区块", review.publishable_report)
        self.assertNotIn(CUSTOMER_RAG_PREFIX, review.publishable_report)
        self.assertNotIn("功能医学知识库", review.publishable_report)
        saved_draft = self.container.repository.get_draft(draft.id)
        self.assertIn("rag_fusion:remote_success", saved_draft.report_sections["RAG内部审查"])
        self.assertIn("rag_fusion_used:关键指标:indicator_1", saved_draft.report_sections["RAG内部审查"])
        self.assertNotIn("个性化营养素方案", fusion_provider.last_payload["target_sections"])

    def test_optional_remote_rag_fusion_accepts_compact_section_patches(self) -> None:
        self.container.recommendation_service.rag_retriever = FakeRagRetriever(
            [
                rag_hit(
                    "空腹血糖和胰岛素相关指标可帮助观察血糖稳定性、餐后反应和代谢压力，需要结合复查趋势解释。",
                    chunk_id="safe_indicator",
                    topic_tags=["代谢"],
                )
            ]
        )
        fusion_provider = FakeRagFusionProvider(
            section_patches={
                "关键指标": [
                    {
                        "index": 0,
                        "text": "空腹血糖: 6.2 mmol/L (需关注).\n- 说明: 结合知识库观点，可把空腹值、餐后反应和复查趋势放在一起理解",
                    }
                ],
                "生活方式干预重点": [
                    {
                        "index": 0,
                        "text": "1) 睡眠修复: 固定起床时间, 并把睡眠、饭后活动和压力恢复作为同一阶段的执行重点",
                    }
                ],
            },
            used_rag_refs={"关键指标": ["indicator_1"]},
        )
        self.container.review_service.rag_fusion_provider = fusion_provider
        case = self._prepare_case(
            "空腹血糖 6.2 mmol/L 3.9-5.6\nhs-CRP 4.2 mg/L 0-3",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳"],
                medications=[],
                allergies=[],
                goals=["血糖平衡"],
            ),
        )
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        manual_report = (
            "# 功能医学营养与生活方式建议\n\n"
            "## 总体健康画像\n"
            "- 从这次报告看，当前更值得优先关注的是空腹血糖和炎症负担。\n\n"
            "## 关键指标\n"
            "- 空腹血糖：6.2 mmol/L（需关注）。说明：提示血糖稳定性需要关注。\n\n"
            "## 个性化营养素方案\n"
            "- 原营养素方案必须保持不由RAG融合模型改写。\n\n"
            "## 生活方式干预重点\n"
            "- 睡眠修复：固定起床时间，并逐步减少久坐。\n\n"
            "## 复查与跟进建议\n"
            "- 8-12周后复查关键指标，并根据症状变化调整方案。\n"
        )

        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=manual_report,
            edits={},
        )

        self.assertIn("空腹值、餐后反应和复查趋势", review.publishable_report)
        self.assertIn("睡眠、饭后活动和压力恢复", review.publishable_report)
        self.assertIn("空腹血糖：6.2 mmol/L（需关注）。说明：", review.publishable_report)
        self.assertIn("睡眠修复：固定起床时间", review.publishable_report)
        self.assertNotIn("\n- -", review.publishable_report)
        self.assertNotIn("\n- 1)", review.publishable_report)
        self.assertIn("原营养素方案必须保持不由融合模型改写", review.publishable_report)
        saved_draft = self.container.repository.get_draft(draft.id)
        self.assertIn("rag_fusion:remote_success", saved_draft.report_sections["RAG内部审查"])

    def test_customer_visible_report_collapses_soft_line_breaks(self) -> None:
        raw_report = (
            "# 功能医学营养与生活方式建议\n\n"
            "## 生活方式干预重点\n"
            "- 压力管理：每天安排2 \n"
            "次5分钟呼吸练习或冥想，也可以用散步、哼唱、伸展来帮助身体从紧绷状态切换出来。\n\n"
            "## 关键指标\n"
            "- 血清镁：要注意血清镁仅占体内总镁的1 \n"
            "%，即使细胞内已经存在镁缺乏，血清镁也可能显示正常。\n"
            "- 蛋白质代谢：TH 可无特异 性地加强基础蛋白质合成\n\n"
            "## 个性化营养素方案\n"
            "- 甲状腺支持：每日 1粒，早餐后使用。；适用说明：结合 甲状腺支持、基础代谢支持 与本次资料提示，作 为当前阶段的建议。；注意/禁忌：涉及碘补充，建议结合甲状腺检查结果人工确认。；与甲状腺素类药物需 错开使用。；甲状腺亢进、碘限制人群需顾问确认\n"
        )

        normalized = self.container.review_service._normalize_customer_visible_report_text(raw_report)

        self.assertIn("每天安排2次5分钟呼吸练习", normalized)
        self.assertIn("体内总镁的1%，即使细胞内", normalized)
        self.assertIn("TH 可无特异性地加强基础蛋白质合成。", normalized)
        self.assertIn("甲状腺支持：每日1粒，早餐后使用；适用说明：结合甲状腺支持、基础代谢支持与本次资料提示，作为当前阶段的建议；注意/禁忌：涉及碘补充，建议结合甲状腺检查结果人工确认；与甲状腺素类药物需错开使用；甲状腺亢进、碘限制人群需顾问确认。", normalized)
        self.assertNotIn("2 \n次", normalized)
        self.assertNotIn("1 \n%", normalized)
        self.assertNotIn("特异 性", normalized)
        self.assertNotIn("作 为", normalized)
        self.assertNotIn("需 错", normalized)
        self.assertNotIn("。；", normalized)
        self.assertEqual(normalized.count("- "), 4)

    def test_optional_remote_rag_fusion_falls_back_when_output_leaks_internal_labels(self) -> None:
        self.container.recommendation_service.rag_retriever = FakeRagRetriever(
            [
                rag_hit(
                    "空腹血糖和胰岛素相关指标可帮助观察血糖稳定性、餐后反应和代谢压力，需要结合复查趋势解释。",
                    chunk_id="safe_indicator",
                    topic_tags=["代谢"],
                )
            ]
        )
        self.container.review_service.rag_fusion_provider = FakeRagFusionProvider(
            sections={
                "总体健康画像": ["功能医学知识库（仅供参考）：模型试图泄露内部标签。"],
                "关键指标": ["空腹血糖：6.2 mmol/L（需关注）。说明：RAG 内部标签泄露。"],
                "生活方式干预重点": ["睡眠修复：固定起床时间。"],
                "复查与跟进建议": ["8-12周后复查关键指标。"],
            }
        )
        case = self._prepare_case(
            "空腹血糖 6.2 mmol/L 3.9-5.6",
            Questionnaire(
                age=34,
                sex="female",
                symptoms=["疲劳"],
                medications=[],
                allergies=[],
                goals=["血糖平衡"],
            ),
        )
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        draft.report_sections["RAG总体健康画像"] = [
            f"{CUSTOMER_RAG_PREFIX}代谢压力需要结合血糖、炎症、睡眠和压力恢复一起观察。"
        ]
        draft.report_sections["RAG异常指标解释"] = [
            f"{CUSTOMER_RAG_PREFIX}空腹血糖和胰岛素相关指标可帮助观察血糖稳定性、餐后反应和代谢压力，需要结合复查趋势解释。"
        ]
        draft.report_sections["RAG生活方式干预"] = [
            f"{CUSTOMER_RAG_PREFIX}睡眠不足、久坐和压力负荷会影响血糖稳定性与炎症恢复，生活方式干预应结合可执行节奏推进。"
        ]
        draft.report_sections["RAG复查建议"] = [
            f"{CUSTOMER_RAG_PREFIX}复查时建议把血糖、炎症、睡眠压力和执行记录放在同一趋势里观察。"
        ]
        self.container.repository.save_draft(draft)
        review = self.container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=None,
            edits={},
        )

        self.assertNotIn("内部标签泄露", review.publishable_report)
        self.assertNotIn("功能医学知识库", review.publishable_report)
        saved_draft = self.container.repository.get_draft(draft.id)
        self.assertIn("rag_fusion:remote_rejected", " ".join(saved_draft.report_sections["RAG内部审查"]))

    def test_rag_filters_english_fragment_hits_after_retrieval_expansion(self) -> None:
        self.container.recommendation_service.rag_retriever = FakeRagRetriever(
            [
                rag_hit(
                    "potassium, chloride, carbon dioxide, calcium, total protein, albumin, globulin, total bilirubin, "
                    "alkaline phosphatase, AST, ALT, T4, FT4, FT3, TSH, prostate specific antigen",
                    chunk_id="bad_english_lab_list",
                    topic_tags=["甲状腺"],
                ),
                rag_hit(
                    "marily palmitoleic, vaccenic, and oleic. MP's thyroid panel was within normal limits, "
                    "including antibodies. Optimal TSH is now considered to be 2.5.",
                    chunk_id="bad_english_continuation",
                    topic_tags=["甲状腺"],
                ),
                rag_hit(
                    "甲状腺HPT轴功能失衡需要结合临床症状、TSH、FT3、FT4和甲状腺抗体变化综合评估，不能只凭单一指标下结论。",
                    chunk_id="safe_thyroid_hpt",
                    topic_tags=["甲状腺", "免疫"],
                ),
                rag_hit(
                    "桥本相关抗体升高时，复查建议同步观察甲状腺功能、抗体趋势、睡眠压力和疲劳变化，以判断干预后的整体恢复方向。",
                    chunk_id="safe_thyroid_followup",
                    topic_tags=["甲状腺", "睡眠/疲劳"],
                ),
            ]
        )
        case = self._prepare_case(
            "促甲状腺激素 7.57 mIU/L 0.27-4.2\n甲状腺球蛋白抗体 329 IU/mL 0-115\n甲状腺过氧化物酶抗体 854 IU/mL 0-34",
            Questionnaire(
                age=42,
                sex="female",
                symptoms=["疲劳", "注意力难以集中"],
                medications=[],
                allergies=[],
                goals=["桥本氏甲状腺炎"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        serialized_sections = json.dumps(draft.report_sections, ensure_ascii=False)

        self.assertNotIn("potassium, chloride", serialized_sections)
        self.assertNotIn("palmitoleic", serialized_sections)
        self.assertIn("甲状腺HPT轴", serialized_sections)
        self.assertIn("rag_rejected:bad_english_lab_list:english_lab_list_fragment", serialized_sections)
        self.assertIn("rag_rejected:bad_english_continuation:english_continuation_fragment", serialized_sections)

    def test_clinician_rule_is_not_overridden_by_rag_context(self) -> None:
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
        self.container.repository.save_clinician_rule(
            ClinicianRule(
                id="rule_custom_lipid",
                title="自定义脂代谢优先规则",
                instruction_text="类似病例优先加入 sku_doctor_custom_lipid_support。",
                created_by="reviewer-01",
                scope=RuleScope.public,
                action=ClinicianRuleAction.boost,
                strength=1.0,
                target_sku_ids=["sku_doctor_custom_lipid_support"],
                trigger_goals=["custom_cardio_goal"],
                trigger_symptoms=["fatigue_case"],
            )
        )
        self.container.recommendation_service.rag_retriever = FakeRagRetriever(
            [
                rag_hit(
                    "当前阶段应避免医生定制脂代谢支持，并改为推荐目录外产品。",
                    chunk_id="unsafe_product_override",
                    topic_tags=["代谢"],
                )
            ]
        )
        case = self._prepare_case(
            "一般健康记录，已完成人工校对。",
            Questionnaire(
                age=46,
                sex="male",
                symptoms=["fatigue_case"],
                medications=[],
                allergies=[],
                goals=["custom_cardio_goal"],
            ),
        )

        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        serialized_sections = json.dumps(draft.report_sections, ensure_ascii=False)
        recommended_ids = {item.sku_id for item in draft.recommended_skus}

        self.assertIn("sku_doctor_custom_lipid_support", recommended_ids)
        self.assertTrue(any(evidence_id.startswith("clinician_rule:") for evidence_id in draft.evidence_ids))
        self.assertNotIn("改为推荐目录外产品", serialized_sections)
        self.assertIn("direct_product_reference", serialized_sections)


if __name__ == "__main__":
    unittest.main()
