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
        self.assertNotIn("结合本次炎症和免疫状态进行支持", rows[0]["effect"])
        self.assertEqual(rows[0]["warnings"], ["抗凝药物使用者需人工复核"])
        self.assertNotIn("?????", rows[0]["effect"])

    def test_customer_catalog_keeps_current_numbering_authority(self) -> None:
        products = self.exporter.product_report_catalog["products"]
        by_sequence = {profile["sequence"]: profile for profile in products.values()}

        self.assertEqual(by_sequence["21"]["product_name"], "支持胆汁分泌")
        self.assertNotIn("谷胱甘肽", by_sequence["21"]["product_name"])
        self.assertEqual(by_sequence["31"]["product_name"], "肝脏氨基酸解毒支持")
        self.assertNotIn("26", by_sequence)
        self.assertFalse(any(profile["product_name"] == "复合益生菌" for profile in products.values()))

    def test_customer_catalog_uses_full_product_description(self) -> None:
        self.exporter.product_report_catalog = {
            "products": {
                "sku_full_description": {
                    "sequence": "99",
                    "product_name": "完整介绍测试",
                    "description": (
                        "第一句用于模拟产品定位。"
                        "第二句包含完整机制说明，不能被截断。"
                        "第三句包含适用人群和长期支持说明，也要完整保留。"
                    ),
                }
            }
        }

        rows = self.exporter._nutrition_table_rows(
            [
                SimpleNamespace(
                    sku_id="sku_full_description",
                    display_name="完整介绍测试",
                    dosage="每日 1 粒，餐后使用。",
                    reason="结合本次情况进行个性化支持。",
                    warnings=[],
                )
            ]
        )

        self.assertIn("第一句用于模拟产品定位", rows[0]["effect"])
        self.assertIn("第二句包含完整机制说明，不能被截断", rows[0]["effect"])
        self.assertIn("第三句包含适用人群和长期支持说明，也要完整保留", rows[0]["effect"])
        self.assertNotIn("结合本次情况进行个性化支持", rows[0]["effect"])

    def test_nutrition_basis_items_polish_internal_matching_evidence(self) -> None:
        rows = [
            {
                "product_name": "肝脏氨基酸解毒支持",
                "reason": (
                    "关联度约 95%：结合 解毒支持、恢复支持，"
                    "命中产品标签命中：肝脏/解毒系统、产品标签命中：抗氧化轴，"
                    "作为当前阶段的候选推荐。"
                ),
            }
        ]

        items = self.exporter._nutrition_basis_items(rows)

        self.assertEqual(len(items), 1)
        self.assertIn("肝胆代谢", items[0])
        self.assertIn("氧化压力", items[0])
        self.assertNotIn("关联度", items[0])
        self.assertNotIn("命中产品标签", items[0])

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
                    "## 核心结论与健康画像",
                    "- 可展示内容",
                    "## RAG内部审查",
                    "- 不应展示",
                    "## 功能医学知识库（仅供参考）",
                    "- 不应展示",
                    "## 首月营养素干预方案",
                    "- 旧版营养素文本",
                ]
            )
        )

        section_titles = [title for title, _ in sections]
        self.assertEqual(section_titles, ["核心结论与健康画像", "首月营养素干预方案"])

    def test_structured_subheadings_render_without_bullet_prefix(self) -> None:
        formatted = self.exporter._format_item("功能医学系统失衡分析", "### 1. 代谢/内分泌系统")

        self.assertIn("1. 代谢/内分泌系统", formatted)
        self.assertNotIn("- ", formatted)

    def test_export_generates_pdf_with_structured_nutrition_table(self) -> None:
        pdf_path = self.exporter.export(
            draft_id="draft_demo",
            customer_name="测试客户",
            report_text="\n".join(
                [
                    "# 客户报告",
                    "## 核心结论与健康画像",
                    "- 当前以营养支持和生活方式管理为重点。",
                    "## 首月营养素干预方案",
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
