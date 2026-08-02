from __future__ import annotations

import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.llm_rate_limiter import LLMRateLimiter, estimate_llm_prompt_tokens
from app.domain.models import LLMRequestUsage


class LLMRateLimiterTests(unittest.TestCase):
    def test_concurrency_waiter_is_released_after_active_request_completes(self) -> None:
        limiter = LLMRateLimiter(
            max_concurrency=1,
            requests_per_minute=10,
            tokens_per_minute=10_000,
            default_completion_reservation=100,
        )
        first = limiter.acquire(
            operation="document_analysis",
            estimated_prompt_tokens=100,
        )
        acquired = threading.Event()
        second_lease = []

        def acquire_second() -> None:
            second_lease.append(
                limiter.acquire(
                    operation="document_analysis",
                    estimated_prompt_tokens=100,
                )
            )
            acquired.set()

        worker = threading.Thread(target=acquire_second)
        worker.start()
        self.assertFalse(acquired.wait(0.05))
        self.assertEqual(limiter.snapshot()["queued"], 1)

        limiter.complete(
            first,
            prompt_tokens=50,
            completion_tokens=10,
            total_tokens=60,
        )
        self.assertTrue(acquired.wait(1.0))
        limiter.complete(
            second_lease[0],
            prompt_tokens=50,
            completion_tokens=10,
            total_tokens=60,
        )
        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())

    def test_actual_usage_replaces_reservation_and_frees_tpm_capacity(self) -> None:
        limiter = LLMRateLimiter(
            max_concurrency=2,
            requests_per_minute=10,
            tokens_per_minute=100,
            default_completion_reservation=40,
        )
        first = limiter.acquire(
            operation="document_analysis",
            estimated_prompt_tokens=10,
        )
        self.assertEqual(first.reserved_tokens, 50)
        limiter.complete(
            first,
            prompt_tokens=15,
            completion_tokens=5,
            total_tokens=20,
        )

        second = limiter.acquire(
            operation="document_analysis",
            estimated_prompt_tokens=10,
        )
        self.assertEqual(limiter.snapshot()["reserved_tokens_in_window"], 70)
        limiter.complete(
            second,
            prompt_tokens=15,
            completion_tokens=5,
            total_tokens=20,
        )

    def test_history_switches_completion_reservation_to_observed_p95(self) -> None:
        old = datetime(2000, 1, 1, tzinfo=timezone.utc)
        history = [
            LLMRequestUsage(
                id=f"usage-{index}",
                request_group_id=f"group-{index}",
                attempt=1,
                operation="case_synthesis",
                schema_name="case_synthesis",
                model="kimi-k2.6",
                api_style="chat",
                status="completed",
                prompt_tokens=200 + index,
                completion_tokens=100 + index,
                cached_tokens=0,
                total_tokens=300 + index * 2,
                started_at=old,
                completed_at=old,
            )
            for index in range(5)
        ]
        limiter = LLMRateLimiter(
            max_concurrency=1,
            requests_per_minute=10,
            tokens_per_minute=100_000,
            default_completion_reservation=32_768,
            history=history,
        )

        lease = limiter.acquire(
            operation="case_synthesis",
            estimated_prompt_tokens=100,
        )

        self.assertEqual(lease.reserved_tokens, 204 + 2_048)
        limiter.complete(
            lease,
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
        )

    def test_prompt_estimator_does_not_count_base64_as_text_tokens(self) -> None:
        estimate = estimate_llm_prompt_tokens(
            instructions="分析医学资料",
            content=[
                {"type": "input_text", "text": "患者资料" * 100},
                {
                    "type": "input_image",
                    "image_url": "data:image/jpeg;base64," + "A" * 100_000,
                },
            ],
            schema={"type": "object"},
        )

        self.assertLess(estimate, 5_000)
        self.assertGreater(estimate, 2_048)


if __name__ == "__main__":
    unittest.main()
