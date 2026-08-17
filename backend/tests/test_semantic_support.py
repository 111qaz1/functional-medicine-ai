from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.domain.models import (
    AbnormalFinding,
    AnalysisStatus,
    CaseAnalysis,
    FindingStandardizationStatus,
    SemanticEvidenceReference,
    SemanticEvidenceStrength,
    SemanticSupportNeed,
    SupportDirection,
    SupportEligibilityStatus,
)
from app.services.finding_standardization import FindingStandardizationService
from app.services.semantic_support import SemanticSupportService


DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "data"


def finding(*, finding_id: str, name: str, raw_value: str | None = None, unit: str | None = None):
    return AbnormalFinding(
        id=finding_id,
        name=name,
        result_text=raw_value or "存在",
        raw_value=raw_value,
        unit=unit,
        reference_range="0-100" if raw_value else None,
        abnormal_flag="high" if raw_value else "positive",
        source_file_id="file-report",
        source_file_name="synthetic-report.pdf",
        source_page=2,
        source_text=f"{name} {raw_value or '存在'}",
        confidence=0.95,
    )


class ExactFindingStandardizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FindingStandardizationService(
            DATA_DIR / "marker_dictionary.json",
            DATA_DIR / "clinical_finding_dictionary.json",
            DATA_DIR / "product_tag_matrix.json",
        )

    def test_beta_glucuronidase_cannot_become_fasting_glucose(self) -> None:
        item = finding(
            finding_id="finding-beta-glucuronidase",
            name="β-葡萄糖醛酸酶升高",
            raw_value="5101",
            unit="U/L",
        ).model_copy(update={"marker_code_candidate": "fasting_glucose"})
        standardized = self.service.standardize(item, doctor_confirmed=True)
        self.assertIsNone(standardized.marker_code)

    def test_transferrin_cannot_become_ferritin(self) -> None:
        item = finding(
            finding_id="finding-transferrin",
            name="转铁蛋白偏低",
            raw_value="1.6",
            unit="g/L",
        ).model_copy(update={"marker_code_candidate": "ferritin"})
        standardized = self.service.standardize(item, doctor_confirmed=True)
        self.assertIsNone(standardized.marker_code)

    def test_full_alias_with_direction_suffix_and_compatible_unit_is_allowed(self) -> None:
        item = finding(
            finding_id="finding-vitamin-d",
            name="维生素D减少",
            raw_value="18",
            unit="ng/mL",
        ).model_copy(update={"marker_code_candidate": "vitamin_d", "abnormal_flag": "low"})
        standardized = self.service.standardize(item, doctor_confirmed=True)
        self.assertEqual(standardized.marker_code, "vitamin_d")

    def test_lobar_thyroid_nodule_name_confirms_exact_finding_code(self) -> None:
        item = finding(
            finding_id="finding-thyroid-left",
            name="甲状腺左叶结节",
        ).model_copy(
            update={
                "finding_code_candidate": "thyroid_nodule",
                "abnormal_flag": "positive",
            }
        )

        standardized = self.service.standardize(item, doctor_confirmed=True)

        self.assertEqual(standardized.finding_code, "thyroid_nodule")
        self.assertEqual(
            standardized.standardization_status,
            FindingStandardizationStatus.validated,
        )


class SemanticSupportValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SemanticSupportService(DATA_DIR / "support_goal_catalog.json")

    def _analysis(self, findings: list[AbnormalFinding]) -> CaseAnalysis:
        return CaseAnalysis(
            id="analysis-semantic",
            case_id="case-semantic",
            status=AnalysisStatus.reviewed,
            snapshot_hash="synthetic",
            model_version="synthetic",
            reviewed_abnormal_findings=findings,
        )

    def test_unknown_indicator_can_drive_support_need_without_standard_code(self) -> None:
        unknown = finding(
            finding_id="finding-beta",
            name="β-葡萄糖醛酸酶升高",
            raw_value="5101",
            unit="U/L",
        )
        candidate = SemanticSupportNeed(
            id="support-beta",
            support_need_text="肠道菌群生态与相关代谢支持",
            support_goal_code="gut_microbiome",
            system_id="digestive_gut",
            evidence_refs=[
                SemanticEvidenceReference(
                    ref="finding:finding-beta",
                    evidence_strength=SemanticEvidenceStrength.direct,
                )
            ],
            rationale="报告数值与解释提示肠道菌群代谢需要关注。",
            model_confidence=0.61,
        )
        result = self.service.validate_needs(
            candidates=[candidate],
            analysis=self._analysis([unknown]),
            clinical_summary_text=None,
        )[0]
        self.assertEqual(result.eligibility_status, SupportEligibilityStatus.eligible)
        self.assertEqual(result.corroboration_count, 1)

    def test_provider_dropped_finding_prefix_is_safely_canonicalized(self) -> None:
        gut_finding = finding(
            finding_id="finding_eff888d232d6",
            name="肠道屏障异常",
        )
        candidate = SemanticSupportNeed(
            id="support-gut-prefix-compat",
            support_need_text="胃肠黏膜与屏障支持",
            support_goal_code="gut_mucosa",
            system_id="digestive_gut",
            evidence_refs=[
                SemanticEvidenceReference(
                    ref="finding:eff888d232d6",
                    evidence_strength=SemanticEvidenceStrength.explicit_conclusion,
                )
            ],
            rationale="报告明确记录肠道屏障异常。",
            model_confidence=0.72,
        )

        result = self.service.validate_needs(
            candidates=[candidate],
            analysis=self._analysis([gut_finding]),
            clinical_summary_text=None,
        )[0]

        self.assertEqual(result.eligibility_status, SupportEligibilityStatus.eligible)
        self.assertEqual(result.evidence_refs[0].ref, "finding:finding_eff888d232d6")
        self.assertTrue(any("ID格式已规范化" in note for note in result.validation_notes))

    def test_unresolvable_finding_suffix_remains_rejected(self) -> None:
        candidate = SemanticSupportNeed(
            id="support-unresolvable-prefix",
            support_need_text="无法验证的支持需求",
            support_goal_code="gut_mucosa",
            system_id="digestive_gut",
            evidence_refs=[
                SemanticEvidenceReference(
                    ref="finding:not-a-real-id",
                    evidence_strength=SemanticEvidenceStrength.explicit_conclusion,
                )
            ],
            rationale="引用不存在。",
            model_confidence=0.9,
        )

        result = self.service.validate_needs(
            candidates=[candidate],
            analysis=self._analysis([]),
            clinical_summary_text=None,
        )[0]

        self.assertEqual(result.eligibility_status, SupportEligibilityStatus.narrative_only)
        self.assertEqual(result.evidence_refs, [])

    def test_missing_or_deleted_evidence_is_rejected_for_products(self) -> None:
        candidate = SemanticSupportNeed(
            id="support-fake",
            support_need_text="虚构支持需求",
            support_goal_code="glycemic_balance",
            system_id="endocrine_metabolic",
            evidence_refs=[
                SemanticEvidenceReference(
                    ref="finding:not-present",
                    evidence_strength=SemanticEvidenceStrength.direct,
                )
            ],
            rationale="无有效证据。",
            model_confidence=0.99,
        )
        result = self.service.validate_needs(
            candidates=[candidate],
            analysis=self._analysis([]),
            clinical_summary_text=None,
        )[0]
        self.assertEqual(result.eligibility_status, SupportEligibilityStatus.narrative_only)
        self.assertEqual(result.evidence_refs, [])

    def test_doctor_summary_can_form_contextual_sleep_candidate(self) -> None:
        candidate = SemanticSupportNeed(
            id="support-sleep",
            support_need_text="睡眠节律与压力恢复支持",
            support_goal_code="sleep_stress",
            system_id="neuro_sleep",
            evidence_refs=[
                SemanticEvidenceReference(
                    ref="clinical_summary:sleep",
                    evidence_strength=SemanticEvidenceStrength.contextual,
                )
            ],
            rationale="医生病例总结明确记录持续睡眠障碍。",
            model_confidence=0.58,
        )
        result = self.service.validate_needs(
            candidates=[candidate],
            analysis=self._analysis([]),
            clinical_summary_text="患者持续入睡困难并早醒。",
        )[0]
        self.assertEqual(result.eligibility_status, SupportEligibilityStatus.eligible)

    def test_nodule_cannot_independently_trigger_product(self) -> None:
        nodule = finding(finding_id="finding-nodule", name="右肺下叶结节")
        candidate = SemanticSupportNeed(
            id="support-nodule",
            support_need_text="免疫功能支持",
            support_goal_code="immune",
            system_id="immune_inflammation",
            evidence_refs=[
                SemanticEvidenceReference(
                    ref="finding:finding-nodule",
                    evidence_strength=SemanticEvidenceStrength.explicit_conclusion,
                )
            ],
            rationale="肺结节需要随访。",
            model_confidence=0.9,
        )
        result = self.service.validate_needs(
            candidates=[candidate],
            analysis=self._analysis([nodule]),
            clinical_summary_text=None,
        )[0]
        self.assertEqual(result.eligibility_status, SupportEligibilityStatus.narrative_only)

    def test_underweight_fact_blocks_weight_loss_support_need(self) -> None:
        underweight = finding(
            finding_id="finding-underweight",
            name="体重过轻",
        ).model_copy(update={"finding_code": "underweight"})
        candidate = SemanticSupportNeed(
            id="support-weight-loss-conflict",
            support_need_text="体重与脂肪代谢支持",
            support_goal_code="weight_metabolism",
            support_direction=SupportDirection.decrease,
            system_id="endocrine_metabolic",
            evidence_refs=[
                SemanticEvidenceReference(
                    ref="finding:finding-underweight",
                    evidence_strength=SemanticEvidenceStrength.explicit_conclusion,
                )
            ],
            rationale="模型提出减脂方向，但证据实际为体重过轻。",
            model_confidence=0.9,
        )

        result = self.service.validate_needs(
            candidates=[candidate],
            analysis=self._analysis([underweight]),
            clinical_summary_text=None,
        )[0]

        self.assertEqual(result.eligibility_status, SupportEligibilityStatus.narrative_only)
        self.assertTrue(any("underweight" in note for note in result.validation_notes))

    def test_unknown_weight_indicator_can_remain_semantic_when_direction_is_valid(self) -> None:
        body_fat = finding(
            finding_id="finding-body-fat-novel",
            name="内脏脂肪面积偏高",
            raw_value="128",
            unit="cm2",
        )
        candidate = SemanticSupportNeed(
            id="support-weight-loss-valid",
            support_need_text="体脂管理与健康减重支持",
            support_goal_code="weight_metabolism",
            support_direction=SupportDirection.decrease,
            system_id="endocrine_metabolic",
            evidence_refs=[
                SemanticEvidenceReference(
                    ref="finding:finding-body-fat-novel",
                    evidence_strength=SemanticEvidenceStrength.direct,
                )
            ],
            rationale="报告明确记录内脏脂肪面积偏高。",
            model_confidence=0.66,
        )

        result = self.service.validate_needs(
            candidates=[candidate],
            analysis=self._analysis([body_fat]),
            clinical_summary_text=None,
        )[0]

        self.assertEqual(result.eligibility_status, SupportEligibilityStatus.eligible)
        self.assertIsNone(body_fat.marker_code)


if __name__ == "__main__":
    unittest.main()
