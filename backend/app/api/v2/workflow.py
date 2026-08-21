from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.api.v2.mappers import (
    analysis_to_response,
    attachment_is_accepted,
    attachment_is_parsed,
    apply_review_changes,
    approval_request_to_edits,
    approval_to_response,
    build_uploaded_file,
    case_to_response,
    doctor_workspace_scope,
    draft_to_response,
    mark_attachment_parse_failed,
    operation_to_response,
    report_to_response,
)
from app.api.v2.problems import V2ApiError
from app.api.v2.schemas import (
    AnalysisResponse,
    ApprovalRequest,
    ApprovalResponse,
    AttachmentBatchMeta,
    AttachmentBatchResponse,
    AttachmentFailure,
    AttachmentUploadItem,
    CaseCreateRequest,
    CaseResponse,
    ClinicalSummaryUpdateRequest,
    DraftResponse,
    OperationResponse,
    ReportResponse,
    ReviewSubmitRequest,
    StartAnalysisRequest,
)
from app.services.review_local import InvalidDosageOverrideError


logger = logging.getLogger(__name__)

_ACTIVE_DRAFT_GENERATION_STATUSES = {
    "queued",
    "final_synthesizing",
    "validating_support_needs",
    "mapping_products",
    "checking_safety",
    "generating_draft",
}


@dataclass(frozen=True)
class PreparedAttachment:
    filename: str
    media_type: str
    content: bytes


