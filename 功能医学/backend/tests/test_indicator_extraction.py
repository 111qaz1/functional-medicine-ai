from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.domain.models import AbnormalFlag, CaseRecord, ExtractedLabItem, FileParseStatus, SourceSpan, UploadedFile, utc_now
from app.services.indicator_extraction import CaseIndicatorService


class IndicatorExtractionTests(unittest.TestCase):
    def test_places_attention_indicators_before_normal_indicators(self) -> None:
        service = CaseIndicatorService()
        case = CaseRecord(
            id="case_labs",
            customer_name="化验排序",
            created_at=utc_now(),
            updated_at=utc_now(),
            extracted_lab_items=[
                ExtractedLabItem(
                    marker_code="glucose",
                    marker_name="空腹血糖",
                    value=6.4,
                    unit="mmol/L",
                    normalized_value=6.4,
                    normalized_unit="mmol/L",
                    abnormal_flag=AbnormalFlag.high,
                    confidence=0.95,
                    source_span=SourceSpan(file_name="report.txt", page=1, line_number=1, snippet="空腹血糖 6.4 mmol/L"),
                ),
                ExtractedLabItem(
                    marker_code="creatinine",
                    marker_name="肌酐",
                    value=68,
                    unit="umol/L",
                    normalized_value=68,
                    normalized_unit="umol/L",
                    abnormal_flag=AbnormalFlag.normal,
                    confidence=0.95,
                    source_span=SourceSpan(file_name="report.txt", page=1, line_number=2, snippet="肌酐 68 umol/L"),
                ),
            ],
        )

        indicators = service.build(case)

        self.assertEqual([item.indicator_name for item in indicators[:2]], ["空腹血糖", "肌酐"])
        self.assertEqual(indicators[0].status.value, "attention")
        self.assertEqual(indicators[1].status.value, "normal")

    def test_extracts_generic_single_line_lab_rows_and_keeps_normal_items(self) -> None:
        service = CaseIndicatorService()
        file = UploadedFile(
            id="file_1",
            case_id="case_1",
            filename="case.png",
            content_type="image/png",
            size_bytes=1024,
            parse_status=FileParseStatus.parsed,
            corrected_text="\n".join(
                [
                    "GLU 葡萄糖 10.8 3.9--6.1mmol/L",
                    "ALT 谷丙转氨酶 32.1 0--40U/L",
                    "TP 总蛋白 84.2↑ 64--82g/L",
                    "ALB 白蛋白 52.4↑ 34--50g/L",
                    "GLO 球蛋白 34.2 20--40g/L",
                    "A/G 白球比 1.6 1.3--2.5",
                ]
            ),
        )
        case = CaseRecord(
            id="case_1",
            customer_name="化验图片",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
            extracted_lab_items=[
                ExtractedLabItem(
                    marker_code="fasting_glucose",
                    marker_name="空腹血糖",
                    value=10.8,
                    unit="mmol/L",
                    normalized_value=10.8,
                    normalized_unit="mmol/L",
                    abnormal_flag=AbnormalFlag.high,
                    confidence=0.95,
                    source_span=SourceSpan(file_name="case.png", page=1, line_number=1, snippet="GLU 葡萄糖 10.8 3.9--6.1mmol/L"),
                )
            ],
        )

        indicators = service.build(case)
        by_name = {item.indicator_name: item for item in indicators}

        self.assertIn("空腹血糖", by_name)
        self.assertIn("谷丙转氨酶", by_name)
        self.assertIn("总蛋白", by_name)
        self.assertIn("白蛋白", by_name)
        self.assertIn("球蛋白", by_name)
        self.assertIn("白球比", by_name)
        self.assertEqual(by_name["谷丙转氨酶"].status.value, "normal")
        self.assertEqual(by_name["球蛋白"].status.value, "normal")
        self.assertEqual(by_name["白球比"].status.value, "normal")

    def test_dedupes_docx_table_row_with_attached_abbreviation(self) -> None:
        service = CaseIndicatorService()
        source_span = SourceSpan(
            file_name="word-report.docx",
            page=1,
            line_number=1,
            snippet="总胆固醇 | TC | 5.38 | | 0—5.18 | mmol/L",
        )
        file = UploadedFile(
            id="file_docx_lipid",
            case_id="case_docx_lipid",
            filename="word-report.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=2048,
            parse_status=FileParseStatus.parsed,
            corrected_text="总胆固醇 | TC | 5.38 | | 0—5.18 | mmol/L",
        )
        case = CaseRecord(
            id="case_docx_lipid",
            customer_name="DOCX 血脂",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
            extracted_lab_items=[
                ExtractedLabItem(
                    marker_code="total_cholesterol",
                    marker_name="总胆固醇",
                    raw_name="总胆固醇",
                    value=5.38,
                    unit="mmol/L",
                    normalized_value=5.38,
                    normalized_unit="mmol/L",
                    abnormal_flag=AbnormalFlag.high,
                    confidence=0.95,
                    source_span=source_span,
                )
            ],
        )

        indicators = service.build(case)
        cholesterol = [item for item in indicators if "总胆固醇" in item.indicator_name]

        self.assertEqual(len(cholesterol), 1)
        self.assertEqual(cholesterol[0].indicator_name, "总胆固醇")
        self.assertEqual(cholesterol[0].result_text, "5.38 mmol/L")

    def test_ignores_exam_keyword_inside_explanatory_narrative(self) -> None:
        service = CaseIndicatorService()
        file = UploadedFile(
            id="file_narrative",
            case_id="case_narrative",
            filename="explanation.txt",
            content_type="text/plain",
            size_bytes=1024,
            parse_status=FileParseStatus.parsed,
            corrected_text=(
                "总胆固醇增高的原因包括高脂蛋白血症、糖尿病、"
                "甲状腺功能减退症、胆汁淤积性黄疸等疾病。"
            ),
        )
        case = CaseRecord(
            id="case_narrative",
            customer_name="解释文字",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
        )

        indicators = service.build(case)

        self.assertNotIn("甲状腺功能", {item.indicator_name for item in indicators})

    def test_extracts_stacked_lab_rows(self) -> None:
        service = CaseIndicatorService()
        file = UploadedFile(
            id="file_2",
            case_id="case_2",
            filename="R.jpg",
            content_type="image/jpeg",
            size_bytes=2048,
            parse_status=FileParseStatus.parsed,
            corrected_text="\n".join(
                [
                    "检验项目: 生化-肾功",
                    "血清尿素",
                    "4.40",
                    "2.8-7.2",
                    "mmol/L",
                    "血清肌酐",
                    "67.60",
                    "59-104",
                    "umol/L",
                    "血清尿酸",
                    "529.7",
                    "90-420",
                    "umol/L",
                ]
            ),
        )
        case = CaseRecord(
            id="case_2",
            customer_name="分行化验单",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
        )

        indicators = service.build(case)
        by_name = {item.indicator_name: item for item in indicators}

        self.assertIn("血清尿素", by_name)
        self.assertIn("血清肌酐", by_name)
        self.assertIn("血清尿酸", by_name)
        self.assertEqual(by_name["血清尿素"].status.value, "normal")
        self.assertEqual(by_name["血清肌酐"].status.value, "normal")
        self.assertEqual(by_name["血清尿酸"].status.value, "attention")

    def test_extracts_case_labs_even_when_not_prestandardized(self) -> None:
        service = CaseIndicatorService()
        file = UploadedFile(
            id="file_4",
            case_id="case_4",
            filename="iron-panel.jpg",
            content_type="image/jpeg",
            size_bytes=4096,
            parse_status=FileParseStatus.parsed,
            corrected_text="\n".join(
                [
                    "主诉: 乏力、头晕、月经量偏多半年",
                    "血清铁 6.2 9-27 μmol/L ↓",
                    "铁蛋白 11 12-150 ng/mL ↓",
                    "维生素B12 350 180-914 pg/mL 正常",
                    "叶酸 8.5 3.1-19.9 ng/mL 正常",
                    "血钙 2.25 2.11-2.52 mmol/L 正常",
                    "25-羟维生素D 22 20-100 ng/mL 正常",
                ]
            ),
        )
        case = CaseRecord(
            id="case_4",
            customer_name="营养缺口",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
        )

        indicators = service.build(case)
        by_name = {item.indicator_name: item for item in indicators}

        self.assertIn("血清铁", by_name)
        self.assertIn("铁蛋白", by_name)
        self.assertIn("维生素B12", by_name)
        self.assertIn("叶酸", by_name)
        self.assertIn("血钙", by_name)
        self.assertIn("25-羟维生素D", by_name)
        self.assertEqual(by_name["血清铁"].status.value, "attention")
        self.assertEqual(by_name["维生素B12"].status.value, "normal")
        self.assertEqual(by_name["血钙"].status.value, "normal")

    def test_skips_lab_items_extracted_from_order_metadata_lines(self) -> None:
        service = CaseIndicatorService()
        case = CaseRecord(
            id="case_admin_line",
            customer_name="医嘱元信息",
            created_at=utc_now(),
            updated_at=utc_now(),
            extracted_lab_items=[
                ExtractedLabItem(
                    marker_code="homocysteine",
                    marker_name="同型半胱氨酸",
                    value=14.0,
                    unit=None,
                    normalized_value=14.0,
                    normalized_unit="umol/L",
                    abnormal_flag=AbnormalFlag.high,
                    confidence=0.95,
                    source_span=SourceSpan(
                        file_name="scan.pdf",
                        page=1,
                        line_number=1,
                        snippet="医嘱名: 同型半胱氨酸+肝功14项",
                    ),
                ),
                ExtractedLabItem(
                    marker_code="homocysteine",
                    marker_name="同型半胱氨酸",
                    value=12.6,
                    unit="umol/L",
                    normalized_value=12.6,
                    normalized_unit="umol/L",
                    abnormal_flag=AbnormalFlag.normal,
                    confidence=0.95,
                    source_span=SourceSpan(
                        file_name="scan.pdf",
                        page=1,
                        line_number=2,
                        snippet="HCY 同型半胱氨酸 12.6 4.0-15.4 μmol/L",
                    ),
                ),
            ],
        )

        indicators = service.build(case)
        homocysteine = [item for item in indicators if item.indicator_name == "同型半胱氨酸"]

        self.assertEqual(len(homocysteine), 1)
        self.assertEqual(homocysteine[0].result_text, "12.6 umol/L")
        self.assertEqual(homocysteine[0].status.value, "normal")

    def test_extracts_multiple_labs_from_one_row(self) -> None:
        service = CaseIndicatorService()
        file = UploadedFile(
            id="file_5",
            case_id="case_5",
            filename="cbc.jpg",
            content_type="image/jpeg",
            size_bytes=4096,
            parse_status=FileParseStatus.parsed,
            corrected_text="1 白细胞(WBC) 5.25 4-10 10^9/L BC5800 15 红细胞平均体积(MCV) 89.7 82-99 fL",
        )
        case = CaseRecord(
            id="case_5",
            customer_name="血常规",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
        )

        indicators = service.build(case)
        by_name = {item.indicator_name: item for item in indicators}

        self.assertIn("白细胞", by_name)
        self.assertIn("红细胞平均体积", by_name)
        self.assertEqual(by_name["白细胞"].status.value, "normal")
        self.assertEqual(by_name["红细胞平均体积"].status.value, "normal")

    def test_extracts_vertical_lipid_report_rows(self) -> None:
        service = CaseIndicatorService()
        file = UploadedFile(
            id="file_6",
            case_id="case_6",
            filename="lipid.jpg",
            content_type="image/jpeg",
            size_bytes=4096,
            parse_status=FileParseStatus.parsed,
            corrected_text="\n".join(
                [
                    "一、检测结果",
                    "血脂检测(样本类型:xx;条码号:xxxxxx)",
                    "甘油三酯",
                    "TG",
                    "1.45",
                    "0.56~1.70",
                    "脂蛋白检测(样本类型:xx;条码号:xxxxxx)",
                    "高密度脂蛋白胆固醇",
                    "HDL-C",
                    "1.39",
                    "0.91~1.55",
                    "非高密度脂蛋白胆固醇",
                    "非HDL-C",
                    "5.84",
                    "计算法",
                    "载脂蛋白A1",
                    "Apo A1",
                    "1.59",
                    "g/L",
                    "1.20~1.60",
                ]
            ),
        )
        case = CaseRecord(
            id="case_6",
            customer_name="血脂报告",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
        )

        indicators = service.build(case)
        by_name = {item.indicator_name: item for item in indicators}

        self.assertIn("甘油三酯", by_name)
        self.assertIn("高密度脂蛋白胆固醇", by_name)
        self.assertIn("非高密度脂蛋白胆固醇", by_name)
        self.assertIn("载脂蛋白A1", by_name)
        self.assertEqual(by_name["甘油三酯"].status.value, "normal")
        self.assertEqual(by_name["高密度脂蛋白胆固醇"].status.value, "normal")
        self.assertEqual(by_name["非高密度脂蛋白胆固醇"].status.value, "info")
        self.assertEqual(by_name["载脂蛋白A1"].status.value, "normal")

    def test_builds_case_indicators_from_uploaded_case_text(self) -> None:
        service = CaseIndicatorService()
        file = UploadedFile(
            id="file_3",
            case_id="case_3",
            filename="case.png",
            content_type="image/png",
            size_bytes=1024,
            parse_status=FileParseStatus.parsed,
            corrected_text="\n".join(
                [
                    "【病例介绍】",
                    "主诉",
                    "主诉口角歪斜，右眼闭合无力2天",
                    "查体",
                    "T: 36.5℃ P: 80次/分 R: 18次/分 BP: 105/69mmHg",
                    "辅助检查",
                    "血常规: 无异常 生化: 肝功、肾功、离子、血糖、血脂、肌酐无异常 心电图: 窦性心律，正常心电图",
                ]
            ),
        )
        case = CaseRecord(
            id="case_3",
            customer_name="测试病例",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
        )

        indicators = service.build(case)
        by_name = {item.indicator_name: item for item in indicators}

        self.assertIn("主诉", by_name)
        self.assertIn("体温", by_name)
        self.assertIn("心率", by_name)
        self.assertIn("呼吸", by_name)
        self.assertIn("血压", by_name)
        self.assertIn("血常规", by_name)
        self.assertIn("生化", by_name)
        self.assertIn("心电图", by_name)
        self.assertEqual(by_name["主诉"].status.value, "attention")
        self.assertEqual(by_name["体温"].result_text, "36.5℃")
        self.assertEqual(by_name["血压"].result_text, "105/69mmHg")
        self.assertEqual(by_name["血常规"].status.value, "normal")
        self.assertEqual(by_name["主诉"].source_span.snippet, "主诉口角歪斜，右眼闭合无力2天")
        self.assertEqual(by_name["体温"].source_span.snippet, "T: 36.5℃")
        self.assertEqual(by_name["心电图"].source_span.snippet, "心电图: 窦性心律，正常心电图")

    def test_uses_precise_snippets_for_case_text_indicators(self) -> None:
        service = CaseIndicatorService()
        file = UploadedFile(
            id="file_7",
            case_id="case_7",
            filename="case-detail.txt",
            content_type="text/plain",
            size_bytes=1024,
            parse_status=FileParseStatus.parsed,
            corrected_text="\n".join(
                [
                    "主诉口角歪斜，右眼闭合无力2天",
                    "查体周围性面瘫，右眼闭合无力，口角左偏",
                    "辅助检查异常。尿常规及便常规: 无异常 心电图: 窦性心律，正常心电图 头CT 未见出血。头磁共振无新发脑",
                ]
            ),
        )
        case = CaseRecord(
            id="case_7",
            customer_name="片段精度",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
        )

        indicators = service.build(case)
        by_name = {item.indicator_name: item for item in indicators}

        self.assertEqual(by_name["主诉"].source_span.snippet, "主诉口角歪斜，右眼闭合无力2天")
        self.assertEqual(by_name["阳性体征"].source_span.snippet, "查体周围性面瘫；右眼闭合无力；口角左偏")
        self.assertEqual(by_name["尿常规及便常规"].source_span.snippet, "尿常规及便常规: 无异常")
        self.assertEqual(by_name["心电图"].source_span.snippet, "心电图: 窦性心律，正常心电图")
        self.assertEqual(by_name["头CT"].source_span.snippet, "头CT 未见出血")

    def test_filters_row_numbers_from_stacked_pptx_like_rows(self) -> None:
        service = CaseIndicatorService()
        file = UploadedFile(
            id="file_8",
            case_id="case_8",
            filename="pptx-like.txt",
            content_type="text/plain",
            size_bytes=1024,
            parse_status=FileParseStatus.parsed,
            corrected_text="\n".join(
                [
                    "体质指数",
                    "27.42",
                    "18.5-24",
                    "腰围",
                    "85",
                    "60-84.999",
                    "维生素 C",
                    "VitC",
                    "10",
                    "酮体",
                    "KET",
                    "12",
                    "亚硝酸盐",
                    "NIT",
                    "13",
                    "PH 值",
                    "PH",
                    "6.0",
                    "4.6-8.0",
                    "14",
                    "酵母样菌",
                    "Mildew",
                    "20",
                    "透明管型",
                    "HC",
                    "22",
                    "非透明管型",
                    "NHC",
                    "23",
                    "黏液丝",
                    "MS",
                    "703",
                    "未见",
                    "24",
                    "血清白蛋白",
                    "ALB",
                    "43",
                    "38-55",
                    "g/L",
                ]
            ),
        )
        case = CaseRecord(
            id="case_8",
            customer_name="PPTX 体检单",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
        )

        indicators = service.build(case)
        by_name = {item.indicator_name: item for item in indicators}

        self.assertIn("体质指数", by_name)
        self.assertIn("腰围", by_name)
        self.assertIn("PH 值", by_name)
        self.assertIn("血清白蛋白", by_name)
        self.assertNotIn("维生素 C", by_name)
        self.assertNotIn("酮体", by_name)
        self.assertNotIn("亚硝酸盐", by_name)
        self.assertNotIn("酵母样菌", by_name)
        self.assertNotIn("透明管型", by_name)
        self.assertNotIn("非透明管型", by_name)
        self.assertEqual(by_name["PH 值"].source_span.snippet, "PH 值 PH 6.0 4.6-8.0")

    def test_sorts_normal_before_info_items(self) -> None:
        service = CaseIndicatorService()
        file = UploadedFile(
            id="file_9",
            case_id="case_9",
            filename="sorting.txt",
            content_type="text/plain",
            size_bytes=512,
            parse_status=FileParseStatus.parsed,
            corrected_text="\n".join(
                [
                    "心电图",
                    "检查",
                    "PH 值",
                    "PH",
                    "6.0",
                    "4.6-8.0",
                    "14",
                ]
            ),
        )
        case = CaseRecord(
            id="case_9",
            customer_name="指标排序",
            created_at=utc_now(),
            updated_at=utc_now(),
            files=[file],
        )

        indicators = service.build(case)

        self.assertEqual([item.indicator_name for item in indicators], ["PH 值"])


if __name__ == "__main__":
    unittest.main()
