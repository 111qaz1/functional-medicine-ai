from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.bootstrap import build_container
from app.core.llm_rate_limiter import LLMRateLimiter
from app.core.settings import AppSettings
from app.domain.models import LLMRequestUsage
from app.main import create_app
from app.repositories.in_memory import LocalRepository
from app.services.case_analysis import OpenAICompatibleCaseAnalysisProvider


SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
    "additionalProperties": False,
}


class LLMUsageCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = LocalRepository(Path(self.temp_dir.name) / "usage.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_chat_usage_is_persisted_with_case_context(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"status":"connected"}'}}],
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 30,
                        "total_tokens": 150,
                        "prompt_tokens_details": {"cached_tokens": 20},
                    },
                },
                request=request,
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleCaseAnalysisProvider(
            base_url="https://api.moonshot.cn/v1",
            api_key="synthetic",
            model="kimi-k2.6",
            api_style="chat",
            http_client=client,
            usage_recorder=self.repository.save_llm_request_usage,
            rate_limiter=LLMRateLimiter(
                max_concurrency=2,
                requests_per_minute=10,
                tokens_per_minute=100_000,
                default_completion_reservation=1_000,
            ),
        )
        try:
            with provider.usage_context(
                case_id="case-1",
                analysis_id="analysis-1",
                file_id="file-1",
            ):
                result = provider._call_json(
                    instructions="synthetic",
                    content=[{"type": "input_text", "text": "synthetic"}],
                    schema=SCHEMA,
                    schema_name="document_analysis",
                )
        finally:
            client.close()

        self.assertEqual(result, {"status": "connected"})
        saved = self.repository.list_llm_request_usage(analysis_id="analysis-1")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].case_id, "case-1")
        self.assertEqual(saved[0].file_id, "file-1")
        self.assertEqual(saved[0].operation, "document_analysis")
        self.assertEqual(saved[0].prompt_tokens, 120)
        self.assertEqual(saved[0].completion_tokens, 30)
        self.assertEqual(saved[0].cached_tokens, 20)
        self.assertEqual(saved[0].total_tokens, 150)
        self.assertGreater(saved[0].reserved_tokens, 1_000)
        self.assertGreaterEqual(saved[0].queue_duration_ms, 0)
        self.assertEqual(saved[0].status, "completed")

    def test_retry_attempts_and_responses_usage_are_recorded_separately(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(503, json={"error": "busy"}, request=request)
            return httpx.Response(
                200,
                json={
                    "output_text": '{"status":"connected"}',
                    "usage": {
                        "input_tokens": 300,
                        "output_tokens": 40,
                        "total_tokens": 340,
                        "input_tokens_details": {"cached_tokens": 100},
                    },
                },
                request=request,
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleCaseAnalysisProvider(
            base_url="https://synthetic.invalid/v1",
            api_key="synthetic",
            model="kimi-k2.6",
            api_style="responses",
            http_client=client,
            retry_attempts=1,
            retry_base_delay_seconds=0,
            retry_max_delay_seconds=0,
            usage_recorder=self.repository.save_llm_request_usage,
        )
        try:
            with provider.usage_context(
                case_id="case-2",
                analysis_id="analysis-2",
                operation="final_case_synthesis",
            ):
                result = provider._call_json(
                    instructions="synthetic",
                    content=[{"type": "input_text", "text": "synthetic"}],
                    schema=SCHEMA,
                    schema_name="case_synthesis",
                )
        finally:
            client.close()

        self.assertEqual(result, {"status": "connected"})
        saved = list(reversed(self.repository.list_llm_request_usage(analysis_id="analysis-2")))
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0].status, "failed")
        self.assertEqual(saved[0].http_status, 503)
        self.assertEqual(saved[1].status, "completed")
        self.assertEqual(saved[1].attempt, 2)
        self.assertEqual(saved[1].operation, "final_case_synthesis")
        self.assertEqual(saved[1].cached_tokens, 100)
        self.assertEqual(saved[1].total_tokens, 340)
        self.assertEqual(saved[0].request_group_id, saved[1].request_group_id)

        summary = self.repository.summarize_llm_request_usage(
            since=datetime(2000, 1, 1, tzinfo=timezone.utc),
            analysis_id="analysis-2",
        )
        self.assertEqual(summary["request_count"], 2)
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["total_tokens"], 340)


class LLMUsageAPITests(unittest.TestCase):
    def test_usage_endpoints_are_admin_only_and_return_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "knowledge").mkdir(parents=True, exist_ok=True)
            settings = AppSettings(
                project_root=root,
                data_dir=Path(__file__).resolve().parents[1] / "app" / "data",
                runtime_dir=root / ".runtime",
                upload_dir=root / ".runtime" / "uploads",
                report_export_dir=root / ".runtime" / "reports",
                sqlite_path=root / ".runtime" / "test.sqlite3",
                knowledge_root=root / "knowledge",
                report_reference_path=root / "report-reference.pdf",
            )
            app = create_app()
            app.state.container = build_container(settings)
            now = datetime.now(timezone.utc)
            app.state.container.repository.save_llm_request_usage(
                LLMRequestUsage(
                    id="usage-api-1",
                    request_group_id="group-api-1",
                    attempt=1,
                    case_id="case-api-1",
                    analysis_id="analysis-api-1",
                    operation="document_analysis",
                    schema_name="document_analysis",
                    model="kimi-k2.6",
                    api_style="chat",
                    status="completed",
                    http_status=200,
                    prompt_tokens=80,
                    completion_tokens=20,
                    cached_tokens=10,
                    total_tokens=100,
                    started_at=now,
                    completed_at=now,
                )
            )
            with TestClient(app) as anonymous:
                self.assertEqual(anonymous.get("/system/llm-usage").status_code, 401)
                registered = anonymous.post(
                    "/auth/register",
                    json={
                        "username": "admin",
                        "password": "secret123",
                        "display_name": "Admin",
                    },
                )
                self.assertEqual(registered.status_code, 200, registered.text)
                recent = anonymous.get(
                    "/system/llm-usage",
                    params={"analysis_id": "analysis-api-1"},
                )
                summary = anonymous.get(
                    "/system/llm-usage/summary",
                    params={"analysis_id": "analysis-api-1", "window_minutes": 60},
                )
                limiter_status = anonymous.get("/system/llm-rate-limit")

            self.assertEqual(recent.status_code, 200, recent.text)
            self.assertEqual(len(recent.json()["items"]), 1)
            self.assertEqual(recent.json()["items"][0]["prompt_tokens"], 80)
            self.assertEqual(summary.status_code, 200, summary.text)
            self.assertEqual(summary.json()["request_count"], 1)
            self.assertEqual(summary.json()["total_tokens"], 100)
            self.assertEqual(limiter_status.status_code, 200, limiter_status.text)
            self.assertEqual(limiter_status.json()["max_concurrency"], 90)
            self.assertEqual(limiter_status.json()["tpm_soft_limit"], 2_850_000)


if __name__ == "__main__":
    unittest.main()
