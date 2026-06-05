from __future__ import annotations

import unittest

import httpx

from app.providers.remote import OpenAICompatibleRagReportFusion, RemoteLLMHTTPStatusError


class RemoteRagFusionProviderTests(unittest.TestCase):
    def test_http_status_error_is_reduced_to_safe_metadata(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "code": "SetLimitExceeded",
                        "message": "account 123 reached a limit with request id req_456",
                    }
                },
            )

        provider = OpenAICompatibleRagReportFusion(
            base_url="https://example.test/api/v3",
            api_key="secret-key",
            model="test-model",
            api_style="chat",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        target_sections = {title: ["original item"] for title in provider.allowed_sections}
        rag_context = {title: [] for title in provider.allowed_sections}

        with self.assertRaises(RemoteLLMHTTPStatusError) as raised:
            provider.fuse_report_sections(
                report_text="# report",
                target_sections=target_sections,
                rag_context=rag_context,
                case_context={},
            )

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.error_code, "SetLimitExceeded")
        self.assertNotIn("account 123", str(raised.exception))
        self.assertNotIn("req_456", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
