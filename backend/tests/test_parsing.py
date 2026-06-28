from __future__ import annotations

import sys
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.domain.models import AbnormalFlag, SourceSpan
from app.providers.local import DemoOCRProvider
from app.services.parsing import LabNormalizationService


class ParsingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog_path = Path(__file__).resolve().parents[1] / "app" / "data" / "marker_dictionary.json"
        self.service = LabNormalizationService(catalog_path)

    def test_normalizes_supported_lab_lines(self) -> None:
        spans = [
            SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="25-OH维生素D 18 ng/mL 30-100"),
            SourceSpan(file_name="report.txt", page=1, line_number=2, snippet="空腹血糖 6.2 mmol/L 3.9-5.6"),
        ]

        items = self.service.normalize(spans=spans)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].marker_code, "vitamin_d")
        self.assertAlmostEqual(items[0].normalized_value or 0, 18.0)
        self.assertEqual(items[0].abnormal_flag, AbnormalFlag.low)
        self.assertEqual(items[1].marker_code, "fasting_glucose")
        self.assertEqual(items[1].abnormal_flag, AbnormalFlag.high)

    def test_normalizes_pdf_exponent_unit_symbols(self) -> None:
        spans = [
            SourceSpan(file_name="report.pdf", page=1, line_number=1, snippet="1 白细胞 WBC 5.50 10∧9/L 3.5-9.5"),
            SourceSpan(file_name="report.pdf", page=1, line_number=2, snippet="2 红细胞 RBC 5.10 10∧12/L 4.3-5.8"),
        ]

        items = self.service.normalize(spans=spans)
        by_code = {item.marker_code: item for item in items}

        self.assertEqual(by_code["wbc"].value, 5.5)
        self.assertEqual(by_code["wbc"].unit, "10^9/L")
        self.assertEqual(by_code["wbc"].normalized_unit, "10^9/L")
        self.assertEqual(by_code["wbc"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["rbc"].unit, "10^12/L")
        self.assertEqual(by_code["rbc"].normalized_unit, "10^12/L")

    def test_normalizes_split_exponent_and_micro_units(self) -> None:
        spans = [
            SourceSpan(file_name="report.pdf", page=1, line_number=1, snippet="白细胞 WBC 5.50 10 9/L 3.5-9.5"),
            SourceSpan(file_name="report.pdf", page=1, line_number=2, snippet="红细胞 RBC 5.10 10*12/L 4.3-5.8"),
            SourceSpan(file_name="report.pdf", page=1, line_number=3, snippet="血清肌酐 67.6 59-104 碌mol/L"),
            SourceSpan(file_name="report.pdf", page=1, line_number=4, snippet="血清尿酸 529.7 90-420 渭mol/L ↑"),
        ]

        items = self.service.normalize(spans=spans)
        by_code = {item.marker_code: item for item in items}

        self.assertEqual(by_code["wbc"].unit, "10^9/L")
        self.assertEqual(by_code["rbc"].unit, "10^12/L")
        self.assertEqual(by_code["creatinine"].unit, "umol/L")
        self.assertEqual(by_code["uric_acid"].unit, "umol/L")
        self.assertEqual(by_code["uric_acid"].abnormal_flag, AbnormalFlag.high)

    def test_surfaces_unknown_lab_candidates_for_manual_review(self) -> None:
        spans = [
            SourceSpan(file_name="report.pdf", page=1, line_number=1, snippet="白细胞 WBC 5.50 10 9/L 3.5-9.5"),
            SourceSpan(file_name="report.pdf", page=1, line_number=2, snippet="载脂蛋白E 1.82 g/L ↑ 0.20-1.20"),
            SourceSpan(file_name="report.pdf", page=1, line_number=3, snippet="报告日期 2026-06-25"),
            SourceSpan(file_name="report.pdf", page=1, line_number=4, snippet="体重 66.6 kg 0-120"),
            SourceSpan(file_name="report.pdf", page=1, line_number=5, snippet="血小板 PLT 210 125-350"),
        ]

        items = self.service.normalize(spans=spans)
        candidates = self.service.find_unknown_lab_candidates(spans=spans, lab_items=items)

        self.assertTrue(any("载脂蛋白E" in item for item in candidates))
        self.assertFalse(any("白细胞" in item for item in candidates))
        self.assertFalse(any("报告日期" in item for item in candidates))
        self.assertFalse(any("体重" in item for item in candidates))
        self.assertFalse(any("血小板" in item for item in candidates))

    def test_normalizes_body_metrics_and_cbc_differentials(self) -> None:
        samples = [
            ("体重 66.6 kg 0-120", "body_weight", "kg"),
            ("Weight 72.5 kg 0-120", "body_weight", "kg"),
            ("身高 166.6 cm 120-220", "body_height", "cm"),
            ("Height 1.72 m 1.2-2.2", "body_height", "cm"),
            ("体质指数 21.8 18.5-23.9999", "bmi", "kg/m2"),
            ("BMI 24.6 18.5-23.9999", "bmi", "kg/m2"),
            ("收缩压 116 mmHg 90-139", "systolic_bp", "mmHg"),
            ("SBP 128 mmHg 90-139", "systolic_bp", "mmHg"),
            ("舒张压 74 60-89", "diastolic_bp", "mmHg"),
            ("DBP 82 mmHg 60-89", "diastolic_bp", "mmHg"),
            ("腰围 76 cm 55-90", "waist_circumference", "cm"),
            ("Waist 82 cm 55-90", "waist_circumference", "cm"),
            ("臀围 94 cm 50-150", "hip_circumference", "cm"),
            ("Hip 100 cm 50-150", "hip_circumference", "cm"),
            ("腰臀比 0.80 0-0.929", "waist_hip_ratio", "ratio"),
            ("WHR 0.87 0-0.929", "waist_hip_ratio", "ratio"),
            ("血小板 PLT 210 125-350", "platelet_count", "10^9/L"),
            ("PLT 205 10^9/L 125-350", "platelet_count", "10^9/L"),
            ("中性粒细胞 NEUT 58.1 40-75", "neutrophil_percentage", "%"),
            ("NEUT% 60.2 % 40-75", "neutrophil_percentage", "%"),
            ("淋巴细胞 LYM 34.2 20-50", "lymphocyte_percentage", "%"),
            ("LYM% 32.1 % 20-50", "lymphocyte_percentage", "%"),
            ("单核细胞 MONO 5.1 3-10", "monocyte_percentage", "%"),
            ("MONO% 5.4 % 3-10", "monocyte_percentage", "%"),
            ("嗜酸性粒细胞 EOS 2.0 0.4-8", "eosinophil_percentage", "%"),
            ("EOS% 2.3 % 0.4-8", "eosinophil_percentage", "%"),
            ("中性粒细胞绝对值 NEUT# 3.00 1.8-6.3", "neutrophil_absolute", "10^9/L"),
            ("NEUT# 3.10 10^9/L 1.8-6.3", "neutrophil_absolute", "10^9/L"),
            ("淋巴细胞绝对值 LYM# 1.90 1.1-3.2", "lymphocyte_absolute", "10^9/L"),
            ("LYM# 2.10 10^9/L 1.1-3.2", "lymphocyte_absolute", "10^9/L"),
            ("单核细胞数绝对值 MONO# 0.33 0.1-0.6", "monocyte_absolute", "10^9/L"),
            ("MONO# 0.31 10^9/L 0.1-0.6", "monocyte_absolute", "10^9/L"),
            ("嗜酸性粒细胞绝对值 EOS# 0.11 0.0-0.5", "eosinophil_absolute", "10^9/L"),
            ("EOS# 0.12 10^9/L 0.0-0.5", "eosinophil_absolute", "10^9/L"),
            ("平均血小板体积 MPV 10.0 fl 6.5-12.0", "mean_platelet_volume", "fL"),
            ("MPV 10.1 fL 6.5-12.0", "mean_platelet_volume", "fL"),
            ("血小板压积 PCT 0.19 0.108-0.282", "plateletcrit", "%"),
            ("PCT 0.18 % 0.108-0.282", "plateletcrit", "%"),
            ("估算肾小球滤过率 EGFR 77.14 mL/min 80-120", "egfr", "mL/min"),
            ("钙卫蛋白 Cal 69.8 ug/g 0-60.0", "fecal_calprotectin", "ug/g"),
        ]
        spans = [
            SourceSpan(file_name="report.pdf", page=1, line_number=index + 1, snippet=snippet)
            for index, (snippet, _, _) in enumerate(samples)
        ]

        items = self.service.normalize(spans=spans)
        candidates = self.service.find_unknown_lab_candidates(spans=spans, lab_items=items)
        recognized = {(item.source_span.snippet, item.marker_code, item.normalized_unit) for item in items}

        for snippet, expected_code, expected_unit in samples:
            with self.subTest(snippet=snippet):
                self.assertIn((snippet, expected_code, expected_unit), recognized)
        self.assertFalse(candidates)

    def test_ignores_service_hotline_metadata(self) -> None:
        spans = [
            SourceSpan(file_name="report.pdf", page=1, line_number=1, snippet="服务热线:010-00000000"),
            SourceSpan(file_name="report.pdf", page=1, line_number=2, snippet="联系电话 400-000-0000"),
        ]

        items = self.service.normalize(spans=spans)
        candidates = self.service.find_unknown_lab_candidates(spans=spans, lab_items=items)

        self.assertFalse(items)
        self.assertFalse(candidates)

    def test_normalizes_multiline_marker_blocks(self) -> None:
        spans = [
            SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="血清丙氨酸氨基转移酶"),
            SourceSpan(file_name="report.txt", page=1, line_number=2, snippet="43.6"),
            SourceSpan(file_name="report.txt", page=1, line_number=3, snippet="男:9-50U/L"),
            SourceSpan(file_name="report.txt", page=1, line_number=4, snippet="女:7-40U/L"),
            SourceSpan(file_name="report.txt", page=1, line_number=5, snippet="U/L"),
        ]

        items = self.service.normalize(spans=spans)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].marker_code, "alt")
        self.assertEqual(items[0].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(items[0].ref_range.upper, 50.0)

    def test_treats_double_dash_ranges_as_normal_positive_bounds(self) -> None:
        spans = [
            SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="ALT 谷丙转氨酶 32.1 0--40U/L"),
            SourceSpan(file_name="report.txt", page=1, line_number=2, snippet="LDL 低密度胆固醇 2.93 0--3.7mmol/L"),
        ]

        items = self.service.normalize(spans=spans)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(items[0].ref_range.upper, 40.0)
        self.assertEqual(items[1].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(items[1].ref_range.upper, 3.7)

    def test_parses_upper_bound_only_ranges_for_psa(self) -> None:
        spans = [
            SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="PSA 0.56 <4.4 ng/ml"),
        ]

        items = self.service.normalize(spans=spans)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].marker_code, "psa")
        self.assertEqual(items[0].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(items[0].ref_range.upper, 4.4)

    def test_parses_common_biochemistry_markers(self) -> None:
        spans = [
            SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="血清尿素 4.40 2.8-7.2 mmol/L"),
            SourceSpan(file_name="report.txt", page=1, line_number=2, snippet="血清肌酐 67.60 59-104 umol/L"),
            SourceSpan(file_name="report.txt", page=1, line_number=3, snippet="血清尿酸 529.7 90-420 umol/L"),
            SourceSpan(file_name="report.txt", page=1, line_number=4, snippet="血清总蛋白 79.3 65-85 g/L"),
        ]

        items = self.service.normalize(spans=spans)
        by_code = {item.marker_code: item for item in items}

        self.assertIn("bun", by_code)
        self.assertIn("creatinine", by_code)
        self.assertIn("uric_acid", by_code)
        self.assertIn("total_protein", by_code)
        self.assertEqual(by_code["bun"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["creatinine"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["uric_acid"].abnormal_flag, AbnormalFlag.high)
        self.assertEqual(by_code["total_protein"].abnormal_flag, AbnormalFlag.normal)

    def test_parses_common_nutrition_markers(self) -> None:
        spans = [
            SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="血清铁 6.2 9-27 μmol/L ↓"),
            SourceSpan(file_name="report.txt", page=1, line_number=2, snippet="铁蛋白 11 12-150 ng/mL ↓"),
            SourceSpan(file_name="report.txt", page=1, line_number=3, snippet="维生素B12 350 180-914 pg/mL 正常"),
            SourceSpan(file_name="report.txt", page=1, line_number=4, snippet="叶酸 8.5 3.1-19.9 ng/mL 正常"),
            SourceSpan(file_name="report.txt", page=1, line_number=5, snippet="血钙 2.25 2.11-2.52 mmol/L 正常"),
        ]

        items = self.service.normalize(spans=spans)
        by_code = {item.marker_code: item for item in items}

        self.assertIn("serum_iron", by_code)
        self.assertIn("ferritin", by_code)
        self.assertIn("vitamin_b12", by_code)
        self.assertIn("folate", by_code)
        self.assertIn("calcium", by_code)
        self.assertEqual(by_code["serum_iron"].abnormal_flag, AbnormalFlag.low)
        self.assertEqual(by_code["ferritin"].abnormal_flag, AbnormalFlag.low)
        self.assertEqual(by_code["vitamin_b12"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["folate"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["calcium"].abnormal_flag, AbnormalFlag.normal)

    def test_parses_hospital_abnormal_rows_with_unit_before_range_and_trailing_arrow(self) -> None:
        spans = [
            SourceSpan(file_name="checkup.pdf", page=9, line_number=1, snippet="血小板分布宽度 8.6 fL 9.9～17.0 ↓"),
            SourceSpan(file_name="checkup.pdf", page=9, line_number=2, snippet="嗜酸性粒细胞比率 7.3 % 0.5～5.0 ↑"),
            SourceSpan(file_name="checkup.pdf", page=9, line_number=3, snippet="甘油三脂 1.97 mmol/L 0.00～1.70 ↑"),
            SourceSpan(file_name="checkup.pdf", page=9, line_number=4, snippet="高密度脂蛋白胆固醇 1.16 mmol/L ＞1.16 ↓"),
            SourceSpan(file_name="checkup.pdf", page=10, line_number=5, snippet="尿酸碱度 6.50 4.80～7.50"),
            SourceSpan(
                file_name="checkup.pdf",
                page=4,
                line_number=6,
                snippet="若嗜酸性粒细胞比率在10～20%，需检查有无寄生虫感染，必要时驱虫治疗。",
            ),
        ]

        items = self.service.normalize(spans=spans)
        by_code = {item.marker_code: item for item in items}

        self.assertEqual(set(by_code), {"platelet_distribution_width", "eosinophil_percentage", "triglycerides", "hdl_c"})
        self.assertEqual(by_code["platelet_distribution_width"].abnormal_flag, AbnormalFlag.low)
        self.assertEqual(by_code["eosinophil_percentage"].abnormal_flag, AbnormalFlag.high)
        self.assertEqual(by_code["triglycerides"].abnormal_flag, AbnormalFlag.high)
        self.assertEqual(by_code["hdl_c"].abnormal_flag, AbnormalFlag.low)

    def test_ignores_order_name_lines_that_only_contain_panel_counts(self) -> None:
        spans = [
            SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="医嘱名: 同型半胱氨酸+肝功14项"),
            SourceSpan(file_name="report.txt", page=1, line_number=2, snippet="HCY 同型半胱氨酸 12.6 4.0-15.4 μmol/L"),
        ]

        items = self.service.normalize(spans=spans)
        homocysteine_items = [item for item in items if item.marker_code == "homocysteine"]

        self.assertEqual(len(homocysteine_items), 1)
        self.assertEqual(homocysteine_items[0].normalized_value, 12.6)
        self.assertEqual(homocysteine_items[0].abnormal_flag, AbnormalFlag.normal)

    def test_parses_vertical_lipid_marker_blocks_with_abbreviation_lines(self) -> None:
        spans = [
            SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="甘油三酯"),
            SourceSpan(file_name="report.txt", page=1, line_number=2, snippet="TG"),
            SourceSpan(file_name="report.txt", page=1, line_number=3, snippet="1.45"),
            SourceSpan(file_name="report.txt", page=1, line_number=4, snippet="0.56~1.70"),
            SourceSpan(file_name="report.txt", page=1, line_number=5, snippet="高密度脂蛋白胆固醇"),
            SourceSpan(file_name="report.txt", page=1, line_number=6, snippet="HDL-C"),
            SourceSpan(file_name="report.txt", page=1, line_number=7, snippet="1.39"),
            SourceSpan(file_name="report.txt", page=1, line_number=8, snippet="0.91~1.55"),
            SourceSpan(file_name="report.txt", page=1, line_number=9, snippet="载脂蛋白A1"),
            SourceSpan(file_name="report.txt", page=1, line_number=10, snippet="Apo A1"),
            SourceSpan(file_name="report.txt", page=1, line_number=11, snippet="1.59"),
            SourceSpan(file_name="report.txt", page=1, line_number=12, snippet="g/L"),
            SourceSpan(file_name="report.txt", page=1, line_number=13, snippet="1.20~1.60"),
        ]

        items = self.service.normalize(spans=spans)
        by_code = {item.marker_code: item for item in items}

        self.assertIn("triglycerides", by_code)
        self.assertIn("hdl_c", by_code)
        self.assertIn("apolipoprotein_a1", by_code)
        self.assertEqual(by_code["triglycerides"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["hdl_c"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["apolipoprotein_a1"].abnormal_flag, AbnormalFlag.normal)

    def test_prefers_full_name_before_abbreviation_in_stacked_lab_blocks(self) -> None:
        spans = [
            SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="血清总蛋白"),
            SourceSpan(file_name="report.txt", page=1, line_number=2, snippet="TP"),
            SourceSpan(file_name="report.txt", page=1, line_number=3, snippet="68"),
            SourceSpan(file_name="report.txt", page=1, line_number=4, snippet="g/L"),
            SourceSpan(file_name="report.txt", page=1, line_number=5, snippet="65-85"),
            SourceSpan(file_name="report.txt", page=1, line_number=6, snippet="血清白蛋白"),
            SourceSpan(file_name="report.txt", page=1, line_number=7, snippet="ALB"),
            SourceSpan(file_name="report.txt", page=1, line_number=8, snippet="43"),
            SourceSpan(file_name="report.txt", page=1, line_number=9, snippet="38-55"),
            SourceSpan(file_name="report.txt", page=1, line_number=10, snippet="血清球蛋白"),
            SourceSpan(file_name="report.txt", page=1, line_number=11, snippet="GLB"),
            SourceSpan(file_name="report.txt", page=1, line_number=12, snippet="25"),
            SourceSpan(file_name="report.txt", page=1, line_number=13, snippet="20-40"),
        ]

        items = self.service.normalize(spans=spans)
        by_code = {item.marker_code: item for item in items}

        self.assertEqual(by_code["total_protein"].normalized_value, 68.0)
        self.assertEqual(by_code["total_protein"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["albumin"].normalized_value, 43.0)
        self.assertEqual(by_code["albumin"].ref_range.lower, 38.0)
        self.assertEqual(by_code["albumin"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["globulin"].normalized_value, 25.0)
        self.assertEqual(by_code["globulin"].abnormal_flag, AbnormalFlag.normal)

    def test_structured_table_parser_skips_serial_numbers_and_keeps_ranges(self) -> None:
        spans = [
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=1, snippet="项目名称"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=2, snippet="英文缩写"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=3, snippet="检查结果"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=4, snippet="血清白蛋白"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=5, snippet="ALB"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=6, snippet="43"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=7, snippet="38-55"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=8, snippet="2"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=9, snippet="血清球蛋白"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=10, snippet="GLB"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=11, snippet="25"),
            SourceSpan(file_name="pptx-report.txt", page=1, line_number=12, snippet="3"),
        ]

        items = self.service.normalize(spans=spans)
        by_code = {item.marker_code: item for item in items}

        self.assertIn("albumin", by_code)
        self.assertNotIn("globulin", by_code)
        self.assertEqual(by_code["albumin"].normalized_value, 43.0)
        self.assertEqual(by_code["albumin"].ref_range.lower, 38.0)
        self.assertEqual(by_code["albumin"].source_span.snippet, "血清白蛋白 ALB 43 38-55")

    def test_single_line_marker_without_range_still_parses_when_value_is_inline(self) -> None:
        spans = [
            SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="癌胚抗原 CEA 1.2"),
        ]

        items = self.service.normalize(spans=spans)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].marker_code, "cea")
        self.assertEqual(items[0].abnormal_flag, AbnormalFlag.normal)

    def test_ignores_narrative_lines_without_real_lab_format(self) -> None:
        spans = [
            SourceSpan(
                file_name="case-note.txt",
                page=1,
                line_number=1,
                snippet="生化、离子、血糖、血脂、肌酐无异常，考虑继续对症处理。",
            ),
            SourceSpan(
                file_name="case-note.txt",
                page=1,
                line_number=2,
                snippet="主诉口角歪斜，右眼闭合无力2天，无发热，无头痛。",
            ),
        ]

        items = self.service.normalize(spans=spans)

        self.assertEqual(items, [])

    def test_ignores_short_latin_synonyms_inside_gibberish(self) -> None:
        spans = [
            SourceSpan(
                file_name="bad.txt",
                page=1,
                line_number=1,
                snippet="xT+I6[D;ZAxDLr`F000MgnYZ)MGtJui&Vq_ey1]i",
            ),
            SourceSpan(
                file_name="bad.txt",
                page=1,
                line_number=2,
                snippet='2r|dVMgK4bspQo ?"d[',
            ),
        ]

        items = self.service.normalize(spans=spans)

        self.assertEqual(items, [])

    def test_parses_pipe_separated_docx_table_rows(self) -> None:
        spans = [
            SourceSpan(
                file_name="word-report.docx",
                page=1,
                line_number=1,
                snippet="红细胞比容 | HCT | 0.358 | | 0.35—0.45 | L/L",
            ),
            SourceSpan(
                file_name="word-report.docx",
                page=1,
                line_number=2,
                snippet="红细胞分布宽度-变异系数 | RDW-CV | 12.90 | | 11—16 | %",
            ),
            SourceSpan(
                file_name="word-report.docx",
                page=1,
                line_number=3,
                snippet="红细胞分布宽度-标准差 | RDW-SD | 43.00 | | 39—52 | fL",
            ),
        ]

        items = self.service.normalize(spans=spans)
        by_code = {item.marker_code: item for item in items}

        self.assertEqual(by_code["hematocrit"].normalized_value, 35.8)
        self.assertEqual(by_code["hematocrit"].normalized_unit, "%")
        self.assertEqual(by_code["hematocrit"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["rdw_cv"].abnormal_flag, AbnormalFlag.normal)
        self.assertEqual(by_code["rdw_sd"].abnormal_flag, AbnormalFlag.normal)


class OCRProviderTests(unittest.TestCase):
    def test_falls_back_to_embedded_image_ocr_for_scanned_pdf(self) -> None:
        class StubPdfOCRProvider(DemoOCRProvider):
            def __init__(self) -> None:
                super().__init__(base_url="https://example.test/v1", api_key="token", model="vision-model")
                self.calls: list[tuple[int, str]] = []

            def _extract_image_text(self, *, content: bytes, content_type: str) -> str:
                self.calls.append((len(content), content_type))
                return "空腹血糖 6.8 mmol/L 3.9-6.1\n甘油三酯 2.1 mmol/L 0.56-1.70"

        provider = StubPdfOCRProvider()
        page = SimpleNamespace(
            extract_text=lambda: "",
            images=[
                SimpleNamespace(name="scan.jpg", data=b"\xff\xd8\xff" + b"0" * 50000),
                SimpleNamespace(name="icon.jpg", data=b"\xff\xd8\xff" + b"1" * 2000),
            ],
        )
        reader = SimpleNamespace(pages=[page])

        with patch("app.providers.local.PdfReader", return_value=reader):
            extraction = provider.extract(
                filename="scan.pdf",
                content_type="application/pdf",
                content=b"%PDF-1.4 scanned",
            )

        self.assertIn("空腹血糖 6.8 mmol/L 3.9-6.1", extraction.text)
        self.assertIn("甘油三酯 2.1 mmol/L 0.56-1.70", extraction.text)
        self.assertEqual(provider.calls, [(50003, "image/jpeg")])
        self.assertEqual([span.page for span in extraction.spans], [1, 1])

    def test_keeps_pdf_text_layer_when_it_is_already_readable(self) -> None:
        class GuardPdfOCRProvider(DemoOCRProvider):
            def __init__(self) -> None:
                super().__init__(base_url="https://example.test/v1", api_key="token", model="vision-model")

            def _extract_image_text(self, *, content: bytes, content_type: str) -> str:
                raise AssertionError("Should not OCR a page that already has a readable text layer")

        provider = GuardPdfOCRProvider()
        page = SimpleNamespace(
            extract_text=lambda: "空腹血糖 6.8 mmol/L 3.9-6.1\n甘油三酯 2.1 mmol/L 0.56-1.70\n主诉：乏力",
            images=[SimpleNamespace(name="scan.jpg", data=b"\xff\xd8\xff" + b"0" * 50000)],
        )
        reader = SimpleNamespace(pages=[page])

        with patch("app.providers.local.PdfReader", return_value=reader):
            extraction = provider.extract(
                filename="report.pdf",
                content_type="application/pdf",
                content=b"%PDF-1.4 text-layer",
            )

        self.assertIn("空腹血糖 6.8 mmol/L 3.9-6.1", extraction.text)
        self.assertIn("甘油三酯 2.1 mmol/L 0.56-1.70", extraction.text)
        self.assertIn("主诉:乏力", extraction.text)

    def test_extracts_docx_tables_as_separate_rows(self) -> None:
        provider = DemoOCRProvider()
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "word/document.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
                  <w:body>
                    <w:p><w:r><w:t>专家建议与指导</w:t></w:r></w:p>
                    <w:tbl>
                      <w:tr>
                        <w:tc><w:p><w:r><w:t>红细胞比容</w:t></w:r></w:p></w:tc>
                        <w:tc><w:p><w:r><w:t>HCT</w:t></w:r></w:p></w:tc>
                        <w:tc><w:p><w:r><w:t>0.358</w:t></w:r></w:p></w:tc>
                        <w:tc><w:p><w:r><w:t></w:t></w:r></w:p></w:tc>
                        <w:tc><w:p><w:r><w:t>0.35—0.45</w:t></w:r></w:p></w:tc>
                        <w:tc><w:p><w:r><w:t>L/L</w:t></w:r></w:p></w:tc>
                      </w:tr>
                      <w:tr>
                        <w:tc><w:p><w:r><w:t>红细胞分布宽度-标准差</w:t></w:r></w:p></w:tc>
                        <w:tc><w:p><w:r><w:t>RDW-SD</w:t></w:r></w:p></w:tc>
                        <w:tc><w:p><w:r><w:t>43.00</w:t></w:r></w:p></w:tc>
                        <w:tc><w:p><w:r><w:t></w:t></w:r></w:p></w:tc>
                        <w:tc><w:p><w:r><w:t>39—52</w:t></w:r></w:p></w:tc>
                        <w:tc><w:p><w:r><w:t>fL</w:t></w:r></w:p></w:tc>
                      </w:tr>
                    </w:tbl>
                  </w:body>
                </w:document>
                """,
            )

        extraction = provider.extract(
            filename="word-report.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content=buffer.getvalue(),
        )

        self.assertIn("专家建议与指导", extraction.text)
        self.assertIn("红细胞比容 | HCT | 0.358 | | 0.35—0.45 | L/L", extraction.text)
        self.assertIn("红细胞分布宽度-标准差 | RDW-SD | 43.00 | | 39—52 | fL", extraction.text)

    def test_extracts_text_from_pptx_slides(self) -> None:
        provider = DemoOCRProvider()
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "ppt/slides/slide1.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
                  <p:cSld>
                    <p:spTree>
                      <p:sp>
                        <p:txBody>
                          <a:p><a:r><a:t>空腹血糖 6.8 mmol/L 3.9-6.1</a:t></a:r></a:p>
                          <a:p><a:r><a:t>甘油三酯 2.1 mmol/L 0.56-1.70</a:t></a:r></a:p>
                        </p:txBody>
                      </p:sp>
                    </p:spTree>
                  </p:cSld>
                </p:sld>
                """,
            )
            archive.writestr(
                "ppt/slides/slide2.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
                  <p:cSld>
                    <p:spTree>
                      <p:sp>
                        <p:txBody>
                          <a:p><a:r><a:t>主诉：乏力、头晕半年</a:t></a:r></a:p>
                        </p:txBody>
                      </p:sp>
                    </p:spTree>
                  </p:cSld>
                </p:sld>
                """,
            )

        extraction = provider.extract(
            filename="case-deck.pptx",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            content=buffer.getvalue(),
        )

        self.assertIn("空腹血糖 6.8 mmol/L 3.9-6.1", extraction.text)
        self.assertIn("甘油三酯 2.1 mmol/L 0.56-1.70", extraction.text)
        self.assertIn("主诉:乏力、头晕半年", extraction.text)
        self.assertEqual([span.page for span in extraction.spans], [1, 1, 2])
        self.assertGreater(extraction.confidence, 0.8)

    def test_binary_image_without_ocr_config_returns_empty_text(self) -> None:
        provider = DemoOCRProvider()
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128 + b"Mg 8.0 mmol/L"

        extraction = provider.extract(filename="report.png", content_type="image/png", content=fake_png)

        self.assertEqual(extraction.text, "")
        self.assertEqual(extraction.spans, [])
        self.assertLess(extraction.confidence, 0.2)

    def test_extracts_text_from_responses_summary_payload(self) -> None:
        provider = DemoOCRProvider()
        payload = {
            "output": [
                {
                    "type": "reasoning",
                    "summary": [
                        {
                            "type": "summary_text",
                            "text": '整理结果如下：{"text_lines":["体检报告单","人民医院检验报告单"],"confidence":"high"}',
                        }
                    ],
                }
            ]
        }

        raw_response = provider._extract_response_text(payload)
        parsed = provider._parse_ocr_response(raw_response)

        self.assertIn("体检报告单", parsed)
        self.assertIn("人民医院检验报告单", parsed)

    def test_filters_placeholder_ocr_lines(self) -> None:
        provider = DemoOCRProvider()

        self.assertEqual(provider._clean_line("逐行文本"), "")
        self.assertEqual(provider._clean_line("逐行的每个放数组里"), "")
        self.assertEqual(provider._clean_line('{"text_lines":["逐行文本"],"confidence":"high"}'), "")


if __name__ == "__main__":
    unittest.main()
