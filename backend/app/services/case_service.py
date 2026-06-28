from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.domain.models import (
    AnalysisMode,
    AuditLog,
    CaseIndicator,
    CaseRecord,
    CaseStatus,
    ConsentRecord,
    ExtractedLabItem,
    FileParseStatus,
    Questionnaire,
    UploadedFile,
    WorkspaceScope,
)
from app.repositories.in_memory import LocalRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CaseService:
    def __init__(self, repository: LocalRepository) -> None:
        self.repository = repository

    def list_cases(
        self,
        *,
        workspace_scope: WorkspaceScope | str | None = None,
        owner_doctor_id: str | None = None,
    ) -> list[CaseRecord]:
        scope_value = getattr(workspace_scope, "value", workspace_scope)
        cases = self.repository.list_cases(workspace_scope=scope_value, owner_doctor_id=owner_doctor_id)
        return sorted(cases, key=lambda item: item.created_at, reverse=True)

    def get_case(self, case_id: str) -> CaseRecord:
        record = self.repository.get_case(case_id)
        if not record:
            raise KeyError(f"Case {case_id} not found")
        return record

    def create_case(
        self,
        *,
        customer_name: str,
        consultant_id: str | None,
        notes: str | None,
        consent: ConsentRecord | None,
        analysis_mode: AnalysisMode = AnalysisMode.llm_primary,
        workspace_scope: WorkspaceScope = WorkspaceScope.public,
        owner_doctor_id: str | None = None,
    ) -> CaseRecord:
        case = CaseRecord(
            id=f"case_{uuid.uuid4().hex[:12]}",
            customer_name=customer_name,
            consultant_id=consultant_id,
            workspace_scope=workspace_scope,
            owner_doctor_id=owner_doctor_id,
            analysis_mode=analysis_mode,
            notes=notes,
            consent=consent,
        )
        self.repository.save_case(case)
        self._audit(
            case.id,
            "case_created",
            "system",
            {
                "customer_name": customer_name,
                "analysis_mode": analysis_mode.value,
                "workspace_scope": workspace_scope.value,
                "owner_doctor_id": owner_doctor_id,
            },
        )
        return case

    def add_uploaded_file(self, case_id: str, uploaded_file: UploadedFile) -> CaseRecord:
        case = self.get_case(case_id)
        case.files.append(uploaded_file)
        case.status = CaseStatus.files_received
        case.parsing_review_completed = False
        case.parsing_reviewed_at = None
        case.parsing_reviewed_by = None
        case.parsing_missing_fields = []
        case.parsing_review_notes = None
        case.updated_at = utc_now()
        self.repository.save_case(case)
        self._audit(case.id, "file_uploaded", "system", {"file_id": uploaded_file.id, "filename": uploaded_file.filename})
        return case

    def attach_parse_results(
        self,
        case_id: str,
        file_id: str,
        *,
        extracted_text: str,
        parse_confidence: float,
        source_spans,
        lab_items,
        parse_warnings: list[str] | None = None,
    ) -> CaseRecord:
        case = self.get_case(case_id)
        parse_warnings = list(parse_warnings or [])
        for uploaded in case.files:
            if uploaded.id == file_id:
                uploaded.raw_extracted_text = extracted_text
                uploaded.corrected_text = extracted_text
                uploaded.parse_confidence = parse_confidence
                uploaded.source_spans = list(source_spans)
                uploaded.parse_status = FileParseStatus.parsed if extracted_text else FileParseStatus.failed
                uploaded.needs_manual_review = True
                uploaded.missing_fields = parse_warnings
                break
        case.extracted_lab_items = lab_items
        case.parsing_review_completed = False
        case.parsing_reviewed_at = None
        case.parsing_reviewed_by = None
        case.parsing_missing_fields = parse_warnings
        case.parsing_review_notes = None
        case.status = CaseStatus.parsing_completed if case.extracted_lab_items or extracted_text else CaseStatus.files_received
        case.updated_at = utc_now()
        self.repository.save_case(case)
        self._audit(
            case.id,
            "parse_completed",
            "system",
            {
                "file_id": file_id,
                "lab_item_count": len(lab_items),
                "parse_confidence": parse_confidence,
                "parse_warning_count": len(parse_warnings),
            },
        )
        return case

    def submit_questionnaire(self, case_id: str, questionnaire: Questionnaire) -> CaseRecord:
        case = self.get_case(case_id)
        case.questionnaire = questionnaire
        case.updated_at = utc_now()
        if case.parsing_review_completed:
            case.status = CaseStatus.ready_for_recommendation
        self.repository.save_case(case)
        self._audit(case.id, "questionnaire_submitted", "system", questionnaire.model_dump(mode="json"))
        return case

    def import_questionnaire(self, case_id: str, questionnaire: Questionnaire, *, filename: str) -> CaseRecord:
        case = self.submit_questionnaire(case_id, questionnaire)
        self._audit(case.id, "questionnaire_imported", "system", {"filename": filename})
        return case

    def update_clinical_summary(
        self,
        case_id: str,
        *,
        clinical_summary_text: str | None,
        actor_id: str = "system",
    ) -> CaseRecord:
        case = self.get_case(case_id)
        normalized_text = (clinical_summary_text or "").strip() or None
        case.clinical_summary_text = normalized_text
        case.updated_at = utc_now()
        self.repository.save_case(case)
        self._audit(
            case.id,
            "clinical_summary_updated",
            actor_id,
            {
                "has_clinical_summary": bool(normalized_text),
                "clinical_summary_preview": (normalized_text or "")[:240],
            },
        )
        return case

    def review_parsing(
        self,
        case_id: str,
        *,
        reviewer_id: str,
        file_updates: list[dict],
        normalized_lab_items: list[ExtractedLabItem],
        missing_fields: list[str],
        review_notes: str | None,
        manual_indicators: list[CaseIndicator] | None = None,
    ) -> CaseRecord:
        case = self.get_case(case_id)
        manual_indicators = list(manual_indicators or [])
        updates_by_file = {item["file_id"]: item for item in file_updates}
        for uploaded in case.files:
            update = updates_by_file.get(uploaded.id)
            if not update:
                continue
            corrected_text = update.get("corrected_text")
            if corrected_text is not None:
                uploaded.corrected_text = corrected_text
            uploaded.missing_fields = list(update.get("missing_fields", []))
            uploaded.parse_status = FileParseStatus.reviewed
            uploaded.needs_manual_review = False

        case.extracted_lab_items = normalized_lab_items
        case.manual_indicators = manual_indicators
        case.parsing_review_completed = True
        case.parsing_reviewed_at = utc_now()
        case.parsing_reviewed_by = reviewer_id
        case.parsing_missing_fields = missing_fields
        case.parsing_review_notes = review_notes
        case.updated_at = utc_now()
        case.status = CaseStatus.ready_for_recommendation if case.questionnaire else CaseStatus.parsing_completed
        self.repository.save_case(case)
        self._audit(
            case.id,
            "parsing_review_saved",
            reviewer_id,
            {
                "reviewed_file_count": len(file_updates),
                "lab_item_count": len(normalized_lab_items),
                "manual_indicator_count": len(manual_indicators),
                "missing_fields": missing_fields,
                "review_notes": review_notes,
            },
        )
        return case

    def append_draft(self, case_id: str, draft_id: str) -> CaseRecord:
        case = self.get_case(case_id)
        case.draft_ids.append(draft_id)
        case.status = CaseStatus.under_review
        case.updated_at = utc_now()
        self.repository.save_case(case)
        return case

    def mark_approved(self, case_id: str) -> CaseRecord:
        case = self.get_case(case_id)
        case.status = CaseStatus.approved
        case.updated_at = utc_now()
        self.repository.save_case(case)
        return case

    def delete_case(self, case_id: str) -> None:
        case = self.get_case(case_id)

        for uploaded_file in case.files:
            self._safe_unlink(uploaded_file.storage_uri)

        for draft_id in case.draft_ids:
            review = self.repository.get_review_decision(draft_id)
            if review:
                self._safe_unlink(review.pdf_report_path)

        self.repository.delete_case_bundle(case.id, case.draft_ids)

    def _audit(self, case_id: str, action: str, actor_id: str, payload: dict) -> None:
        self.repository.add_audit_log(
            AuditLog(
                id=f"audit_{uuid.uuid4().hex[:12]}",
                entity_type="case",
                entity_id=case_id,
                action=action,
                actor_id=actor_id,
                payload=payload,
            )
        )

    def _safe_unlink(self, raw_path: str | None) -> None:
        if not raw_path:
            return

        try:
            path = Path(raw_path)
        except (TypeError, ValueError, OSError):
            return

        if path.exists() and path.is_file():
            path.unlink(missing_ok=True)
