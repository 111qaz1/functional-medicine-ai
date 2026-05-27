from __future__ import annotations

import json
import re
import unicodedata
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.responses import Response

from app.api.schemas import (
    AssistantCaseChatRequest,
    AssistantCaseChatResponse,
    AuthLoginRequest,
    AuthMeResponse,
    AuthRegisterRequest,
    AuthResponse,
    ApproveDraftRequest,
    CaseDetailResponse,
    CaseSummaryResponse,
    ClinicalSummaryImageImportResponse,
    ClinicalSummaryUpdateRequest,
    ClinicianRuleListResponse,
    CreateClinicianRuleFromCaseRequest,
    CreateCaseRequest,
    DashboardResponse,
    GenerateDraftRequest,
    KnowledgeManifestResponse,
    LLMConfigResponse,
    LLMConfigUpdateRequest,
    ParsingReviewRequest,
    ProductCatalogResponse,
    ProductRuleCreateRequest,
    ProductRuleUpdateRequest,
    QuestionnaireRequest,
    UpdateClinicianRuleRequest,
)
from app.core.bootstrap import build_container
from app.core.settings import (
    LLMConfig,
    llm_config_from_settings,
    llm_config_validation_error,
    load_settings,
    save_llm_config,
)
from app.domain.models import (
    AuditLog,
    CaseIndicator,
    DoctorAccount,
    DoctorRole,
    ProductRule,
    RuleScope,
    SourceSpan,
    UploadedFile,
    WorkspaceScope,
)


router = APIRouter()
_CLINICAL_SUMMARY_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
SESSION_COOKIE_NAME = "fm_session"


def _container(request: Request):
    return request.app.state.container


def _doctor_response(doctor: DoctorAccount):
    from app.api.schemas import DoctorAccountResponse

    return DoctorAccountResponse.from_account(doctor)


def _current_doctor(request: Request) -> DoctorAccount | None:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    return _container(request).auth_service.get_doctor_for_session(session_id)


def _require_doctor(request: Request) -> DoctorAccount:
    doctor = _current_doctor(request)
    if not doctor:
        raise HTTPException(status_code=401, detail="请先登录医生账号。")
    return doctor


def _require_admin(request: Request) -> DoctorAccount:
    container = _container(request)
    if container.repository.count_doctors() == 0:
        return DoctorAccount(
            id="system_setup",
            username="system_setup",
            display_name="System setup",
            password_hash="",
            role=DoctorRole.admin,
        )
    doctor = _require_doctor(request)
    if doctor.role != DoctorRole.admin:
        raise HTTPException(status_code=403, detail="只有管理员可以执行该操作。")
    return doctor


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=14 * 24 * 60 * 60,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def _can_access_case(case, doctor: DoctorAccount | None) -> bool:
    scope = getattr(case.workspace_scope, "value", str(case.workspace_scope))
    if scope == WorkspaceScope.public.value:
        return True
    return bool(doctor and case.owner_doctor_id == doctor.id)


