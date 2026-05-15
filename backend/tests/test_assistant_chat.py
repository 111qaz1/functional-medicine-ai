from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.bootstrap import build_container
from app.core.settings import AppSettings
from app.domain.models import Questionnaire, UploadedFile


class AssistantChatServiceTests(unittest.TestCase):
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
            report_reference_path=root / "0316测试报告1.pdf",
        )
        self.container = build_container(self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _prepare_case(self):
        report_text = "25-OH维生素D 18 ng/mL 30-100\n空腹血糖 6.2 mmol/L 3.9-5.6\nhs-CRP 4.2 mg/L 0-3"
        questionnaire = Questionnaire(
            age=34,
            sex="female",
            symptoms=["疲劳", "便秘"],
            known_conditions=[],
            medications=[],
            allergies=[],
            goals=["血糖平衡", "免疫支持", "睡眠恢复"],
            sleep_hours=5.5,
            sleep_quality="差",
            bowel_habits="便秘",
            stress_level="high",
        )
        case = self.container.case_service.create_case(
            customer_name="聊天测试用户",
            consultant_id="nutrition-team",
            notes=None,
            consent=None,
        )
        uploaded_file = UploadedFile(
            id="file_demo",
            case_id=case.id,
            filename="report.txt",
            content_type="text/plain",
            size_bytes=len(report_text.encode("utf-8")),
            storage_uri="memory://report.txt",
        )
        self.container.case_service.add_uploaded_file(case.id, uploaded_file)
        extraction, lab_items = self.container.parsing_service.parse(
            filename="report.txt",
            content_type="text/plain",
            content=report_text.encode("utf-8"),
        )
        self.container.case_service.attach_parse_results(
            case.id,
            uploaded_file.id,
            extracted_text=extraction.text,
            parse_confidence=extraction.confidence,
            source_spans=extraction.spans,
            lab_items=lab_items,
        )
        self.container.case_service.review_parsing(
            case.id,
            reviewer_id="reviewer-01",
            file_updates=[{"file_id": uploaded_file.id, "corrected_text": extraction.text, "missing_fields": []}],
            normalized_lab_items=lab_items,
            missing_fields=[],
            review_notes="assistant-test",
        )
        self.container.case_service.submit_questionnaire(case.id, questionnaire)
        draft = self.container.recommendation_service.generate(case.id, requested_by="unit-test")
        return case.id, draft

    def test_local_assistant_explains_current_recommendation(self) -> None:
        case_id, draft = self._prepare_case()

        result = self.container.assistant_chat_service.reply(
            case_id=case_id,
            user_message="为什么当前这样推荐？",
            history=[],
        )

        self.assertEqual(result.mode, "local")
        self.assertTrue(draft.recommended_skus)
        self.assertIn(draft.recommended_skus[0].display_name, result.reply)

    def test_local_assistant_no_longer_returns_fixed_placeholder_only(self) -> None:
        case_id, _ = self._prepare_case()

        result = self.container.assistant_chat_service.reply(
            case_id=case_id,
            user_message="帮我看看这个病例",
            history=[],
        )

        self.assertEqual(result.mode, "local")
        self.assertNotIn("我可以在这个病例里做三类事", result.reply)
        self.assertTrue("指标" in result.reply or "草案" in result.reply or "推荐" in result.reply)


if __name__ == "__main__":
    unittest.main()
