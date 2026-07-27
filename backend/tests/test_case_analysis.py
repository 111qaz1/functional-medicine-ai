from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.core.bootstrap import build_container
from app.core.settings import AppSettings
from app.domain.models import (
    AbnormalFinding,
    AnalysisStatus,
    CaseAnalysis,
    ChronicFoodSensitivityResult,
    DocumentAnalysisResult,
    DraftRecommendationItem,
    EvidenceStatus,
    FileIntakeStatus,
    FinalGenerationStatus,
    FindingStandardizationStatus,
    PageText,
    Questionnaire,
    RecommendationDraft,
    StructuredSystemFinding,
    UploadedFile,
    WorkspaceScope,
)
from app.repositories.in_memory import LocalRepository
from app.services.case_analysis import CaseAnalysisService, OpenAICompatibleCaseAnalysisProvider
from app.services.case_service import CaseService
from app.services.document_intake import DocumentIntakeService
from app.services.indicator_extraction import CaseIndicatorService
from app.services.finding_standardization import FindingStandardizationService
from app.services.pdf_export import PdfReportExporter
from app.services.review_local import ReviewService


class FakeAnalysisProvider:
    def __init__(self) -> None:
        self.document_calls = 0
        self.synthesis_calls = 0
        self.synthesis_thinking_types: list[str] = []
        self.synthesis_document_names: list[list[str]] = []

    def analyze_document(self, uploaded_file) -> DocumentAnalysisResult:
        self.document_calls += 1
        return DocumentAnalysisResult(
            file_id=uploaded_file.id,
            file_name=uploaded_file.filename,
            report_type="lab",
            summary="合成资料摘要",
            abnormal_findings=[
                AbnormalFinding(
                    id=f"finding-{uploaded_file.id}",
                    name="合成指标A",
                    result_text="12.3 偏高",
                    raw_value="12.3",
                    unit="U/L",
                    reference_range="1.0-10.0",
                    abnormal_flag="high",
                    source_file_id=uploaded_file.id,
                    source_file_name=uploaded_file.filename,
                    source_page=1,
                    source_text="合成指标A 12.3 U/L 1.0-10.0",
                    confidence=0.98,
                ),
                AbnormalFinding(
                    id=f"finding-non-numeric-{uploaded_file.id}",
                    name="合成非数值异常",
                    result_text="存在",
                    abnormal_flag="positive",
                    source_file_id=uploaded_file.id,
                    source_file_name=uploaded_file.filename,
                    source_page=1,
                    source_text="检查提示合成非数值异常存在",
                    confidence=0.95,
                ),
            ],
            questionnaire=Questionnaire(chief_concerns=["合成诉求"]).model_dump(mode="json"),
            food_sensitivity=ChronicFoodSensitivityResult(
                source_file_id=uploaded_file.id,
                source_file_name=uploaded_file.filename,
                mild_foods=["合成食物1", "合成食物2", "合成食物3", "合成食物4", "合成食物5"],
                interpretations=["合成解读一", "合成解读二", "合成解读三"],
                valid=True,
            ),
        )

    def synthesize_case(self, **kwargs):
        self.synthesis_calls += 1
        self.synthesis_thinking_types.append(kwargs.get("thinking_type", "enabled"))
        self.synthesis_document_names.append(
            [item.file_name for item in kwargs.get("document_results", [])]
        )
        reviewed = kwargs.get("reviewed_findings")
        return SimpleNamespace(
            case_summary="医生确认后的病例总结" if reviewed is not None else "初步病例总结",
            system_findings=["合成系统失衡"],
            warnings=[],
        )


class FakeRecommendationService:
    def __init__(self, repository, case_service, *, fail: bool = False) -> None:
        self.repository = repository
        self.case_service = case_service
        self.fail = fail
        self.parsing_service = None
        self.generation_count = 0

    def generate(self, case_id: str, requested_by: str) -> RecommendationDraft:
        if self.fail:
            raise RuntimeError("synthetic draft failure")
        self.generation_count += 1
        draft = RecommendationDraft(
            id=f"draft-{case_id}-{self.generation_count}",
            case_id=case_id,
            case_summary=["旧摘要"],
            key_lab_highlights=["合成指标A 12.3 偏高"],
            recommended_skus=[
                DraftRecommendationItem(
                    sku_id="sku-synthetic",
                    display_name="合成营养素",
                    dosage="每日 1 次",
                    reason="合成医生确认异常支持",
                )
            ],
            lifestyle_actions=["合成生活方式"],
            structured_system_findings=[
                StructuredSystemFinding(
                    system_id="endocrine_metabolic",
                    system_name="内分泌/代谢系统",
                    priority_level="优先级高",
                    priority_score=70,
                    summary="合成结构化系统分析",
                )
            ],
            report_sections={
                "异常指标汇总": ["合成指标A 12.3 偏高"],
                "功能医学系统失衡分析": ["旧系统分析"],
                "生活方式干预处方": ["合成生活方式"],
                "首月营养素干预方案": ["合成营养素方案"],
                "审核备注": ["合成总医嘱"],
            },
            model_version="synthetic",
            prompt_version="synthetic",
            rule_version="synthetic",
        )
        self.repository.save_draft(draft)
        self.case_service.append_draft(case_id, draft.id)
        return draft


