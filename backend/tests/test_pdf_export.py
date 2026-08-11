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
                    dosage="医生复核剂量；每日 1 粒，餐后使用。",
                    reason="结合本次炎症和免疫状态进行支持。",
                    warnings=["抗凝药物使用者需人工复核"],
                )
            ]
        )

        self.assertEqual(rows[0]["sequence"], "23")
        self.assertEqual(rows[0]["product_name"], "槲皮素复合物")
        self.assertIn("槲皮素复合物", rows[0]["effect"])
        self.assertNotIn("结合本次炎症和免疫状态进行支持", rows[0]["effect"])
        self.assertEqual(rows[0]["dosage"], "每日 1 粒，餐后使用。")
        self.assertNotIn("warnings", rows[0])
        self.assertNotIn("?????", rows[0]["effect"])

    def test_customer_catalog_keeps_current_numbering_authority(self) -> None:
        products = self.exporter.product_report_catalog["products"]
        by_sequence = {profile["sequence"]: profile for profile in products.values()}

        self.assertEqual(by_sequence["21"]["product_name"], "支持胆汁分泌")
        self.assertNotIn("谷胱甘肽", by_sequence["21"]["product_name"])
        self.assertEqual(by_sequence["31"]["product_name"], "肝脏氨基酸解毒支持")
        self.assertEqual(by_sequence["25"]["product_name"], "综合消化酶")
        self.assertEqual(by_sequence["26"]["product_name"], "复合益生菌")
        self.assertIn("11种消化酶", by_sequence["25"]["description"])
        self.assertIn("7种菌株", by_sequence["26"]["description"])

    def test_digestive_products_are_not_filtered_from_pdf_rows(self) -> None:
        rows = self.exporter._nutrition_table_rows(
            [
                SimpleNamespace(
                    sku_id="sku_digestive_enzymes",
                    display_name="综合消化酶",
                    dosage="每日 1 粒，随主餐使用。",
                    reason="结合消化酶支持需求使用。",
                    warnings=[],
                ),
                SimpleNamespace(
                    sku_id="sku_probiotic_complex",
                    display_name="复合益生菌",
                    dosage="每日 1 粒，早餐后使用。",
                    reason="结合菌群恢复需求使用。",
                    warnings=[],
                ),
            ]
        )

        self.assertEqual([row["sequence"] for row in rows], ["25", "26"])
        self.assertEqual([row["product_name"] for row in rows], ["综合消化酶", "复合益生菌"])

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

    def test_canonical_vitamin_c_sku_uses_confirmed_report_profile(self) -> None:
        rows = self.exporter._nutrition_table_rows(
            [
                SimpleNamespace(
                    sku_id="sku_liposomal_vitamin_c_500",
                    display_name="脂质体维生素C",
                    dosage="每日 1 粒，餐后使用。",
                    reason="用于基础抗氧化支持。",
                    warnings=[],
                )
            ]
        )

        self.assertEqual(rows[0]["sequence"], "5")
        self.assertEqual(rows[0]["product_name"], "脂质体维生素C")
        self.assertIn("425mg", rows[0]["effect"])

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

    def test_nutrition_table_does_not_add_total_advice(self) -> None:
        flowables = self.exporter._build_nutrition_table_flowables(
            [
                SimpleNamespace(
                    sku_id="sku_immune_support",
                    display_name="免疫支持（现货）",
                    dosage="医生复核剂量；每日 1 粒，餐后使用。",
                    reason="结合本次炎症和免疫状态进行支持。",
                    warnings=[],
                )
            ],
            self.exporter._styles(),
        )

        paragraph_text = [item.getPlainText() for item in flowables if hasattr(item, "getPlainText")]

        self.assertNotIn("总医嘱说明", paragraph_text)
        self.assertNotIn("推荐搭配说明", paragraph_text)

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
                    dosage="医生复核剂量；每日 1 粒，餐后使用。",
                    reason="结合本次炎症和免疫状态进行支持。",
                    warnings=["抗凝药物使用者需人工复核"],
                )
            ],
        )

        self.assertTrue(pdf_path.exists())
        self.assertEqual(pdf_path.name, "测试客户.pdf")
        self.assertGreater(pdf_path.stat().st_size, 1_000)
        reader = PdfReader(str(pdf_path))
        self.assertGreaterEqual(len(reader.pages), 1)
        pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("每日 1 粒，餐后使用", pdf_text)
        self.assertNotIn("医生复核剂量", pdf_text)
        self.assertNotIn("注意/禁忌", pdf_text)
        self.assertNotIn("抗凝药物使用者需人工复核", pdf_text)


if __name__ == "__main__":
    unittest.main()
