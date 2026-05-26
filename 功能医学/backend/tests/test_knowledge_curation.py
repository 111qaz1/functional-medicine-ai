from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.bootstrap import load_knowledge
from app.core.settings import AppSettings


DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "data"
CURATED_KNOWLEDGE_PATH = DATA_DIR / "knowledge_statements_tfm2010_curated.json"


class CuratedFunctionalMedicineKnowledgeTests(unittest.TestCase):
    def test_curated_knowledge_is_reviewed_and_not_source_facing(self) -> None:
        payload = json.loads(CURATED_KNOWLEDGE_PATH.read_text(encoding="utf-8-sig"))

        self.assertGreaterEqual(len(payload), 20)
        for item in payload:
            self.assertEqual(item["review_status"], "reviewed")
            self.assertEqual(item["source_doc_id"], "local_functional_medicine_curated_knowledge")
            self.assertIsNone(item.get("source_path"))
            self.assertEqual(item["source_type"], "local_text")
            self.assertLessEqual(len(item["normalized_text"]), 180)

            serialized = json.dumps(item, ensure_ascii=False)
            self.assertNotIn("Textbook Of Functional Medicine", serialized)
            self.assertNotIn("9780977371372", serialized)
            self.assertNotIn("All rights reserved", serialized)

    def test_load_knowledge_includes_supplemental_curated_statements(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = AppSettings(
                project_root=root,
                data_dir=DATA_DIR,
                runtime_dir=root / ".runtime",
                upload_dir=root / ".runtime" / "uploads",
                report_export_dir=root / ".runtime" / "reports",
                sqlite_path=root / ".runtime" / "test.sqlite3",
                knowledge_root=root / "功能医学相关资料",
                report_reference_path=root / "0316测试报告1.pdf",
            )

            statements = load_knowledge(settings)

        statement_by_id = {item.statement_id: item for item in statements}
        self.assertIn("stmt_tfm2010_functional_matrix_systems_view", statement_by_id)
        self.assertIn("stmt_tfm2010_gut_liver_axis", statement_by_id)
        self.assertIn("stmt_tfm2010_thyroid_contextual_support", statement_by_id)

        thyroid = statement_by_id["stmt_tfm2010_thyroid_contextual_support"]
        self.assertEqual(thyroid.review_status.value, "reviewed")
        self.assertIn("sku_thyroid_support", thyroid.related_skus)
        self.assertIn("thyroid_peroxidase_antibody", thyroid.related_markers)


if __name__ == "__main__":
    unittest.main()
