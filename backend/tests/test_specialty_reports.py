from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.domain.models import AbnormalFlag, SpecialtyReviewStatus
from app.providers.base import OCRExtraction
from app.services.specialty_reports import SpecialtyReportParser


class SpecialtyReportParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = SpecialtyReportParser()

    def _parse(self, pages: dict[int, str], *, filename: str = "synthetic.pdf"):
        combined = "\n".join(pages.values())
        with patch.object(self.parser, "_document_pages", return_value=(pages, pages)):
            return self.parser.parse(
                filename=filename,
                content_type="application/pdf",
                content=b"synthetic",
                extraction=OCRExtraction(text=combined, confidence=0.99),
                file_id="file_synthetic",
            )

    def test_food_sensitivity_extracts_severity_and_fixed_interpretation(self) -> None:
        reports = self._parse(
            {
                1: "慢性食物敏感分析 IgG抗体",
                3: (
                    "建议报告\n"
                    "轻度(Mild)：牛奶、羊奶、蛋白\n"
                    "中度(Moderate)：无\n"
                    "重度(High)：无\n"
                    "1.延迟性过敏反应IgG呈现轻度反应：建议停止摄食6周。\n"
                    "2.延迟性过敏反应IgG呈现中度反应：建议停止摄食3个月。\n"
                    "3.延迟性过敏反应IgG呈现重度反应：建议停止摄食6个月。"
                ),
            }
        )

        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertEqual(report.report_type, "chronic_food_sensitivity")
        self.assertEqual(report.mild_foods, ["牛奶", "羊奶", "蛋白"])
        self.assertEqual(report.moderate_foods, [])
        self.assertEqual(report.high_foods, [])
        self.assertEqual(len(report.interpretations), 3)
        self.assertEqual(report.review_status, SpecialtyReviewStatus.pending_review)
        self.assertTrue(report.needs_manual_review)

    def test_gut_function_uses_report_ranges_and_flags_high_calprotectin(self) -> None:
        reports = self._parse(
            {
                1: "GI Function (Stool) Anti-Gliadin Calprotectin Zonulin Pancreatic Elastase",
                2: (
                    "检测结果\n"
                    "麦胶蛋白抗体 12 U/L < 20\n"
                    "组织转麸胺酶抗体 8 U/L < 20\n"
                    "分泌型免疫球蛋白 sIgA 920 μg/mL 510-2040\n"
                    "钙卫蛋白 Calprotectin 180 mg/kg < 60\n"
                    "解连蛋白 Zonulin 42 ng/mL < 60\n"
                    "β-葡萄糖醛酸酶 2100 U/g 400-6000\n"
                    "胰弹性蛋白酶 Pancreatic Elastase 480 μg/g > 200"
                ),
                3: "偏高：您的钙卫蛋白结果高于本报告参考范围。",
                4: "肠道健康改善建议\n建议结合临床评估炎症原因。",
            }
        )

        report = reports[0]
        self.assertEqual(report.report_type, "gut_function")
        self.assertEqual(len(report.metrics), 7)
        calprotectin = next(item for item in report.metrics if item.code == "fecal_calprotectin")
        self.assertEqual(calprotectin.value, 180)
        self.assertEqual(calprotectin.ref_range.upper, 60)
        self.assertEqual(calprotectin.abnormal_flag, AbnormalFlag.high)
        self.assertEqual(calprotectin.source_span.file_id, "file_synthetic")

    def test_gut_function_missing_value_is_not_inferred(self) -> None:
        reports = self._parse(
            {
                1: "GI Function (Stool) Anti-Gliadin Calprotectin Zonulin Pancreatic Elastase",
                2: "检测结果\n麦胶蛋白抗体 待人工确认 U/L < 20\nCalprotectin\nZonulin\nPancreatic Elastase",
            }
        )

        report = reports[0]
        self.assertFalse(any(item.code == "anti_gliadin_siga" for item in report.metrics))
        self.assertEqual(report.review_status, SpecialtyReviewStatus.needs_review)
        self.assertTrue(report.warnings)

    def test_microbiome_extracts_key_summary_only(self) -> None:
        reports = self._parse(
            {
                1: "Gut Microbiota Profile 肠道菌群健康综合评估\n73.5",
                2: "肠道菌群多样性\n参考范围 2.00-4.00\n2.65",
                8: (
                    "报告总结\n"
                    "85\n"
                    "拟杆菌属\n"
                    "较好\n正常\n高碳水型\n"
                    "可能对健康产生影响\n梭杆菌属, 克雷伯菌属\n有益菌指标\n"
                    "含量偏低\n双歧杆菌属, 乳杆菌属\n03\n"
                    "一定影响\n代谢风险, 炎症风险\n神经递质, 肝脏解毒\n04\n"
                    "营养素成分存在一定的影响\n维生素B族, 短链脂肪酸\n"
                    "本报告只对本次样本负责。"
                ),
            }
        )

        report = reports[0]
        self.assertEqual(report.report_type, "gut_microbiome")
        self.assertEqual(report.health_score, 73.5)
        self.assertEqual(report.diversity_index, 2.65)
        self.assertEqual(report.detected_genera_count, 85)
        self.assertEqual(report.dominant_genus, "拟杆菌属")
        self.assertEqual(report.enterotype, "高碳水型")
        self.assertEqual(report.harmful_or_elevated_genera, ["梭杆菌属", "克雷伯菌属"])
        self.assertEqual(report.low_beneficial_genera, ["双歧杆菌属", "乳杆菌属"])

    def test_unmatched_document_does_not_create_specialty_report(self) -> None:
        self.assertEqual(self._parse({1: "普通血常规 WBC 5.2 10^9/L"}), [])


if __name__ == "__main__":
    unittest.main()