class V2WorkflowAdapter:
    """Translate the v2 transport contract to the existing application services."""

    def __init__(self, container: Any) -> None:
        self.container = container

    def require_owned_case(self, case_id: str, doctor: Any):
        try:
            case = self.container.case_service.get_case(case_id)
        except KeyError as exc:
            raise V2ApiError(
                status=404,
                code="CASE_NOT_FOUND",
                title="Case not found",
                detail="The requested case does not exist.",
            ) from exc
        if case.owner_doctor_id != doctor.id:
            raise V2ApiError(
                status=403,
                code="CASE_ACCESS_DENIED",
                title="Case access denied",
                detail="The authenticated doctor does not own this case.",
            )
        return case

    def _require_owned_analysis(self, analysis_id: str, doctor: Any):
        analysis = self.container.repository.get_case_analysis(analysis_id)
        if analysis is None:
            raise V2ApiError(
                status=404,
                code="ANALYSIS_NOT_FOUND",
                title="Analysis not found",
                detail="The requested analysis does not exist.",
            )
        self.require_owned_case(analysis.case_id, doctor)
        return analysis

    def _require_owned_draft(self, draft_id: str, doctor: Any):
        draft = self.container.repository.get_draft(draft_id)
        if draft is None:
            raise V2ApiError(
                status=404,
                code="DRAFT_NOT_FOUND",
                title="Draft not found",
                detail="The requested recommendation draft does not exist.",
            )
        case = self.require_owned_case(draft.case_id, doctor)
        return case, draft

    def create_case(self, request: CaseCreateRequest, doctor: Any) -> CaseResponse:
        case = self.container.case_service.create_case(
            customer_name=request.customer_name.strip(),
            consultant_id=(
                (request.consultant_id or "").strip()
                or doctor.display_name
                or doctor.username
            ),
            notes=(request.notes or "").strip() or None,
            consent=None,
            workspace_scope=doctor_workspace_scope(),
            owner_doctor_id=doctor.id,
        )
        return case_to_response(case)

    def get_case(self, case_id: str, doctor: Any) -> CaseResponse:
        return case_to_response(self.require_owned_case(case_id, doctor))

    def update_clinical_summary(
        self,
        case_id: str,
        request: ClinicalSummaryUpdateRequest,
        doctor: Any,
    ) -> CaseResponse:
        self.require_owned_case(case_id, doctor)
        try:
            case = self.container.case_service.update_clinical_summary(
                case_id,
                clinical_summary_text=request.clinical_summary,
                actor_id=doctor.id,
            )
        except KeyError as exc:
            raise V2ApiError(
                status=404,
                code="CASE_NOT_FOUND",
                title="Case not found",
                detail="The requested case does not exist.",
            ) from exc
        return case_to_response(case)

    def upload_attachments(
        self,
        case_id: str,
        attachments: list[PreparedAttachment],
        attachment_type: Literal["medical_record", "questionnaire"],
        doctor: Any,
    ) -> AttachmentBatchResponse:
        case = self.require_owned_case(case_id, doctor)
        if not attachments:
            raise V2ApiError(
                status=422,
                code="ATTACHMENT_BATCH_EMPTY",
                title="Attachment batch is empty",
                detail="At least one attachment is required.",
            )

        items: list[AttachmentUploadItem] = []
        accepted_count = 0
        for prepared in attachments:
            if len(prepared.content) > self.container.settings.max_upload_bytes:
                items.append(
                    self._failed_attachment(
                        prepared,
                        attachment_type,
                        code="PAYLOAD_TOO_LARGE",
                        message="The file exceeds the configured upload size limit.",
                    )
                )
                continue

            try:
                intake = self.container.document_intake_service.preflight(
                    filename=prepared.filename,
                    content_type=prepared.media_type,
                    content=prepared.content,
                )
            except Exception:
                logger.exception("Attachment preflight failed case_id=%s", case.id)
                items.append(
                    self._failed_attachment(
                        prepared,
                        attachment_type,
                        code="ATTACHMENT_PREFLIGHT_FAILED",
                        message="The server could not validate this attachment.",
                        retryable=True,
                    )
                )
                continue
            if intake.validation_error:
                items.append(
                    self._failed_attachment(
                        prepared,
                        attachment_type,
                        code="ATTACHMENT_REJECTED",
                        message=intake.validation_error,
                    )
                )
                continue

            if attachment_type == "questionnaire":
                try:
                    questionnaire = self.container.questionnaire_import_service.parse(
                        filename=prepared.filename,
                        content_type=prepared.media_type,
                        content=prepared.content,
                    )
                    case = self.container.case_service.import_questionnaire(
                        case.id,
                        questionnaire,
                        filename=prepared.filename,
                    )
                except ValueError as exc:
                    items.append(
                        self._failed_attachment(
                            prepared,
                            attachment_type,
                            code="QUESTIONNAIRE_REJECTED",
                            message=str(exc),
                        )
                    )
                    continue
                except Exception:
                    logger.exception("Questionnaire processing failed case_id=%s", case.id)
                    items.append(
                        self._failed_attachment(
                            prepared,
                            attachment_type,
                            code="QUESTIONNAIRE_PROCESSING_FAILED",
                            message="The server could not process this questionnaire.",
                            retryable=True,
                        )
                    )
                    continue
                accepted_count += 1
                items.append(
                    AttachmentUploadItem(
                        filename=prepared.filename,
                        attachment_type=attachment_type,
                        status="questionnaire_imported",
                        media_type=prepared.media_type,
                        size_bytes=len(prepared.content),
                    )
                )
                continue

            duplicate = next(
                (
                    existing
                    for existing in case.files
                    if existing.content_sha256
                    and existing.content_sha256 == intake.content_sha256
                    and attachment_is_accepted(existing)
                ),
                None,
            )
            if duplicate is not None:
                accepted_count += 1
                items.append(
                    AttachmentUploadItem(
                        file_id=duplicate.id,
                        filename=prepared.filename,
                        attachment_type=attachment_type,
                        status="duplicate",
                        media_type=prepared.media_type,
                        size_bytes=len(prepared.content),
                        parse_status=str(duplicate.parse_status.value),
                        warnings=list(duplicate.missing_fields),
                    )
                )
                continue

            try:
                storage_uri = self.container.recommendation_service.object_store.save(
                    prepared.filename,
                    prepared.content,
                )
            except Exception:
                logger.exception("Attachment storage failed case_id=%s", case.id)
                items.append(
                    self._failed_attachment(
                        prepared,
                        attachment_type,
                        code="ATTACHMENT_STORAGE_FAILED",
                        message="The server could not store this attachment.",
                        retryable=True,
                    )
                )
                continue

            uploaded = build_uploaded_file(
                case_id=case.id,
                filename=prepared.filename,
                media_type=prepared.media_type,
                size_bytes=len(prepared.content),
                storage_uri=storage_uri,
                intake=intake,
            )
            try:
                case = self.container.case_service.add_uploaded_file(case.id, uploaded)
            except Exception:
                logger.exception("Attachment persistence failed case_id=%s", case.id)
                items.append(
                    self._failed_attachment(
                        prepared,
                        attachment_type,
                        code="ATTACHMENT_PERSISTENCE_FAILED",
                        message="The server could not register this attachment.",
                        retryable=True,
                    )
                )
                continue
            accepted_count += 1
            try:
                extraction, lab_items = self.container.parsing_service.parse(
                    filename=uploaded.filename,
                    content_type=uploaded.content_type,
                    content=prepared.content,
                    case_id=case.id,
                    file_id=uploaded.id,
                )
                parse_warnings = (
                    self.container.parsing_service.normalization_service.find_unknown_lab_candidates(
                        spans=extraction.spans,
                        lab_items=lab_items,
                    )
                )
                case = self.container.case_service.attach_parse_results(
                    case.id,
                    uploaded.id,
                    extracted_text=extraction.text,
                    parse_confidence=extraction.confidence,
                    source_spans=extraction.spans,
                    lab_items=lab_items,
                    parse_warnings=parse_warnings,
                )
                parsed = next(item for item in case.files if item.id == uploaded.id)
                status = "parsed" if attachment_is_parsed(parsed) else "pending"
                items.append(
                    AttachmentUploadItem(
                        file_id=uploaded.id,
                        filename=prepared.filename,
                        attachment_type=attachment_type,
                        status=status,
                        media_type=prepared.media_type,
                        size_bytes=len(prepared.content),
                        parse_status=parsed.parse_status.value,
                        lab_item_count=len(lab_items),
                        warnings=list(parse_warnings),
                    )
                )
            except Exception:
                persisted = next(item for item in case.files if item.id == uploaded.id)
                mark_attachment_parse_failed(persisted)
                self.container.repository.save_case(case)
                items.append(
                    AttachmentUploadItem(
                        file_id=uploaded.id,
                        filename=prepared.filename,
                        attachment_type=attachment_type,
                        status="failed",
                        media_type=prepared.media_type,
                        size_bytes=len(prepared.content),
                        parse_status=persisted.parse_status.value,
                        failure=AttachmentFailure(
                            code="ATTACHMENT_PARSE_FAILED",
                            message="The attachment was stored but could not be parsed.",
                            retryable=True,
                        ),
                    )
                )

        if accepted_count == 0:
            raise V2ApiError(
                status=422,
                code="ATTACHMENT_BATCH_REJECTED",
                title="Attachment batch rejected",
                detail="No attachment in the batch could be accepted.",
            )
        failed_count = sum(item.status == "failed" for item in items)
        return AttachmentBatchResponse(
            items=items,
            meta=AttachmentBatchMeta(
                case_id=case.id,
                case_status=str(case.status.value),
                accepted_count=accepted_count,
                failed_count=failed_count,
            ),
        )

    @staticmethod
    def _failed_attachment(
        prepared: PreparedAttachment,
        attachment_type: Literal["medical_record", "questionnaire"],
        *,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> AttachmentUploadItem:
        return AttachmentUploadItem(
            filename=prepared.filename,
            attachment_type=attachment_type,
            status="failed",
            media_type=prepared.media_type,
            size_bytes=len(prepared.content),
            failure=AttachmentFailure(
                code=code,
                message=message,
                retryable=retryable,
            ),
        )

    def start_analysis(
        self,
        case_id: str,
        request: StartAnalysisRequest,
        doctor: Any,
    ) -> OperationResponse:
        self.require_owned_case(case_id, doctor)
        if not request.third_party_processing_confirmed:
            raise V2ApiError(
                status=422,
                code="THIRD_PARTY_PROCESSING_CONFIRMATION_REQUIRED",
                title="Third-party processing confirmation required",
                detail="Confirm third-party processing before starting an analysis.",
            )
        try:
            analysis = self.container.case_analysis_service.create_analysis(
                case_id,
                third_party_processing_confirmed=True,
            )
        except ValueError as exc:
            raise V2ApiError(
                status=409,
                code="ANALYSIS_START_CONFLICT",
                title="Analysis cannot start",
                detail="The case is not currently ready to start an analysis.",
            ) from exc
        return operation_to_response(analysis)

    def get_operation(self, operation_id: str, doctor: Any) -> OperationResponse:
        return operation_to_response(self._require_owned_analysis(operation_id, doctor))

    def get_latest_analysis(self, case_id: str, doctor: Any) -> AnalysisResponse:
        self.require_owned_case(case_id, doctor)
        analysis = self.container.repository.get_latest_case_analysis(case_id)
        if analysis is None:
            raise V2ApiError(
                status=404,
                code="ANALYSIS_NOT_FOUND",
                title="Analysis not found",
                detail="This case does not have an analysis yet.",
            )
        return analysis_to_response(analysis)

    def submit_review(
        self,
        case_id: str,
        analysis_id: str,
        request: ReviewSubmitRequest,
        doctor: Any,
    ) -> OperationResponse:
        self.require_owned_case(case_id, doctor)
        analysis = self._require_owned_analysis(analysis_id, doctor)
        if analysis.case_id != case_id:
            raise V2ApiError(
                status=404,
                code="ANALYSIS_NOT_FOUND",
                title="Analysis not found",
                detail="The analysis does not belong to the requested case.",
            )
        final_generation_status = str(analysis.final_generation_status.value)
        if final_generation_status in _ACTIVE_DRAFT_GENERATION_STATUSES:
            raise V2ApiError(
                status=409,
                code="DRAFT_GENERATION_IN_PROGRESS",
                title="Draft generation in progress",
                detail="Wait for the current draft generation operation to finish.",
            )
        findings, supplements, food_sensitivity = apply_review_changes(analysis, request)
        try:
            updated, _, _ = self.container.case_analysis_service.review_and_generate(
                case_id=case_id,
                analysis_id=analysis_id,
                reviewer_id=request.reviewer_id,
                expected_revision=request.expected_revision,
                abnormal_findings=findings,
                current_supplements=supplements,
                food_sensitivity=food_sensitivity,
            )
        except KeyError as exc:
            raise V2ApiError(
                status=404,
                code="ANALYSIS_NOT_FOUND",
                title="Analysis not found",
                detail="The requested analysis does not exist.",
            ) from exc
        except ValueError as exc:
            message = str(exc)
            if "版本已变化" in message:
                raise V2ApiError(
                    status=409,
                    code="ANALYSIS_REVISION_CONFLICT",
                    title="Analysis revision conflict",
                    detail="The analysis was updated. Fetch the latest revision before submitting.",
                ) from exc
            if "尚未进入可校对状态" in message or "资料已变化" in message:
                raise V2ApiError(
                    status=409,
                    code="ANALYSIS_REVIEW_CONFLICT",
                    title="Analysis review conflict",
                    detail="The analysis is not currently available for review.",
                ) from exc
            raise V2ApiError(
                status=422,
                code="INVALID_REVIEW_CHANGES",
                title="Invalid review changes",
                detail="The submitted review changes are not valid for this analysis.",
            ) from exc
        if updated.revision != request.expected_revision + 1:
            raise V2ApiError(
                status=409,
                code="ANALYSIS_REVIEW_CONFLICT",
                title="Analysis review conflict",
                detail="The submitted review was not applied. Fetch the latest analysis and retry.",
            )
        return operation_to_response(updated)

    def retry_draft_generation(
        self,
        case_id: str,
        analysis_id: str,
        doctor: Any,
    ) -> OperationResponse:
        self.require_owned_case(case_id, doctor)
        analysis = self._require_owned_analysis(analysis_id, doctor)
        if analysis.case_id != case_id:
            raise V2ApiError(
                status=404,
                code="ANALYSIS_NOT_FOUND",
                title="Analysis not found",
                detail="The analysis does not belong to the requested case.",
            )
        try:
            updated = self.container.case_analysis_service.retry_draft_generation(
                case_id=case_id,
                analysis_id=analysis_id,
            )
        except KeyError as exc:
            raise V2ApiError(
                status=404,
                code="ANALYSIS_NOT_FOUND",
                title="Analysis not found",
                detail="The requested analysis does not exist.",
            ) from exc
        except ValueError as exc:
            raise V2ApiError(
                status=409,
                code="DRAFT_GENERATION_RETRY_CONFLICT",
                title="Draft generation retry conflict",
                detail=str(exc),
            ) from exc
        return operation_to_response(updated)

    def get_draft(self, draft_id: str, doctor: Any) -> DraftResponse:
        _, draft = self._require_owned_draft(draft_id, doctor)
        review = self.container.repository.get_review_decision(draft_id)
        if review is not None:
            draft = self.container.review_service._draft_with_filtered_recommendations(
                draft,
                review.edits,
            )
        return draft_to_response(draft)

    def approve_draft(
        self,
        draft_id: str,
        request: ApprovalRequest,
        doctor: Any,
    ) -> ApprovalResponse:
        case, draft = self._require_owned_draft(draft_id, doctor)
        if draft.source_analysis_id:
            analysis = self.container.repository.get_case_analysis(draft.source_analysis_id)
            snapshot_changed = bool(
                draft.source_snapshot_hash
                and self.container.case_analysis_service.current_snapshot_hash(case)
                != draft.source_snapshot_hash
            )
            if analysis is None or str(analysis.status.value) == "stale" or snapshot_changed:
                raise V2ApiError(
                    status=409,
                    code="DRAFT_STALE",
                    title="Draft is stale",
                    detail="The case changed after this draft was generated.",
                )
        edits = approval_request_to_edits(draft, request)
        try:
            review = self.container.review_service.approve(
                draft_id,
                reviewer_id=request.reviewer_id,
                publishable_summary=(request.publishable_summary or "").strip() or None,
                edits=edits,
            )
        except KeyError as exc:
            raise V2ApiError(
                status=404,
                code="DRAFT_NOT_FOUND",
                title="Draft not found",
                detail="The requested recommendation draft does not exist.",
            ) from exc
        except InvalidDosageOverrideError as exc:
            raise V2ApiError(
                status=422,
                code="INVALID_DOSAGE_OVERRIDE",
                title="Invalid dosage override",
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise V2ApiError(
                status=409,
                code="DRAFT_APPROVAL_CONFLICT",
                title="Draft approval conflict",
                detail=str(exc),
            ) from exc
        return approval_to_response(review)

    def get_report(self, draft_id: str, doctor: Any) -> ReportResponse:
        self._require_owned_draft(draft_id, doctor)
        review = self.container.repository.get_review_decision(draft_id)
        if review is None:
            raise V2ApiError(
                status=409,
                code="REPORT_NOT_READY",
                title="Report not ready",
                detail="The draft must be approved before its report is available.",
            )
        if (
            not review.pdf_report_path
            or not review.pdf_report_filename
            or not Path(review.pdf_report_path).is_file()
        ):
            raise V2ApiError(
                status=409,
                code="REPORT_NOT_READY",
                title="Report not ready",
                detail="The published report file is not currently available.",
            )
        return report_to_response(review)

    def ensure_pdf(self, draft_id: str, doctor: Any):
        self._require_owned_draft(draft_id, doctor)
        if self.container.repository.get_review_decision(draft_id) is None:
            raise V2ApiError(
                status=409,
                code="REPORT_NOT_READY",
                title="Report not ready",
                detail="The draft must be approved before its report is available.",
            )
        try:
            pdf_path, filename = self.container.review_service.ensure_pdf(draft_id)
        except KeyError as exc:
            raise V2ApiError(
                status=404,
                code="REPORT_NOT_FOUND",
                title="Report not found",
                detail="The requested report does not exist.",
            ) from exc
        if not Path(pdf_path).is_file():
            raise V2ApiError(
                status=404,
                code="REPORT_NOT_FOUND",
                title="Report not found",
                detail="The requested report file does not exist.",
            )
        return pdf_path, filename
