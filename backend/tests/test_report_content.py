from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.report_content import (
    build_core_health_portrait,
    build_plan_summary,
    normalize_plan_summary_items,
)
from app.services.lifestyle_planning import remove_generic_lifestyle_confirmation
from app.services.case_analysis import is_chronic_food_sensitivity_result
from app.services.review_local import ReviewService


def _finding(
    system_id: str,
    finding_id: str,
    *,
    priority_level: str = "优先级高",
    priority_score: float = 50.0,
):
    return SimpleNamespace(
        system_id=system_id,
        finding_ids=[finding_id],
        priority_level=priority_level,
        priority_score=priority_score,
    )


def _recommendation(system_id: str, finding_id: str):
    return SimpleNamespace(
        primary_system_id=system_id,
        covered_system_ids=[system_id],
        matched_finding_ids=[finding_id],
    )


class PlanSummaryTests(unittest.TestCase):
    def test_three_problem_summary_matches_confirmed_style(self) -> None:
        findings = [
            _finding("endocrine_metabolic", "finding-glucose", priority_score=90),
            _finding("digestive_gut", "finding-gastritis", priority_score=80),
            _finding("immune_inflammation", "finding-b-cell", priority_score=70),
        ]
        recommendations = [
            _recommendation("endocrine_metabolic", "finding-glucose"),
            _recommendation("digestive_gut", "finding-gastritis"),
            _recommendation("immune_inflammation", "finding-b-cell"),
        ]
        finding_names = {
            "finding-glucose": "血糖代谢异常",
            "finding-gastritis": "慢性非萎缩性胃炎伴糜烂",
            "finding-b-cell": "B细胞相关免疫炎症",
        }

        summary = build_plan_summary(findings, recommendations, finding_names)

        self.assertEqual(
            summary,
            [
                "本方案针对血糖代谢异常、慢性非萎缩性胃炎伴糜烂、B细胞相关免疫炎症三类问题，"
                "分别稳定血糖代谢、修复胃肠消化及黏膜屏障功能、调节免疫并抗氧化抗炎。"
            ],
        )
        self.assertLessEqual(len(summary[0]), 100)

    def test_uses_at_most_three_highest_priority_distinct_systems(self) -> None:
        findings = [
            _finding(
                "skin_mucosa",
                "finding-skin",
                priority_level="中度关注",
                priority_score=20,
            ),
            _finding("immune_inflammation", "finding-immune", priority_score=60),
            _finding("digestive_gut", "finding-digestive", priority_score=70),
            _finding("endocrine_metabolic", "finding-glucose", priority_score=80),
            _finding("endocrine_metabolic", "finding-glucose-duplicate", priority_score=10),
        ]
        recommendations = [
            _recommendation(item.system_id, item.finding_ids[0]) for item in findings
        ]
        finding_names = {
            "finding-skin": "皮肤屏障异常",
            "finding-immune": "免疫炎症异常",
            "finding-digestive": "胃肠消化功能异常",
            "finding-glucose": "血糖代谢异常",
            "finding-glucose-duplicate": "胰岛素代谢异常",
        }

        summary = build_plan_summary(
            findings,
            recommendations,
            finding_names,
            max_named_problems=99,
        )[0]

        self.assertIn("血糖代谢异常、胃肠消化功能异常、免疫炎症异常三类问题", summary)
        self.assertNotIn("皮肤屏障异常", summary)
        self.assertNotIn("胰岛素代谢异常", summary)
        self.assertLessEqual(len(summary), 100)

    def test_single_problem_uses_focus_wording(self) -> None:
        summary = build_plan_summary(
            [_finding("endocrine_metabolic", "finding-glucose")],
            [_recommendation("endocrine_metabolic", "finding-glucose")],
            {"finding-glucose": "血糖代谢异常"},
        )

        self.assertEqual(summary, ["本方案针对血糖代谢异常，重点稳定血糖代谢。"])

    def test_uncovered_problems_share_clinical_follow_up_direction(self) -> None:
        summary = build_plan_summary(
            [
                _finding("digestive_gut", "finding-digestive", priority_score=80),
                _finding("immune_inflammation", "finding-immune", priority_score=70),
            ],
            [],
            {
                "finding-digestive": "慢性胃炎",
                "finding-immune": "免疫指标异常",
            },
        )

        self.assertEqual(
            summary,
            [
                "本方案针对慢性胃炎、免疫指标异常两类问题，"
                "均配合生活方式调整、复查与医生评估。"
            ],
        )

    def test_overlong_problem_falls_back_without_mid_sentence_truncation(self) -> None:
        summary = build_plan_summary(
            [_finding("digestive_gut", "finding-long")],
            [_recommendation("digestive_gut", "finding-long")],
            {"finding-long": "超长疾病名称" * 40},
        )[0]

        self.assertLessEqual(len(summary), 100)
        self.assertEqual(
            summary,
            "本方案针对消化系统/肠道相关问题，重点修复胃肠消化及黏膜屏障功能。",
        )
        self.assertTrue(summary.endswith("。"))

    def test_legacy_summary_is_one_sentence_with_hard_limit(self) -> None:
        self.assertEqual(
            normalize_plan_summary_items(["第一项", "第二项"]),
            ["第一项；第二项。"],
        )
        normalized = normalize_plan_summary_items(["旧版总结内容" * 30])
        self.assertEqual(len(normalized), 1)
        self.assertLessEqual(len(normalized[0]), 100)
        self.assertEqual(
            normalized[0],
            "本方案针对当前已确认的重点健康问题，重点配合生活方式调整、必要复查及医生评估。",
        )

    def test_review_replaces_legacy_summary_once_and_keeps_dynamic_numbering(self) -> None:
        service = ReviewService.__new__(ReviewService)
        draft = SimpleNamespace(
            structured_system_findings=[],
            recommended_skus=[],
            report_sections={"方案总结": ["历史方案总结" * 30]},
        )
        case = SimpleNamespace(confirmed_clinical_findings=[])
        report = (
            "# 功能医学综合分析与首月干预方案\n\n"
            "## 一、核心结论与健康画像\n\n现有内容\n\n"
            "## 十、方案总结\n\n需要被替换的旧总结"
        )

        replaced = service._ensure_plan_summary_section(report, draft, case)
        numbered = service._number_customer_sections(replaced)

        self.assertEqual(numbered.count("方案总结"), 1)
        self.assertIn("## 二、方案总结", numbered)
        match = re.search(r"## 二、方案总结\s+([^\n]+)", numbered)
        self.assertIsNotNone(match)
        self.assertLessEqual(len(match.group(1)), 100)
        self.assertNotIn("需要被替换的旧总结", numbered)


