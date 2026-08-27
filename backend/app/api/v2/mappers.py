from __future__ import annotations

import uuid
from typing import Any, Iterable

from app.api.v2.problems import V2ApiError
from app.api.v2.schemas import (
    AnalysisProgress,
    AnalysisResponse,
    ApprovalRequest,
    ApprovalResponse,
    AttachmentResponse,
    CaseSummaryResponse,
    CaseResponse,
    CoreHealthPortraitResponse,
    DosageOptionResponse,
    DosageRegimenResponse,
    DraftGenerationState,
    DraftRecommendationResponse,
    DraftResponse,
    FindingResponse,
    FoodSensitivityItemResponse,
    FoodSensitivityResponse,
    LifestylePlanResponse,
    OperationFailure,
    OperationProgress,
    OperationResponse,
    ReportResponse,
    ReportSectionResponse,
    ReviewSubmitRequest,
    SafetyDecisionResponse,
    StructuredSystemFindingResponse,
    SupplementResponse,
)
from app.domain.models import (
    AbnormalFinding,
    AnalysisStatus,
    CaseAnalysis,
    CaseRecord,
    ChronicFoodSensitivityResult,
    CurrentSupplement,
    DosageRegimen,
    DraftRecommendationItem,
    EvidenceStatus,
    FileIntakeStatus,
    FileParseStatus,
    FinalGenerationStatus,
    FoodSensitivityItem,
    RecommendationDraft,
    ReviewDecision,
    UploadedFile,
    WorkspaceScope,
)


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _analysis_progress_percent(analysis: CaseAnalysis) -> int:
    if analysis.status == AnalysisStatus.queued:
        return 5
    if analysis.status == AnalysisStatus.preparing:
        return 12
    if analysis.status == AnalysisStatus.analyzing_documents:
        ratio = analysis.progress_current / max(analysis.progress_total, 1)
        return 18 + int((max(0.0, min(1.0, ratio)) * 58) + 0.5)
    if analysis.status == AnalysisStatus.synthesizing:
        return 82
    if analysis.status == AnalysisStatus.validating:
        return 94
    return 100


def _analysis_progress_detail(analysis: CaseAnalysis) -> str:
    total = max(analysis.progress_total, 0)
    completed = min(max(analysis.progress_current, 0), total)
    if analysis.status == AnalysisStatus.queued:
        return "排队中"
    if analysis.status == AnalysisStatus.preparing:
        return "正在准备资料"
    if analysis.status == AnalysisStatus.analyzing_documents:
        detail = f"文件分析 {completed}/{total} {'已完成' if completed >= total and total > 0 else '处理中'}"
        return f"{detail}：{analysis.current_file_name}" if analysis.current_file_name else detail
    if analysis.status == AnalysisStatus.synthesizing:
        return f"文件分析 {total}/{total} 已完成，正在进行病例级综合"
    if analysis.status == AnalysisStatus.validating:
        return "病例级综合已完成，正在进行证据校验"
    if analysis.status in {AnalysisStatus.ready_for_review, AnalysisStatus.reviewed}:
        return "综合分析已完成，可以开始医生校对"
    if analysis.status == AnalysisStatus.stale:
        return "病例资料已变化，需要重新分析"
    return "综合分析失败"


def _draft_progress_detail(status: FinalGenerationStatus) -> str:
    return {
        FinalGenerationStatus.queued: "医生校对已保存，任务排队中",
        FinalGenerationStatus.final_synthesizing: "最终病例深度综合",
        FinalGenerationStatus.validating_support_needs: "支持需求校验",
        FinalGenerationStatus.mapping_products: "产品能力匹配",
        FinalGenerationStatus.checking_safety: "禁忌与安全检查",
        FinalGenerationStatus.generating_draft: "生成营养素草案",
        FinalGenerationStatus.ready: "草案生成完成",
        FinalGenerationStatus.failed: "草案生成失败",
    }.get(status, "等待医生校对")


def doctor_workspace_scope() -> WorkspaceScope:
    return WorkspaceScope.doctor


