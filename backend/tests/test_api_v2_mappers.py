from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.api.v2.mappers import (
    analysis_to_response,
    apply_review_changes,
    approval_request_to_edits,
    operation_to_response,
)
from app.api.v2.problems import V2ApiError
from app.api.v2.schemas import ApprovalRequest, CaseCreateRequest, ReviewSubmitRequest
from app.domain.models import (
    AbnormalFinding,
    AnalysisStatus,
    CaseAnalysis,
    ChronicFoodSensitivityResult,
    CurrentSupplement,
    DosageOptionSummary,
    DraftRecommendationItem,
    EvidenceStatus,
    FinalGenerationStatus,
    FoodSensitivityItem,
    RecommendationDraft,
)


def _finding() -> AbnormalFinding:
    return AbnormalFinding(
        id="finding-1",
        name="Synthetic marker",
        result_text="12 high",
        raw_value="12",
        unit="U/L",
        reference_range="1-10",
        abnormal_flag="high",
        source_file_id="file-1",
        source_file_name="synthetic.txt",
        source_page=1,
        source_text="Synthetic marker 12 U/L",
        confidence=0.95,
        evidence_status=EvidenceStatus.verified_text,
        marker_code="INTERNAL-MARKER",
        system_ids=["internal-system"],
    )


def _analysis() -> CaseAnalysis:
    return CaseAnalysis(
        id="analysis-1",
        case_id="case-1",
        status=AnalysisStatus.ready_for_review,
        snapshot_hash="internal-snapshot",
        model_version="internal-model",
        prompt_version="internal-prompt",
        progress_current=1,
        progress_total=1,
        file_ids=["file-1", "file-food"],
        case_summary="Synthetic summary",
        abnormal_findings=[_finding()],
        current_supplements=[CurrentSupplement(id="supplement-1", name="Synthetic A")],
        food_sensitivity=ChronicFoodSensitivityResult(
            source_file_id="file-food",
            source_file_name="synthetic-food.txt",
            items=[
                FoodSensitivityItem(
                    id="food-1",
                    name="Synthetic food",
                    severity="mild",
                    source_text="Synthetic food mild",
                )
            ],
            valid=True,
        ),
    )


def _draft() -> RecommendationDraft:
    return RecommendationDraft(
        id="draft-1",
        case_id="case-1",
        model_version="internal-model",
        prompt_version="internal-prompt",
        rule_version="internal-rules",
        recommended_skus=[
            DraftRecommendationItem(
                sku_id="SKU-1",
                display_name="Synthetic product one",
                dosage="one daily",
                dosage_option_id="default",
                dosage_option_label="Default",
                dosage_options=[
                    DosageOptionSummary(
                        option_id="default",
                        label="Default",
                        display_text="one daily",
                    ),
                    DosageOptionSummary(
                        option_id="alternate",
                        label="Alternate",
                        display_text="two daily",
                        requires_review=True,
                    ),
                ],
                reason="Synthetic reason",
            ),
            DraftRecommendationItem(
                sku_id="SKU-2",
                display_name="Synthetic product two",
                dosage="one daily",
                dosage_option_id="default",
                dosage_options=[
                    DosageOptionSummary(
                        option_id="default",
                        label="Default",
                        display_text="one daily",
                    )
                ],
                reason="Synthetic reason",
            ),
        ],
    )


class V2SchemaTests(unittest.TestCase):
    def test_requests_reject_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            CaseCreateRequest(customer_name="Synthetic", owner_doctor_id="not-public")

    def test_empty_update_object_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ReviewSubmitRequest.model_validate(
                {
                    "expected_revision": 1,
                    "finding_changes": [
                        {"op": "update", "id": "finding-1", "changes": {}}
                    ],
                }
            )