class FakeStructuredQuestionnaireImportService:
    def matches_template(self, **kwargs) -> bool:
        return True

    def parse(self, **kwargs) -> Questionnaire:
        return Questionnaire(
            chief_concerns=["合成问卷诉求"],
            symptoms=["合成已选症状"],
            goals=["合成健康目标"],
            msq_system_scores={"消化道": 2},
        )


class CaseAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.repository = LocalRepository(root / "test.sqlite3")
        self.case_service = CaseService(self.repository)
        self.provider = FakeAnalysisProvider()
        self.recommendation = FakeRecommendationService(self.repository, self.case_service)
        self.service = CaseAnalysisService(
            repository=self.repository,
            case_service=self.case_service,
            recommendation_service=self.recommendation,
            provider=self.provider,
            model_version="synthetic-model",
        )

    def tearDown(self) -> None:
        self.service.executor.shutdown(wait=True, cancel_futures=True)
        self.temp_dir.cleanup()

    def _create_case(self, *, owner: str | None = None):
        return self.case_service.create_case(
            customer_name="脱敏合成病例",
            consultant_id=None,
            notes=None,
            consent=None,
            workspace_scope=WorkspaceScope.doctor if owner else WorkspaceScope.public,
            owner_doctor_id=owner,
        )

    def _add_text_file(
        self,
        case_id: str,
        *,
        file_id: str = "file-a",
        digest: str = "same-digest",
        filename: str = "synthetic-report.txt",
    ):
        text = "合成指标A 12.3 U/L 1.0-10.0\n检查提示合成非数值异常存在"
        uploaded = UploadedFile(
            id=file_id,
            case_id=case_id,
            filename=filename,
            content_type="text/plain",
            size_bytes=len(text.encode()),
            content_sha256=digest,
            intake_status=FileIntakeStatus.uploaded,
            page_count=1,
            page_texts=[PageText(page=1, text=text)],
        )
        return self.case_service.add_uploaded_file(case_id, uploaded)

    def _wait(self, analysis_id: str) -> CaseAnalysis:
        deadline = time.time() + 5
        while time.time() < deadline:
            analysis = self.repository.get_case_analysis(analysis_id)
            if analysis and analysis.status not in self.service.ACTIVE_STATUSES:
                return analysis
            time.sleep(0.02)
        self.fail("analysis did not finish")

    def _wait_final(self, analysis_id: str) -> CaseAnalysis:
        deadline = time.time() + 5
        while time.time() < deadline:
            analysis = self.repository.get_case_analysis(analysis_id)
            if analysis and analysis.final_generation_status not in self.service.ACTIVE_FINAL_GENERATION_STATUSES:
                return analysis
            time.sleep(0.02)
        self.fail("final draft generation did not finish")

    def test_light_intake_only_prechecks_and_irrelevant_hint_does_not_block(self) -> None:
        service = DocumentIntakeService(max_upload_bytes=1024, max_pdf_pages=20)
        result = service.preflight(
            filename="synthetic-invoice.txt",
            content_type="text/plain",
            content="invoice for synthetic deployment".encode(),
        )
        self.assertEqual(result.intake_status, FileIntakeStatus.suspected_irrelevant)
        self.assertIsNone(result.validation_error)
        self.assertEqual(self.provider.document_calls, 0)

    def test_analysis_requires_third_party_processing_confirmation(self) -> None:
        case = self._create_case()
        self._add_text_file(case.id)
        with self.assertRaisesRegex(ValueError, "第三方大模型"):
            self.service.create_analysis(case.id)
        self.assertEqual(self.provider.document_calls, 0)

    def test_hybrid_standardization_validates_vitamin_d_and_preserves_unmapped_findings(self) -> None:
        data_dir = Path(__file__).resolve().parents[1] / "app" / "data"
        service = FindingStandardizationService(
            data_dir / "marker_dictionary.json",
            data_dir / "clinical_finding_dictionary.json",
        )
        vitamin_d = AbnormalFinding(
            id="finding-vitamin-d",
            name="维生素D减少",
            result_text="偏低",
            abnormal_flag="low",
            source_file_id="file-a",
            source_file_name="report.pdf",
            source_page=1,
            source_text="维生素D减少",
            confidence=0.96,
            marker_code_candidate="vitamin_d",
        )
        standardized = service.standardize(vitamin_d, doctor_confirmed=True)
        self.assertEqual(standardized.marker_code, "vitamin_d")
        self.assertEqual(standardized.abnormal_flag, "low")
        self.assertEqual(standardized.standardization_status, FindingStandardizationStatus.validated)
        self.assertEqual(service.to_lab_item(standardized).marker_code, "vitamin_d")

        unmapped = service.standardize(vitamin_d.model_copy(update={
            "id": "finding-unmapped",
            "name": "未收录合成异常",
            "source_text": "未收录合成异常",
            "marker_code_candidate": None,
        }), doctor_confirmed=True)
        self.assertEqual(unmapped.standardization_status, FindingStandardizationStatus.unmapped)
        self.assertIsNone(unmapped.marker_code)

    def test_support_goal_fallback_is_local_and_blocks_follow_up_only_findings(self) -> None:
        data_dir = Path(__file__).resolve().parents[1] / "app" / "data"
        service = FindingStandardizationService(
            data_dir / "marker_dictionary.json",
            data_dir / "clinical_finding_dictionary.json",
            data_dir / "product_tag_matrix.json",
        )
        support_finding = AbnormalFinding(
            id="finding-bone-support",
            name="骨代谢支持需求",
            result_text="需要关注",
            abnormal_flag="positive",
            source_file_id="file-a",
            source_file_name="report.pdf",
            source_page=1,
            source_text="骨代谢支持需求",
            confidence=0.95,
            system_id_candidates=["bone_muscle"],
            support_goal_candidates=["vitamin_d_repletion"],
            mapping_confidence=0.92,
        )
        standardized = service.standardize(support_finding, doctor_confirmed=True)
        self.assertEqual(standardized.standardization_status, FindingStandardizationStatus.support_mapped)
        self.assertEqual(standardized.support_goals, ["vitamin_d_repletion"])
        self.assertEqual(service.to_clinical_finding(standardized).system_ids, ["bone_muscle"])

        antibody = support_finding.model_copy(update={
            "id": "finding-antibody",
            "name": "PM-Scl抗体阳性",
            "source_text": "PM-Scl抗体阳性",
            "system_id_candidates": ["immune_inflammation"],
            "support_goal_candidates": ["immune"],
            "mapping_confidence": 0.98,
        })
        blocked = service.standardize(antibody, doctor_confirmed=True)
        self.assertEqual(blocked.standardization_status, FindingStandardizationStatus.system_mapped)
        self.assertEqual(blocked.support_goals, [])

    def test_named_food_sensitivity_report_only_populates_specialty_section(self) -> None:
        case = self._create_case()
        self._add_text_file(case.id, filename="慢性食物敏感报告（1）.pdf")

        analysis = self._wait(
            self.service.create_analysis(case.id, third_party_processing_confirmed=True).id
        )

        self.assertEqual(analysis.status, AnalysisStatus.ready_for_review)
        self.assertEqual(analysis.abnormal_findings, [])
        self.assertIsNotNone(analysis.food_sensitivity)
        self.assertEqual(len(analysis.food_sensitivity.mild_foods), 5)
        self.assertEqual(self.provider.synthesis_document_names[-1], [])

    def test_analysis_persists_each_result_validates_evidence_and_reuses_cache(self) -> None:
        case = self._create_case()
        self._add_text_file(case.id)
        first = self._wait(self.service.create_analysis(case.id, third_party_processing_confirmed=True).id)
        self.assertEqual(first.status, AnalysisStatus.ready_for_review)
        self.assertEqual(first.progress_current, 1)
        self.assertEqual(self.provider.document_calls, 1)
        self.assertEqual(len(first.abnormal_findings), 2)
        self.assertTrue(all(item.evidence_status == EvidenceStatus.verified_text for item in first.abnormal_findings))
        self.assertEqual(len(first.food_sensitivity.mild_foods), 5)

        second = self._wait(self.service.create_analysis(case.id, third_party_processing_confirmed=True).id)
        self.assertEqual(second.status, AnalysisStatus.ready_for_review)
        self.assertEqual(self.provider.document_calls, 1)

    def test_documents_run_with_at_most_two_workers_and_preserve_upload_order(self) -> None:
        class ConcurrentProvider(FakeAnalysisProvider):
            def __init__(self) -> None:
                super().__init__()
                self.active_calls = 0
                self.max_active_calls = 0
                self.lock = threading.Lock()

            def analyze_document(self, uploaded_file) -> DocumentAnalysisResult:
                with self.lock:
                    self.active_calls += 1
                    self.max_active_calls = max(self.max_active_calls, self.active_calls)
                try:
                    time.sleep(0.08)
                    return super().analyze_document(uploaded_file)
                finally:
                    with self.lock:
                        self.active_calls -= 1

        case = self._create_case()
        for index in range(4):
            self._add_text_file(
                case.id,
                file_id=f"file-{index}",
                digest=f"unique-digest-{index}",
            )
        provider = ConcurrentProvider()
        self.service.provider = provider

        analysis = self._wait(
            self.service.create_analysis(case.id, third_party_processing_confirmed=True).id
        )

        self.assertEqual(analysis.status, AnalysisStatus.ready_for_review)
        self.assertEqual(provider.document_calls, 4)
        self.assertEqual(provider.max_active_calls, 2)
        self.assertEqual(
            [item.file_id for item in analysis.document_results],
            [f"file-{index}" for index in range(4)],
        )

    def test_cache_is_isolated_by_doctor_scope(self) -> None:
        first_case = self._create_case(owner="doctor-a")
        second_case = self._create_case(owner="doctor-b")
        self._add_text_file(first_case.id, file_id="file-a")
        self._add_text_file(second_case.id, file_id="file-b")
        self._wait(self.service.create_analysis(first_case.id, third_party_processing_confirmed=True).id)
        self._wait(self.service.create_analysis(second_case.id, third_party_processing_confirmed=True).id)
        self.assertEqual(self.provider.document_calls, 2)

    def test_cache_is_reused_within_doctor_scope_and_remaps_file_evidence(self) -> None:
        first_case = self._create_case(owner="doctor-shared")
        second_case = self._create_case(owner="doctor-shared")
        self._add_text_file(first_case.id, file_id="file-first")
        self._add_text_file(second_case.id, file_id="file-second")
        self._wait(self.service.create_analysis(first_case.id, third_party_processing_confirmed=True).id)
        second = self._wait(
            self.service.create_analysis(second_case.id, third_party_processing_confirmed=True).id
        )
        self.assertEqual(self.provider.document_calls, 1)
        self.assertTrue(all(item.source_file_id == "file-second" for item in second.abnormal_findings))
        self.assertEqual(second.food_sensitivity.source_file_id, "file-second")

    def test_fixed_template_msq_uses_structured_parser_before_document_model(self) -> None:
        case = self._create_case()
        storage_path = Path(self.temp_dir.name) / "synthetic-msq.docx"
        storage_path.write_bytes(b"synthetic structured questionnaire")
        uploaded = UploadedFile(
            id="file-msq",
            case_id=case.id,
            filename="synthetic-msq.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=storage_path.stat().st_size,
            storage_uri=str(storage_path),
            content_sha256="structured-msq-digest",
            intake_status=FileIntakeStatus.uploaded,
            page_count=1,
            page_texts=[PageText(page=1, text="synthetic")],
        )
        self.case_service.add_uploaded_file(case.id, uploaded)
        self.service.questionnaire_import_service = FakeStructuredQuestionnaireImportService()

        result = self.service._analyze_with_cache(case, uploaded)

        self.assertEqual(result.report_type, "msq")
        self.assertEqual(result.questionnaire["symptoms"], ["合成已选症状"])
        self.assertEqual(result.questionnaire["msq_system_scores"], {"消化道": 2})
        self.assertEqual(result.abnormal_findings, [])
        self.assertEqual(self.provider.document_calls, 0)

    def test_msq_with_many_symptoms_and_no_scores_is_skipped(self) -> None:
        case = self._create_case()
        self._add_text_file(case.id)
        analysis = CaseAnalysis(
            id="analysis-invalid-msq",
            case_id=case.id,
            snapshot_hash=self.service.current_snapshot_hash(case),
            file_ids=["file-a"],
            model_version="synthetic-model",
            document_results=[
                DocumentAnalysisResult(
                    file_id="file-a",
                    file_name="synthetic-msq.pdf",
                    report_type="msq",
                    questionnaire=Questionnaire(
                        symptoms=[f"合成症状{index}" for index in range(10)],
                        msq_system_scores={},
                    ).model_dump(mode="json"),
                )
            ],
        )

        self.service._assemble_and_validate(case, analysis)

        self.assertIsNone(analysis.questionnaire)
        self.assertTrue(any("没有有效 MSQ 评分" in warning for warning in analysis.warnings))

    def test_file_change_marks_analysis_stale(self) -> None:
        case = self._create_case()
        self._add_text_file(case.id)
        analysis = self._wait(self.service.create_analysis(case.id, third_party_processing_confirmed=True).id)
        self.case_service.update_clinical_summary(case.id, clinical_summary_text="新的医生总结")
        self.assertEqual(self.repository.get_case_analysis(analysis.id).status, AnalysisStatus.stale)

    def test_restart_marks_only_active_final_generation_as_failed(self) -> None:
        case = self._create_case()
        analysis = CaseAnalysis(
            id="analysis-final-restart",
            case_id=case.id,
            status=AnalysisStatus.reviewed,
            snapshot_hash="synthetic",
            model_version="synthetic",
            final_generation_status=FinalGenerationStatus.final_synthesizing,
            final_generation_progress=20,
        )
        self.repository.save_case_analysis(analysis)
        self.assertEqual(self.repository.mark_active_analyses_interrupted(), 1)
        interrupted = self.repository.get_case_analysis(analysis.id)
        self.assertEqual(interrupted.status, AnalysisStatus.reviewed)
        self.assertEqual(interrupted.final_generation_status, FinalGenerationStatus.failed)
        self.assertIn("重启", interrupted.final_generation_error)

    def test_review_is_durable_when_draft_generation_fails(self) -> None:
        case = self._create_case()
        self._add_text_file(case.id)
        analysis = self._wait(self.service.create_analysis(case.id, third_party_processing_confirmed=True).id)
        failing_recommendation = FakeRecommendationService(self.repository, self.case_service, fail=True)
        failing_service = CaseAnalysisService(
            repository=self.repository,
            case_service=self.case_service,
            recommendation_service=failing_recommendation,
            provider=self.provider,
            model_version="synthetic-model",
        )
        try:
            saved, draft, error = failing_service.review_and_generate(
                case_id=case.id,
                analysis_id=analysis.id,
                reviewer_id="synthetic-reviewer",
                expected_revision=analysis.revision,
                abnormal_findings=analysis.abnormal_findings,
            )
            completed = self._wait_final(saved.id)
            self.assertEqual(completed.status, AnalysisStatus.reviewed)
            self.assertIsNone(draft)
            self.assertIsNone(error)
            self.assertEqual(completed.final_generation_status, FinalGenerationStatus.failed)
            self.assertIn("synthetic draft failure", completed.final_generation_error)
            self.assertEqual(len(completed.reviewed_abnormal_findings), 2)

            synthesis_calls = self.provider.synthesis_calls
            failing_recommendation.fail = False
            queued = failing_service.retry_draft_generation(case_id=case.id, analysis_id=analysis.id)
            retried = self._wait_final(queued.id)
            self.assertEqual(retried.final_generation_status, FinalGenerationStatus.ready)
            self.assertIsNotNone(retried.draft_id)
            self.assertEqual(self.provider.synthesis_calls, synthesis_calls)
        finally:
            failing_service.executor.shutdown(wait=True, cancel_futures=True)

    def test_unchanged_findings_run_review_synthesis_without_thinking(self) -> None:
        case = self._create_case()
        self._add_text_file(case.id)
        analysis = self._wait(
            self.service.create_analysis(case.id, third_party_processing_confirmed=True).id
        )
        self.assertEqual(self.provider.synthesis_calls, 1)
        self.assertEqual(self.provider.synthesis_thinking_types, ["disabled"])

        saved, draft, error = self.service.review_and_generate(
            case_id=case.id,
            analysis_id=analysis.id,
            reviewer_id="synthetic-reviewer",
            expected_revision=analysis.revision,
            abnormal_findings=analysis.abnormal_findings,
        )

        self.assertIsNone(error)
        self.assertIsNone(draft)
        completed = self._wait_final(saved.id)
        draft = self.repository.get_draft(completed.draft_id)
        self.assertIsNotNone(draft)
        self.assertEqual(self.provider.synthesis_calls, 2)
        self.assertEqual(self.provider.synthesis_thinking_types, ["disabled", "disabled"])
        self.assertEqual(completed.reviewed_case_summary, "医生确认后的病例总结")

    def test_changed_findings_run_review_synthesis(self) -> None:
        case = self._create_case()
        self._add_text_file(case.id)
        analysis = self._wait(
            self.service.create_analysis(case.id, third_party_processing_confirmed=True).id
        )
        changed = [
            item.model_copy(update={"result_text": "医生修正结果"})
            if index == 0
            else item
            for index, item in enumerate(analysis.abnormal_findings)
        ]

        saved, draft, error = self.service.review_and_generate(
            case_id=case.id,
            analysis_id=analysis.id,
            reviewer_id="synthetic-reviewer",
            expected_revision=analysis.revision,
            abnormal_findings=changed,
        )

        self.assertIsNone(error)
        self.assertIsNone(draft)
        completed = self._wait_final(saved.id)
        draft = self.repository.get_draft(completed.draft_id)
        self.assertIsNotNone(draft)
        self.assertEqual(self.provider.synthesis_calls, 2)
        self.assertEqual(self.provider.synthesis_thinking_types, ["disabled", "disabled"])
        self.assertEqual(completed.reviewed_case_summary, "医生确认后的病例总结")

    def test_reviewed_findings_can_regenerate_draft_and_preserve_report_order(self) -> None:
        case = self._create_case()
        self._add_text_file(case.id)
        analysis = self._wait(self.service.create_analysis(case.id, third_party_processing_confirmed=True).id)
        saved, draft, error = self.service.review_and_generate(
            case_id=case.id,
            analysis_id=analysis.id,
            reviewer_id="synthetic-reviewer",
            expected_revision=analysis.revision,
            abnormal_findings=analysis.abnormal_findings,
        )
        self.assertIsNone(error)
        self.assertIsNone(draft)
        saved = self._wait_final(saved.id)
        draft = self.repository.get_draft(saved.draft_id)
        self.assertIsNotNone(draft)
        self.assertEqual(
            list(draft.report_sections),
            ["核心结论与健康画像", "异常指标汇总", "慢性食物敏感检测结果", "功能医学系统失衡分析", "生活方式干预", "首月营养素干预方案", "总医嘱说明"],
        )
        review_service = ReviewService(
            self.repository,
            self.case_service,
            CaseIndicatorService(),
            PdfReportExporter(Path(self.temp_dir.name) / "reports"),
        )
        rendered = review_service._render_report(draft, self.case_service.get_case(case.id))
        headings = [
            "## 核心结论与健康画像",
            "## 异常指标汇总",
            "## 慢性食物敏感检测结果",
            "## 功能医学系统失衡分析",
            "## 生活方式干预",
            "## 首月营养素干预方案",
            "## 总医嘱说明",
        ]
        positions = [rendered.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("合成食物5", rendered)
        manual_preview = "\n\n".join(
            "\n".join([f"## {title}", *[f"- {item}" for item in content]])
            for title, content in draft.report_sections.items()
        )
        review = review_service.approve(
            draft.id,
            reviewer_id="synthetic-reviewer",
            publishable_summary=manual_preview,
            edits={},
        )
        published_positions = [review.publishable_report.index(heading) for heading in headings]
        self.assertEqual(published_positions, sorted(published_positions))
        self.assertIn("合成食物5", review.publishable_report)
        self.assertTrue(Path(review.pdf_report_path).exists())
        repeated, repeated_draft, repeated_error = self.service.review_and_generate(
            case_id=case.id,
            analysis_id=analysis.id,
            reviewer_id="synthetic-reviewer",
            expected_revision=saved.revision,
            abnormal_findings=[],
        )
        self.assertIsNone(repeated_error)
        self.assertIsNone(repeated_draft)
        repeated = self._wait_final(repeated.id)
        repeated_draft = self.repository.get_draft(repeated.draft_id)
        self.assertNotEqual(repeated_draft.id, draft.id)
        self.assertGreater(repeated.revision, saved.revision)

    def test_final_report_groups_abnormal_findings_by_upload_order_without_pages(self) -> None:
        case = self._create_case()
        self._add_text_file(
            case.id,
            file_id="file-first",
            digest="digest-first",
            filename="first-report.txt",
        )
        self._add_text_file(
            case.id,
            file_id="file-second",
            digest="digest-second",
            filename="second-report.txt",
        )
        analysis = self._wait(self.service.create_analysis(case.id, third_party_processing_confirmed=True).id)

        queued, draft, error = self.service.review_and_generate(
            case_id=case.id,
            analysis_id=analysis.id,
            reviewer_id="synthetic-reviewer",
            expected_revision=analysis.revision,
            abnormal_findings=analysis.abnormal_findings,
        )

        self.assertIsNone(error)
        self.assertIsNone(draft)
        completed = self._wait_final(queued.id)
        draft = self.repository.get_draft(completed.draft_id)
        grouped = draft.report_sections["异常指标汇总"]
        headings = [item for item in grouped if item.startswith("### ")]
        self.assertEqual(headings, ["### 1. first-report.txt", "### 2. second-report.txt"])
        self.assertFalse(any("页" in item or "page" in item.lower() for item in grouped))
        self.assertEqual(list(draft.report_sections)[0], "核心结论与健康画像")

    def test_empty_legacy_draft_can_be_regenerated_without_reading_documents_again(self) -> None:
        case = self._create_case()
        self._add_text_file(case.id)
        analysis = self._wait(self.service.create_analysis(case.id, third_party_processing_confirmed=True).id)
        document_calls = self.provider.document_calls
        legacy_empty = RecommendationDraft(
            id="draft-empty-legacy",
            case_id=case.id,
            recommended_skus=[],
            model_version="legacy",
            prompt_version="legacy",
            rule_version="legacy",
        )
        self.repository.save_draft(legacy_empty)
        self.case_service.append_draft(case.id, legacy_empty.id)
        analysis.status = AnalysisStatus.reviewed
        analysis.draft_id = legacy_empty.id
        self.repository.save_case_analysis(analysis)

        saved, draft, error = self.service.review_and_generate(
            case_id=case.id,
            analysis_id=analysis.id,
            reviewer_id="synthetic-reviewer",
            expected_revision=analysis.revision,
            abnormal_findings=analysis.abnormal_findings,
        )

        self.assertIsNone(error)
        self.assertIsNone(draft)
        saved = self._wait_final(saved.id)
        draft = self.repository.get_draft(saved.draft_id)
        self.assertNotEqual(draft.id, legacy_empty.id)
        self.assertTrue(draft.recommended_skus)
        self.assertEqual(saved.draft_id, draft.id)
        self.assertEqual(self.provider.document_calls, document_calls)

    def test_review_rejects_publishing_an_empty_nutrition_draft(self) -> None:
        case = self._create_case()
        empty = RecommendationDraft(
            id="draft-empty",
            case_id=case.id,
            recommended_skus=[],
            model_version="synthetic",
            prompt_version="synthetic",
            rule_version="synthetic",
        )
        self.repository.save_draft(empty)
        review_service = ReviewService(
            self.repository,
            self.case_service,
            CaseIndicatorService(),
            PdfReportExporter(Path(self.temp_dir.name) / "reports"),
        )

        with self.assertRaisesRegex(ValueError, "至少保留一项营养素推荐"):
            review_service.approve(
                empty.id,
                reviewer_id="synthetic-reviewer",
                publishable_summary=None,
                edits={},
            )

    def test_docx_model_page_numbers_are_normalized_to_one_logical_page(self) -> None:
        provider = OpenAICompatibleCaseAnalysisProvider(
            base_url="https://synthetic.invalid",
            api_key="synthetic",
            model="synthetic",
        )
        uploaded = SimpleNamespace(
            id="docx-file",
            filename="synthetic-report.docx",
            is_scanned=False,
            page_texts=[PageText(page=1, text="合成指标A 12.3 U/L 1.0-10.0")],
        )
        response = {
            "report_type": "lab",
            "medical_content": True,
            "summary": "合成摘要",
            "abnormal_findings": [
                {
                    "name": "合成指标A",
                    "result_text": "12.3 偏高",
                    "raw_value": "12.3",
                    "unit": "U/L",
                    "reference_range": "1.0-10.0",
                    "abnormal_flag": "high",
                    "interpretation": None,
                    "source_page": 7,
                    "source_text": "合成指标A 12.3 U/L 1.0-10.0",
                    "confidence": 0.9,
                }
            ],
            "system_findings": [],
            "questionnaire": None,
            "food_sensitivity": {
                "source_page": 5,
                "mild_foods": ["合成食物"],
                "moderate_foods": [],
                "high_foods": [],
                "interpretations": [],
                "valid": True,
                "warning": None,
            },
            "warnings": [],
        }
        with patch.object(provider, "_call_json", return_value=response):
            result = provider.analyze_document(uploaded)

        self.assertEqual(result.abnormal_findings[0].source_page, 1)
        self.assertEqual(result.food_sensitivity.source_page, 1)

    def test_case_synthesis_retries_once_when_first_result_is_english(self) -> None:
        provider = OpenAICompatibleCaseAnalysisProvider(
            base_url="https://synthetic.invalid",
            api_key="synthetic",
            model="synthetic",
        )
        first = {
            "case_summary": "This is an English clinical summary with multiple abnormal findings and system observations.",
            "system_findings": ["Endocrine system imbalance"],
            "warnings": [],
        }
        second = {
            "case_summary": "这是重新生成的简体中文病例总结，包含已确认的异常发现和系统分析。",
            "system_findings": ["内分泌系统功能失衡"],
            "warnings": [],
        }

        with patch.object(provider, "_call_json", side_effect=[first, second]) as mocked_call:
            result = provider.synthesize_case(
                clinical_summary_text=None,
                document_results=[],
            )

        self.assertEqual(result.case_summary, second["case_summary"])
        self.assertEqual(mocked_call.call_count, 2)
        self.assertEqual(mocked_call.call_args_list[1].kwargs["schema_name"], "case_synthesis_zh_retry")
        self.assertTrue(
            all(call.kwargs["thinking_type"] == "enabled" for call in mocked_call.call_args_list)
        )

    def test_case_synthesis_rejects_two_english_results(self) -> None:
        provider = OpenAICompatibleCaseAnalysisProvider(
            base_url="https://synthetic.invalid",
            api_key="synthetic",
            model="synthetic",
        )
        english = {
            "case_summary": "This is an English clinical summary with multiple abnormal findings and system observations.",
            "system_findings": ["Endocrine system imbalance"],
            "warnings": [],
        }

        with patch.object(provider, "_call_json", side_effect=[english, english]):
            with self.assertRaisesRegex(ValueError, "简体中文"):
                provider.synthesize_case(clinical_summary_text=None, document_results=[])

    def test_case_analysis_auto_style_uses_responses_endpoint(self) -> None:
        class StubResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"output_text": '{"status":"connected"}'}

        class StubClient:
            def __init__(self):
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return StubResponse()

        client = StubClient()
        provider = OpenAICompatibleCaseAnalysisProvider(
            base_url="https://synthetic.invalid/v1",
            api_key="synthetic",
            model="synthetic",
            api_style="auto",
            http_client=client,
        )

        result = provider._call_json(
            instructions="synthetic",
            content=[{"type": "input_text", "text": "synthetic"}],
            schema={
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
                "additionalProperties": False,
            },
            schema_name="synthetic_connection",
        )

        self.assertEqual(result, {"status": "connected"})
        self.assertEqual(client.calls[0][0], "https://synthetic.invalid/v1/responses")
        self.assertEqual(client.calls[0][1]["json"]["thinking"], {"type": "disabled"})

    def test_kimi_chat_style_maps_multimodal_content_and_thinking_timeout(self) -> None:
        class StubResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": '{"status":"connected"}'}}]}

        class StubClient:
            def __init__(self):
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return StubResponse()

        client = StubClient()
        provider = OpenAICompatibleCaseAnalysisProvider(
            base_url="https://api.moonshot.cn/v1",
            api_key="synthetic",
            model="kimi-k2.6",
            api_style="chat",
            timeout_seconds=90,
            thinking_timeout_seconds=600,
            temperature=0.1,
            http_client=client,
        )

        result = provider._call_json(
            instructions="synthetic",
            content=[
                {"type": "input_text", "text": "synthetic"},
                {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"},
            ],
            schema={
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
                "additionalProperties": False,
            },
            schema_name="synthetic_connection",
            thinking_type="enabled",
        )

        self.assertEqual(result, {"status": "connected"})
        self.assertEqual(client.calls[0][0], "https://api.moonshot.cn/v1/chat/completions")
        request = client.calls[0][1]
        self.assertEqual(request["timeout"], 600)
        self.assertEqual(request["json"]["thinking"], {"type": "enabled"})
        self.assertNotIn("temperature", request["json"])
        self.assertEqual(
            request["json"]["messages"][1]["content"][1],
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,AAAA"},
            },
        )
        self.assertEqual(request["json"]["response_format"]["type"], "json_schema")

    def test_scanned_input_images_are_each_sent_once(self) -> None:
        provider = OpenAICompatibleCaseAnalysisProvider(
            base_url="https://synthetic.invalid",
            api_key="synthetic",
            model="synthetic",
        )
        seen_urls: list[str] = []
        thinking_types: list[str] = []

        def fake_call_json(**kwargs):
            thinking_types.append(kwargs["thinking_type"])
            seen_urls.extend(
                item["image_url"] for item in kwargs["content"] if item.get("type") == "input_image"
            )
            return {
                "report_type": "unknown_medical",
                "medical_content": True,
                "summary": None,
                "abnormal_findings": [],
                "system_findings": [],
                "questionnaire": None,
                "food_sensitivity": None,
                "warnings": [],
            }

        uploaded = SimpleNamespace(
            id="scan",
            filename="scan.pdf",
            is_scanned=True,
            storage_uri="unused",
            content_type="application/pdf",
            page_texts=[],
        )
        rendered = [
            {"type": "input_text", "text": f"page {index}"} if index % 2 == 0
            else {"type": "input_image", "image_url": f"data:image/jpeg;base64,page-{index}"}
            for index in range(8)
        ]
        with patch.object(provider, "_render_images", return_value=rendered), patch.object(provider, "_call_json", side_effect=fake_call_json):
            provider.analyze_document(uploaded)
        self.assertEqual(len(seen_urls), 4)
        self.assertEqual(len(set(seen_urls)), 4)
        self.assertEqual(len(thinking_types), 1)
        self.assertTrue(thinking_types)
        self.assertTrue(all(item == "disabled" for item in thinking_types))

    def test_restart_marks_inflight_tasks_failed(self) -> None:
        case = self._create_case()
        analysis = CaseAnalysis(
            id="analysis-inflight",
            case_id=case.id,
            status=AnalysisStatus.synthesizing,
            snapshot_hash="snapshot",
            model_version="synthetic",
        )
        self.repository.save_case_analysis(analysis)
        self.assertEqual(self.repository.mark_active_analyses_interrupted(), 1)
        interrupted = self.repository.get_case_analysis(analysis.id)
        self.assertEqual(interrupted.status, AnalysisStatus.failed)
        self.assertEqual(interrupted.error_code, "interrupted_by_restart")


class InternalWorkbenchApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        settings = AppSettings(
            project_root=root,
            data_dir=Path(__file__).resolve().parents[1] / "app" / "data",
            runtime_dir=root / ".runtime",
            upload_dir=root / ".runtime" / "uploads",
            report_export_dir=root / ".runtime" / "reports",
            sqlite_path=root / ".runtime" / "test.sqlite3",
            knowledge_root=root / "knowledge",
            report_reference_path=root / "report-reference.pdf",
        )
        self.container = build_container(settings)
        self.container.case_analysis_service.provider = FakeAnalysisProvider()
        self.container.case_analysis_service.model_version = "synthetic-model"
        self.app = FastAPI()
        self.app.state.container = self.container
        self.app.include_router(router)
        self.client = TestClient(self.app)
        case = self.container.case_service.create_case(
            customer_name="脱敏接口病例",
            consultant_id=None,
            notes=None,
            consent=None,
        )
        self.case_id = case.id

    def tearDown(self) -> None:
        self.client.close()
        self.container.case_analysis_service.shutdown()
        self.temp_dir.cleanup()

    def test_upload_analysis_delete_and_stale_workflow(self) -> None:
        uploaded = self.client.post(
            f"/cases/{self.case_id}/files",
            files={
                "file": (
                    "synthetic-report.txt",
                    "合成指标A 12.3 U/L 1.0-10.0\n检查提示合成非数值异常存在".encode(),
                    "text/plain",
                )
            },
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        file_payload = uploaded.json()["case"]["files"][0]
        self.assertEqual(file_payload["intake_status"], "uploaded")
        self.assertEqual(uploaded.json()["case"]["extracted_lab_items"], [])

        denied = self.client.post(
            f"/cases/{self.case_id}/analyses",
            json={"third_party_processing_confirmed": False},
        )
        self.assertEqual(denied.status_code, 422, denied.text)

        started = self.client.post(
            f"/cases/{self.case_id}/analyses",
            json={"third_party_processing_confirmed": True},
        )
        self.assertEqual(started.status_code, 200, started.text)
        deadline = time.time() + 5
        latest = None
        while time.time() < deadline:
            latest = self.client.get(f"/cases/{self.case_id}/analyses/latest")
            if latest.json()["status"] == "ready_for_review":
                break
            time.sleep(0.02)
        self.assertEqual(latest.json()["status"], "ready_for_review")

        reviewed = self.client.post(
            f"/cases/{self.case_id}/analyses/{latest.json()['id']}:review-and-generate",
            json={
                "reviewer_id": "synthetic-reviewer",
                "expected_revision": latest.json()["revision"],
                "abnormal_findings": latest.json()["abnormal_findings"],
            },
        )
        self.assertEqual(reviewed.status_code, 202, reviewed.text)
        self.assertTrue(reviewed.json()["review_saved"])
        self.assertFalse(reviewed.json()["draft_generated"])
        self.assertIn(reviewed.json()["analysis"]["final_generation_status"], {"queued", "final_synthesizing", "validating_support_needs", "mapping_products", "checking_safety", "generating_draft", "ready"})
        deadline = time.time() + 5
        while time.time() < deadline:
            latest = self.client.get(f"/cases/{self.case_id}/analyses/latest")
            if latest.json()["final_generation_status"] in {"ready", "failed"}:
                break
            time.sleep(0.02)
        self.assertEqual(latest.json()["final_generation_status"], "ready")

        deleted = self.client.delete(f"/cases/{self.case_id}/files/{file_payload['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        stale = self.client.get(f"/cases/{self.case_id}/analyses/latest")
        self.assertEqual(stale.json()["status"], "stale")


if __name__ == "__main__":
    unittest.main()
