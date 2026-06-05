from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfReader

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.pdf_export import PdfReportExporter


class PdfReportExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.exporter = PdfReportExporter(Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_customer_catalog_maps_immune_support_to_quercetin_sequence(self) -> None:
        rows = self.exporter._nutrition_table_rows(
            [
                SimpleNamespace(
                    sku_id="sku_immune_support",
                    display_name="免疫支持（现货）",
                    dosage="每日 1 粒，餐后使用。",
                    reason="结合本次炎症和免疫状态进行支持。",
                    warnings=["抗凝药物使用者需人工复核"],
                )
            ]
        )

        self.assertEqual(rows[0]["sequence"], "23")
        self.assertEqual(rows[0]["product_name"], "槲皮素复合物")
        self.assertIn("槲皮素复合物", rows[0]["effect"])
        self.assertIn("结合本次炎症和免疫状态进行支持", rows[0]["effect"])
        self.assertEqual(rows[0]["warnings"], ["抗凝药物使用者需人工复核"])
        self.assertNotIn("?????", rows[0]["effect"])

    def test_unmapped_sku_uses_confirmed_fallback_sequence(self) -> None:
        rows = self.exporter._nutrition_table_rows(
            [
                SimpleNamespace(
                    sku_id="sku_liposomal_vitamin_c_300",
                    display_name="脂质体维生素C",
                    dosage="每日 1 粒，餐后使用。",
                    reason="用于基础抗氧化支持。",
                    warnings=[],
                )
            ]
        )

        self.assertEqual(rows[0]["sequence"], "待确认")
        self.assertEqual(rows[0]["product_name"], "脂质体维生素C")

    def test_warning_text_does_not_stack_sentence_and_semicolon_marks(self) -> None:
        formatted = self.exporter._format_warning_text(
            [
                "若近期手术或有出血风险，请先人工评估。",
                "合并抗凝药或出血风险较高时需人工确认。",
            ]
        )

        self.assertEqual(formatted, "若近期手术或有出血风险，请先人工评估；合并抗凝药或出血风险较高时需人工确认。")
        self.assertNotIn("。；", formatted)

    def test_parse_report_hides_internal_rag_sections(self) -> None:
        _, sections = self.exporter._parse_report(
            "\n".join(
                [
                    "# 客户报告",
                    "## 总体健康画像",
                    "- 可展示内容",
                    "## RAG内部审查",
                    "- 不应展示",
                    "## 功能医学知识库（仅供参考）",
                    "- 不应展示",
                    "## 个性化营养素方案",
                    "- 旧版营养素文本",
                ]
            )
        )

        section_titles = [title for title, _ in sections]
        self.assertEqual(section_titles, ["总体健康画像", "个性化营养素方案"])

    def test_export_generates_pdf_with_structured_nutrition_table(self) -> None:
        pdf_path = self.exporter.export(
            draft_id="draft_demo",
            customer_name="测试客户",
            report_text="\n".join(
                [
                    "# 客户报告",
                    "## 总体健康画像",
                    "- 当前以营养支持和生活方式管理为重点。",
                    "## 个性化营养素方案",
                    "- 旧版营养素文本不应阻止表格生成。",
                    "## RAG内部审查",
                    "- 内部调试信息",
                ]
            ),
            recommended_skus=[
                SimpleNamespace(
                    sku_id="sku_immune_support",
                    display_name="免疫支持（现货）",
                    dosage="每日 1 粒，餐后使用。",
                    reason="结合本次炎症和免疫状态进行支持。",
                    warnings=["抗凝药物使用者需人工复核"],
                )
            ],
        )

        self.assertTrue(pdf_path.exists())
        self.assertGreater(pdf_path.stat().st_size, 1_000)
        reader = PdfReader(str(pdf_path))
        self.assertGreaterEqual(len(reader.pages), 1)


if __name__ == "__main__":
    unittest.main()
