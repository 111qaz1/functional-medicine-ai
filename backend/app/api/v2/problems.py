from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.v2.schemas import ProblemDetails, ValidationIssue


logger = logging.getLogger(__name__)


class V2ApiError(Exception):
    def __init__(self, *, status: int, code: str, title: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


_HTTP_DEFAULTS: dict[int, tuple[str, str]] = {
    400: ("BAD_REQUEST", "Bad request"),
    401: ("AUTHENTICATION_REQUIRED", "Authentication required"),
    403: ("FORBIDDEN", "Forbidden"),
    404: ("RESOURCE_NOT_FOUND", "Resource not found"),
    409: ("WORKFLOW_CONFLICT", "Workflow conflict"),
    413: ("PAYLOAD_TOO_LARGE", "Payload too large"),
    422: ("REQUEST_VALIDATION_FAILED", "Request validation failed"),
    500: ("INTERNAL_SERVER_ERROR", "Internal server error"),
    503: ("SERVICE_UNAVAILABLE", "Service unavailable"),
}


def _problem_type(code: str) -> str:
    return f"urn:fm-ai:problem:{code.lower().replace('_', '-')}"


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    errors: list[ValidationIssue] | None = None,
) -> JSONResponse:
    problem = ProblemDetails(
        type=_problem_type(code),
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
        code=code,
        errors=list(errors or []),
    )
    return JSONResponse(
        status_code=status,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
    )


class ProblemDetailsRoute(APIRoute):
    """Confine RFC-style error handling to the v2 router."""

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                return await original_route_handler(request)
            except RequestValidationError as exc:
                issues = [
                    ValidationIssue(
                        location=list(error.get("loc", ())),
                        message=str(error.get("msg", "Invalid value")),
                        error_type=str(error.get("type", "validation_error")),
                    )
                    for error in exc.errors()
                ]
                return problem_response(
                    request,
                    status=422,
                    code="REQUEST_VALIDATION_FAILED",
                    title="Request validation failed",
                    detail="The request does not match the API contract.",
                    errors=issues,
                )
            except V2ApiError as exc:
                return problem_response(
                    request,
                    status=exc.status,
                    code=exc.code,
                    title=exc.title,
                    detail=exc.detail,
                )
            except HTTPException as exc:
                code, title = _HTTP_DEFAULTS.get(
                    exc.status_code,
                    ("HTTP_ERROR", "HTTP error"),
                )
                detail = exc.detail if isinstance(exc.detail, str) else title
                return problem_response(
                    request,
                    status=exc.status_code,
                    code=code,
                    title=title,
                    detail=detail,
                )
            except Exception:
                logger.exception(
                    "Unhandled v2 API error method=%s path=%s",
                    request.method,
                    request.url.path,
                )
                return problem_response(
                    request,
                    status=500,
                    code="INTERNAL_SERVER_ERROR",
                    title="Internal server error",
                    detail="The server could not complete the request.",
                )

        return custom_route_handler


def documented_problem_responses(*statuses: int) -> dict[int, dict[str, Any]]:
    responses: dict[int, dict[str, Any]] = {}
    for status in statuses:
        _, title = _HTTP_DEFAULTS.get(status, ("HTTP_ERROR", "HTTP error"))
        responses[status] = {
            "description": title,
            "content": {
                "application/problem+json": {
                    "schema": ProblemDetails.model_json_schema(),
                }
            },
        }
    return responses