class CoreHealthPortraitTests(unittest.TestCase):
    def test_excludes_chronic_food_sensitivity_from_mainlines_and_evidence(self) -> None:
        structured = [
            SimpleNamespace(
                system_id="digestive_gut",
                finding_ids=["finding-gut", "finding-sunflower", "finding-pumpkin"],
                priority_level="最高优先级",
                priority_score=95,
                summary="肠道菌群失衡并伴多项慢性食物敏感IgG阳性",
            ),
            _finding("endocrine_metabolic", "finding-thyroid", priority_score=90),
            _finding("reproductive_breast", "finding-breast", priority_score=85),
            _finding("cardiovascular", "finding-cardio", priority_score=80),
            _finding("liver_detox", "finding-liver", priority_score=75),
        ]
        confirmed = [
            SimpleNamespace(
                finding_id="finding-gut",
                finding_name="肠道菌群失衡",
                system_ids=["digestive_gut"],
                evidence_class="clinical_confirmed",
                source_span=SimpleNamespace(file_name="肠道检测.pdf"),
            ),
            SimpleNamespace(
                finding_id="finding-sunflower",
                finding_name="葵花籽慢性食物过敏IgG",
                system_ids=["digestive_gut"],
                evidence_class="lab_abnormal",
                source_span=SimpleNamespace(file_name="慢性食物敏感检测.pdf"),
            ),
            SimpleNamespace(
                finding_id="finding-pumpkin",
                finding_name="南瓜慢性食物过敏IgG",
                system_ids=["digestive_gut"],
                evidence_class="lab_abnormal",
                source_span=SimpleNamespace(file_name="慢性食物敏感检测.pdf"),
            ),
            *[
                SimpleNamespace(
                    finding_id=finding_id,
                    finding_name=name,
                    system_ids=[system_id],
                    evidence_class="clinical_confirmed",
                    source_span=SimpleNamespace(file_name="影像检查.pdf"),
                )
                for finding_id, name, system_id in (
                    ("finding-thyroid", "甲状腺结节", "endocrine_metabolic"),
                    ("finding-breast", "乳腺结节", "reproductive_breast"),
                )
            ],
        ]
        abnormal = [
            SimpleNamespace(
                id="finding-gut",
                name="肠道菌群失衡",
                result_text="阳性",
                raw_value="阳性",
                unit="",
                abnormal_flag="positive",
                evidence_class="lab_abnormal",
                source_file_name="肠道检测.pdf",
            ),
            *[
                SimpleNamespace(
                    id=finding_id,
                    name=name,
                    result_text="阳性",
                    raw_value="阳性",
                    unit="",
                    abnormal_flag="positive",
                    evidence_class="lab_abnormal",
                    source_file_name="慢性食物敏感检测.pdf",
                )
                for finding_id, name in (
                    ("finding-sunflower", "葵花籽慢性食物过敏IgG"),
                    ("finding-pumpkin", "南瓜慢性食物过敏IgG"),
                )
            ],
        ]

        portrait = build_core_health_portrait(
            structured,
            confirmed_findings=confirmed,
            abnormal_findings=abnormal,
            objective_evidence_items=[
                "葵花籽慢性食物过敏IgG：阳性",
                "肠道菌群失衡：阳性",
            ],
        )[0]

        self.assertIn("「肠道菌群失衡—甲状腺结节—乳腺结节", portrait)
        self.assertIn("肠道菌群失衡阳性", portrait)
        self.assertNotIn("葵花籽", portrait)
        self.assertNotIn("南瓜", portrait)
        self.assertNotIn("食物过敏IgG", portrait)

    def test_food_sensitivity_only_system_does_not_become_a_mainline(self) -> None:
        portrait = build_core_health_portrait(
            [
                _finding("digestive_gut", "finding-food", priority_score=99),
                _finding("neuro_sleep", "finding-sleep", priority_score=80),
            ],
            confirmed_findings=[
                SimpleNamespace(
                    finding_id="finding-food",
                    finding_name="蛋清慢性食物过敏IgG",
                    system_ids=["digestive_gut"],
                    evidence_class="lab_abnormal",
                    source_span=SimpleNamespace(file_name="食物IgG报告.pdf"),
                ),
                SimpleNamespace(
                    finding_id="finding-sleep",
                    finding_name="睡眠节律异常",
                    system_ids=["neuro_sleep"],
                    evidence_class="symptom",
                    source_span=SimpleNamespace(file_name="MSQ.pdf"),
                ),
            ],
        )[0]

        self.assertIn("「睡眠节律异常」一条主线", portrait)
        self.assertNotIn("蛋清", portrait)
        self.assertNotIn("胃肠消化", portrait)

    def test_builds_one_three_sentence_cross_system_conclusion(self) -> None:
        structured = [
            _finding("urinary_renal", "finding-creatinine", priority_score=95),
            _finding("endocrine_metabolic", "finding-uric-acid", priority_score=80),
            _finding("neuro_sleep", "finding-sleep", priority_score=70),
        ]
        confirmed = [
            SimpleNamespace(
                finding_id="finding-creatinine",
                finding_name="肾功能异常",
                system_ids=["urinary_renal"],
                evidence_class="clinical_confirmed",
            ),
            SimpleNamespace(
                finding_id="finding-uric-acid",
                finding_name="高尿酸血症",
                system_ids=["endocrine_metabolic", "urinary_renal"],
                evidence_class="clinical_confirmed",
            ),
            SimpleNamespace(
                finding_id="finding-sleep",
                finding_name="神经睡眠节律异常",
                system_ids=["neuro_sleep"],
                evidence_class="symptom",
            ),
        ]
        abnormal = [
            SimpleNamespace(
                id="finding-creatinine",
                name="肌酐",
                result_text="100 μmol/L",
                raw_value="100",
                unit="μmol/L",
                abnormal_flag="high",
                evidence_class="lab_abnormal",
            ),
            SimpleNamespace(
                id="finding-uric-acid",
                name="尿酸",
                result_text="435 μmol/L",
                raw_value="435",
                unit="μmol/L",
                abnormal_flag="high",
                evidence_class="lab_abnormal",
            ),
            SimpleNamespace(
                id="finding-sleep",
                name="入睡困难",
                result_text="患者自述",
                raw_value="",
                unit="",
                abnormal_flag="patient_reported",
                evidence_class="symptom",
            ),
        ]

        portrait = build_core_health_portrait(
            structured,
            confirmed_findings=confirmed,
            abnormal_findings=abnormal,
        )

        self.assertEqual(len(portrait), 1)
        self.assertEqual(portrait[0].count("。"), 3)
        self.assertIn("肾功能异常", portrait[0])
        self.assertIn("高尿酸血症", portrait[0])
        self.assertIn("神经睡眠节律异常", portrait[0])
        self.assertIn("肌酐100 μmol/L↑/尿酸435 μmol/L↑", portrait[0])
        self.assertIn("首月以「肾脏减负」为核心", portrait[0])
        self.assertNotIn("患者自述", portrait[0])

    def test_risk_notice_replaces_window_wording_with_medical_evaluation(self) -> None:
        portrait = build_core_health_portrait(
            [_finding("cardiovascular", "finding-cardiovascular")],
            confirmed_findings=[
                SimpleNamespace(
                    finding_id="finding-cardiovascular",
                    finding_name="冠状动脉粥样硬化",
                    system_ids=["cardiovascular"],
                    evidence_class="clinical_confirmed",
                )
            ],
            risk_notices=["存在胸痛红旗，需要及时就医。"],
        )[0]

        self.assertIn("应优先完成医学评估与风险控制", portrait)
        self.assertNotIn("重要窗口期", portrait)

    def test_explicit_serious_objective_abnormality_uses_risk_wording(self) -> None:
        portrait = build_core_health_portrait(
            [_finding("urinary_renal", "finding-creatinine")],
            abnormal_findings=[
                SimpleNamespace(
                    id="finding-creatinine",
                    name="肌酐",
                    result_text="1001 μmol/L",
                    raw_value="1001",
                    unit="μmol/L",
                    abnormal_flag="high",
                    evidence_class="lab_abnormal",
                    interpretation="该指标为严重异常，应尽快完成医学评估。",
                    report_explanation="",
                    neutral_interpretation="",
                    support_need_text="",
                )
            ],
        )[0]

        self.assertIn("应优先完成医学评估与风险控制", portrait)
        self.assertNotIn("重要窗口期", portrait)

    def test_uses_system_fallback_when_only_a_lab_name_is_available(self) -> None:
        portrait = build_core_health_portrait(
            [_finding("urinary_renal", "finding-creatinine")],
            abnormal_findings=[
                SimpleNamespace(
                    id="finding-creatinine",
                    name="肌酐",
                    result_text="110 μmol/L",
                    raw_value="110",
                    unit="μmol/L",
                    abnormal_flag="high",
                    evidence_class="lab_abnormal",
                )
            ],
        )[0]

        self.assertIn("「肾脏与泌尿代谢异常」一条主线", portrait)
        self.assertIn("肌酐110 μmol/L↑", portrait)

    def test_limits_portrait_to_five_distinct_system_mainlines(self) -> None:
        structured = [
            _finding(system_id, f"finding-{index}", priority_score=100 - index)
            for index, system_id in enumerate(
                (
                    "urinary_renal",
                    "cardiovascular",
                    "endocrine_metabolic",
                    "digestive_gut",
                    "immune_inflammation",
                    "neuro_sleep",
                )
            )
        ]

        portrait = build_core_health_portrait(structured)[0]

        self.assertIn("五条主线", portrait)
        self.assertNotIn("神经、情绪与睡眠节律异常", portrait)

    def test_review_replaces_old_and_rag_augmented_portrait_once(self) -> None:
        service = ReviewService.__new__(ReviewService)
        draft = SimpleNamespace(
            structured_system_findings=[
                _finding("digestive_gut", "finding-gut", priority_score=80)
            ],
            key_lab_highlights=["肠道菌群：明显失衡（需关注）"],
            red_flags=[],
        )
        case = SimpleNamespace(
            confirmed_clinical_findings=[
                SimpleNamespace(
                    finding_id="finding-gut",
                    finding_name="肠道菌群失衡",
                    system_ids=["digestive_gut"],
                    evidence_class="clinical_confirmed",
                )
            ]
        )
        report = (
            "# 功能医学综合分析与首月干预方案\n\n"
            "## 一、核心结论与健康画像\n\n旧版画像。RAG附加内容。\n\n"
            "## 二、异常指标汇总\n\n现有内容"
        )

        replaced = service._ensure_core_health_portrait_section(report, draft, case)

        self.assertEqual(replaced.count("核心结论与健康画像"), 1)
        self.assertNotIn("旧版画像", replaced)
        self.assertNotIn("RAG附加内容", replaced)
        self.assertIn("肠道菌群失衡", replaced)
        self.assertIn("首月以「肠道微生态与屏障修复」为核心", replaced)


