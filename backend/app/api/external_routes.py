from __future__ import annotations

import hashlib
import hmac
import math
import os
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import (
    AnalysisMode,
    DoctorAccount,
    ExtractedLabItem,
    SpecialtyReportResult,
    UploadedFile,
    WorkspaceScope,
)
from app.services.prescription_advice import PrescriptionAdviceService


router = APIRouter(prefix="/api/v1", tags=["external-api"])
bearer_scheme = HTTPBearer(auto_error=False)
EXTERNAL_TRUST_SECRET_ENV = "FM_EXTERNAL_TRUST_SHARED_SECRET"
EXTERNAL_TRUST_MAX_SKEW_SECONDS = 300


class ExternalStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExternalTokenRequest(BaseModel):
    issuer: str = Field(min_length=1)
    doctor_id: str = Field(min_length=1)
    doctor_name: str | None = None
    timestamp: int
    nonce: str = Field(min_length=8)
    signature: str = Field(min_length=32)


class ExternalTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_days: int = 14
    doctor_id: str
    display_name: str


class ExternalCaseCreateRequest(BaseModel):
    customer_name: str = Field(min_length=1)
    consultant_id: str | None = None
    notes: str | None = None
    analysis_mode: AnalysisMode = AnalysisMode.llm_primary


class ExternalCaseResponse(BaseModel):
    case_id: str
    status: str
    customer_name: str
    owner_doctor_id: str | None = None


class ExternalAttachmentResult(BaseModel):
    file_id: str | None = None
    filename: str
    attachment_type: str
    status: str
    lab_item_count: int = 0
    specialty_report_count: int = 0
    parse_warnings: list[str] = Field(default_factory=list)


class ExternalAttachmentUploadResponse(BaseModel):
    case_id: str
    status: str
    results: list[ExternalAttachmentResult] = Field(default_factory=list)


class ExternalNutritionRecommendation(BaseModel):
    sku_id: str
    name: str
    category: str | None = None
    dosage: str
    reason: str
    warnings: list[str] = Field(default_factory=list)


class ExternalNutritionRecommendationResponse(BaseModel):
    case_id: str
    draft_id: str
    status: str
    manual_review_required: bool
    confidence: float
    revision: int
    editable_fields: list[str] = Field(default_factory=list)
    recommendations: list[ExternalNutritionRecommendation] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class ExternalPrescriptionItemsResponse(BaseModel):
    case_id: str
    draft_id: str
    status: str
    medical_advice: str
    advice_source: Literal["llm", "local_fallback"]


class ExternalReportDownloadResponse(BaseModel):
    draft_id: str
    filename: str
    download_url: str


class ExternalParsingFile(BaseModel):
    file_id: str
    filename: str
    parse_status: str
    parse_confidence: float
    corrected_text: str | None = None
    needs_manual_review: bool
    missing_fields: list[str] = Field(default_factory=list)


class ExternalParsingReviewResponse(BaseModel):
    case_id: str
    revision: int
    review_completed: bool
    files: list[ExternalParsingFile] = Field(default_factory=list)
    normalized_lab_items: list[ExtractedLabItem] = Field(default_factory=list)
    specialty_reports: list[SpecialtyReportResult] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    editable_fields: list[str] = Field(default_factory=list)


class ExternalParsingFileUpdate(ExternalStrictModel):
    file_id: str = Field(min_length=1)
    corrected_text: str | None = None
    missing_fields: list[str] = Field(default_factory=list)


class ExternalParsingReviewRequest(ExternalStrictModel):
    expected_revision: int = Field(ge=0)
    files: list[ExternalParsingFileUpdate]
    normalized_lab_items: list[ExtractedLabItem] = Field(default_factory=list)
    specialty_reports: list[SpecialtyReportResult] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    review_notes: str | None = Field(default=None, max_length=1000)


class ExternalNutritionRecommendationPatchItem(ExternalStrictModel):
    sku_id: str = Field(min_length=1, max_length=100)
    dosage: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class ExternalNutritionRecommendationPatchRequest(ExternalStrictModel):
    expected_revision: int = Field(ge=1)
    recommendations: list[ExternalNutritionRecommendationPatchItem] = Field(max_length=12)
    edit_reason: str | None = Field(default=None, max_length=300)


