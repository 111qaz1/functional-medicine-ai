from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.llm_rate_limiter import LLMRateLimiter
from app.core.llm_request_control import LLMRequestController, llm_request_context
from app.core.settings import AppSettings
from app.providers.local import DocumentOCRProvider, GroundedDraftComposer
from app.providers.remote import (
    OpenAICompatibleCaseAssistant,
    OpenAICompatibleGroundedComposer,
    OpenAICompatibleRagReportFusion,
)
from app.repositories.in_memory import LocalRepository


class AllLLMPathsAccountingTests(unittest.TestCase):
    def test_remaining_remote_paths_share_limiter_and_usage_recorder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = LocalRepository(root / "usage.sqlite3")
            limiter = LLMRateLimiter(
                max_concurrency=10,
                requests_per_minute=100,
                tokens_per_minute=1_000_000,
                default_completion_reservation=1_000,
            )
            controller = LLMRequestController(
                model="kimi-k2.6",
                rate_limiter=limiter,
                usage_recorder=repository.save_llm_request_usage,
            )
            request_payloads: list[dict] = []

            def handler(request: httpx.Request) -> httpx.Response:
                request_payloads.append(json.loads(request.content.decode("utf-8")))
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "{}"}}],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 20,
                            "cached_tokens": 10,
                            "total_tokens": 120,
                        },
                    },
                    request=request,
                )

            client = httpx.Client(transport=httpx.MockTransport(handler))
            settings = AppSettings(
                project_root=root,
                data_dir=root,
                runtime_dir=root,
                upload_dir=root,
                report_export_dir=root,
                sqlite_path=root / "usage.sqlite3",
                knowledge_root=root,
                report_reference_path=root / "reference.pdf",
                llm_base_url="https://api.moonshot.cn/v1",
                llm_api_key="synthetic",
                llm_model="kimi-k2.6",
                llm_api_style="chat",
            )
            try:
                with llm_request_context(
                    case_id="case-all",
                    analysis_id="analysis-all",
                    file_id="file-all",
                    draft_id="draft-all",
                ):
                    ocr = DocumentOCRProvider(
                        base_url=settings.llm_base_url,
                        api_key=settings.llm_api_key,
                        model=settings.llm_model,
                        api_style="chat",
                        http_client=client,
                        request_controller=controller,
                    )
                    ocr._extract_image_text(
                        content=b"synthetic-image",
                        content_type="image/jpeg",
                    )

                    assistant = OpenAICompatibleCaseAssistant(
                        base_url=settings.llm_base_url,
                        api_key=settings.llm_api_key,
                        model=settings.llm_model,
                        api_style="chat",
                        http_client=client,
                        request_controller=controller,
                    )
                    assistant._call_remote_model(
                        {"case_snapshot": {}, "user_message": "synthetic"},
                        [],
                    )

                    composer = OpenAICompatibleGroundedComposer(
                        base_url=settings.llm_base_url,
                        api_key=settings.llm_api_key,
                        model=settings.llm_model,
                        fallback=GroundedDraftComposer(),
                        api_style="chat",
                        http_client=client,
                        request_controller=controller,
                    )
                    composer._call_remote_model({})

                    fusion = OpenAICompatibleRagReportFusion(
                        base_url=settings.llm_base_url,
                        api_key=settings.llm_api_key,
                        model=settings.llm_model,
                        api_style="chat",
                        http_client=client,
                        request_controller=controller,
                    )
                    fusion._call_remote_model({})

            finally:
                client.close()

            saved = repository.list_llm_request_usage(case_id="case-all")
            self.assertEqual(len(saved), 4)
            self.assertEqual(
                {item.operation for item in saved},
                {
                    "document_ocr",
                    "case_assistant",
                    "grounded_composer",
                    "rag_report_fusion",
                },
            )
            self.assertTrue(all(item.total_tokens == 120 for item in saved))
            self.assertTrue(all(item.cached_tokens == 10 for item in saved))
            self.assertTrue(all(item.analysis_id == "analysis-all" for item in saved))
            self.assertTrue(all(item.draft_id == "draft-all" for item in saved))
            self.assertEqual(limiter.snapshot()["inflight"], 0)
            self.assertEqual(limiter.snapshot()["requests_in_window"], 4)
            self.assertTrue(
                all(
                    "max_tokens" not in payload
                    and "max_completion_tokens" not in payload
                    and "max_output_tokens" not in payload
                    for payload in request_payloads
                )
            )


if __name__ == "__main__":
    unittest.main()