def attachment_is_accepted(uploaded: UploadedFile) -> bool:
    return uploaded.intake_status != FileIntakeStatus.invalid


def attachment_is_parsed(uploaded: UploadedFile) -> bool:
    return uploaded.parse_status == FileParseStatus.parsed


def build_uploaded_file(
    *,
    case_id: str,
    filename: str,
    media_type: str,
    size_bytes: int,
    storage_uri: Any,
    intake: Any,
) -> UploadedFile:
    return UploadedFile(
        id=f"file_{uuid.uuid4().hex[:12]}",
        case_id=case_id,
        filename=filename,
        content_type=media_type,
        size_bytes=size_bytes,
        storage_uri=storage_uri,
        raw_extracted_text=intake.extracted_text or None,
        content_sha256=intake.content_sha256,
        intake_status=intake.intake_status,
        page_count=intake.page_count,
        page_texts=intake.page_texts,
        is_scanned=intake.is_scanned,
        precheck_warning=intake.precheck_warning,
        validation_error=intake.validation_error,
    )


def mark_attachment_parse_failed(uploaded: UploadedFile) -> UploadedFile:
    uploaded.parse_status = FileParseStatus.failed
    uploaded.validation_error = "Attachment parsing failed."
    return uploaded


def attachment_to_response(uploaded: UploadedFile) -> AttachmentResponse:
    return AttachmentResponse(
        id=uploaded.id,
        filename=uploaded.filename,
        media_type=uploaded.content_type,
        size_bytes=uploaded.size_bytes,
        uploaded_at=uploaded.uploaded_at,
        intake_status=_value(uploaded.intake_status),
        parse_status=_value(uploaded.parse_status),
        parse_confidence=uploaded.parse_confidence,
        needs_manual_review=uploaded.needs_manual_review,
        missing_fields=list(uploaded.missing_fields),
        page_count=uploaded.page_count,
        is_scanned=uploaded.is_scanned,
        warning=uploaded.precheck_warning,
        error=uploaded.validation_error,
    )


def case_to_response(case: CaseRecord) -> CaseResponse:
    return CaseResponse(
        id=case.id,
        customer_name=case.customer_name,
        consultant_id=case.consultant_id,
        status=_value(case.status),
        notes=case.notes,
        clinical_summary=case.clinical_summary_text,
        created_at=case.created_at,
        updated_at=case.updated_at,
        attachments=[attachment_to_response(uploaded) for uploaded in case.files],
    )