def _container(request: Request):
    return request.app.state.container


def _require_external_doctor(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> DoctorAccount:
    token = credentials.credentials if credentials else None
    doctor = _container(request).auth_service.get_doctor_for_session(token)
    if not doctor:
        raise HTTPException(status_code=401, detail="Invalid or expired bearer token")
    return doctor


def _canonical_trust_payload(payload: ExternalTokenRequest) -> str:
    return "\n".join(
        [
            payload.issuer.strip(),
            payload.doctor_id.strip(),
            (payload.doctor_name or "").strip(),
            str(payload.timestamp),
            payload.nonce.strip(),
        ]
    )


def _normalize_signature(value: str) -> str:
    normalized = (value or "").strip()
    if normalized.lower().startswith("sha256="):
        normalized = normalized.split("=", 1)[1]
    return normalized.lower()


def _verify_external_trust_signature(payload: ExternalTokenRequest) -> None:
    secret = os.getenv(EXTERNAL_TRUST_SECRET_ENV)
    if not secret:
        raise HTTPException(status_code=503, detail=f"{EXTERNAL_TRUST_SECRET_ENV} is not configured")

    now = int(time.time())
    if abs(now - payload.timestamp) > EXTERNAL_TRUST_MAX_SKEW_SECONDS:
        raise HTTPException(status_code=401, detail="External trust token timestamp is outside the allowed window")

    expected = hmac.new(
        secret.encode("utf-8"),
        _canonical_trust_payload(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, _normalize_signature(payload.signature)):
        raise HTTPException(status_code=401, detail="Invalid external trust signature")


def _require_owned_case(container, case_id: str, doctor: DoctorAccount):
    try:
        case = container.case_service.get_case(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if case.owner_doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Case is not owned by the authenticated doctor")
    return case


def _require_owned_draft(container, draft_id: str, doctor: DoctorAccount):
    draft = container.repository.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    case = _require_owned_case(container, draft.case_id, doctor)
    return case, draft


def _nutrition_response(container, draft) -> ExternalNutritionRecommendationResponse:
    product_by_id = {product.sku_id: product for product in container.repository.list_products(enabled_only=False)}
    recommendations: list[ExternalNutritionRecommendation] = []
    for item in draft.recommended_skus:
        product = product_by_id.get(item.sku_id)
        recommendations.append(
            ExternalNutritionRecommendation(
                sku_id=item.sku_id,
                name=item.display_name,
                category=product.category if product else None,
                dosage=item.dosage,
                reason=item.reason,
                warnings=item.warnings,
            )
        )
    return ExternalNutritionRecommendationResponse(
        case_id=draft.case_id,
        draft_id=draft.id,
        status=getattr(draft.status, "value", str(draft.status)),
        manual_review_required=draft.manual_review_required,
        confidence=draft.confidence,
        revision=draft.revision,
        editable_fields=(
            ["recommendations.order", "recommendations.sku_id", "recommendations.dosage", "recommendations.reason"]
            if getattr(draft.status, "value", str(draft.status)) == "pending_review"
            and not container.repository.get_review_decision(draft.id)
            else []
        ),
        recommendations=recommendations,
        contraindications=draft.contraindications,
        missing_info=draft.missing_info,
    )


def _parsing_review_response(case) -> ExternalParsingReviewResponse:
    return ExternalParsingReviewResponse(
        case_id=case.id,
        revision=case.parsing_revision,
        review_completed=case.parsing_review_completed,
        files=[
            ExternalParsingFile(
                file_id=item.id,
                filename=item.filename,
                parse_status=getattr(item.parse_status, "value", str(item.parse_status)),
                parse_confidence=item.parse_confidence,
                corrected_text=item.corrected_text,
                needs_manual_review=item.needs_manual_review,
                missing_fields=item.missing_fields,
            )
            for item in case.files
        ],
        normalized_lab_items=case.extracted_lab_items,
        specialty_reports=case.specialty_reports,
        missing_fields=case.parsing_missing_fields,
        editable_fields=[
            "files.corrected_text",
            "normalized_lab_items",
            "specialty_reports",
            "missing_fields",
            "review_notes",
        ],
    )


def _finite_or_none(value: float | None) -> bool:
    return value is None or math.isfinite(value)


def _validate_external_parsing_review(container, case, payload: ExternalParsingReviewRequest) -> None:
    valid_file_ids = {item.id for item in case.files}
    update_file_ids = {item.file_id for item in payload.files}
    if update_file_ids != valid_file_ids or len(update_file_ids) != len(payload.files):
        raise HTTPException(status_code=422, detail="files must contain each case attachment exactly once")

    markers_by_code = {
        str(item.get("code") or ""): item
        for item in container.parsing_service.normalization_service.markers
    }
    for item in payload.normalized_lab_items:
        if item.marker_code not in markers_by_code:
            raise HTTPException(status_code=422, detail=f"Unknown marker_code: {item.marker_code}")
        if item.source_span.file_id not in valid_file_ids:
            raise HTTPException(status_code=422, detail="Lab item source does not belong to this case")
        values = (
            item.value,
            item.normalized_value,
            item.ref_range.lower,
            item.ref_range.upper,
        )
        if not all(_finite_or_none(value) for value in values):
            raise HTTPException(status_code=422, detail=f"Non-finite value for marker: {item.marker_code}")
        if (
            item.ref_range.lower is not None
            and item.ref_range.upper is not None
            and item.ref_range.lower > item.ref_range.upper
        ):
            raise HTTPException(status_code=422, detail=f"Invalid reference range for marker: {item.marker_code}")
        expected_unit = str(markers_by_code[item.marker_code].get("normalized_unit") or "").strip()
        if expected_unit and item.normalized_unit and item.normalized_unit != expected_unit:
            raise HTTPException(status_code=422, detail=f"Invalid normalized unit for marker: {item.marker_code}")

    original_reports = {report.id: report for report in case.specialty_reports}
    submitted_report_ids = {report.id for report in payload.specialty_reports}
    if submitted_report_ids != set(original_reports) or len(submitted_report_ids) != len(payload.specialty_reports):
        raise HTTPException(status_code=422, detail="specialty_reports must contain each parsed report exactly once")

    report_ids: set[str] = set()
    for report in payload.specialty_reports:
        if report.id in report_ids:
            raise HTTPException(status_code=422, detail="Duplicate specialty report id")
        report_ids.add(report.id)
        original = original_reports[report.id]
        if report.report_type != original.report_type or report.file_id != original.file_id:
            raise HTTPException(status_code=422, detail="Specialty report identity does not match parsed source")
        if report.file_id not in valid_file_ids:
            raise HTTPException(status_code=422, detail="Specialty report source does not belong to this case")
        original_metric_codes = [metric.code for metric in getattr(original, "metrics", [])]
        submitted_metric_codes = [metric.code for metric in getattr(report, "metrics", [])]
        if (
            len(set(submitted_metric_codes)) != len(submitted_metric_codes)
            or set(submitted_metric_codes) != set(original_metric_codes)
        ):
            raise HTTPException(status_code=422, detail="Specialty metric codes do not match parsed report")
        for metric in getattr(report, "metrics", []):
            if metric.source_span.file_id != report.file_id:
                raise HTTPException(status_code=422, detail="Specialty metric source does not match its report")
            values = (metric.value, metric.ref_range.lower, metric.ref_range.upper)
            if not all(_finite_or_none(value) for value in values):
                raise HTTPException(status_code=422, detail=f"Non-finite specialty metric: {metric.code}")
            if (
                metric.ref_range.lower is not None
                and metric.ref_range.upper is not None
                and metric.ref_range.lower > metric.ref_range.upper
            ):
                raise HTTPException(status_code=422, detail=f"Invalid specialty reference range: {metric.code}")


def _prescription_items_response(container, draft) -> ExternalPrescriptionItemsResponse:
    advice = PrescriptionAdviceService(container.settings).build_advice(draft)
    return ExternalPrescriptionItemsResponse(
        case_id=draft.case_id,
        draft_id=draft.id,
        status=getattr(draft.status, "value", str(draft.status)),
        medical_advice=advice.medical_advice,
        advice_source=advice.advice_source,
    )


@router.post("/auth/token", response_model=ExternalTokenResponse)
def issue_external_token(payload: ExternalTokenRequest, request: Request):
    container = _container(request)
    _verify_external_trust_signature(payload)
    try:
        session = container.auth_service.issue_external_trust_session(
            issuer=payload.issuer,
            external_doctor_id=payload.doctor_id,
            display_name=payload.doctor_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return ExternalTokenResponse(
        access_token=session.session.id,
        doctor_id=session.doctor.id,
        display_name=session.doctor.display_name,
    )


@router.post("/cases", response_model=ExternalCaseResponse)
def create_external_case(
    payload: ExternalCaseCreateRequest,
    request: Request,
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    container = _container(request)
    case = container.case_service.create_case(
        customer_name=payload.customer_name,
        consultant_id=payload.consultant_id or doctor.display_name or doctor.username,
        notes=payload.notes,
        consent=None,
        analysis_mode=payload.analysis_mode,
        workspace_scope=WorkspaceScope.doctor,
        owner_doctor_id=doctor.id,
    )
    return ExternalCaseResponse(
        case_id=case.id,
        status=getattr(case.status, "value", str(case.status)),
        customer_name=case.customer_name,
        owner_doctor_id=case.owner_doctor_id,
    )


@router.post("/cases/{case_id}/attachments", response_model=ExternalAttachmentUploadResponse)
async def upload_external_attachments(
    case_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    attachment_type: Literal["case", "questionnaire"] = Form("case"),
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    container = _container(request)
    case = _require_owned_case(container, case_id, doctor)
    results: list[ExternalAttachmentResult] = []

    for file in files:
        content = await file.read()
        filename = file.filename or "upload.bin"
        content_type = file.content_type or "application/octet-stream"
        if attachment_type == "questionnaire":
            try:
                questionnaire = container.questionnaire_import_service.parse(
                    filename=filename,
                    content_type=content_type,
                    content=content,
                )
                case = container.case_service.import_questionnaire(case.id, questionnaire, filename=filename)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            results.append(
                ExternalAttachmentResult(
                    filename=filename,
                    attachment_type=attachment_type,
                    status="questionnaire_imported",
                )
            )
            continue

        uploaded_file = UploadedFile(
            id=f"file_{uuid.uuid4().hex[:12]}",
            case_id=case.id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            storage_uri=container.recommendation_service.object_store.save(filename, content),
        )
        case = container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items, specialty_reports = container.parsing_service.parse_with_specialty_reports(
            filename=uploaded_file.filename,
            content_type=uploaded_file.content_type,
            content=content,
            file_id=uploaded_file.id,
        )
        parse_warnings = container.parsing_service.normalization_service.find_unknown_lab_candidates(
            spans=extraction.spans,
            lab_items=lab_items,
        )
        parse_warnings = list(
            dict.fromkeys(
                [
                    *parse_warnings,
                    *(warning for report in specialty_reports for warning in report.warnings),
                ]
            )
        )
        case = container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
            parse_warnings=parse_warnings,
            specialty_reports=specialty_reports,
        )
        parsed_file = next((item for item in case.files if item.id == uploaded_file.id), uploaded_file)
        results.append(
            ExternalAttachmentResult(
                file_id=uploaded_file.id,
                filename=filename,
                attachment_type=attachment_type,
                status=getattr(parsed_file.parse_status, "value", str(parsed_file.parse_status)),
                lab_item_count=len(lab_items),
                specialty_report_count=len(specialty_reports),
                parse_warnings=parse_warnings,
            )
        )

    return ExternalAttachmentUploadResponse(
        case_id=case.id,
        status=getattr(case.status, "value", str(case.status)),
        results=results,
    )


@router.get("/cases/{case_id}/parsing-review", response_model=ExternalParsingReviewResponse)
def get_external_parsing_review(
    case_id: str,
    request: Request,
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    case = _require_owned_case(_container(request), case_id, doctor)
    return _parsing_review_response(case)


@router.put("/cases/{case_id}/parsing-review", response_model=ExternalParsingReviewResponse)
def save_external_parsing_review(
    case_id: str,
    payload: ExternalParsingReviewRequest,
    request: Request,
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    container = _container(request)
    case = _require_owned_case(container, case_id, doctor)
    _validate_external_parsing_review(container, case, payload)
    try:
        case = container.case_service.review_parsing(
            case_id,
            reviewer_id=doctor.id,
            file_updates=[item.model_dump() for item in payload.files],
            normalized_lab_items=payload.normalized_lab_items,
            specialty_reports=payload.specialty_reports,
            missing_fields=payload.missing_fields,
            review_notes=payload.review_notes,
            expected_revision=payload.expected_revision,
        )
    except ValueError as exc:
        status_code = 409 if str(exc) == "parsing_revision_conflict" else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return _parsing_review_response(case)


@router.post("/cases/{case_id}/nutrition-recommendations", response_model=ExternalNutritionRecommendationResponse)
def generate_external_nutrition_recommendations(
    case_id: str,
    request: Request,
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    container = _container(request)
    _require_owned_case(container, case_id, doctor)
    try:
        draft = container.recommendation_service.generate(case_id, doctor.display_name or doctor.username)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _nutrition_response(container, draft)


@router.get("/cases/{case_id}/nutrition-recommendations/latest", response_model=ExternalNutritionRecommendationResponse)
def get_latest_external_nutrition_recommendations(
    case_id: str,
    request: Request,
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    container = _container(request)
    case = _require_owned_case(container, case_id, doctor)
    if not case.draft_ids:
        raise HTTPException(status_code=404, detail="No recommendation draft has been generated for this case")
    draft = container.repository.get_draft(case.draft_ids[-1])
    if not draft:
        raise HTTPException(status_code=404, detail="Latest draft not found")
    return _nutrition_response(container, draft)


@router.get("/drafts/{draft_id}/nutrition-recommendations", response_model=ExternalNutritionRecommendationResponse)
def get_external_draft_nutrition_recommendations(
    draft_id: str,
    request: Request,
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    container = _container(request)
    _, draft = _require_owned_draft(container, draft_id, doctor)
    return _nutrition_response(container, draft)


@router.patch("/drafts/{draft_id}/nutrition-recommendations", response_model=ExternalNutritionRecommendationResponse)
def patch_external_draft_nutrition_recommendations(
    draft_id: str,
    payload: ExternalNutritionRecommendationPatchRequest,
    request: Request,
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    container = _container(request)
    _require_owned_draft(container, draft_id, doctor)
    try:
        draft = container.recommendation_service.update_draft_nutrition_recommendations(
            draft_id,
            expected_revision=payload.expected_revision,
            recommendations=[item.model_dump() for item in payload.recommendations],
            actor_id=doctor.id,
            edit_reason=payload.edit_reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        status_code = 409 if str(exc) in {"draft_revision_conflict", "draft_not_editable"} else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return _nutrition_response(container, draft)


@router.get("/drafts/{draft_id}/prescription-items", response_model=ExternalPrescriptionItemsResponse)
def get_external_prescription_items(
    draft_id: str,
    request: Request,
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    container = _container(request)
    _, draft = _require_owned_draft(container, draft_id, doctor)
    return _prescription_items_response(container, draft)


@router.get("/drafts/{draft_id}/report-download", response_model=ExternalReportDownloadResponse)
def get_external_report_download_url(
    draft_id: str,
    request: Request,
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    container = _container(request)
    _require_owned_draft(container, draft_id, doctor)
    if not container.repository.get_review_decision(draft_id):
        raise HTTPException(status_code=409, detail="Report is not approved yet")
    try:
        _, filename = container.review_service.ensure_pdf(draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ExternalReportDownloadResponse(
        draft_id=draft_id,
        filename=filename,
        download_url=str(request.url_for("download_external_pdf_report", draft_id=draft_id)),
    )


@router.get("/drafts/{draft_id}/report.pdf", name="download_external_pdf_report")
def download_external_pdf_report(
    draft_id: str,
    request: Request,
    doctor: DoctorAccount = Depends(_require_external_doctor),
):
    container = _container(request)
    _require_owned_draft(container, draft_id, doctor)
    if not container.repository.get_review_decision(draft_id):
        raise HTTPException(status_code=409, detail="Report is not approved yet")
    try:
        pdf_path, filename = container.review_service.ensure_pdf(draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=pdf_path, media_type="application/pdf", filename=filename)
