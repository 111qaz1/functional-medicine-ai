from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.bootstrap import build_container
from app.core.settings import AppSettings
from app.main import create_app
from app.providers.base import OCRExtraction


class ClinicalSummaryImportApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
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
        self.container = build_container(settings)
        self.app = create_app()
        self.app.state.container = self.container
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_imports_summary_image_and_merges_text_into_case(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="图片总结案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.case_service.update_clinical_summary(
            case.id,
            clinical_summary_text="既有总结：疲劳明显。",
            actor_id="unit-test",
        )

        self.container.parsing_service.extract_text = lambda **_: OCRExtraction(
            text="健康评估报告总结\n细胞能量生成反应不佳\n所需要的营养素\n肉碱(Carnitine)\nB1(硫胺素, Thiamine)\nNO:CM202406130065",
            confidence=0.84,
        )

        response = self.client.post(
            f"/cases/{case.id}/clinical-summary-image",
            files={"file": ("summary.png", b"fake-image", "image/png")},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["filename"], "summary.png")
        self.assertGreater(payload["confidence"], 0.8)
        self.assertIn("细胞能量生成反应不佳", payload["extracted_text"])
        self.assertNotIn("NO:CM202406130065", payload["extracted_text"])
        merged_text = payload["case_detail"]["case"]["clinical_summary_text"]
        self.assertIn("既有总结：疲劳明显。", merged_text)
        self.assertIn("所需要的营养素", merged_text)
        self.assertIn("肉碱(Carnitine)", merged_text)

    def test_rejects_non_image_summary_upload(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="格式校验案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )

        response = self.client.post(
            f"/cases/{case.id}/clinical-summary-image",
            files={"file": ("summary.txt", b"not-an-image", "text/plain")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("只支持", response.text)

    def test_surfaces_ocr_provider_error_for_summary_image(self) -> None:
        case = self.container.case_service.create_case(
            customer_name="OCR配置错误案例",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        self.container.parsing_service.extract_text = lambda **_: OCRExtraction(
            text="",
            confidence=0.0,
            error_message="图片 OCR 认证失败：API Key 无效、过期或无权限，请在大模型配置中更新后重试。",
        )

        response = self.client.post(
            f"/cases/{case.id}/clinical-summary-image",
            files={"file": ("summary.png", b"fake-image", "image/png")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("图片 OCR 认证失败", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
