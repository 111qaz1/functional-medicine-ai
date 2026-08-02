from __future__ import annotations

import json
import math
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Condition
from typing import Any, Iterable

from app.domain.models import LLMRequestUsage


@dataclass
class _TokenEvent:
    timestamp: float
    amount: int
    active: bool = True


@dataclass(frozen=True)
class LLMRateLimitLease:
    operation: str
    reserved_tokens: int
    queue_duration_ms: int
    token_event: _TokenEvent


class LLMRateLimiter:
    """Process-wide FIFO admission control for an organization-level LLM quota."""

    def __init__(
        self,
        *,
        max_concurrency: int = 90,
        requests_per_minute: int = 475,
        tokens_per_minute: int = 2_850_000,
        window_seconds: float = 60.0,
        default_completion_reservation: int = 32_768,
        history: Iterable[LLMRequestUsage] = (),
    ) -> None:
        self.max_concurrency = max(1, int(max_concurrency))
        self.requests_per_minute = max(1, int(requests_per_minute))
        self.tokens_per_minute = max(1, int(tokens_per_minute))
        self.window_seconds = max(1.0, float(window_seconds))
        self.default_completion_reservation = max(
            1,
            int(default_completion_reservation),
        )
        self._condition = Condition()
        self._inflight = 0
        self._waiters: deque[object] = deque()
        self._request_times: deque[float] = deque()
        self._token_events: deque[_TokenEvent] = deque()
        self._rolling_tokens = 0
        self._prompt_history: dict[str, deque[int]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._completion_history: dict[str, deque[int]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._seed_history(history)

    def acquire(
        self,
        *,
        operation: str,
        estimated_prompt_tokens: int,
    ) -> LLMRateLimitLease:
        queued_at = time.monotonic()
        ticket = object()
        with self._condition:
            self._waiters.append(ticket)
            while True:
                now = time.monotonic()
                self._expire(now)
                reserved_tokens = self._reservation_tokens(
                    operation,
                    estimated_prompt_tokens,
                )
                is_head = bool(self._waiters and self._waiters[0] is ticket)
                has_capacity = (
                    self._inflight < self.max_concurrency
                    and len(self._request_times) < self.requests_per_minute
                    and self._rolling_tokens + reserved_tokens
                    <= self.tokens_per_minute
                )
                if is_head and has_capacity:
                    self._waiters.popleft()
                    token_event = _TokenEvent(now, reserved_tokens)
                    self._request_times.append(now)
                    self._token_events.append(token_event)
                    self._rolling_tokens += reserved_tokens
                    self._inflight += 1
                    self._condition.notify_all()
                    return LLMRateLimitLease(
                        operation=operation,
                        reserved_tokens=reserved_tokens,
                        queue_duration_ms=max(
                            0,
                            int((now - queued_at) * 1000),
                        ),
                        token_event=token_event,
                    )
                self._condition.wait(timeout=self._next_wakeup_seconds(now))

    def complete(
        self,
        lease: LLMRateLimitLease,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
    ) -> None:
        with self._condition:
            now = time.monotonic()
            self._expire(now)
            if lease.token_event.active and total_tokens is not None:
                actual_tokens = max(0, int(total_tokens))
                delta = actual_tokens - lease.token_event.amount
                lease.token_event.amount = actual_tokens
                self._rolling_tokens = max(0, self._rolling_tokens + delta)
            if prompt_tokens is not None and prompt_tokens >= 0:
                self._prompt_history[lease.operation].append(int(prompt_tokens))
            if completion_tokens is not None and completion_tokens >= 0:
                self._completion_history[lease.operation].append(
                    int(completion_tokens)
                )
            self._inflight = max(0, self._inflight - 1)
            self._condition.notify_all()

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            self._expire(time.monotonic())
            return {
                "max_concurrency": self.max_concurrency,
                "inflight": self._inflight,
                "queued": len(self._waiters),
                "rpm_soft_limit": self.requests_per_minute,
                "requests_in_window": len(self._request_times),
                "tpm_soft_limit": self.tokens_per_minute,
                "reserved_tokens_in_window": self._rolling_tokens,
                "default_completion_reservation": (
                    self.default_completion_reservation
                ),
            }

    def _reservation_tokens(
        self,
        operation: str,
        estimated_prompt_tokens: int,
    ) -> int:
        prompt_reservation = max(1, int(estimated_prompt_tokens))
        prompt_history = self._prompt_history.get(operation)
        if prompt_history and len(prompt_history) >= 5:
            prompt_reservation = max(
                prompt_reservation,
                self._percentile(prompt_history, 0.95),
            )
        completion_history = self._completion_history.get(operation)
        if completion_history and len(completion_history) >= 5:
            completion_reservation = min(
                self.default_completion_reservation,
                max(
                    2_048,
                    math.ceil(self._percentile(completion_history, 0.95) * 1.2),
                ),
            )
        else:
            completion_reservation = self.default_completion_reservation
        return min(
            self.tokens_per_minute,
            prompt_reservation + completion_reservation,
        )

    def _expire(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._request_times and self._request_times[0] <= cutoff:
            self._request_times.popleft()
        while self._token_events and self._token_events[0].timestamp <= cutoff:
            event = self._token_events.popleft()
            if event.active:
                self._rolling_tokens = max(
                    0,
                    self._rolling_tokens - event.amount,
                )
                event.active = False

    def _next_wakeup_seconds(self, now: float) -> float:
        candidates = [1.0]
        if self._request_times:
            candidates.append(
                max(0.01, self._request_times[0] + self.window_seconds - now)
            )
        if self._token_events:
            candidates.append(
                max(0.01, self._token_events[0].timestamp + self.window_seconds - now)
            )
        return max(0.01, min(candidates))

    def _seed_history(self, history: Iterable[LLMRequestUsage]) -> None:
        now_utc = datetime.now(timezone.utc)
        now_monotonic = time.monotonic()
        for item in reversed(list(history)):
            if item.prompt_tokens is not None:
                self._prompt_history[item.operation].append(item.prompt_tokens)
            if item.completion_tokens is not None:
                self._completion_history[item.operation].append(
                    item.completion_tokens
                )
            age = (now_utc - item.started_at).total_seconds()
            if age < 0 or age >= self.window_seconds:
                continue
            timestamp = now_monotonic - age
            self._request_times.append(timestamp)
            amount = item.total_tokens or 0
            if amount > 0:
                event = _TokenEvent(timestamp, amount)
                self._token_events.append(event)
                self._rolling_tokens += amount

    @staticmethod
    def _percentile(values: Iterable[int], percentile: float) -> int:
        ordered = sorted(values)
        index = max(0, math.ceil(len(ordered) * percentile) - 1)
        return int(ordered[index])


def estimate_llm_prompt_tokens(
    *,
    instructions: str,
    content: list[dict[str, Any]],
    schema: dict[str, Any],
) -> int:
    """Conservative local estimate used only for admission, never for billing."""

    text_parts = [
        instructions,
        json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
    ]
    image_count = 0
    for item in content:
        if item.get("type") == "input_image":
            image_count += 1
            continue
        value = item.get("text")
        if isinstance(value, str):
            text_parts.append(value)
    text = "\n".join(text_parts)
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
    remaining_count = max(0, len(text) - cjk_count)
    text_tokens = cjk_count + math.ceil(remaining_count / 4)
    return max(1, text_tokens + image_count * 2_048 + 256)


def estimate_llm_payload_tokens(payload: Any) -> int:
    """Estimate an arbitrary OpenAI-compatible JSON body without counting base64."""

    text_parts: list[str] = []
    image_count = 0

    def visit(value: Any) -> None:
        nonlocal image_count
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"image_url", "url"} and isinstance(item, str):
                    if item.startswith("data:image/"):
                        image_count += 1
                        continue
                visit(item)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if isinstance(value, str):
            if value.startswith("data:image/"):
                image_count += 1
            else:
                text_parts.append(value)

    visit(payload)
    text = "\n".join(text_parts)
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
    remaining_count = max(0, len(text) - cjk_count)
    return max(
        1,
        cjk_count + math.ceil(remaining_count / 4) + image_count * 2_048 + 256,
    )