def _authorized_case(container, case_id: str, doctor: DoctorAccount | None):
    try:
        case = container.case_service.get_case(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _can_access_case(case, doctor):
        raise HTTPException(status_code=403, detail="没有权限访问这个医生工作台病例。")
    return case


def _authorized_case_for_draft(container, draft_id: str, doctor: DoctorAccount | None):
    draft = container.repository.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    case = _authorized_case(container, draft.case_id, doctor)
    return case, draft


def _persist_product_catalog(container) -> None:
    catalog_path = container.settings.data_dir / "product_catalog.json"
    existing_items = json.loads(catalog_path.read_text(encoding="utf-8-sig")) if catalog_path.exists() else []
    latest_by_sku = {
        product.sku_id: product.model_dump(mode="json")
        for product in container.repository.list_products(enabled_only=False)
    }

    ordered_payload: list[dict] = []
    seen_skus: set[str] = set()
    for item in existing_items:
        sku_id = item.get("sku_id")
        if not sku_id or sku_id not in latest_by_sku:
            continue
        ordered_payload.append(latest_by_sku[sku_id])
        seen_skus.add(sku_id)

    for sku_id, item in latest_by_sku.items():
        if sku_id not in seen_skus:
            ordered_payload.append(item)

    catalog_path.write_text(
        json.dumps(ordered_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8-sig",
    )


def _supports_clinical_summary_image(filename: str, content_type: str | None) -> bool:
    suffix = Path(filename).suffix.lower()
    return bool((content_type or "").startswith("image/") or suffix in _CLINICAL_SUMMARY_IMAGE_SUFFIXES)


def _clean_clinical_summary_import_text(text: str) -> str:
    cleaned_lines: list[str] = []
    seen: set[str] = set()
    for raw_line in re.split(r"[\r\n]+", text or ""):
        line = unicodedata.normalize("NFKC", raw_line).strip()
        line = re.sub(r"\s+", " ", line)
        if not line:
            continue

        compact = re.sub(r"\s+", "", line).lower()
        if re.fullmatch(r"no[:：]?[a-z0-9\-]+", compact):
            continue
        if compact in seen:
            continue

        cleaned_lines.append(line)
        seen.add(compact)
    return "\n".join(cleaned_lines).strip()


def _merge_clinical_summary_text(existing_text: str | None, imported_text: str) -> str:
    merged_lines: list[str] = []
    seen: set[str] = set()
    for block in (existing_text or "", imported_text):
        for raw_line in re.split(r"[\r\n]+", block):
            line = raw_line.strip()
            if not line:
                continue
            compact = re.sub(r"\s+", "", line).lower()
            if compact in seen:
                continue
            merged_lines.append(line)
            seen.add(compact)
    return "\n".join(merged_lines).strip()


def _case_detail_response(container, case, *, include_audit_logs: bool = False) -> CaseDetailResponse:
    latest_draft = container.repository.get_draft(case.draft_ids[-1]) if case.draft_ids else None
    review = container.repository.get_review_decision(case.draft_ids[-1]) if case.draft_ids else None
    audit_logs = []
    if include_audit_logs:
        audit_logs = container.repository.list_audit_logs(case.id)
        if latest_draft:
            audit_logs += container.repository.list_audit_logs(latest_draft.id)

    return CaseDetailResponse(
        case=case,
        display_indicators=container.indicator_service.build(case),
        latest_draft=latest_draft,
        review_decision=review,
        audit_logs=audit_logs,
        matched_clinician_rules=container.assistant_rule_service.match_rules_for_case(case),
    )


@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.get("/health/rag")
def rag_health_check(request: Request):
    container = _container(request)
    settings = container.settings
    index_dir = settings.rag_index_dir
    manifest_path = index_dir / "manifest.json" if index_dir else None
    retriever = getattr(container.recommendation_service, "rag_retriever", None)
    payload = {
        "enabled": settings.rag_enabled,
        "loaded": retriever is not None,
        "index_dir": str(index_dir) if index_dir else None,
        "manifest_exists": bool(manifest_path and manifest_path.exists()),
    }
    if retriever is not None:
        manifest = getattr(retriever, "manifest", {}) or {}
        payload.update(
            {
                "document_count": manifest.get("document_count"),
                "model_name": manifest.get("model_name"),
                "embedding_backend": manifest.get("embedding_backend"),
                "faiss_index_type": manifest.get("faiss_index_type"),
            }
        )
        readiness = retriever.readiness_check()
        payload.update(readiness)
        payload["ready"] = bool(readiness.get("dense_ready")) and bool(readiness.get("faiss_loaded"))
    else:
        payload["ready"] = False
    return payload


@router.get("/auth/me", response_model=AuthMeResponse)
def auth_me(request: Request):
    doctor = _current_doctor(request)
    return AuthMeResponse(doctor=_doctor_response(doctor) if doctor else None)


@router.post("/auth/register", response_model=AuthResponse)
def auth_register(payload: AuthRegisterRequest, request: Request, response: Response):
    container = _container(request)
    try:
        doctor = container.auth_service.register(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
        )
        session = container.auth_service.login(username=payload.username, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _set_session_cookie(response, session.session.id)
    return AuthResponse(doctor=_doctor_response(doctor))


@router.post("/auth/login", response_model=AuthResponse)
def auth_login(payload: AuthLoginRequest, request: Request, response: Response):
    container = _container(request)
    try:
        session = container.auth_service.login(username=payload.username, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookie(response, session.session.id)
    return AuthResponse(doctor=_doctor_response(session.doctor))


@router.post("/auth/logout")
def auth_logout(request: Request, response: Response):
    _container(request).auth_service.logout(request.cookies.get(SESSION_COOKIE_NAME))
    _clear_session_cookie(response)
    return {"logged_out": True}


@router.get("/cases", response_model=DashboardResponse)
def list_cases(request: Request):
    container = _container(request)
    workspace = request.query_params.get("workspace", WorkspaceScope.public.value)
    if workspace not in {WorkspaceScope.public.value, WorkspaceScope.doctor.value}:
        raise HTTPException(status_code=422, detail="workspace must be public or doctor")
    doctor = _current_doctor(request)
    if workspace == WorkspaceScope.doctor.value:
        doctor = _require_doctor(request)
    cases = []
    list_kwargs = {"workspace_scope": workspace}
    if workspace == WorkspaceScope.doctor.value:
        list_kwargs["owner_doctor_id"] = doctor.id
    for item in container.case_service.list_cases(**list_kwargs):
        latest_draft_id = item.draft_ids[-1] if item.draft_ids else None
        cases.append(
            CaseSummaryResponse(
                id=item.id,
                customer_name=item.customer_name,
                analysis_mode=item.analysis_mode,
                status=item.status.value,
                consultant_id=item.consultant_id,
                workspace_scope=item.workspace_scope,
                owner_doctor_id=item.owner_doctor_id,
                created_at=item.created_at,
                updated_at=item.updated_at,
                file_count=len(item.files),
                lab_item_count=len(item.extracted_lab_items),
                latest_draft_id=latest_draft_id,
            )
        )
    return DashboardResponse(cases=cases)


@router.post("/cases", response_model=CaseDetailResponse)
def create_case(payload: CreateCaseRequest, request: Request):
    container = _container(request)
    doctor = _current_doctor(request)
    workspace_scope = payload.workspace_scope
    owner_doctor_id = None
    consultant_id = payload.consultant_id
    if workspace_scope == WorkspaceScope.doctor:
        doctor = _require_doctor(request)
        owner_doctor_id = doctor.id
        consultant_id = consultant_id or doctor.display_name or doctor.username
    record = container.case_service.create_case(
        customer_name=payload.customer_name,
        consultant_id=consultant_id,
        notes=payload.notes,
        consent=payload.consent,
        analysis_mode=payload.analysis_mode,
        workspace_scope=workspace_scope,
        owner_doctor_id=owner_doctor_id,
    )
    return _case_detail_response(container, record)


@router.get("/cases/{case_id}", response_model=CaseDetailResponse)
def get_case(case_id: str, request: Request):
    container = _container(request)
    case = _authorized_case(container, case_id, _current_doctor(request))

    return _case_detail_response(container, case, include_audit_logs=True)


@router.delete("/cases/{case_id}")
def delete_case(case_id: str, request: Request):
    container = _container(request)
    _authorized_case(container, case_id, _current_doctor(request))
    try:
        container.case_service.delete_case(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True, "case_id": case_id}


@router.post("/cases/{case_id}/files", response_model=CaseDetailResponse)
async def upload_file(case_id: str, request: Request, file: UploadFile = File(...)):
    container = _container(request)
    case = _authorized_case(container, case_id, _current_doctor(request))

    content = await file.read()
    filename = file.filename or "upload.bin"
    uploaded_file = UploadedFile(
        id=f"file_{uuid.uuid4().hex[:12]}",
        case_id=case_id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        storage_uri=container.recommendation_service.object_store.save(filename, content),
    )
    case = container.case_service.add_uploaded_file(case.id, uploaded_file)
    extraction, lab_items = container.parsing_service.parse(
        filename=uploaded_file.filename,
        content_type=uploaded_file.content_type,
        content=content,
    )
    case = container.case_service.attach_parse_results(
        case.id,
        uploaded_file.id,
        extracted_text=extraction.text,
        parse_confidence=extraction.confidence,
        source_spans=extraction.spans,
        lab_items=lab_items,
    )
    return _case_detail_response(container, case)


@router.post("/cases/{case_id}/files/{file_id}:reparse", response_model=CaseDetailResponse)
def reparse_file(case_id: str, file_id: str, request: Request):
    container = _container(request)
    case = _authorized_case(container, case_id, _current_doctor(request))

    target_file = next((item for item in case.files if item.id == file_id), None)
    if not target_file or not target_file.storage_uri:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(target_file.storage_uri)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stored file is missing")

    content = file_path.read_bytes()
    extraction, lab_items = container.parsing_service.parse(
        filename=target_file.filename,
        content_type=target_file.content_type,
        content=content,
    )
    case = container.case_service.attach_parse_results(
        case.id,
        target_file.id,
        extracted_text=extraction.text,
        parse_confidence=extraction.confidence,
        source_spans=extraction.spans,
        lab_items=lab_items,
    )
    return _case_detail_response(container, case)


@router.post("/cases/{case_id}/questionnaire", response_model=CaseDetailResponse)
def submit_questionnaire(case_id: str, payload: QuestionnaireRequest, request: Request):
    container = _container(request)
    _authorized_case(container, case_id, _current_doctor(request))
    try:
        case = container.case_service.submit_questionnaire(case_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _case_detail_response(container, case)


@router.post("/cases/{case_id}/questionnaire-file", response_model=CaseDetailResponse)
async def import_questionnaire_file(case_id: str, request: Request, file: UploadFile = File(...)):
    container = _container(request)
    _authorized_case(container, case_id, _current_doctor(request))

    filename = file.filename or "questionnaire-upload.bin"
    content = await file.read()
    try:
        questionnaire = container.questionnaire_import_service.parse(
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            content=content,
        )
        case = container.case_service.import_questionnaire(case_id, questionnaire, filename=filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _case_detail_response(container, case)


@router.put("/cases/{case_id}/clinical-summary", response_model=CaseDetailResponse)
def update_clinical_summary(case_id: str, payload: ClinicalSummaryUpdateRequest, request: Request):
    container = _container(request)
    _authorized_case(container, case_id, _current_doctor(request))
    try:
        case = container.case_service.update_clinical_summary(
            case_id,
            clinical_summary_text=payload.clinical_summary_text,
            actor_id="case-workbench-ui",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _case_detail_response(container, case)


@router.post("/cases/{case_id}/clinical-summary-image", response_model=ClinicalSummaryImageImportResponse)
async def import_clinical_summary_image(case_id: str, request: Request, file: UploadFile = File(...)):
    container = _container(request)
    case = _authorized_case(container, case_id, _current_doctor(request))

    filename = file.filename or "clinical-summary-image.bin"
    if not _supports_clinical_summary_image(filename, file.content_type):
        raise HTTPException(status_code=400, detail="只支持 PNG / JPG / JPEG / BMP / GIF / TIFF / WEBP 图片。")

    content = await file.read()
    extraction = container.parsing_service.extract_text(
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        content=content,
    )
    if extraction.error_message:
        raise HTTPException(status_code=400, detail=extraction.error_message)
    extracted_text = _clean_clinical_summary_import_text(extraction.text)
    if not extracted_text:
        raise HTTPException(status_code=400, detail="未从图片中识别到可用于病例总结的文本，请更换更清晰的截图后重试。")

    merged_text = _merge_clinical_summary_text(case.clinical_summary_text, extracted_text)
    case = container.case_service.update_clinical_summary(
        case_id,
        clinical_summary_text=merged_text,
        actor_id="clinical-summary-image-ui",
    )
    return ClinicalSummaryImageImportResponse(
        case_detail=_case_detail_response(container, case),
        filename=filename,
        extracted_text=extracted_text,
        confidence=extraction.confidence,
    )


@router.put("/cases/{case_id}/parsing-review", response_model=CaseDetailResponse)
def save_parsing_review(case_id: str, payload: ParsingReviewRequest, request: Request):
    container = _container(request)
    _authorized_case(container, case_id, _current_doctor(request))
    try:
        manual_indicators = [
            CaseIndicator(
                indicator_name=item.indicator_name.strip(),
                result_text=item.result_text.strip(),
                status=item.status,
                category="manual",
                source_span=SourceSpan(
                    file_name="人工录入",
                    snippet=(item.evidence_text or "解析校对人工补录").strip(),
                ),
            )
            for item in payload.manual_indicators
        ]
        case = container.case_service.review_parsing(
            case_id,
            reviewer_id=payload.reviewer_id,
            file_updates=[item.model_dump() for item in payload.files],
            normalized_lab_items=payload.normalized_lab_items,
            manual_indicators=manual_indicators,
            missing_fields=payload.missing_fields,
            review_notes=payload.review_notes,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _case_detail_response(container, case)


@router.post("/cases/{case_id}/drafts:generate")
def generate_draft(case_id: str, payload: GenerateDraftRequest, request: Request):
    container = _container(request)
    _authorized_case(container, case_id, _current_doctor(request))
    try:
        draft = container.recommendation_service.generate(case_id, payload.requested_by)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return draft


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, request: Request):
    container = _container(request)
    _, draft = _authorized_case_for_draft(container, draft_id, _current_doctor(request))
    review = container.repository.get_review_decision(draft_id)
    return {"draft": draft, "review_decision": review}


@router.post("/drafts/{draft_id}/approve")
def approve_draft(draft_id: str, payload: ApproveDraftRequest, request: Request):
    container = _container(request)
    _authorized_case_for_draft(container, draft_id, _current_doctor(request))
    try:
        review = container.review_service.approve(
            draft_id,
            reviewer_id=payload.reviewer_id,
            publishable_summary=payload.publishable_summary,
            edits=payload.edits,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return review


@router.get("/drafts/{draft_id}/report.pdf")
def download_pdf_report(draft_id: str, request: Request):
    container = _container(request)
    _authorized_case_for_draft(container, draft_id, _current_doctor(request))
    try:
        pdf_path, filename = container.review_service.ensure_pdf(draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=filename,
    )


@router.get("/catalog/products", response_model=ProductCatalogResponse)
def list_product_catalog(request: Request):
    container = _container(request)
    return ProductCatalogResponse(products=container.repository.list_products(enabled_only=False))


@router.get("/catalog/products/{sku_id}")
def get_product_rule(sku_id: str, request: Request):
    container = _container(request)
    product = container.repository.get_product(sku_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("/catalog/products")
def create_product_rule(payload: ProductRuleCreateRequest, request: Request):
    container = _container(request)
    doctor = _require_admin(request)
    sku_id = payload.sku_id.strip()
    if not sku_id:
        raise HTTPException(status_code=422, detail="sku_id is required")
    if container.repository.get_product(sku_id):
        raise HTTPException(status_code=409, detail="Product already exists")

    product = ProductRule(
        sku_id=sku_id,
        display_name=payload.display_name,
        category=payload.category,
        source_refs=payload.source_refs,
        formula_summary=payload.formula_summary,
        core_ingredients=payload.core_ingredients,
        candidate_use_cases=payload.candidate_use_cases,
        contraindications=payload.contraindications,
        enabled=payload.enabled,
        merge_status=payload.merge_status,
        indications=payload.indications,
        exclusions=payload.exclusions,
        dosage_rule=payload.dosage_rule,
        interaction_rule=payload.interaction_rule,
        warning_text=payload.warning_text,
        lifestyle_tags=payload.lifestyle_tags,
        priority=payload.priority,
    )
    container.repository.save_product(product)
    container.repository.add_audit_log(
        AuditLog(
            id=f"audit_{uuid.uuid4().hex[:12]}",
            entity_type="product",
            entity_id=product.sku_id,
            action="product_created",
            actor_id=doctor.username,
            payload=product.model_dump(mode="json"),
        )
    )
    _persist_product_catalog(container)
    return product


@router.put("/catalog/products/{sku_id}")
def update_product_rule(sku_id: str, payload: ProductRuleUpdateRequest, request: Request):
    container = _container(request)
    doctor = _require_admin(request)
    product = container.repository.get_product(sku_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    updated_product = product.model_copy(
        update={
            "display_name": payload.display_name,
            "category": payload.category,
            "source_refs": payload.source_refs,
            "formula_summary": payload.formula_summary,
            "core_ingredients": payload.core_ingredients,
            "candidate_use_cases": payload.candidate_use_cases,
            "contraindications": payload.contraindications,
            "enabled": payload.enabled,
            "merge_status": payload.merge_status,
            "indications": payload.indications,
            "exclusions": payload.exclusions,
            "dosage_rule": payload.dosage_rule,
            "interaction_rule": payload.interaction_rule,
            "warning_text": payload.warning_text,
            "lifestyle_tags": payload.lifestyle_tags,
            "priority": payload.priority,
        }
    )
    container.repository.save_product(updated_product)
    container.repository.add_audit_log(
        AuditLog(
            id=f"audit_{uuid.uuid4().hex[:12]}",
            entity_type="product",
            entity_id=updated_product.sku_id,
            action="product_updated",
            actor_id=doctor.username,
            payload=updated_product.model_dump(mode="json"),
        )
    )
    _persist_product_catalog(container)
    return updated_product


@router.delete("/catalog/products/{sku_id}")
def delete_product_rule(sku_id: str, request: Request):
    container = _container(request)
    doctor = _require_admin(request)
    product = container.repository.get_product(sku_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    container.repository.delete_product(sku_id)
    container.repository.add_audit_log(
        AuditLog(
            id=f"audit_{uuid.uuid4().hex[:12]}",
            entity_type="product",
            entity_id=sku_id,
            action="product_deleted",
            actor_id=doctor.username,
            payload={"sku_id": sku_id, "display_name": product.display_name},
        )
    )
    _persist_product_catalog(container)
    return {"deleted": True, "sku_id": sku_id}


@router.get("/assistant/rules", response_model=ClinicianRuleListResponse)
def list_clinician_rules(request: Request):
    container = _container(request)
    return ClinicianRuleListResponse(rules=container.assistant_rule_service.list_rules(doctor=_current_doctor(request)))


@router.post("/assistant/rules/from-case")
def create_clinician_rule_from_case(payload: CreateClinicianRuleFromCaseRequest, request: Request):
    container = _container(request)
    doctor = _require_doctor(request)
    _authorized_case(container, payload.case_id, doctor)
    try:
        rule = container.assistant_rule_service.create_from_case(
            case_id=payload.case_id,
            author_id=doctor.display_name or doctor.username,
            instruction_text=payload.instruction_text,
            scope=payload.scope,
            owner_doctor_id=doctor.id if payload.scope == RuleScope.private else None,
            created_by_doctor_id=doctor.id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rule


@router.post("/assistant/cases/{case_id}/chat", response_model=AssistantCaseChatResponse)
def chat_with_case_assistant(case_id: str, payload: AssistantCaseChatRequest, request: Request):
    container = _container(request)
    _authorized_case(container, case_id, _current_doctor(request))
    try:
        result = container.assistant_chat_service.reply(
            case_id=case_id,
            user_message=payload.message,
            history=payload.history,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return AssistantCaseChatResponse(
        reply=result.reply,
        mode=result.mode,
        model_label=result.model_label,
    )


@router.put("/assistant/rules/{rule_id}")
def update_clinician_rule(rule_id: str, payload: UpdateClinicianRuleRequest, request: Request):
    container = _container(request)
    doctor = _require_doctor(request)
    updates = dict(
        title=payload.title,
        instruction_text=payload.instruction_text,
        enabled=payload.enabled,
        action=payload.action,
        strength=payload.strength,
        target_sku_ids=payload.target_sku_ids,
        trigger_marker_rules=payload.trigger_marker_rules,
        trigger_support_profiles=payload.trigger_support_profiles,
        trigger_goals=payload.trigger_goals,
        trigger_symptoms=payload.trigger_symptoms,
        trigger_chief_concerns=payload.trigger_chief_concerns,
        trigger_conditions=payload.trigger_conditions,
        notes=payload.notes,
    )
    if payload.scope is not None:
        updates["scope"] = payload.scope
        updates["owner_doctor_id"] = doctor.id if payload.scope == RuleScope.private else None
    try:
        rule = container.assistant_rule_service.update_rule(
            rule_id,
            doctor=doctor,
            **updates,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rule


@router.delete("/assistant/rules/{rule_id}")
def delete_clinician_rule(rule_id: str, request: Request):
    container = _container(request)
    doctor = _require_doctor(request)
    try:
        container.assistant_rule_service.delete_rule(rule_id, doctor=doctor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"deleted": True, "rule_id": rule_id}


@router.get("/system/llm-config", response_model=LLMConfigResponse)
def get_llm_config(request: Request):
    container = _container(request)
    config = llm_config_from_settings(container.settings)
    validation_error = llm_config_validation_error(config)
    return LLMConfigResponse(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        api_style=config.api_style,
        timeout_seconds=config.timeout_seconds,
        temperature=config.temperature,
        configured=bool(config.base_url and config.api_key and config.model and not validation_error),
        validation_error=validation_error,
    )


@router.put("/system/llm-config", response_model=LLMConfigResponse)
def update_llm_config(payload: LLMConfigUpdateRequest, request: Request):
    _require_admin(request)
    current_container = _container(request)
    new_config = LLMConfig(
        base_url=payload.base_url,
        api_key=payload.api_key,
        model=payload.model,
        api_style=payload.api_style,
        timeout_seconds=payload.timeout_seconds,
        temperature=payload.temperature,
    )
    validation_error = llm_config_validation_error(new_config)
    if validation_error:
        raise HTTPException(status_code=400, detail=validation_error)
    save_llm_config(current_container.settings.project_root, new_config)
    refreshed_settings = load_settings()
    request.app.state.container = build_container(refreshed_settings)
    refreshed_config = llm_config_from_settings(request.app.state.container.settings)
    refreshed_validation_error = llm_config_validation_error(refreshed_config)
    return LLMConfigResponse(
        base_url=refreshed_config.base_url,
        api_key=refreshed_config.api_key,
        model=refreshed_config.model,
        api_style=refreshed_config.api_style,
        timeout_seconds=refreshed_config.timeout_seconds,
        temperature=refreshed_config.temperature,
        configured=bool(refreshed_config.base_url and refreshed_config.api_key and refreshed_config.model and not refreshed_validation_error),
        validation_error=refreshed_validation_error,
    )


@router.get("/knowledge/manifest", response_model=KnowledgeManifestResponse)
def list_knowledge_manifest(request: Request):
    container = _container(request)
    return KnowledgeManifestResponse(entries=container.repository.list_knowledge_manifest())
