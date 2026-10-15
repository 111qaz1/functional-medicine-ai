from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.v2.problems import ProblemDetailsRoute, V2ApiError, documented_problem_responses
from app.api.v2.schemas import (
    JoolunApprovedRecommendationsResponse,
    JoolunEmbedContextResponse,
    JoolunEmbedSessionRequest,
    JoolunEmbedSessionResponse,
    JoolunEmbedTicketExchangeRequest,
    JoolunEmbedTicketExchangeResponse,
    JoolunTokenSessionRequest,
)
from app.services.joolun_embed import JoolunEmbedError
from app.api.v2.joolun_auth import authorize_request


router = APIRouter(
    prefix="/api/v2/integrations/joolun",
    tags=["joolun-embed"],
    route_class=ProblemDetailsRoute,
)
bearer_scheme = HTTPBearer(auto_error=False)


def _service(request: Request):
    return request.app.state.container.joolun_embed_service


def _translate_error(exc: JoolunEmbedError) -> V2ApiError:
    status = {
        "EMBED_NOT_CONFIGURED": 503,
        "EMBED_SIGNATURE_EXPIRED": 401,
        "EMBED_SIGNATURE_INVALID": 401,
        "EMBED_NONCE_REPLAYED": 409,
        "EMBED_PARENT_ORIGIN_REJECTED": 403,
        "EMBED_DOCTOR_DISABLED": 403,
        "EMBED_ENCOUNTER_IDENTITY_CONFLICT": 409,
        "EMBED_TICKET_INVALID": 401,
        "EMBED_SESSION_REQUIRED": 401,
        "EMBED_APPROVED_DRAFT_NOT_FOUND": 409,
        "EMBED_SKU_MAPPING_INVALID": 503,
    }.get(exc.code, 400)
    return V2ApiError(
        status=status,
        code=exc.code,
        title="Joolun embed request rejected",
        detail=str(exc),
    )


def _session_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    token = credentials.credentials if credentials else None
    doctor = request.app.state.container.auth_service.get_doctor_for_session(token)
    if not token or doctor is None:
        raise V2ApiError(
            status=401,
            code="AUTHENTICATION_REQUIRED",
            title="Authentication required",
            detail="A valid embed session is required.",
        )
    authorize_request(request, token, doctor)
    try:
        _service(request).get_context(token)
    except JoolunEmbedError as exc:
        raise _translate_error(exc) from exc
    return token


@router.post(
    "/embed-sessions",
    response_model=JoolunEmbedSessionResponse,
    status_code=201,
    responses=documented_problem_responses(401, 403, 409, 422, 500, 503),
)
def create_embed_session(payload: JoolunEmbedSessionRequest, request: Request):
    if request.app.state.container.settings.joolun_auth_mode == "java_token":
        raise V2ApiError(status=410, code="LEGACY_EMBED_DISABLED", title="Legacy authentication disabled", detail="Use the partner login session entry.")
    try:
        return _service(request).create_embed_session(payload)
    except JoolunEmbedError as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/embed-tickets:exchange",
    response_model=JoolunEmbedTicketExchangeResponse,
    responses=documented_problem_responses(401, 422, 500, 503),
)
def exchange_embed_ticket(payload: JoolunEmbedTicketExchangeRequest, request: Request):
    if request.app.state.container.settings.joolun_auth_mode == "java_token":
        raise V2ApiError(status=410, code="LEGACY_EMBED_DISABLED", title="Legacy authentication disabled", detail="Use the partner login session entry.")
    try:
        result = _service(request).exchange_ticket(payload.ticket)
    except JoolunEmbedError as exc:
        raise _translate_error(exc) from exc
    return {
        "access_token": result["session_id"],
        "case_id": result["case_id"],
        "expires_at": result["expires_at"],
    }


@router.post("/token-sessions", status_code=201)
def create_token_session(payload: JoolunTokenSessionRequest, request: Request,
                         credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if request.headers.get("X-FM-Session-BFF") != "1":
        raise V2ApiError(status=404, code="RESOURCE_NOT_FOUND", title="Not found", detail="Not found")
    return request.app.state.container.joolun_java_auth.create(
        payload, credentials.credentials if credentials else None, request.cookies.get("fm_session"))


@router.get("/token-sessions")
def current_token_session(request: Request, session_id: str = Depends(_session_token)):
    if request.headers.get("X-FM-Session-BFF") != "1":
        raise V2ApiError(status=404, code="RESOURCE_NOT_FOUND", title="Not found", detail="Not found")
    doctor = request.app.state.container.auth_service.get_doctor_for_session(session_id)
    return request.app.state.container.joolun_java_auth.response(session_id, doctor)


@router.get(
    "/embed-context",
    response_model=JoolunEmbedContextResponse,
    responses=documented_problem_responses(401, 422, 500),
)
def get_embed_context(request: Request, session_id: str = Depends(_session_token)):
    try:
        context = _service(request).get_context(session_id)
        return {
            "issuer": context["issuer"],
            "external_encounter_id": context["external_encounter_id"],
            "parent_origin": context["parent_origin"],
            "case_id": context["case_id"],
            "read_only": bool((request.app.state.container.repository.get_joolun_login_binding(session_id) or {}).get("read_only")),
        }
    except JoolunEmbedError as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/approved-recommendations",
    response_model=JoolunApprovedRecommendationsResponse,
    responses=documented_problem_responses(401, 409, 422, 500, 503),
)
def get_approved_recommendations(request: Request, session_id: str = Depends(_session_token)):
    try:
        return _service(request).approved_recommendations(session_id)
    except JoolunEmbedError as exc:
        raise _translate_error(exc) from exc
