from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.domain.models import (  # noqa: E402
    AbnormalFlag,
    ClinicalEvidenceClass,
    ConfirmedClinicalFinding,
    ExtractedLabItem,
    LifestyleAction,
    LifestylePlan,
    LifestyleProtocolSelection,
    LifestyleSection,
    Questionnaire,
    ReferenceRange,
    SourceSpan,
)
from app.services.lifestyle_planning import LifestylePlanningService  # noqa: E402


class LifestylePlanningServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = LifestylePlanningService()

    @staticmethod
    def _marker(code: str, name: str, flag: AbnormalFlag, value: float = 1.0) -> ExtractedLabItem:
        return ExtractedLabItem(
            marker_code=code,
            marker_name=name,
            raw_value=str(value),
            value=value,
            normalized_value=value,
            abnormal_flag=flag,
            ref_range=ReferenceRange(),
            source_span=SourceSpan(file_name="test.txt", page=1, snippet=f"{name} {value}"),
        )

    @staticmethod
    def _context(
        questionnaire: Questionnaire,
        *,
        markers: dict[str, list[ExtractedLabItem]] | None = None,
        lifestyle_tags: set[str] | None = None,
        food_sensitivities: set[str] | None = None,
        unresolved: set[str] | None = None,
        msq_system_scores: dict[str, int] | None = None,
        clinical_findings: list[ConfirmedClinicalFinding] | None = None,
        clinical_summary_text: str = "",
        manual_clinical_summary_text: str = "",
    ) -> SimpleNamespace:
        findings = clinical_findings or []
        findings_by_code: dict[str, list[ConfirmedClinicalFinding]] = {}
        for finding in findings:
            if finding.finding_code:
                findings_by_code.setdefault(finding.finding_code, []).append(finding)
        return SimpleNamespace(
            markers_by_code=markers or {},
            clinical_findings=findings,
            clinical_findings_by_code=findings_by_code,
            age=questionnaire.age,
            pregnancy=bool(questionnaire.pregnant_or_lactating),
            sex=questionnaire.sex,
            lifestyle_tags=lifestyle_tags or set(),
            food_sensitivities=food_sensitivities or set(),
            unresolved_questionnaire_fields=unresolved or set(),
            msq_system_scores=msq_system_scores or dict(questionnaire.msq_system_scores),
            clinical_summary_text=clinical_summary_text,
            manual_clinical_summary_text=manual_clinical_summary_text,
        )

    @staticmethod
    def _case(questionnaire: Questionnaire) -> SimpleNamespace:
        return SimpleNamespace(questionnaire=questionnaire)

    @staticmethod
    def _section(plan: LifestylePlan, domain: str) -> LifestyleSection | None:
        return next((item for item in plan.sections if item.domain == domain), None)

    def test_explicit_ibs_term_triggers_low_fodmap_without_duplicate_gut_tag(self) -> None:
        questionnaire = Questionnaire(symptoms=["IBS反复腹胀"], known_conditions=["IBS"])

        plan = self.service.build_plan(self._case(questionnaire), self._context(questionnaire))

        self.assertIn("LP03", {item.protocol_id for item in plan.selected_protocols})
        diet = self._section(plan, "diet")
        self.assertIsNotNone(diet)
        self.assertGreaterEqual(len(diet.actions), 3)
        self.assertLessEqual(len(diet.actions), 5)
        self.assertTrue(any(item.action_id.startswith("LP03") for item in diet.actions))

    def test_confirmed_food_sensitivity_items_trigger_specific_lp02_group(self) -> None:
        questionnaire = Questionnaire(known_conditions=[])
        context = self._context(
            questionnaire,
            food_sensitivities={"牛奶", "鸡蛋"},
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        rendered = "\n".join(self.service.report_items(plan))

        self.assertIn("LP02", {item.protocol_id for item in plan.selected_protocols})
        self.assertEqual(rendered.count("牛奶"), 1)
        self.assertEqual(rendered.count("鸡蛋"), 1)
        self.assertIn("试验性回避", rendered)
        self.assertIn("不依据食物IgG结果直接诊断过敏", rendered)

    def test_normal_adult_movement_uses_existing_baseline_rule_without_diet_placeholder(self) -> None:
        questionnaire = Questionnaire(
            age=34,
            sex="male",
            pregnant_or_lactating=False,
            known_conditions=[],
            exercise_frequency="无规律运动",
        )

        plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(questionnaire, lifestyle_tags={"movement"}),
        )
        rendered = "\n".join(self.service.report_items(plan))

        self.assertIsNone(self._section(plan, "diet"))
        movement = self._section(plan, "movement")
        self.assertIsNotNone(movement)
        self.assertGreaterEqual(len(movement.actions), 3)
        self.assertLessEqual(len(movement.actions), 5)
        self.assertIn("运动建议", rendered)
        self.assertNotIn("饮食建议", rendered)
        self.assertNotIn("暂无", rendered)

    def test_missing_exercise_baseline_only_removes_terminal_adult_target(self) -> None:
        questionnaire = Questionnaire(
            age=30,
            sex="male",
            pregnant_or_lactating=False,
            known_conditions=[],
            exercise_frequency=None,
        )
        marker = self._marker("fasting_glucose", "空腹血糖", AbnormalFlag.high, 6.2)
        context = self._context(questionnaire, markers={"fasting_glucose": [marker]})

        plan = self.service.build_plan(self._case(questionnaire), context)
        movement = self._section(plan, "movement")
        rendered = "\n".join(self.service.report_items(plan))

        self.assertIsNotNone(movement)
        self.assertTrue(any("餐后步行15–20分钟" in item.text for item in movement.actions))
        self.assertIn("PAR-Q+", rendered)
        self.assertNotIn("150分钟", rendered)
        self.assertEqual(rendered.count("空腹血糖偏高"), 1)

    def test_known_movement_red_flag_removes_movement_dimension(self) -> None:
        questionnaire = Questionnaire(
            age=50,
            sex="male",
            pregnant_or_lactating=False,
            known_conditions=["急性心肌梗死"],
            exercise_frequency="有运动习惯",
        )

        plan = self.service.build_plan(self._case(questionnaire), self._context(questionnaire))

        self.assertIsNone(self._section(plan, "movement"))

    def test_problem_basis_is_rendered_once_for_all_actions(self) -> None:
        marker = self._marker("hs_crp", "hs-CRP", AbnormalFlag.high, 4.2)
        questionnaire = Questionnaire()
        plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(questionnaire, markers={"hs_crp": [marker]}),
        )

        rendered = "\n".join(self.service.report_items(plan))

        self.assertEqual(rendered.count("依据：hs-CRP偏高"), 1)
        self.assertNotIn("针对您的hs-CRP偏高", rendered)
        self.assertIn("监测与复查", rendered)

    def test_overlapping_protocol_bases_do_not_repeat_same_finding(self) -> None:
        hs_crp = self._marker("hs_crp", "hs-CRP", AbnormalFlag.high, 4.2)
        vitamin_d = self._marker("vitamin_d", "25-羟维生素D", AbnormalFlag.low, 18.0)
        questionnaire = Questionnaire()
        plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(
                questionnaire,
                markers={"hs_crp": [hs_crp], "vitamin_d": [vitamin_d]},
            ),
        )

        rendered = "\n".join(self.service.report_items(plan))

        self.assertEqual(rendered.count("hs-CRP偏高"), 1)
        self.assertEqual(rendered.count("25-羟维生素D偏低"), 1)

    def test_three_protocol_cap_keeps_highest_diet_and_movement_matches(self) -> None:
        questionnaire = Questionnaire(
            age=35,
            sex="male",
            pregnant_or_lactating=False,
            symptoms=["失眠", "压力大"],
            known_conditions=[],
            exercise_frequency="无规律运动",
        )
        marker = self._marker("hs_crp", "hs-CRP", AbnormalFlag.high, 4.2)
        context = self._context(
            questionnaire,
            markers={"hs_crp": [marker]},
            lifestyle_tags={"movement", "sleep_recovery", "stress_support"},
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        selected_ids = {item.protocol_id for item in plan.selected_protocols}

        self.assertLessEqual(len(selected_ids), 3)
        self.assertTrue(any("diet" in (self.service.protocols_by_id[item]["actions"] or {}) for item in selected_ids))
        self.assertTrue(any("movement" in (self.service.protocols_by_id[item]["actions"] or {}) for item in selected_ids))

    def test_physical_problems_precede_generic_sleep_recovery(self) -> None:
        scenarios = [
            (
                "inflammation",
                Questionnaire(sleep_quality="差"),
                {"hs_crp": [self._marker("hs_crp", "hs-CRP", AbnormalFlag.high, 4.2)]},
                {"sleep_recovery"},
                "LP01",
                "抗炎饮食",
            ),
            (
                "thyroid",
                Questionnaire(sleep_quality="差"),
                {"tsh": [self._marker("tsh", "TSH", AbnormalFlag.high, 7.0)]},
                {"sleep_recovery"},
                "LP11",
                "甲状腺相关生活方式支持",
            ),
            (
                "fatigue",
                Questionnaire(symptoms=["慢性疲劳"], sleep_quality="差"),
                {},
                {"energy_support", "sleep_recovery"},
                "LP09",
                "能量与运动耐受支持",
            ),
            (
                "ibs",
                Questionnaire(
                    symptoms=["IBS反复腹胀"],
                    known_conditions=["IBS"],
                    sleep_quality="差",
                ),
                {},
                {"gut_support", "sleep_recovery"},
                "LP03",
                "低FODMAP短期试验",
            ),
            (
                "glucose",
                Questionnaire(sleep_quality="差"),
                {
                    "fasting_glucose": [
                        self._marker("fasting_glucose", "空腹血糖", AbnormalFlag.high, 6.2)
                    ]
                },
                {"glucose_support", "sleep_recovery"},
                "LP12",
                "血糖稳态",
            ),
            (
                "lipid",
                Questionnaire(sleep_quality="差"),
                {
                    "triglycerides": [
                        self._marker("triglycerides", "甘油三酯", AbnormalFlag.high, 2.8)
                    ]
                },
                {"lipid_support", "sleep_recovery"},
                "LP13",
                "心血管代谢",
            ),
        ]

        for name, questionnaire, markers, tags, expected_id, expected_title in scenarios:
            with self.subTest(name=name):
                plan = self.service.build_plan(
                    self._case(questionnaire),
                    self._context(questionnaire, markers=markers, lifestyle_tags=tags),
                )
                selected = [item.protocol_id for item in plan.selected_protocols]
                rendered = "\n".join(self.service.report_items(plan))
                first_problem = next(
                    item for item in self.service.report_items(plan) if item.startswith("### 1.1")
                )

                self.assertEqual(selected[0], expected_id)
                self.assertIn(expected_title, first_problem)
                self.assertLessEqual(len(set(selected) & {"LP05", "LP06"}), 1)
                self.assertLessEqual(len(selected), 3)
                self.assertNotIn("依据：年龄信息", rendered)

    def test_msq_score_one_eczema_is_demoted_below_stronger_gut_evidence(self) -> None:
        questionnaire = Questionnaire(
            symptoms=["湿疹", "腹胀/胀气"],
            msq_symptom_scores={"湿疹": 1, "腹胀/胀气": 2},
            msq_system_scores={"皮肤": 1, "消化道": 2},
        )
        context = self._context(
            questionnaire,
            lifestyle_tags={"gut_support"},
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        selected = [item.protocol_id for item in plan.selected_protocols]
        rendered = "\n".join(self.service.report_items(plan))

        self.assertEqual(selected[0], "LP03")
        self.assertIn("LP01", selected)
        self.assertLess(rendered.index("低FODMAP短期试验"), rendered.index("抗炎饮食"))
        self.assertIn("依据：MSQ：湿疹（偶尔，1分）", rendered)

    def test_msq_score_one_eczema_can_trigger_when_it_is_the_only_problem(self) -> None:
        questionnaire = Questionnaire(
            symptoms=["湿疹"],
            msq_symptom_scores={"湿疹": 1},
            msq_system_scores={"皮肤": 1},
        )

        plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(questionnaire),
        )
        rendered = "\n".join(self.service.report_items(plan))

        self.assertIn("LP01", {item.protocol_id for item in plan.selected_protocols})
        self.assertIn("抗炎饮食（依据：MSQ：湿疹（偶尔，1分））", rendered)

    def test_confirmed_eczema_upgrades_score_one_msq_evidence(self) -> None:
        questionnaire = Questionnaire(
            symptoms=["湿疹"],
            known_conditions=["湿疹"],
            msq_symptom_scores={"湿疹": 1},
            msq_system_scores={"皮肤": 1},
        )
        evidence = self.service._evidence(
            self._case(questionnaire),
            self._context(questionnaire),
        )

        match = self.service._match_protocol(
            self.service.protocols_by_id["LP01"],
            evidence,
            self._context(questionnaire),
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.evidence_tier, 1)
        self.assertEqual(match.anchor_text, "湿疹")

    def test_model_summary_echo_cannot_upgrade_score_one_eczema(self) -> None:
        questionnaire = Questionnaire(
            symptoms=["湿疹", "腹胀/胀气"],
            msq_symptom_scores={"湿疹": 1, "腹胀/胀气": 2},
            msq_system_scores={"皮肤": 1, "消化道": 2},
        )
        context = self._context(
            questionnaire,
            lifestyle_tags={"gut_support"},
            clinical_summary_text=(
                "模型综合病例总结：患者自述皮肤黏膜症状包括湿疹、红疹，"
                "同时存在腹胀和消化不良。"
            ),
            manual_clinical_summary_text="",
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        rendered = "\n".join(self.service.report_items(plan))
        selected = [item.protocol_id for item in plan.selected_protocols]

        self.assertEqual(selected[0], "LP03")
        self.assertIn("依据：MSQ：湿疹（偶尔，1分）", rendered)
        self.assertNotIn("抗炎饮食（依据：湿疹）", rendered)

    def test_contextual_symptom_support_finding_inherits_msq_score_one_tier(self) -> None:
        symptom = "容易疲劳虚弱，没精神"
        questionnaire = Questionnaire(
            symptoms=[symptom],
            msq_symptom_scores={symptom: 1},
            msq_system_scores={"能量/活动": 1},
        )
        support_finding = ConfirmedClinicalFinding(
            finding_id="sn_energy_fatigue",
            finding_name="细胞能量与疲劳恢复支持",
            system_ids=["neuro_sleep"],
            evidence_class=ClinicalEvidenceClass.symptom,
            abnormal_flag="patient_reported_symptom",
            source_span=SourceSpan(
                file_name="医生确认资料",
                page=1,
                snippet="细胞能量与疲劳恢复支持",
            ),
        )
        context = self._context(
            questionnaire,
            clinical_findings=[support_finding],
            lifestyle_tags={"energy_support"},
        )
        evidence = self.service._evidence(self._case(questionnaire), context)

        match = self.service._match_protocol(
            self.service.protocols_by_id["LP09"],
            evidence,
            context,
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.evidence_tier, 3)
        self.assertEqual(match.anchor_text, f"MSQ：{symptom}（偶尔，1分）")

    def test_huangqi_equivalent_context_prefers_thyroid_and_gut_over_eczema(self) -> None:
        fatigue = "容易疲劳虚弱，没精神"
        questionnaire = Questionnaire(
            exercise_frequency="无规律运动",
            symptoms=["湿疹", "腹胀/胀气", fatigue],
            msq_symptom_scores={"湿疹": 1, "腹胀/胀气": 1, fatigue: 1},
            msq_system_scores={"皮肤": 1, "消化道": 2, "能量/活动": 1},
        )
        thyroid = ConfirmedClinicalFinding(
            finding_id="finding_thyroid_nodule",
            finding_code="thyroid_nodule",
            finding_name="甲状腺左叶结节",
            system_ids=["endocrine_metabolic"],
            evidence_class=ClinicalEvidenceClass.clinical_confirmed,
            source_span=SourceSpan(
                file_name="体检报告.pdf",
                page=1,
                snippet="甲状腺左叶结节，TI-RADS 3类",
            ),
        )
        energy_support = ConfirmedClinicalFinding(
            finding_id="sn_energy_fatigue",
            finding_name="细胞能量与疲劳恢复支持",
            system_ids=["neuro_sleep"],
            evidence_class=ClinicalEvidenceClass.symptom,
            abnormal_flag="patient_reported_symptom",
            source_span=SourceSpan(
                file_name="医生确认资料",
                page=1,
                snippet="细胞能量与疲劳恢复支持",
            ),
        )
        context = self._context(
            questionnaire,
            lifestyle_tags={"gut_support", "movement", "energy_support"},
            clinical_findings=[thyroid, energy_support],
            clinical_summary_text=(
                "模型综合病例总结：湿疹、疲劳、甲状腺左叶结节及胃肠道症状。"
            ),
            manual_clinical_summary_text="",
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        selected = [item.protocol_id for item in plan.selected_protocols]
        rendered = "\n".join(self.service.report_items(plan))

        self.assertEqual(selected, ["LP11", "LP03", "LP09"])
        self.assertNotIn("LP01", selected)
        self.assertIn("甲状腺相关生活方式支持（依据：甲状腺左叶结节）", rendered)
        self.assertIn(f"依据：MSQ：{fatigue}（偶尔，1分）", rendered)
        self.assertNotIn("依据：湿疹", rendered)

    def test_score_one_eczema_does_not_take_a_slot_from_three_stronger_protocols(self) -> None:
        questionnaire = Questionnaire(
            symptoms=["湿疹"],
            known_conditions=["IBS"],
            msq_symptom_scores={"湿疹": 1},
            msq_system_scores={"皮肤": 1, "消化道": 2},
        )
        markers = {
            "fasting_glucose": [
                self._marker("fasting_glucose", "空腹血糖", AbnormalFlag.high, 6.2)
            ],
            "triglycerides": [
                self._marker("triglycerides", "甘油三酯", AbnormalFlag.high, 2.8)
            ],
        }

        plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(
                questionnaire,
                markers=markers,
                lifestyle_tags={"glucose_support", "lipid_support", "gut_support"},
            ),
        )

        selected = {item.protocol_id for item in plan.selected_protocols}
        self.assertEqual(selected, {"LP03", "LP12", "LP13"})
        self.assertNotIn("LP01", selected)

    def test_composite_notes_use_only_the_matched_lipid_anchor(self) -> None:
        questionnaire = Questionnaire(
            additional_notes=(
                "由已填写 MSQ 问卷自动导入，建议人工核对后再生成最终报告；"
                "月经周期28天、经期5-7天、怀孕1次、育有1个、生产方式剖腹产；"
                "睡眠：多梦；病史包括左腿小腿粉碎性骨折（意外）、高血脂、"
                "子宫肌瘤、剖腹产（手术）"
            )
        )

        plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(questionnaire, lifestyle_tags={"lipid_support"}),
        )
        rendered = "\n".join(self.service.report_items(plan))
        cardiovascular_heading = next(
            item
            for item in self.service.report_items(plan)
            if "心血管代谢（依据：" in item
        )

        self.assertIn("心血管代谢（依据：高血脂）", cardiovascular_heading)
        self.assertNotIn("自动导入", cardiovascular_heading)
        self.assertNotIn("人工核对", cardiovascular_heading)
        self.assertNotIn("月经周期", cardiovascular_heading)
        self.assertNotIn("生产方式", cardiovascular_heading)
        self.assertNotIn("已确认的血脂相关问题", cardiovascular_heading)

    def test_generic_sleep_family_uses_one_slot_and_merges_unique_actions(self) -> None:
        questionnaire = Questionnaire(sleep_quality="差")
        context = self._context(questionnaire, lifestyle_tags={"sleep_recovery"})

        plan = self.service.build_plan(self._case(questionnaire), context)
        rendered = "\n".join(self.service.report_items(plan))
        selected = [item.protocol_id for item in plan.selected_protocols]

        self.assertEqual(selected, ["LP06"])
        self.assertIn("睡前至少3小时", rendered)
        self.assertIn("固定每日进餐时间", rendered)
        self.assertNotIn("皮质醇节律支持", rendered)

    def test_osa_sleep_red_flag_precedes_physical_problem(self) -> None:
        questionnaire = Questionnaire(symptoms=["OSAHS"], sleep_quality="差")
        hs_crp = self._marker("hs_crp", "hs-CRP", AbnormalFlag.high, 4.2)
        context = self._context(
            questionnaire,
            markers={"hs_crp": [hs_crp]},
            lifestyle_tags={"sleep_recovery"},
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        selected = [item.protocol_id for item in plan.selected_protocols]
        rendered = "\n".join(self.service.report_items(plan))

        self.assertEqual(selected[0], "LP06")
        self.assertIn("LP01", selected)
        self.assertIn("睡眠呼吸暂停优先处理", rendered)
        report_items = self.service.report_items(plan)
        sleep_section_index = next(
            index
            for index, item in enumerate(report_items)
            if item.endswith("睡眠与节律建议")
        )
        self.assertIn("睡眠呼吸暂停优先处理", report_items[sleep_section_index + 1])

    def test_distinct_cortisol_and_insomnia_evidence_allow_both_sleep_protocols(self) -> None:
        questionnaire = Questionnaire(
            symptoms=["皮质醇节律异常", "失眠"],
            sleep_quality="差",
        )
        context = self._context(questionnaire, lifestyle_tags={"sleep_recovery"})

        plan = self.service.build_plan(self._case(questionnaire), context)
        selected = {item.protocol_id for item in plan.selected_protocols}

        self.assertTrue({"LP05", "LP06"}.issubset(selected))

    def test_uncovered_dizziness_does_not_invent_physical_protocol(self) -> None:
        questionnaire = Questionnaire(symptoms=["反复头晕"], sleep_quality="差")
        context = self._context(questionnaire, lifestyle_tags={"sleep_recovery"})

        plan = self.service.build_plan(self._case(questionnaire), context)

        self.assertEqual(
            [item.protocol_id for item in plan.selected_protocols],
            ["LP06"],
        )

    def test_report_problem_order_uses_selection_priority_not_action_category(self) -> None:
        plan = LifestylePlan(
            status="ready",
            selected_protocols=[
                LifestyleProtocolSelection(
                    protocol_id="LP01",
                    title="抗炎饮食",
                    admission="direct",
                    reason="依据hs-CRP偏高命中",
                    anchor_refs=["marker:hs_crp"],
                ),
                LifestyleProtocolSelection(
                    protocol_id="LP06",
                    title="睡眠修复",
                    admission="direct",
                    reason="依据睡眠质量差命中",
                    anchor_refs=["questionnaire:sleep"],
                ),
            ],
            sections=[
                LifestyleSection(
                    domain="diet",
                    title="饮食建议",
                    actions=[
                        LifestyleAction(
                            action_id="LP06_diet_1",
                            domain="diet",
                            category="limit",
                            text="睡前避免进食。",
                            anchor_refs=["questionnaire:sleep"],
                            quantity="睡前3小时",
                        ),
                        LifestyleAction(
                            action_id="LP01_diet_1",
                            domain="diet",
                            category="recommend",
                            text="每日安排不同颜色蔬菜。",
                            anchor_refs=["marker:hs_crp"],
                            quantity="300克/日",
                        ),
                    ],
                )
            ],
        )

        rendered = self.service.report_items(plan)
        headings = [
            item
            for item in rendered
            if item.startswith("### 1.1") or item.startswith("### 1.2")
        ]

        self.assertIn("抗炎饮食", headings[0])
        self.assertIn("睡眠修复", headings[1])

    def test_missing_renal_history_omits_high_protein_bmi_action_only(self) -> None:
        questionnaire = Questionnaire(
            age=40,
            sex="male",
            pregnant_or_lactating=False,
            known_conditions=[],
        )
        marker = self._marker("bmi", "BMI", AbnormalFlag.high, 29.0)
        context = self._context(
            questionnaire,
            markers={"bmi": [marker]},
            unresolved={"known_conditions"},
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        diet = self._section(plan, "diet")

        self.assertIsNotNone(diet)
        combined = " ".join(item.text for item in diet.actions)
        self.assertIn("300–500千卡", combined)
        self.assertNotIn("1.0–1.2克", combined)

    def test_unknown_pregnancy_status_omits_high_risk_action_not_whole_protocol(self) -> None:
        questionnaire = Questionnaire(age=40, sex="female", pregnant_or_lactating=None)
        marker = self._marker("fasting_glucose", "空腹血糖", AbnormalFlag.high, 6.2)

        plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(questionnaire, markers={"fasting_glucose": [marker]}),
        )
        diet = self._section(plan, "diet")
        combined = " ".join(item.text for item in diet.actions)

        self.assertIn("蔬菜、蛋白质、主食", combined)
        self.assertNotIn("夜间进食间隔", combined)

    def test_same_problem_from_multiple_protocols_uses_one_visible_group(self) -> None:
        refs = ["marker:fasting_glucose"]
        plan = LifestylePlan(
            status="ready",
            selected_protocols=[
                LifestyleProtocolSelection(
                    protocol_id="LP04",
                    title="限时进食",
                    admission="review",
                    reason="依据空腹血糖偏高命中",
                    anchor_refs=refs,
                ),
                LifestyleProtocolSelection(
                    protocol_id="LP12",
                    title="血糖稳态",
                    admission="direct",
                    reason="依据空腹血糖偏高命中",
                    anchor_refs=refs,
                ),
            ],
            sections=[
                LifestyleSection(
                    domain="diet",
                    title="饮食建议",
                    actions=[
                        LifestyleAction(
                            action_id="LP04_diet_1",
                            domain="diet",
                            category="execution",
                            text="连续2周记录进食窗口。",
                            anchor_refs=refs,
                            quantity="2周",
                        ),
                        LifestyleAction(
                            action_id="LP12_diet_1",
                            domain="diet",
                            category="recommend",
                            text="每餐保留1拳头主食。",
                            anchor_refs=refs,
                            quantity="1拳头/餐",
                        ),
                    ],
                )
            ],
        )

        rendered = "\n".join(self.service.report_items(plan))

        self.assertEqual(rendered.count("依据：空腹血糖偏高"), 1)
        self.assertEqual(rendered.count("### 1.1"), 1)

    def test_near_duplicate_actions_merge_evidence_refs(self) -> None:
        sections = {
            "diet": [
                LifestyleAction(
                    action_id="LP01_diet_1",
                    domain="diet",
                    category="recommend",
                    text="每日安排至少300克不同颜色蔬菜。",
                    anchor_refs=["marker:hs_crp"],
                    quantity="300克/日",
                ),
                LifestyleAction(
                    action_id="LP15_diet_1",
                    domain="diet",
                    category="recommend",
                    text="每日安排至少300克不同颜色蔬菜。",
                    anchor_refs=["marker:vitamin_d"],
                    quantity="300克/日",
                ),
            ],
            "movement": [],
            "sleep": [],
            "stress": [],
        }

        self.service._dedupe_sections(sections)

        self.assertEqual(len(sections["diet"]), 1)
        self.assertEqual(
            set(sections["diet"][0].anchor_refs),
            {"marker:hs_crp", "marker:vitamin_d"},
        )

    def test_all_24_protocols_have_runtime_actions_for_declared_domains(self) -> None:
        registry_path = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "data"
            / "lifestyle_protocol_registry.json"
        )
        registry = json.loads(registry_path.read_text(encoding="utf-8-sig"))

        self.assertEqual(
            [item["protocol_id"] for item in registry["protocols"]],
            [f"LP{index:02d}" for index in range(1, 25)],
        )
        expected_domains = {
            "LP01": {"diet"},
            "LP02": {"diet"},
            "LP03": {"diet"},
            "LP04": {"diet"},
            "LP05": {"diet", "movement"},
            "LP06": {"diet"},
            "LP07": {"diet"},
            "LP08": {"diet"},
            "LP09": {"diet", "movement"},
            "LP10": {"diet", "movement"},
            "LP11": {"diet", "movement"},
            "LP12": {"diet", "movement"},
            "LP13": {"diet", "movement"},
            "LP14": {"diet", "movement"},
            "LP15": {"diet", "movement"},
            "LP16": {"diet", "movement"},
            "LP17": {"diet", "movement"},
            "LP18": {"diet", "movement"},
            "LP19": {"movement"},
            "LP20": set(),
            "LP21": {"diet"},
            "LP22": {"diet", "movement"},
            "LP23": {"diet", "movement"},
            "LP24": {"diet", "movement"},
        }
        expected_primary_domains = {
            "LP01": {"diet"}, "LP02": {"diet"}, "LP03": {"diet"},
            "LP04": {"diet"}, "LP05": {"sleep", "stress"}, "LP06": {"sleep"},
            "LP07": {"diet"}, "LP08": {"diet"},
            "LP09": {"diet", "movement"}, "LP10": {"diet", "movement"},
            "LP11": {"diet", "movement"}, "LP12": {"diet", "movement"},
            "LP13": {"diet", "movement"}, "LP14": {"diet", "movement"},
            "LP15": {"diet", "movement"}, "LP16": {"diet", "movement"},
            "LP17": {"diet", "movement"}, "LP18": {"diet", "movement"},
            "LP19": {"movement"}, "LP20": {"stress"}, "LP21": {"diet"},
            "LP22": set(), "LP23": {"diet", "movement"}, "LP24": {"stress"},
        }
        for protocol in registry["protocols"]:
            self.assertEqual(
                set(protocol.get("declared_domains", [])),
                expected_domains[protocol["protocol_id"]],
            )
            actions = protocol.get("actions") or {}
            for domain in protocol.get("declared_domains", []):
                self.assertTrue(
                    actions.get(domain),
                    f"{protocol['protocol_id']} declares {domain} but has no runtime actions",
                )
            self.assertEqual(
                set(protocol.get("primary_domains", [])),
                expected_primary_domains[protocol["protocol_id"]],
            )
            self.assertIn(
                protocol.get("selection_class"),
                {"physical", "sleep_recovery", "stress_emotion", "contextual"},
            )

    def test_confirmed_blood_mercury_and_fatigue_output_both_diet_and_movement(self) -> None:
        questionnaire = Questionnaire(symptoms=["慢性疲劳", "运动不耐受"])
        mercury = self._marker("blood_mercury", "血汞", AbnormalFlag.high, 18.0)
        context = self._context(
            questionnaire,
            markers={"blood_mercury": [mercury]},
            lifestyle_tags={"energy_support"},
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        rendered = "\n".join(self.service.report_items(plan))
        selected = {item.protocol_id for item in plan.selected_protocols}
        diet = self._section(plan, "diet")
        movement = self._section(plan, "movement")

        self.assertTrue({"LP09", "LP18"}.issubset(selected))
        self.assertIsNotNone(diet)
        self.assertIsNotNone(movement)
        self.assertTrue(any(item.action_id.startswith("LP09") for item in diet.actions))
        self.assertTrue(any(item.action_id.startswith("LP18") for item in diet.actions))
        self.assertTrue(any(item.action_id.startswith("LP09") for item in movement.actions))
        self.assertTrue(any(item.action_id.startswith("LP18") for item in movement.actions))
        self.assertIn("果糖", rendered)
        self.assertIn("大型掠食鱼", rendered)
        self.assertEqual(rendered.count("血汞偏高"), 1)
        self.assertEqual(rendered.count("慢性疲劳"), 1)
        self.assertNotIn("桑拿", rendered)
        self.assertNotIn("运动排毒", rendered)
        self.assertEqual(plan.status, "needs_review")

    def test_htma_or_dental_material_description_alone_does_not_trigger_lp18(self) -> None:
        questionnaire = Questionnaire(
            symptoms=["HTMA提示血汞升高"],
            known_conditions=["口腔内有含汞牙科材料", "既往泛化环境暴露"],
        )

        plan = self.service.build_plan(self._case(questionnaire), self._context(questionnaire))

        self.assertNotIn("LP18", {item.protocol_id for item in plan.selected_protocols})

    def test_fatigue_missing_baseline_keeps_safe_actions_and_omits_high_risk_targets(self) -> None:
        questionnaire = Questionnaire(symptoms=["慢性疲劳"], exercise_frequency=None)
        context = self._context(questionnaire, lifestyle_tags={"energy_support"})

        plan = self.service.build_plan(self._case(questionnaire), context)
        diet = self._section(plan, "diet")
        movement = self._section(plan, "movement")
        combined = " ".join(
            item.text
            for section in plan.sections
            for item in section.actions
            if item.action_id.startswith("LP09")
        )

        self.assertIsNotNone(diet)
        self.assertIsNotNone(movement)
        self.assertIn("果糖", combined)
        self.assertIn("10–20分钟", combined)
        self.assertNotIn("生酮", combined)
        self.assertNotIn("16:8", combined)
        self.assertNotIn("3–4次", combined)
        rendered = "\n".join(self.service.report_items(plan))
        self.assertIn("PAR-Q+", rendered)
        self.assertNotIn("依据：年龄信息", rendered)
        self.assertNotIn("运动起步评估", rendered)

    def test_case_equivalent_context_keeps_lp09_after_stress_family_is_folded(self) -> None:
        questionnaire = Questionnaire(
            age=58,
            sex="female",
            pregnant_or_lactating=False,
            symptoms=["容易疲劳虚弱，没精神", "紧张", "焦虑"],
            known_conditions=[],
            exercise_frequency=None,
            msq_symptom_scores={"容易疲劳虚弱，没精神": 3},
            msq_system_scores={"能量/活动": 2},
        )
        triglycerides = self._marker("triglycerides", "甘油三酯", AbnormalFlag.high, 2.8)
        context = self._context(
            questionnaire,
            markers={"triglycerides": [triglycerides]},
            lifestyle_tags={
                "energy_support",
                "stress_support",
                "lipid_support",
                "metabolic_support",
            },
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        rendered = "\n".join(self.service.report_items(plan))
        selected = [item.protocol_id for item in plan.selected_protocols]

        self.assertEqual(set(selected), {"LP13", "LP24", "LP09"})
        self.assertNotIn("LP20", selected)
        self.assertIn("果糖摄入控制在25克以内", rendered)
        self.assertIn("每日10–20分钟低强度步行", rendered)
        self.assertIn("每周总量增加不超过10%", rendered)
        self.assertIn("疲劳持续超过24小时", rendered)
        self.assertIn("依据：MSQ：容易疲劳虚弱，没精神（中等，3分）", rendered)
        self.assertNotIn("MSQ能量/活动评分2分", rendered)
        self.assertNotIn("依据：年龄信息", rendered)
        self.assertNotIn("运动起步评估", rendered)
        self.assertIn("PAR-Q+", rendered)
        self.assertEqual(rendered.count("### 3.1"), 1)
        self.assertNotIn("### 3.2", rendered)

    def test_msq_fatigue_score_one_still_keeps_lp09_actions(self) -> None:
        symptom = "容易疲劳虚弱，没精神"
        questionnaire = Questionnaire(
            symptoms=[symptom],
            msq_symptom_scores={symptom: 1},
            msq_system_scores={"能量/活动": 1},
        )
        context = self._context(
            questionnaire,
            lifestyle_tags={"energy_support"},
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        rendered = "\n".join(self.service.report_items(plan))

        self.assertIn("LP09", [item.protocol_id for item in plan.selected_protocols])
        self.assertIn(f"依据：MSQ：{symptom}（偶尔，1分）", rendered)
        self.assertIn("果糖摄入控制在25克以内", rendered)
        self.assertIn("每日10–20分钟低强度步行", rendered)
        self.assertIn("每周总量增加不超过10%", rendered)
        self.assertIn("疲劳持续超过24小时", rendered)

    def test_energy_score_without_specific_symptom_uses_msq_score_anchor(self) -> None:
        questionnaire = Questionnaire(msq_system_scores={"能量/活动": 2})
        context = self._context(
            questionnaire,
            lifestyle_tags={"energy_support"},
        )

        plan = self.service.build_plan(self._case(questionnaire), context)
        rendered = "\n".join(self.service.report_items(plan))

        self.assertIn("LP09", {item.protocol_id for item in plan.selected_protocols})
        self.assertIn("依据：MSQ能量/活动评分2分", rendered)

    def test_chronic_fatigue_heading_without_positive_evidence_does_not_trigger_lp09(self) -> None:
        questionnaire = Questionnaire(additional_notes="慢性疲劳症")

        plan = self.service.build_plan(self._case(questionnaire), self._context(questionnaire))

        self.assertNotIn("LP09", {item.protocol_id for item in plan.selected_protocols})

    def test_age_alone_does_not_create_independent_movement_problem(self) -> None:
        questionnaire = Questionnaire(
            age=58,
            sex="female",
            pregnant_or_lactating=False,
            known_conditions=[],
            exercise_frequency=None,
        )

        plan = self.service.build_plan(self._case(questionnaire), self._context(questionnaire))
        rendered = "\n".join(self.service.report_items(plan))

        self.assertIsNone(self._section(plan, "movement"))
        self.assertNotIn("年龄信息", rendered)

    def test_lp20_still_applies_when_lp24_does_not_match(self) -> None:
        questionnaire = Questionnaire(symptoms=["慢性疼痛"])

        plan = self.service.build_plan(self._case(questionnaire), self._context(questionnaire))
        selected = {item.protocol_id for item in plan.selected_protocols}

        self.assertIn("LP20", selected)
        self.assertNotIn("LP24", selected)

    def test_high_triglycerides_trigger_lp13_but_not_lp12(self) -> None:
        questionnaire = Questionnaire()
        triglycerides = self._marker("triglycerides", "甘油三酯", AbnormalFlag.high, 2.8)

        plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(questionnaire, markers={"triglycerides": [triglycerides]}),
        )
        selected = {item.protocol_id for item in plan.selected_protocols}

        self.assertIn("LP13", selected)
        self.assertNotIn("LP12", selected)

    def test_lipid_and_generic_metabolic_tags_do_not_trigger_glucose_protocol(self) -> None:
        questionnaire = Questionnaire()

        lipid_plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(questionnaire, lifestyle_tags={"lipid_support", "metabolic_support"}),
        )
        lipid_selected = {item.protocol_id for item in lipid_plan.selected_protocols}

        self.assertIn("LP13", lipid_selected)
        self.assertNotIn("LP12", lipid_selected)

        glucose_plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(questionnaire, lifestyle_tags={"glucose_support", "metabolic_support"}),
        )
        glucose_selected = {item.protocol_id for item in glucose_plan.selected_protocols}

        self.assertIn("LP12", glucose_selected)

    def test_pregnancy_suppresses_lp18_movement_actions_only(self) -> None:
        questionnaire = Questionnaire(
            age=32,
            sex="female",
            pregnant_or_lactating=True,
            known_conditions=[],
            exercise_frequency="无规律运动",
        )
        lead = self._marker("blood_lead", "血铅", AbnormalFlag.high, 12.0)

        plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(questionnaire, markers={"blood_lead": [lead]}),
        )
        diet = self._section(plan, "diet")
        movement = self._section(plan, "movement")

        self.assertIsNotNone(diet)
        self.assertTrue(any(item.action_id.startswith("LP18") for item in diet.actions))
        self.assertFalse(
            movement and any(item.action_id.startswith("LP18") for item in movement.actions)
        )

    def test_acute_heavy_metal_poisoning_keeps_urgent_source_handling_only(self) -> None:
        questionnaire = Questionnaire(known_conditions=["急性汞中毒"])
        mercury = self._marker("blood_mercury", "血汞", AbnormalFlag.high, 80.0)

        plan = self.service.build_plan(
            self._case(questionnaire),
            self._context(questionnaire, markers={"blood_mercury": [mercury]}),
        )
        diet = self._section(plan, "diet")
        movement = self._section(plan, "movement")
        combined = " ".join(item.text for item in diet.actions) if diet else ""

        self.assertIsNotNone(diet)
        self.assertIsNone(movement)
        self.assertIn("暴露源", combined)
        self.assertNotIn("大型掠食鱼", combined)
        self.assertTrue(any("立即急诊" in item for item in plan.monitoring))
        self.assertEqual(plan.status, "needs_review")


if __name__ == "__main__":
    unittest.main()
