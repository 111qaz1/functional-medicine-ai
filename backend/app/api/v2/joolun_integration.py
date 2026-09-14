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
)
from app.services.joolun_embed import JoolunEmbedError


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
    if not token or request.app.state.container.auth_service.get_doctor_for_session(token) is None:
        raise V2ApiError(
            status=401,
            code="AUTHENTICATION_REQUIRED",
            title="Authentication required",
            detail="A valid embed session is required.",
        )
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
    try:
        result = _service(request).exchange_ticket(payload.ticket)
    except JoolunEmbedError as exc:
        raise _translate_error(exc) from exc
    return {
        "access_token": result["session_id"],
        "case_id": result["case_id"],
        "expires_at": result["expires_at"],
    }


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