class V2MapperTests(unittest.TestCase):
    def test_analysis_response_filters_internal_domain_fields(self) -> None:
        payload = analysis_to_response(_analysis()).model_dump(mode="json")
        serialized = str(payload)
        self.assertNotIn("snapshot_hash", serialized)
        self.assertNotIn("model_version", serialized)
        self.assertNotIn("prompt_version", serialized)
        self.assertNotIn("marker_code", serialized)
        self.assertNotIn("system_ids", serialized)
        self.assertEqual(payload["abnormal_findings"][0]["id"], "finding-1")

    def test_review_delta_preserves_server_fields_and_order(self) -> None:
        analysis = _analysis()
        request = ReviewSubmitRequest.model_validate(
            {
                "expected_revision": 1,
                "finding_changes": [
                    {
                        "op": "update",
                        "id": "finding-1",
                        "changes": {"name": "Doctor confirmed marker"},
                    },
                    {
                        "op": "add",
                        "value": {
                            "name": "Manual marker",
                            "source_file_id": "file-1",
                            "source_file_name": "synthetic.txt",
                            "source_text": "Manual marker evidence",
                        },
                    },
                ],
                "supplement_changes": [
                    {"op": "update", "id": "supplement-1", "changes": {"name": "Updated A"}},
                    {"op": "add", "value": {"name": "Manual B"}},
                ],
                "food_sensitivity_changes": [
                    {
                        "op": "update",
                        "id": "food-1",
                        "changes": {"severity": "high"},
                    }
                ],
            }
        )
        findings, supplements, food = apply_review_changes(analysis, request)
        self.assertEqual([item.name for item in findings], ["Doctor confirmed marker", "Manual marker"])
        self.assertEqual(findings[0].marker_code, "INTERNAL-MARKER")
        self.assertEqual(findings[0].system_ids, ["internal-system"])
        self.assertEqual([item.name for item in supplements], ["Updated A", "Manual B"])
        self.assertTrue(supplements[1].doctor_added)
        self.assertIsNotNone(food)
        self.assertEqual(food.items[0].severity, "high")
        self.assertEqual(food.high_foods, ["Synthetic food"])

    def test_review_rejects_duplicate_unknown_and_stale_targets(self) -> None:
        analysis = _analysis()
        duplicate = ReviewSubmitRequest.model_validate(
            {
                "expected_revision": 1,
                "finding_changes": [
                    {"op": "remove", "id": "finding-1"},
                    {"op": "remove", "id": "finding-1"},
                ],
            }
        )
        with self.assertRaisesRegex(V2ApiError, "same finding ID"):
            apply_review_changes(analysis, duplicate)

        unknown = ReviewSubmitRequest.model_validate(
            {
                "expected_revision": 1,
                "finding_changes": [{"op": "remove", "id": "unknown"}],
            }
        )
        with self.assertRaisesRegex(V2ApiError, "not part of the current analysis"):
            apply_review_changes(analysis, unknown)

        stale = ReviewSubmitRequest(expected_revision=2)
        with self.assertRaisesRegex(V2ApiError, "updated"):
            apply_review_changes(analysis, stale)

    def test_operation_projects_analysis_and_draft_generation_states(self) -> None:
        analysis = _analysis()
        operation = operation_to_response(analysis)
        self.assertEqual((operation.stage, operation.status), ("analysis", "succeeded"))
        self.assertEqual(operation.operation_id, analysis.id)

        analysis.status = AnalysisStatus.analyzing_documents
        analysis.progress_current = 1
        analysis.progress_total = 1
        operation = operation_to_response(analysis)
        self.assertEqual(operation.progress.percent, 76)
        self.assertIn("文件分析 1/1", operation.progress.current_item)

        analysis.status = AnalysisStatus.synthesizing
        operation = operation_to_response(analysis)
        self.assertEqual(operation.progress.percent, 82)
        self.assertIn("病例级综合", operation.progress.current_item)

        analysis.status = AnalysisStatus.validating
        operation = operation_to_response(analysis)
        self.assertEqual(operation.progress.percent, 94)
        self.assertIn("证据校验", operation.progress.current_item)
        self.assertEqual(analysis_to_response(analysis).progress.percent, 94)

        analysis.final_generation_status = FinalGenerationStatus.generating_draft
        analysis.final_generation_progress = 64
        operation = operation_to_response(analysis)
        self.assertEqual((operation.stage, operation.status), ("draft_generation", "running"))
        self.assertEqual(operation.progress.percent, 64)
        self.assertEqual(operation.progress.current_item, "生成营养素草案")

        analysis.final_generation_status = FinalGenerationStatus.failed
        analysis.final_generation_error = "Synthetic failure"
        operation = operation_to_response(analysis)
        self.assertEqual(operation.status, "failed")
        self.assertEqual(operation.failure.code, "DRAFT_GENERATION_FAILED")

    def test_approval_mapper_types_internal_edits(self) -> None:
        request = ApprovalRequest.model_validate(
            {
                "expected_revision": 1,
                "excluded_sku_ids": ["SKU-2"],
                "dosage_overrides": [
                    {"sku_id": "SKU-1", "option_id": "alternate", "note": "Doctor decision"}
                ],
            }
        )
        edits = approval_request_to_edits(_draft(), request)
        self.assertEqual(edits["excluded_sku_ids"], ["SKU-2"])
        self.assertEqual(edits["dosage_overrides"]["SKU-1"]["option_id"], "alternate")

    def test_approval_mapper_rejects_invalid_choices(self) -> None:
        with self.assertRaises(ValidationError):
            ApprovalRequest.model_validate(
                {
                    "expected_revision": 1,
                    "excluded_sku_ids": ["SKU-1"],
                    "dosage_overrides": [{"sku_id": "SKU-1", "option_id": "default"}],
                }
            )
        no_note = ApprovalRequest.model_validate(
            {
                "expected_revision": 1,
                "dosage_overrides": [{"sku_id": "SKU-1", "option_id": "alternate"}],
            }
        )
        with self.assertRaisesRegex(V2ApiError, "note is required"):
            approval_request_to_edits(_draft(), no_note)


if __name__ == "__main__":
    unittest.main()
