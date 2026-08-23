from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from app.api.v2.problems import (
    ProblemDetailsRoute,
    V2ApiError,
    documented_problem_responses,
)
from app.api.v2.schemas import (
    AnalysisResponse,
    ApprovalRequest,
    ApprovalResponse,
    AttachmentBatchResponse,
    CaseCreateRequest,
    CaseListResponse,
    CaseResponse,
    ClinicalSummaryUpdateRequest,
    DraftResponse,
    OperationResponse,
    ReportResponse,
    ReviewSubmitRequest,
    StartAnalysisRequest,
)
from app.api.v2.workflow import PreparedAttachment, V2WorkflowAdapter


router = APIRouter(
    prefix="/api/v2",
    tags=["external-api-v2"],
    route_class=ProblemDetailsRoute,
)
bearer_scheme = HTTPBearer(auto_error=False)


async def _read_upload_with_limit(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        chunk = await file.read(min(1024 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _adapter(request: Request) -> V2WorkflowAdapter:
    return V2WorkflowAdapter(request.app.state.container)


def _require_external_doctor(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Any:
    token = credentials.credentials if credentials else None
    doctor = request.app.state.container.auth_service.get_doctor_for_session(token)
    if doctor is None:
        raise V2ApiError(
            status=401,
            code="AUTHENTICATION_REQUIRED",
            title="Authentication required",
            detail="A valid bearer token is required.",
        )
    return doctor


@router.post(
    "/cases",
    response_model=CaseResponse,
    status_code=201,
    responses=documented_problem_responses(401, 422, 500),
)
def create_case(
    payload: CaseCreateRequest,
    request: Request,
    doctor: Any = Depends(_require_external_doctor),
) -> CaseResponse:
    return _adapter(request).create_case(payload, doctor)


@router.get(
    "/cases",
    response_model=CaseListResponse,
    responses=documented_problem_responses(401, 422, 500),
)
def list_cases(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    doctor: Any = Depends(_require_external_doctor),
) -> CaseListResponse:
    return _adapter(request).list_cases(doctor, offset=offset, limit=limit)


@router.get(
    "/cases/{case_id}",
    response_model=CaseResponse,
    responses=documented_problem_responses(401, 403, 404, 422, 500),
)
def get_case(
    case_id: str,
    request: Request,
    doctor: Any = Depends(_require_external_doctor),
) -> CaseResponse:
    return _adapter(request).get_case(case_id, doctor)


@router.put(
    "/cases/{case_id}/clinical-summary",
    response_model=CaseResponse,
    responses=documented_problem_responses(401, 403, 404, 422, 500),
)
def update_clinical_summary(
    case_id: str,
    payload: ClinicalSummaryUpdateRequest,
    request: Request,
    doctor: Any = Depends(_require_external_doctor),
) -> CaseResponse:
    return _adapter(request).update_clinical_summary(case_id, payload, doctor)


@router.post(
    "/cases/{case_id}/attachments",
    response_model=AttachmentBatchResponse,
    status_code=201,
    responses=documented_problem_responses(401, 403, 404, 413, 422, 500),
)
async def upload_attachments(
    case_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    attachment_type: Literal["medical_record", "questionnaire"] = Form("medical_record"),
    doctor: Any = Depends(_require_external_doctor),
) -> AttachmentBatchResponse:
    settings = request.app.state.container.settings
    if len(files) > settings.max_upload_files_per_batch:
        raise V2ApiError(
            status=413,
            code="ATTACHMENT_BATCH_FILE_LIMIT_EXCEEDED",
            title="Attachment batch file limit exceeded",
            detail="The attachment batch contains too many files.",
        )
    prepared: list[PreparedAttachment] = []
    batch_size = 0
    for file in files:
        content = await _read_upload_with_limit(file, settings.max_upload_bytes)
        batch_size += len(content)
        if batch_size > settings.max_upload_batch_bytes:
            raise V2ApiError(
                status=413,
                code="ATTACHMENT_BATCH_SIZE_EXCEEDED",
                title="Attachment batch size exceeded",
                detail="The attachment batch exceeds the configured size limit.",
            )
        prepared.append(
            PreparedAttachment(
                filename=file.filename or "upload.bin",
                media_type=file.content_type or "application/octet-stream",
                content=content,
            )
        )
    return await run_in_threadpool(
        _adapter(request).upload_attachments,
        case_id,
        prepared,
        attachment_type,
        doctor,
    )


@router.post(
    "/cases/{case_id}/analyses",
    response_model=OperationResponse,
    status_code=202,
    responses=documented_problem_responses(401, 403, 404, 409, 422, 500),
)
def start_analysis(
    case_id: str,
    payload: StartAnalysisRequest,
    request: Request,
    response: Response,
    doctor: Any = Depends(_require_external_doctor),
) -> OperationResponse:
    operation = _adapter(request).start_analysis(case_id, payload, doctor)
    response.headers["Location"] = f"/api/v2/operations/{operation.operation_id}"
    return operation


@router.get(
    "/operations/{operation_id}",
    response_model=OperationResponse,
    responses=documented_problem_responses(401, 403, 404, 422, 500),
)
def get_operation(
    operation_id: str,
    request: Request,
    doctor: Any = Depends(_require_external_doctor),
) -> OperationResponse:
    return _adapter(request).get_operation(operation_id, doctor)


@router.get(
    "/cases/{case_id}/analyses/latest",
    response_model=AnalysisResponse,
    responses=documented_problem_responses(401, 403, 404, 422, 500),
)
def get_latest_analysis(
    case_id: str,
    request: Request,
    doctor: Any = Depends(_require_external_doctor),
) -> AnalysisResponse:
    return _adapter(request).get_latest_analysis(case_id, doctor)


@router.post(
    "/cases/{case_id}/analyses/{analysis_id}/reviews",
    response_model=OperationResponse,
    status_code=202,
    responses=documented_problem_responses(401, 403, 404, 409, 422, 500),
)
def submit_review(
    case_id: str,
    analysis_id: str,
    payload: ReviewSubmitRequest,
    request: Request,
    response: Response,
    doctor: Any = Depends(_require_external_doctor),
) -> OperationResponse:
    operation = _adapter(request).submit_review(case_id, analysis_id, payload, doctor)
    response.headers["Location"] = f"/api/v2/operations/{operation.operation_id}"
    return operation


@router.post(
    "/cases/{case_id}/analyses/{analysis_id}/draft-generation:retry",
    response_model=OperationResponse,
    status_code=202,
    responses=documented_problem_responses(401, 403, 404, 409, 422, 500),
)
def retry_draft_generation(
    case_id: str,
    analysis_id: str,
    request: Request,
    response: Response,
    doctor: Any = Depends(_require_external_doctor),
) -> OperationResponse:
    operation = _adapter(request).retry_draft_generation(case_id, analysis_id, doctor)
    response.headers["Location"] = f"/api/v2/operations/{operation.operation_id}"
    return operation


@router.get(
    "/drafts/{draft_id}",
    response_model=DraftResponse,
    responses=documented_problem_responses(401, 403, 404, 422, 500),
)
def get_draft(
    draft_id: str,
    request: Request,
    doctor: Any = Depends(_require_external_doctor),
) -> DraftResponse:
    return _adapter(request).get_draft(draft_id, doctor)


@router.post(
    "/drafts/{draft_id}/approval",
    response_model=ApprovalResponse,
    responses=documented_problem_responses(401, 403, 404, 409, 422, 500),
)
def approve_draft(
    draft_id: str,
    payload: ApprovalRequest,
    request: Request,
    doctor: Any = Depends(_require_external_doctor),
) -> ApprovalResponse:
    return _adapter(request).approve_draft(draft_id, payload, doctor)


@router.get(
    "/drafts/{draft_id}/report",
    response_model=ReportResponse,
    responses=documented_problem_responses(401, 403, 404, 409, 422, 500),
)
def get_report(
    draft_id: str,
    request: Request,
    doctor: Any = Depends(_require_external_doctor),
) -> ReportResponse:
    return _adapter(request).get_report(draft_id, doctor)


@router.get(
    "/drafts/{draft_id}/report.pdf",
    response_class=FileResponse,
    responses={
        200: {
            "description": "Published PDF report",
            "content": {"application/pdf": {}},
        },
        **documented_problem_responses(401, 403, 404, 409, 422, 500),
    },
)
def download_report_pdf(
    draft_id: str,
    request: Request,
    doctor: Any = Depends(_require_external_doctor),
) -> FileResponse:
    pdf_path, filename = _adapter(request).ensure_pdf(draft_id, doctor)
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=filename,
    )
