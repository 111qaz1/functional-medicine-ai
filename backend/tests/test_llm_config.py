from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.bootstrap import build_container
from app.core.settings import AppSettings, LLMConfig, llm_config_validation_error, normalize_llm_api_key
from app.main import create_app


class LLMConfigValidationTests(unittest.TestCase):
    def test_normalizes_bearer_prefix(self) -> None:
        self.assertEqual(normalize_llm_api_key("Bearer sk-test-key"), "sk-test-key")

    def test_rejects_api_key_that_is_base_url(self) -> None:
        config = LLMConfig(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key="https://ark.cn-beijing.volces.com/api/v3",
            model="doubao-vision",
        )

        self.assertIn("API Key 不能填写 Base URL", llm_config_validation_error(config) or "")

    def test_update_llm_config_rejects_url_in_api_key_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "功能医学相关资料").mkdir(parents=True, exist_ok=True)
            settings = AppSettings(
                project_root=root,
                data_dir=Path(__file__).resolve().parents[1] / "app" / "data",
                runtime_dir=root / ".runtime",
                upload_dir=root / ".runtime" / "uploads",
                report_export_dir=root / ".runtime" / "reports",
                sqlite_path=root / ".runtime" / "test.sqlite3",
                knowledge_root=root / "功能医学相关资料",
                report_reference_path=root / "0316测试报告1.pdf",
            )
            app = create_app()
            app.state.container = build_container(settings)
            with TestClient(app) as client:
                response = client.put(
                    "/system/llm-config",
                    json={
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                        "api_key": "https://ark.cn-beijing.volces.com/api/v3",
                        "model": "doubao-vision",
                        "api_style": "responses",
                        "timeout_seconds": 45,
                        "temperature": 0.1,
                    },
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("API Key", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