def case_to_summary_response(case: CaseRecord) -> CaseSummaryResponse:
    return CaseSummaryResponse(
        id=case.id,
        customer_name=case.customer_name,
        consultant_id=case.consultant_id,
        status=_value(case.status),
        attachment_count=len(case.files),
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def finding_to_response(finding: AbnormalFinding) -> FindingResponse:
    return FindingResponse(
        id=finding.id,
        name=finding.name,
        result_text=finding.result_text,
        raw_value=finding.raw_value,
        unit=finding.unit,
        reference_range=finding.reference_range,
        abnormal_flag=finding.abnormal_flag,
        interpretation=finding.interpretation,
        report_explanation=finding.report_explanation,
        neutral_interpretation=finding.neutral_interpretation,
        support_need_text=finding.support_need_text,
        source_file_id=finding.source_file_id,
        source_file_name=finding.source_file_name,
        source_page=finding.source_page,
        source_text=finding.source_text,
        confidence=finding.confidence,
        evidence_status=_value(finding.evidence_status),
        evidence_notes=list(finding.evidence_notes),
        observed_at=finding.observed_at,
    )


def supplement_to_response(supplement: CurrentSupplement) -> SupplementResponse:
    return SupplementResponse(
        id=supplement.id,
        name=supplement.name,
        source_file_ids=list(supplement.source_file_ids),
        source_file_names=list(supplement.source_file_names),
        doctor_added=supplement.doctor_added,
    )


def food_item_to_response(item: FoodSensitivityItem) -> FoodSensitivityItemResponse:
    return FoodSensitivityItemResponse(
        id=item.id,
        name=item.name,
        raw_value=item.raw_value,
        unit=item.unit,
        abnormal_flag=item.abnormal_flag,
        severity=item.severity,
        reported_grade=item.reported_grade,
        reported_grade_meaning=item.reported_grade_meaning,
        reference_range=item.reference_range,
        grading_basis=item.grading_basis,
        source_page=item.source_page,
        source_text=item.source_text,
        evidence_status=_value(item.evidence_status),
    )


def food_sensitivity_to_response(
    result: ChronicFoodSensitivityResult | None,
) -> FoodSensitivityResponse | None:
    if result is None:
        return None
    return FoodSensitivityResponse(
        source_file_id=result.source_file_id,
        source_file_name=result.source_file_name,
        source_page=result.source_page,
        items=[food_item_to_response(item) for item in result.items],
        interpretations=list(result.interpretations),
        valid=result.valid,
        warning=result.warning,
    )


_PUBLIC_ANALYSIS_FAILURES: dict[str, tuple[str, str]] = {
    "model_timeout": (
        "ANALYSIS_MODEL_TIMEOUT",
        "The analysis model timed out. Retry the analysis later.",
    ),
    "model_connection_interrupted": (
        "ANALYSIS_MODEL_UNAVAILABLE",
        "The analysis model is temporarily unavailable. Retry the analysis later.",
    ),
    "model_auth_failed": (
        "ANALYSIS_MODEL_AUTH_FAILED",
        "The analysis model could not be authorized.",
    ),
    "model_rate_limited": (
        "ANALYSIS_MODEL_RATE_LIMITED",
        "The analysis model is temporarily rate limited. Retry the analysis later.",
    ),
    "model_service_unavailable": (
        "ANALYSIS_MODEL_UNAVAILABLE",
        "The analysis model is temporarily unavailable. Retry the analysis later.",
    ),
    "invalid_json": (
        "ANALYSIS_MODEL_INVALID_RESPONSE",
        "The analysis model returned an invalid response. Retry the analysis.",
    ),
    "invalid_schema": (
        "ANALYSIS_MODEL_INVALID_RESPONSE",
        "The analysis model returned an invalid response. Retry the analysis.",
    ),
}


def _analysis_failure_to_response(analysis: CaseAnalysis) -> OperationFailure:
    if analysis.status == AnalysisStatus.stale:
        return OperationFailure(
            code="ANALYSIS_STALE",
            message="The case changed after this analysis started.",
            retryable=True,
        )
    internal_code = str(analysis.error_code or "").strip().lower()
    public = _PUBLIC_ANALYSIS_FAILURES.get(internal_code)
    if public is None and internal_code.startswith("model_http_"):
        public = (
            "ANALYSIS_MODEL_ERROR",
            "The analysis model returned an error. Retry the analysis later.",
        )
    code, message = public or (
        "ANALYSIS_FAILED",
        "The analysis could not be completed.",
    )
    return OperationFailure(code=code, message=message, retryable=True)


def _draft_failure_to_response(error: str | None) -> OperationFailure:
    internal = str(error or "").strip().lower()
    if internal.startswith("model_timeout:"):
        return OperationFailure(
            code="DRAFT_GENERATION_MODEL_TIMEOUT",
            message="The draft generation model timed out. Retry draft generation later.",
            retryable=True,
        )
    if internal.startswith(("model_connection_interrupted:", "model_http_")):
        return OperationFailure(
            code="DRAFT_GENERATION_MODEL_UNAVAILABLE",
            message="The draft generation model is temporarily unavailable.",
            retryable=True,
        )
    if internal.startswith("model_rate_limited:"):
        return OperationFailure(
            code="DRAFT_GENERATION_MODEL_RATE_LIMITED",
            message="Draft generation is temporarily rate limited.",
            retryable=True,
        )
    if internal.startswith(("model_invalid_schema:", "model_invalid_json:")):
        return OperationFailure(
            code="DRAFT_GENERATION_MODEL_INVALID_RESPONSE",
            message="The draft generation model returned an invalid response.",
            retryable=True,
        )
    if "病例资料已变化" in internal:
        return OperationFailure(
            code="DRAFT_GENERATION_STALE",
            message="The case changed after draft generation started.",
            retryable=True,
        )
    return OperationFailure(
        code="DRAFT_GENERATION_FAILED",
        message="Draft generation failed.",
        retryable=True,
    )


def analysis_to_response(analysis: CaseAnalysis) -> AnalysisResponse:
    reviewed = analysis.reviewed_at is not None
    findings = (
        analysis.reviewed_abnormal_findings
        if reviewed
        else analysis.abnormal_findings
    )
    case_summary = (
        analysis.reviewed_case_summary
        if reviewed and analysis.reviewed_case_summary is not None
        else analysis.case_summary
    )
    system_findings = (
        analysis.reviewed_system_findings
        if reviewed
        else analysis.system_findings
    )
    error = None
    if analysis.status in {AnalysisStatus.stale, AnalysisStatus.failed}:
        error = _analysis_failure_to_response(analysis)
    draft_failure = (
        _draft_failure_to_response(analysis.final_generation_error)
        if analysis.final_generation_status == FinalGenerationStatus.failed
        else None
    )
    return AnalysisResponse(
        id=analysis.id,
        case_id=analysis.case_id,
        version=analysis.version,
        revision=analysis.revision,
        status=_value(analysis.status),
        progress=AnalysisProgress(
            current=analysis.progress_current,
            total=analysis.progress_total,
            percent=_analysis_progress_percent(analysis),
            current_file_name=analysis.current_file_name,
        ),
        case_summary=case_summary,
        system_findings=list(system_findings),
        abnormal_findings=[finding_to_response(item) for item in findings],
        current_supplements=[
            supplement_to_response(item) for item in analysis.current_supplements
        ],
        food_sensitivity=food_sensitivity_to_response(analysis.food_sensitivity),
        warnings=list(analysis.warnings),
        error=error,
        draft_generation=DraftGenerationState(
            status=_value(analysis.final_generation_status),
            progress=analysis.final_generation_progress,
            error=draft_failure.message if draft_failure is not None else None,
        ),
        draft_id=analysis.draft_id,
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
    )


def operation_to_response(analysis: CaseAnalysis) -> OperationResponse:
    final_status = analysis.final_generation_status
    draft_stage = final_status != FinalGenerationStatus.idle
    if draft_stage:
        stage = "draft_generation"
        if final_status == FinalGenerationStatus.queued:
            status = "queued"
        elif final_status == FinalGenerationStatus.ready:
            status = "succeeded"
        elif final_status == FinalGenerationStatus.failed:
            status = "failed"
        else:
            status = "running"
        progress = OperationProgress(
            current=analysis.final_generation_progress,
            total=100,
            percent=analysis.final_generation_progress,
            current_item=_draft_progress_detail(final_status),
        )
        failure = (
            _draft_failure_to_response(analysis.final_generation_error)
            if status == "failed"
            else None
        )
    else:
        stage = "analysis"
        if analysis.status == AnalysisStatus.queued:
            status = "queued"
        elif analysis.status in {
            AnalysisStatus.preparing,
            AnalysisStatus.analyzing_documents,
            AnalysisStatus.synthesizing,
            AnalysisStatus.validating,
        }:
            status = "running"
        elif analysis.status in {AnalysisStatus.stale, AnalysisStatus.failed}:
            status = "failed"
        else:
            status = "succeeded"
        progress = OperationProgress(
            current=analysis.progress_current,
            total=analysis.progress_total,
            percent=_analysis_progress_percent(analysis),
            current_item=_analysis_progress_detail(analysis),
        )
        failure = _analysis_failure_to_response(analysis) if status == "failed" else None
    return OperationResponse(
        operation_id=analysis.id,
        stage=stage,
        status=status,
        case_id=analysis.case_id,
        analysis_id=analysis.id,
        draft_id=analysis.draft_id,
        progress=progress,
        failure=failure,
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
    )


def _dosage_regimen_to_response(regimen: DosageRegimen) -> DosageRegimenResponse:
    return DosageRegimenResponse(**regimen.model_dump())


def _draft_item_to_response(item: DraftRecommendationItem) -> DraftRecommendationResponse:
    return DraftRecommendationResponse(
        sku_id=item.sku_id,
        display_name=item.display_name,
        dosage=item.dosage,
        dosage_option_id=item.dosage_option_id,
        dosage_option_label=item.dosage_option_label,
        dosage_match_reasons=list(item.dosage_match_reasons),
        dosage_options=[
            DosageOptionResponse(
                option_id=option.option_id,
                label=option.label,
                display_text=option.display_text,
                requires_review=option.requires_review,
                regimen=_dosage_regimen_to_response(option.regimen),
            )
            for option in item.dosage_options
        ],
        dosage_regimen=(
            _dosage_regimen_to_response(item.dosage_regimen)
            if item.dosage_regimen is not None
            else None
        ),
        reason=item.reason,
        evidence_ids=list(item.evidence_ids),
        evidence_details=list(item.evidence_details),
        warnings=list(item.warnings),
        current_supplement_overlap_notice=item.current_supplement_overlap_notice,
    )


def draft_to_response(draft: RecommendationDraft) -> DraftResponse:
    return DraftResponse(
        id=draft.id,
        case_id=draft.case_id,
        status=_value(draft.status),
        revision=draft.revision,
        public_summary=list(draft.case_summary),
        key_lab_highlights=list(draft.key_lab_highlights),
        recommended_skus=[_draft_item_to_response(item) for item in draft.recommended_skus],
        lifestyle_actions=list(draft.lifestyle_actions),
        rationale=list(draft.rationale),
        evidence_details=list(draft.evidence_details),
        contraindications=list(draft.contraindications),
        missing_info=list(draft.missing_info),
        confidence=draft.confidence,
        abstain_reason=draft.abstain_reason,
        manual_review_required=draft.manual_review_required,
        red_flags=list(draft.red_flags),
        core_health_portrait=(
            CoreHealthPortraitResponse.model_validate(draft.core_health_portrait.model_dump())
            if draft.core_health_portrait is not None
            else None
        ),
        structured_system_findings=[
            StructuredSystemFindingResponse.model_validate(item.model_dump())
            for item in draft.structured_system_findings
        ],
        lifestyle_plan=(
            LifestylePlanResponse.model_validate(draft.lifestyle_plan.model_dump())
            if draft.lifestyle_plan is not None
            else None
        ),
        safety_decisions=[
            SafetyDecisionResponse.model_validate(item.model_dump())
            for item in draft.safety_decisions
        ],
        uncovered_system_ids=list(draft.uncovered_system_ids),
        uncovered_system_reasons=dict(draft.uncovered_system_reasons),
        report_sections=[
            ReportSectionResponse(
                title=title,
                items=[str(value) for value in values],
            )
            for title, values in draft.report_sections.items()
            if isinstance(values, list)
        ],
        generated_at=draft.generated_at,
    )


def approval_to_response(review: ReviewDecision) -> ApprovalResponse:
    return ApprovalResponse(
        draft_id=review.draft_id,
        status=_value(review.final_status),
        reviewer_id=review.reviewer_id,
        publishable_report=review.publishable_report,
        approved_at=review.approved_at,
        report_ready=bool(review.pdf_report_filename),
        report_url=f"/api/v2/drafts/{review.draft_id}/report.pdf",
    )


def report_to_response(review: ReviewDecision) -> ReportResponse:
    return ReportResponse(
        draft_id=review.draft_id,
        filename=review.pdf_report_filename or f"report-{review.draft_id}.pdf",
        download_url=f"/api/v2/drafts/{review.draft_id}/report.pdf",
        reviewer_id=review.reviewer_id,
        publishable_report=review.publishable_report,
        approved_at=review.approved_at,
    )


def _ensure_unique_target_ids(changes: Iterable[Any], label: str) -> None:
    target_ids = [change.id for change in changes if change.op != "add"]
    if len(target_ids) != len(set(target_ids)):
        raise V2ApiError(
            status=422,
            code="DUPLICATE_REVIEW_CHANGE",
            title="Duplicate review change",
            detail=f"The same {label} ID cannot be changed more than once.",
        )


def _require_known_id(item_id: str, known_ids: set[str], label: str) -> None:
    if item_id not in known_ids:
        raise V2ApiError(
            status=422,
            code="UNKNOWN_REVIEW_ITEM",
            title="Unknown review item",
            detail=f"The {label} ID '{item_id}' is not part of the current analysis.",
        )


def _apply_finding_changes(
    findings: list[AbnormalFinding],
    request: ReviewSubmitRequest,
) -> list[AbnormalFinding]:
    _ensure_unique_target_ids(request.finding_changes, "finding")
    known_ids = {item.id for item in findings}
    by_id = {item.id: item for item in findings}
    removed: set[str] = set()
    additions: list[AbnormalFinding] = []
    for change in request.finding_changes:
        if change.op == "add":
            additions.append(
                AbnormalFinding(
                    id=f"finding_manual_{uuid.uuid4().hex[:12]}",
                    evidence_status=EvidenceStatus.needs_review,
                    confidence=1.0,
                    **change.value.model_dump(),
                )
            )
            continue
        _require_known_id(change.id, known_ids, "finding")
        if change.op == "remove":
            removed.add(change.id)
        else:
            by_id[change.id] = by_id[change.id].model_copy(
                update=change.changes.model_dump(exclude_unset=True)
            )
    return [by_id[item.id] for item in findings if item.id not in removed] + additions


def _apply_supplement_changes(
    supplements: list[CurrentSupplement],
    request: ReviewSubmitRequest,
) -> list[CurrentSupplement]:
    _ensure_unique_target_ids(request.supplement_changes, "supplement")
    known_ids = {item.id for item in supplements}
    by_id = {item.id: item for item in supplements}
    removed: set[str] = set()
    additions: list[CurrentSupplement] = []
    for change in request.supplement_changes:
        if change.op == "add":
            additions.append(
                CurrentSupplement(
                    id=f"supplement_manual_{uuid.uuid4().hex[:12]}",
                    name=change.value.name,
                    doctor_added=True,
                )
            )
            continue
        _require_known_id(change.id, known_ids, "supplement")
        if change.op == "remove":
            removed.add(change.id)
        else:
            by_id[change.id] = by_id[change.id].model_copy(
                update=change.changes.model_dump(exclude_unset=True)
            )
    return [by_id[item.id] for item in supplements if item.id not in removed] + additions


def _apply_food_sensitivity_changes(
    result: ChronicFoodSensitivityResult | None,
    request: ReviewSubmitRequest,
) -> ChronicFoodSensitivityResult | None:
    changes = request.food_sensitivity_changes
    _ensure_unique_target_ids(changes, "food sensitivity item")
    items = list(result.items) if result is not None else []
    known_ids = {item.id for item in items}
    by_id = {item.id: item for item in items}
    removed: set[str] = set()
    additions: list[FoodSensitivityItem] = []
    first_add = None
    for change in changes:
        if change.op == "add":
            first_add = first_add or change.value
            value = change.value.model_dump(exclude={"source_file_id", "source_file_name"})
            additions.append(
                FoodSensitivityItem(
                    id=f"food_manual_{uuid.uuid4().hex[:12]}",
                    evidence_status=EvidenceStatus.needs_review,
                    **value,
                )
            )
            continue
        _require_known_id(change.id, known_ids, "food sensitivity item")
        if change.op == "remove":
            removed.add(change.id)
        else:
            by_id[change.id] = by_id[change.id].model_copy(
                update=change.changes.model_dump(exclude_unset=True)
            )
    updated_items = [by_id[item.id] for item in items if item.id not in removed] + additions
    if result is None:
        if not updated_items or first_add is None:
            return None
        result = ChronicFoodSensitivityResult(
            source_file_id=first_add.source_file_id,
            source_file_name=first_add.source_file_name,
            source_page=first_add.source_page,
        )
    return result.model_copy(
        update={
            "items": updated_items,
            "mild_foods": [item.name for item in updated_items if item.severity == "mild"],
            "moderate_foods": [
                item.name for item in updated_items if item.severity == "moderate"
            ],
            "high_foods": [item.name for item in updated_items if item.severity == "high"],
            "valid": bool(updated_items),
        }
    )


def apply_review_changes(
    analysis: CaseAnalysis,
    request: ReviewSubmitRequest,
) -> tuple[
    list[AbnormalFinding],
    list[CurrentSupplement],
    ChronicFoodSensitivityResult | None,
]:
    if analysis.revision != request.expected_revision:
        raise V2ApiError(
            status=409,
            code="ANALYSIS_REVISION_CONFLICT",
            title="Analysis revision conflict",
            detail="The analysis was updated. Fetch the latest revision before submitting.",
        )
    allowed_file_ids = set(analysis.file_ids)
    referenced_file_ids: list[str] = []
    for change in request.finding_changes:
        if change.op == "add":
            referenced_file_ids.append(change.value.source_file_id)
        elif change.op == "update" and "source_file_id" in change.changes.model_fields_set:
            if change.changes.source_file_id is not None:
                referenced_file_ids.append(change.changes.source_file_id)
    for change in request.food_sensitivity_changes:
        if change.op == "add":
            referenced_file_ids.append(change.value.source_file_id)
    if any(file_id not in allowed_file_ids for file_id in referenced_file_ids):
        raise V2ApiError(
            status=422,
            code="INVALID_REVIEW_CHANGES",
            title="Invalid review changes",
            detail="A review change references a file outside the current analysis.",
        )
    findings = (
        analysis.reviewed_abnormal_findings
        if analysis.reviewed_at is not None
        else analysis.abnormal_findings
    )
    return (
        _apply_finding_changes(list(findings), request),
        _apply_supplement_changes(list(analysis.current_supplements), request),
        _apply_food_sensitivity_changes(analysis.food_sensitivity, request),
    )


def approval_request_to_edits(
    draft: RecommendationDraft,
    request: ApprovalRequest,
) -> dict[str, Any]:
    items = {item.sku_id: item for item in draft.recommended_skus}
    excluded = set(request.excluded_sku_ids)
    unknown = sorted(excluded.difference(items))
    unknown.extend(
        sorted({override.sku_id for override in request.dosage_overrides}.difference(items))
    )
    if unknown:
        raise V2ApiError(
            status=422,
            code="UNKNOWN_RECOMMENDATION_SKU",
            title="Unknown recommendation SKU",
            detail=f"The approval references unknown SKU IDs: {', '.join(sorted(set(unknown)))}.",
        )
    if len(excluded) >= len(items):
        raise V2ApiError(
            status=409,
            code="EMPTY_PUBLISHABLE_RECOMMENDATIONS",
            title="No publishable recommendations",
            detail="At least one nutrition recommendation must remain before approval.",
        )
    overrides: dict[str, dict[str, str | None]] = {}
    for override in request.dosage_overrides:
        item = items[override.sku_id]
        option_ids = {option.option_id for option in item.dosage_options}
        if override.option_id not in option_ids:
            raise V2ApiError(
                status=422,
                code="INVALID_DOSAGE_OVERRIDE",
                title="Invalid dosage override",
                detail=(
                    f"Dosage option '{override.option_id}' does not belong to "
                    f"SKU '{override.sku_id}'."
                ),
            )
        note = (override.note or "").strip() or None
        if override.option_id != item.dosage_option_id and note is None:
            raise V2ApiError(
                status=422,
                code="DOSAGE_OVERRIDE_NOTE_REQUIRED",
                title="Dosage override note required",
                detail=f"A note is required when changing the dosage for SKU '{override.sku_id}'.",
            )
        overrides[override.sku_id] = {
            "option_id": override.option_id,
            "note": note,
        }
    return {
        "excluded_sku_ids": list(request.excluded_sku_ids),
        "dosage_overrides": overrides,
    }
