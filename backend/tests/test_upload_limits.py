from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.bootstrap import build_container
from app.core.settings import AppSettings
from app.main import create_app
from app.services.document_intake import DocumentIntakeService


def _pdf_with_pages(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _pptx_with_text_slides() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                   xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody>
                <a:p><a:r><a:t>第一页检查结果</a:t></a:r></a:p>
              </p:txBody></p:sp></p:spTree></p:cSld>
            </p:sld>
            """,
        )
        archive.writestr(
            "ppt/slides/slide2.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                   xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody>
                <a:p><a:r><a:t>第二页病例总结</a:t></a:r></a:p>
              </p:txBody></p:sp></p:spTree></p:cSld>
            </p:sld>
            """,
        )
    return buffer.getvalue()


class PdfUploadLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "功能医学相关资料").mkdir(parents=True, exist_ok=True)
        self.settings = AppSettings(
            project_root=root,
            data_dir=Path(__file__).resolve().parents[1] / "app" / "data",
            runtime_dir=root / ".runtime",
            upload_dir=root / ".runtime" / "uploads",
            report_export_dir=root / ".runtime" / "reports",
            sqlite_path=root / ".runtime" / "test.sqlite3",
            knowledge_root=root / "功能医学相关资料",
            report_reference_path=root / "report-reference.pdf",
            max_pdf_pages=50,
        )
        self.container = build_container(self.settings)
        self.app = create_app()
        self.app.state.container = self.container
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_document_intake_accepts_50_pages_and_rejects_51_with_count(self) -> None:
        service = DocumentIntakeService(
            max_upload_bytes=50 * 1024 * 1024,
            max_pdf_pages=50,
        )

        accepted = service.preflight(
            filename="accepted.pdf",
            content_type="application/pdf",
            content=_pdf_with_pages(50),
        )
        rejected = service.preflight(
            filename="rejected.pdf",
            content_type="application/pdf",
            content=_pdf_with_pages(51),
        )

        self.assertIsNone(accepted.validation_error)
        self.assertEqual(accepted.page_count, 50)
        self.assertEqual(rejected.page_count, 51)
        self.assertEqual(
            rejected.validation_error,
            "PDF 共 51 页，超过单个 PDF 最多 50 页的限制，请拆分为每份不超过 50 页后重新上传。",
        )

    def test_document_intake_extracts_pptx_text_by_slide(self) -> None:
        service = DocumentIntakeService(
            max_upload_bytes=50 * 1024 * 1024,
            max_pdf_pages=50,
        )

        result = service.preflight(
            filename="case-deck.pptx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
            content=_pptx_with_text_slides(),
        )

        self.assertIsNone(result.validation_error)
        self.assertEqual(result.page_count, 2)
        self.assertEqual(
            [(page.page, page.text) for page in result.page_texts],
            [(1, "第一页检查结果"), (2, "第二页病例总结")],
        )
        self.assertIn("第一页检查结果", result.extracted_text)
        self.assertIn("第二页病例总结", result.extracted_text)

    def test_internal_upload_rejects_without_file_record_or_storage_write(self) -> None:
        created = self.client.post(
            "/cases",
            json={"customer_name": "PDF页数限制", "workspace_scope": "public"},
        )
        case_id = created.json()["case"]["id"]

        response = self.client.post(
            f"/cases/{case_id}/files",
            files={"file": ("too-long.pdf", _pdf_with_pages(51), "application/pdf")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"],
            "PDF 共 51 页，超过单个 PDF 最多 50 页的限制，请拆分为每份不超过 50 页后重新上传。",
        )
        case = self.container.case_service.get_case(case_id)
        self.assertEqual(case.files, [])
        stored_files = (
            list(self.settings.upload_dir.iterdir())
            if self.settings.upload_dir.exists()
            else []
        )
        self.assertEqual(stored_files, [])

    def test_internal_msq_import_rejects_without_questionnaire_update(self) -> None:
        created = self.client.post(
            "/cases",
            json={"customer_name": "MSQ PDF页数限制", "workspace_scope": "public"},
        )
        case_id = created.json()["case"]["id"]

        response = self.client.post(
            f"/cases/{case_id}/questionnaire-file",
            files={
                "file": (
                    "too-long-msq.pdf",
                    _pdf_with_pages(51),
                    "application/pdf",
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"],
            "PDF 共 51 页，超过单个 PDF 最多 50 页的限制，请拆分为每份不超过 50 页后重新上传。",
        )
        case = self.container.case_service.get_case(case_id)
        self.assertIsNone(case.questionnaire)
        self.assertEqual(case.files, [])

    def test_internal_upload_skips_duplicate_content(self) -> None:
        created = self.client.post(
            "/cases",
            json={"customer_name": "重复文件保护", "workspace_scope": "public"},
        )
        case_id = created.json()["case"]["id"]
        content = _pdf_with_pages(1)

        first = self.client.post(
            f"/cases/{case_id}/files",
            files={"file": ("first.pdf", content, "application/pdf")},
        )
        repeated = self.client.post(
            f"/cases/{case_id}/files",
            files={"file": ("repeated-name.pdf", content, "application/pdf")},
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(len(repeated.json()["case"]["files"]), 1)
        self.assertIn("已跳过重复上传", repeated.json()["operation"]["message"])
        self.assertEqual(len(self.container.case_service.get_case(case_id).files), 1)


if __name__ == "__main__":
    unittest.main()