class LifestyleReportTextTests(unittest.TestCase):
    def test_removes_only_generic_pre_execution_confirmation(self) -> None:
        self.assertEqual(
            remove_generic_lifestyle_confirmation(
                "针对血糖波动减少精制糖；执行前需由医生确认。"
            ),
            "针对血糖波动减少精制糖。",
        )
        self.assertEqual(
            remove_generic_lifestyle_confirmation(
                "仅在医生确认适用后，从连续2周的12小时夜间进食间隔开始。"
            ),
            "从连续2周的12小时夜间进食间隔开始。",
        )
        self.assertEqual(
            remove_generic_lifestyle_confirmation(
                "开始前需要先与医生沟通确认，逐步增加活动量。"
            ),
            "逐步增加活动量。",
        )
        self.assertEqual(
            remove_generic_lifestyle_confirmation(
                "在医生或营养师确认后每3天重新引入1种食物。"
            ),
            "每3天重新引入1种食物。",
        )

    def test_preserves_material_safety_and_referral_language(self) -> None:
        safety_texts = [
            "出现低血糖表现立即停止并联系医生。",
            "孕期运动需先由产科确认，不采用统一目标心率。",
            "请立即联系精神专科、急诊或当地紧急医疗服务。",
        ]
        for text in safety_texts:
            self.assertEqual(remove_generic_lifestyle_confirmation(text), text)

    def test_review_cleanup_is_limited_to_lifestyle_section(self) -> None:
        service = ReviewService.__new__(ReviewService)
        report = (
            "## 四、生活方式干预\n"
            "减少精制糖；执行前需由医生确认。\n"
            "出现低血糖表现立即停止并联系医生。\n\n"
            "## 五、首月营养素干预方案\n"
            "营养素说明；执行前需由医生确认。"
        )

        cleaned = service._remove_lifestyle_confirmation_clauses(report)

        self.assertIn("减少精制糖。", cleaned)
        self.assertIn("出现低血糖表现立即停止并联系医生。", cleaned)
        self.assertIn("营养素说明；执行前需由医生确认。", cleaned)


class FoodSensitivityRoutingTests(unittest.TestCase):
    def test_content_identified_food_sensitivity_is_routed_without_filename_dependency(self) -> None:
        result = SimpleNamespace(
            file_name="体检附件-03.pdf",
            report_type="unknown_medical",
            food_sensitivity=SimpleNamespace(valid=True),
        )

        self.assertTrue(is_chronic_food_sensitivity_result(result))


if __name__ == "__main__":
    unittest.main()
