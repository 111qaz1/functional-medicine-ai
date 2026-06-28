from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.domain.models import AnalysisMode, DoctorAccount, UploadedFile, WorkspaceScope


router = APIRouter(prefix="/api/v1", tags=["external-api"])
bearer_scheme = HTTPBearer(auto_error=False)
EXTERNAL_TRUST_SECRET_ENV = "FM_EXTERNAL_TRUST_SHARED_SECRET"
EXTERNAL_TRUST_MAX_SKEW_SECONDS = 300


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
    recommendations: list[ExternalNutritionRecommendation] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class ExternalReportDownloadResponse(BaseModel):
    draft_id: str
    filename: str
    download_url: str


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
        recommendations=recommendations,
        contraindications=draft.contraindications,
        missing_info=draft.missing_info,
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
        extraction, lab_items = container.parsing_service.parse(
            filename=uploaded_file.filename,
            content_type=uploaded_file.content_type,
            content=content,
        )
        parse_warnings = container.parsing_service.normalization_service.find_unknown_lab_candidates(
            spans=extraction.spans,
            lab_items=lab_items,
        )
        case = container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
            parse_warnings=parse_warnings,
        )
        parsed_file = next((item for item in case.files if item.id == uploaded_file.id), uploaded_file)
        results.append(
            ExternalAttachmentResult(
                file_id=uploaded_file.id,
                filename=filename,
                attachment_type=attachment_type,
                status=getattr(parsed_file.parse_status, "value", str(parsed_file.parse_status)),
                lab_item_count=len(lab_items),
                parse_warnings=parse_warnings,
            )
        )

    return ExternalAttachmentUploadResponse(
        case_id=case.id,
        status=getattr(case.status, "value", str(case.status)),
        results=results,
    )


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
