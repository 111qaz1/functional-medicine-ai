from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

import httpx

from app.core.llm_rate_limiter import LLMRateLimiter, estimate_llm_payload_tokens
from app.domain.models import LLMRequestUsage


logger = logging.getLogger(__name__)

_REQUEST_CONTEXT: ContextVar[dict[str, str | None] | None] = ContextVar(
    "llm_request_context",
    default=None,
)


@contextmanager
def llm_request_context(**values: str | None) -> Iterator[None]:
    current = _REQUEST_CONTEXT.get() or {}
    token = _REQUEST_CONTEXT.set({**current, **values})
    try:
        yield
    finally:
        _REQUEST_CONTEXT.reset(token)


def current_llm_request_context() -> dict[str, str | None]:
    return _REQUEST_CONTEXT.get() or {}


class LLMRequestController:
    """Shared admission and privacy-safe accounting for one provider request."""

    def __init__(
        self,
        *,
        model: str,
        rate_limiter: LLMRateLimiter,
        usage_recorder: Callable[[LLMRequestUsage], Any],
    ) -> None:
        self.model = model
        self.rate_limiter = rate_limiter
        self.usage_recorder = usage_recorder

    def post(
        self,
        *,
        operation: str,
        schema_name: str,
        api_style: str,
        request_payload: dict[str, Any],
        send: Callable[[], httpx.Response],
        request_group_id: str | None = None,
        attempt: int = 1,
    ) -> httpx.Response:
        lease = self.rate_limiter.acquire(
            operation=operation,
            estimated_prompt_tokens=estimate_llm_payload_tokens(request_payload),
        )
        started_at = datetime.now(timezone.utc)
        response: httpx.Response | None = None
        response_payload: dict[str, Any] | None = None
        error_code: str | None = None
        try:
            response = send()
            response_payload = self.safe_response_json(response)
            if not self._response_is_success(response):
                error_code = f"http_{getattr(response, 'status_code', 500)}"
            return response
        except Exception as exc:
            error_code = exc.__class__.__name__
            raise
        finally:
            token_usage = self.extract_token_usage(response_payload)
            self.rate_limiter.complete(
                lease,
                prompt_tokens=token_usage["prompt_tokens"],
                completion_tokens=token_usage["completion_tokens"],
                total_tokens=token_usage["total_tokens"],
            )
            self._record(
                request_group_id=request_group_id or f"llm_group_{uuid.uuid4().hex}",
                attempt=attempt,
                operation=operation,
                schema_name=schema_name,
                api_style=api_style,
                lease=lease,
                started_at=started_at,
                response=response,
                token_usage=token_usage,
                error_code=error_code,
            )

    def _record(
        self,
        *,
        request_group_id: str,
        attempt: int,
        operation: str,
        schema_name: str,
        api_style: str,
        lease,
        started_at: datetime,
        response: httpx.Response | None,
        token_usage: dict[str, int | None],
        error_code: str | None,
    ) -> None:
        completed_at = datetime.now(timezone.utc)
        context = current_llm_request_context()
        if response is None or not self._response_is_success(response):
            status = "failed"
        elif token_usage["total_tokens"] is None:
            status = "completed_without_usage"
        else:
            status = "completed"
        usage = LLMRequestUsage(
            id=f"llm_usage_{uuid.uuid4().hex}",
            request_group_id=request_group_id,
            attempt=attempt,
            case_id=context.get("case_id"),
            analysis_id=context.get("analysis_id"),
            file_id=context.get("file_id"),
            draft_id=context.get("draft_id"),
            operation=operation,
            schema_name=schema_name,
            model=self.model,
            api_style=api_style,
            status=status,
            http_status=(
                getattr(response, "status_code", 200)
                if response is not None
                else None
            ),
            error_code=error_code,
            prompt_tokens=token_usage["prompt_tokens"],
            completion_tokens=token_usage["completion_tokens"],
            cached_tokens=token_usage["cached_tokens"],
            total_tokens=token_usage["total_tokens"],
            reserved_tokens=lease.reserved_tokens,
            queue_duration_ms=lease.queue_duration_ms,
            request_duration_ms=max(
                0,
                int((completed_at - started_at).total_seconds() * 1000),
            ),
            started_at=started_at,
            completed_at=completed_at,
        )
        try:
            self.usage_recorder(usage)
        except Exception:
            logger.exception("Failed to persist LLM request usage id=%s", usage.id)

    @staticmethod
    def safe_response_json(response: httpx.Response | None) -> dict[str, Any] | None:
        if response is None:
            return None
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _response_is_success(response: httpx.Response) -> bool:
        value = getattr(response, "is_success", None)
        if isinstance(value, bool):
            return value
        status_code = getattr(response, "status_code", 200)
        return 200 <= int(status_code) < 400

    @classmethod
    def extract_token_usage(
        cls,
        response_payload: dict[str, Any] | None,
    ) -> dict[str, int | None]:
        usage = response_payload.get("usage") if response_payload else None
        if not isinstance(usage, dict):
            return {
                "prompt_tokens": None,
                "completion_tokens": None,
                "cached_tokens": None,
                "total_tokens": None,
            }
        prompt_tokens = cls._nonnegative_int(
            usage.get("prompt_tokens", usage.get("input_tokens"))
        )
        completion_tokens = cls._nonnegative_int(
            usage.get("completion_tokens", usage.get("output_tokens"))
        )
        total_tokens = cls._nonnegative_int(usage.get("total_tokens"))
        details = usage.get("prompt_tokens_details")
        if not isinstance(details, dict):
            details = usage.get("input_tokens_details")
        cached_tokens = cls._nonnegative_int(usage.get("cached_tokens"))
        if cached_tokens is None and isinstance(details, dict):
            cached_tokens = cls._nonnegative_int(details.get("cached_tokens"))
        if cached_tokens is None:
            cached_tokens = 0
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value >= 0:
            return value
        return None
