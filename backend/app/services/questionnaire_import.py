from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from app.domain.models import Questionnaire


@dataclass
class QuestionnaireParseResult:
    questionnaire: Questionnaire
    uncertain_fields: dict[str, list[str]] = field(default_factory=dict)
    partial_fields: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    field_warnings: dict[str, str] = field(default_factory=dict)

    def add_uncertainty(self, field_name: str, evidence: str, message: str) -> None:
        cleaned = re.sub(r"\s+", " ", evidence or "").strip()
        if cleaned:
            self.uncertain_fields.setdefault(field_name, []).append(cleaned[:800])
        else:
            self.uncertain_fields.setdefault(field_name, [])
        previous = self.field_warnings.get(field_name)
        if previous and previous in self.warnings:
            self.warnings.remove(previous)
        self.field_warnings[field_name] = message
        if message not in self.warnings:
            self.warnings.append(message)

    def add_partial(self, field_name: str, evidence: str, message: str) -> None:
        cleaned = re.sub(r"\s+", " ", evidence or "").strip()
        if cleaned:
            self.partial_fields.setdefault(field_name, []).append(cleaned[:800])
        else:
            self.partial_fields.setdefault(field_name, [])
        previous = self.field_warnings.pop(field_name, None)
        if previous and previous in self.warnings:
            self.warnings.remove(previous)
        self.field_warnings[field_name] = message
        if message not in self.warnings:
            self.warnings.append(message)


class QuestionnaireImportService:
    PARSER_VERSION = "msq-structured-v5-medication-fields"
    _WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    _MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    _DOCX_SUFFIXES = {".docx"}
    _PDF_SUFFIXES = {".pdf"}
    _CHECKED_MARKERS = {"☑", "√", "■"}
    _ALL_MARKERS = {"☑", "□", "√", "■"}
    _MSQ_SECTION_MAP = {
        "头/脑力方面": ["头部", "思维"],
        "眼睛": ["眼部"],
        "耳朵": ["耳部"],
        "耳部": ["耳部"],
        "鼻子": ["鼻部"],
        "口腔": ["口腔/咽喉"],
        "心脏": ["心脏"],
        "肺/喉咙": ["口腔/咽喉", "肺部"],
        "消化功能": ["消化道"],
        "免疫功能": ["其他"],
        "关节/肌肉": ["关节/肌肉", "能量/活动"],
        "头发/皮肤": ["皮肤"],
        "体能及情绪": ["能量/活动", "情绪"],
        "排泄功能": ["消化道"],
        "荷尔蒙及性功": ["其他"],
        "慢性疲劳症": ["能量/活动", "头部"],
    }
    _TEMPLATE_MARKERS = (
        "症状评估",
        "级别序号症状描述从来没有",
        "您希望以何种方式来促进健康",
        "您的睡眠质量如何",
        "您日常三餐主要食用",
    )
    _DERIVED_AGE_LABELS = (
        "初次月经年龄",
        "停经年龄",
        "绝经年龄",
        "生物年龄",
        "代谢年龄",
        "骨龄",
        "系统年龄",
    )
    _EMPTY_VALUES: dict[str, Any] = {
        "age": None,
        "sex": "unknown",
        "medications": [],
        "allergies": [],
        "pregnant_or_lactating": None,
        "sleep_hours": None,
        "sleep_quality": None,
        "diet_pattern": None,
        "exercise_frequency": None,
        "symptoms": [],
        "msq_system_scores": {},
    }

    def matches_template(self, *, filename: str, content_type: str, content: bytes) -> bool:
        """Recognize the fixed MSQ form from its content, never from filename alone."""
        suffix = Path(filename).suffix.lower()
        has_structured_symptom_table = False
        try:
            if suffix in self._DOCX_SUFFIXES:
                paragraphs, tables = self._extract_docx_structure(content)
                has_structured_symptom_table = bool(
                    self._find_msq_symptom_tables(tables)
                )
                text = " ".join(
                    [*paragraphs, *(cell for table in tables for row in table for cell in row)]
                )
            elif suffix in self._PDF_SUFFIXES:
                text = self._extract_pdf_text(content)
            else:
                return False
        except ValueError:
            return False

        compact = re.sub(r"\s+", "", text)
        marker_count = sum(marker in compact for marker in self._TEMPLATE_MARKERS)
        has_symptom_matrix = (
            has_structured_symptom_table
            or self._has_msq_symptom_header(compact)
            or ("症状评估" in compact and all(score in compact for score in ("0", "1", "2", "3", "4")))
        )
        if has_structured_symptom_table and self._TEMPLATE_MARKERS[1] not in compact:
            marker_count += 1
        return has_symptom_matrix and marker_count >= 2

    def parse(self, *, filename: str, content_type: str, content: bytes) -> Questionnaire:
        return self.parse_for_analysis(
            filename=filename,
            content_type=content_type,
            content=content,
        ).questionnaire

    def parse_for_analysis(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> QuestionnaireParseResult:
        suffix = Path(filename).suffix.lower()
        if suffix in self._DOCX_SUFFIXES:
            paragraphs, tables = self._extract_docx_structure(content)
            questionnaire = self._build_questionnaire(paragraphs=paragraphs, tables=tables)
            if not self._has_meaningful_content(questionnaire):
                raise ValueError("未能从该 MSQ 问卷中识别出有效字段，请检查文档内容或人工填写。")
            return self._assess_docx_result(questionnaire, paragraphs, tables)

        if suffix in self._PDF_SUFFIXES:
            text = self._extract_pdf_text(content)
            questionnaire = self._build_pdf_questionnaire(text)
            if not self._has_meaningful_content(questionnaire):
                raise ValueError("未能从该 MSQ PDF 问卷中识别出有效字段，请检查文档内容或人工填写。")
            return self._assess_pdf_result(questionnaire, text)

        raise ValueError("当前问卷导入支持已填写的 DOCX 或 PDF 文件。")

    def _extract_pdf_text(self, content: bytes) -> str:
        try:
            reader = PdfReader(BytesIO(content))
        except Exception as exc:
            raise ValueError("问卷 PDF 读取失败，请确认文件未损坏。") from exc

        page_texts = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(page_texts)
        if not self._clean_text(text):
            raise ValueError("问卷 PDF 未识别到可读取文字；如果是扫描图片版，请先转换为可复制文本 PDF 或 DOCX。")
        return text

    def _extract_docx_structure(self, content: bytes) -> tuple[list[str], list[list[list[str]]]]:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                xml = archive.read("word/document.xml")
        except Exception as exc:
            raise ValueError("问卷 DOCX 读取失败，请确认文件未损坏。") from exc

        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ValueError("问卷 DOCX 结构无法解析，请确认文件格式正确。") from exc

        self._select_active_alternate_content(root)

        paragraphs: list[str] = []
        body = root.find("w:body", self._WORD_NS)
        if body is not None:
            paragraph_tag = f"{{{self._WORD_NS['w']}}}p"
            for child in body:
                if child.tag != paragraph_tag:
                    continue
                text = "".join(node.text or "" for node in child.findall(".//w:t", self._WORD_NS))
                cleaned = self._clean_text(text)
                if cleaned:
                    paragraphs.append(cleaned)

        tables: list[list[list[str]]] = []
        for table in root.findall(".//w:tbl", self._WORD_NS):
            rows: list[list[str]] = []
            for row in table.findall("w:tr", self._WORD_NS):
                cells: list[str] = []
                for cell in row.findall("w:tc", self._WORD_NS):
                    text = "".join(node.text or "" for node in cell.findall(".//w:t", self._WORD_NS))
                    cells.append(self._clean_text(text))
                if any(cells):
                    rows.append(cells)
            if rows:
                tables.append(rows)
        return paragraphs, tables

    def _select_active_alternate_content(self, root: ET.Element) -> None:
        """Keep one Word compatibility branch so visible content is parsed once."""
        alternate_tag = f"{{{self._MC_NS}}}AlternateContent"
        choice_tag = f"{{{self._MC_NS}}}Choice"
        fallback_tag = f"{{{self._MC_NS}}}Fallback"

        def visit(parent: ET.Element) -> None:
            for child in list(parent):
                if child.tag != alternate_tag:
                    visit(child)
                    continue

                choice = next(
                    (item for item in list(child) if item.tag == choice_tag),
                    None,
                )
                fallback = next(
                    (item for item in list(child) if item.tag == fallback_tag),
                    None,
                )
                selected = choice if choice is not None else fallback
                insert_at = list(parent).index(child)
                parent.remove(child)
                if selected is None:
                    continue
                for selected_child in list(selected):
                    parent.insert(insert_at, selected_child)
                    visit(selected_child)
                    insert_at += 1

        visit(root)

    def _build_pdf_questionnaire(self, text: str) -> Questionnaire:
        full_text = self._normalize_pdf_text(text)

        demographic_lines = self._pdf_demographic_lines(text)
        age, sex, _, _ = self._extract_patient_demographics(demographic_lines)

        known_conditions: list[str] = []
        chief_concerns: list[str] = []
        family_history: list[str] = []
        medications: list[str] = []
        allergies: list[str] = []
        food_sensitivities: list[str] = []
        symptoms: list[str] = []
        emotional_state: list[str] = []
        goals: list[str] = []
        msq_system_scores: dict[str, int] = {}
        additional_notes: list[str] = ["由已填写 MSQ PDF 问卷自动导入，建议人工核对后再生成最终报告。"]

        major_problem = self._extract_between_text(full_text, "您最近一次体检查出的主要问题？", "2. 您是否被诊断出患有某种慢性疾病？")
        major_items = self._split_pdf_terms(major_problem)
        known_conditions.extend(major_items)
        chief_concerns.extend(major_items[:2])

        family_block = self._extract_between_text(full_text, "第二部分：家族病史", "第三部分：生活和饮食习惯")
        self_block = self._extract_between_text(family_block, "您本人", "您的父亲")
        known_conditions.extend(self._split_pdf_terms(self_block.split("无", 1)[0]))
        father_block = self._extract_between_text(family_block, "您的父亲", "您的母亲")
        mother_block = self._extract_between_text(family_block, "您的母亲", "第三部分")
        for member, block in (("父亲", father_block), ("母亲", mother_block)):
            items = self._split_pdf_terms(block.rsplit("无", 1)[0])
            if items:
                family_history.append(f"{member}：{'、'.join(self._dedupe(items))}")

        sleep_block = self._extract_between_text(full_text, "您的睡眠质量如何？", "健康信息调查问卷 第一页", "第三部分：生活和饮食习惯（续）")
        sleep_quality_line = self._extract_between_text(sleep_block, "", "原因:", "2. 您一般什么时间上床睡觉？")
        sleep_quality_parts = self._checked_labels(sleep_quality_line)
        bedtime_text = self._extract_between_text(sleep_block, "2. 您一般什么时间上床睡觉？", "3. 您一般早上什么时间醒来？")
        wake_text = self._extract_between_text(sleep_block, "3. 您一般早上什么时间醒来？", "健康信息调查问卷 第一页")
        bedtime = self._extract_time_value(bedtime_text)
        wake_time = self._extract_time_value(wake_text)
        sleep_hours: float | None = None
        if bedtime is not None and wake_time is not None:
            if wake_time < bedtime:
                wake_time += 24
            sleep_hours = round(wake_time - bedtime, 1)

        diet_block = self._extract_between_text(full_text, "4. 您日常三餐主要食用？", "第四部分：营养品补充")
        primary_diet_line = self._extract_between_text(diet_block, "", "5. 您能够规律地吃早餐吗？")
        breakfast_line = self._extract_between_text(diet_block, "5. 您能够规律地吃早餐吗？", "6.常饮用：")
        drink_line = self._extract_between_text(diet_block, "6.常饮用：", "7. 您喝酒吗？")
        alcohol_line = self._extract_between_text(diet_block, "7. 您喝酒吗？", "10. 每周外出就餐次数？")
        dining_line = self._extract_between_text(diet_block, "10. 每周外出就餐次数？", "11. 经常使用快餐或加工食物？")
        processed_food_line = self._extract_between_text(diet_block, "11. 经常使用快餐或加工食物？", "12. 对食物添加物及防腐剂敏感？")
        additive_line = self._extract_between_text(diet_block, "12. 对食物添加物及防腐剂敏感？", "第四部分")

        diet_pattern_parts: list[str] = []
        seafood_intake_ratio = self._extract_percent_after(primary_diet_line, "鱼类和海鲜")
        red_meat_intake_ratio = self._extract_percent_after(primary_diet_line, "红肉")
        rice_ratio = self._extract_percent_after(primary_diet_line, "米饭")
        noodle_ratio = self._extract_percent_after(primary_diet_line, "面食")
        vegetable_ratio = self._extract_percent_after(primary_diet_line, "蔬菜")
        fruit_ratio = self._extract_percent_after(primary_diet_line, "水果")
        if any([rice_ratio, noodle_ratio, vegetable_ratio, fruit_ratio, red_meat_intake_ratio, seafood_intake_ratio]):
            summary_parts: list[str] = []
            if rice_ratio:
                summary_parts.append(f"米饭约{rice_ratio}")
            if noodle_ratio:
                summary_parts.append(f"面食约{noodle_ratio}")
            if vegetable_ratio:
                summary_parts.append(f"蔬菜约{vegetable_ratio}")
            if fruit_ratio:
                summary_parts.append(f"水果约{fruit_ratio}")
            if red_meat_intake_ratio:
                summary_parts.append(f"红肉约{red_meat_intake_ratio}")
            if seafood_intake_ratio:
                summary_parts.append(f"鱼海鲜约{seafood_intake_ratio}")
            diet_pattern_parts.append("三餐结构：" + "，".join(summary_parts))

        breakfast_choices = self._checked_labels(breakfast_line)
        if breakfast_choices:
            diet_pattern_parts.append(f"早餐：{'、'.join(self._dedupe(breakfast_choices))}")
        drink_parts = self._extract_pdf_drink_parts(drink_line)
        if drink_parts:
            diet_pattern_parts.append("饮品：" + "，".join(drink_parts))
        alcohol_summary = self._extract_pdf_alcohol_summary(alcohol_line)
        if alcohol_summary:
            diet_pattern_parts.append(alcohol_summary)
        if self._is_no_selected(processed_food_line):
            diet_pattern_parts.append("较少使用快餐或加工食物")
        if self._is_yes_selected(additive_line):
            food_sensitivities.append("食品添加剂/防腐剂")
        dining_out_frequency = None
        dining_match = re.search(r"(\d+次)", dining_line)
        if dining_match:
            dining_out_frequency = f"每周{dining_match.group(1)}"

        supplement_block = self._extract_between_text(full_text, "第四部分：营养品补充", "第五部分：工作")
        supplement_use = self._extract_pdf_supplement_use(supplement_block)

        work_block = self._extract_between_text(full_text, "第五部分：工作", "第八部分：男性/女性状况")
        work_pattern_parts: list[str] = []
        sitting_hours_per_day: float | None = None
        work_hours_match = re.search(r"工作小时数.*?(\d+(?:\.\d+)?)\s*[＿_\s]*小时/日", work_block)
        computer_hours_match = re.search(r"使用电脑时间多长？\s*(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?\s*小时/日", work_block)
        if work_hours_match:
            work_pattern_parts.append(f"工作{work_hours_match.group(1)}小时/日")
        if computer_hours_match:
            if computer_hours_match.group(2):
                start = float(computer_hours_match.group(1))
                end = float(computer_hours_match.group(2))
                sitting_hours_per_day = round((start + end) / 2, 1)
                work_pattern_parts.append(f"电脑{computer_hours_match.group(1)}-{computer_hours_match.group(2)}小时/日")
            else:
                sitting_hours_per_day = float(computer_hours_match.group(1))
                work_pattern_parts.append(f"电脑{computer_hours_match.group(1)}小时/日")

        chemical_sensitivity = None
        sensitivity_line = self._extract_between_text(work_block, "2. 对杀虫剂", "3. 在工作或居家环境附近")
        if self._is_yes_selected(sensitivity_line):
            chemical_sensitivity = "对杀虫剂、烟雾、香水等刺激明显敏感"
        travel_line = self._extract_between_text(work_block, "5. 长途旅行后会有时差症状？", "第七部分：运动状况")
        if self._is_yes_selected(travel_line):
            additional_notes.append("长途旅行后有时差反应")

        exercise_block = self._extract_between_text(work_block, "第七部分：运动状况", "第八部分")
        exercise_frequency = None
        exercise_habit_line = self._extract_between_text(exercise_block, "1. 有运动习惯？", "2. 每周运动量")
        if self._is_yes_selected(exercise_habit_line):
            exercise_frequency = "有运动习惯"
        elif self._is_no_selected(exercise_habit_line):
            exercise_frequency = "无规律运动"
        stress_relief_line = self._extract_between_text(exercise_block, "3. 经常练习任何舒解压力的活动、瑜伽或静坐", "4. 体能较差")
        if self._is_yes_selected(stress_relief_line):
            additional_notes.append("有瑜伽、静坐或其他减压习惯")
        activity_limit_line = self._extract_between_text(exercise_block, "6. 目前的体能状况限制了体能活动", "第八部分")
        if self._is_yes_selected(activity_limit_line):
            additional_notes.append("当前体能状况限制部分体能活动")

        pdf_symptoms, pdf_emotions, bowel_markers, pdf_scores = self._extract_pdf_msq_symptoms(full_text)
        symptoms.extend(pdf_symptoms)
        emotional_state.extend(pdf_emotions)
        msq_system_scores.update(pdf_scores)

        goal_block = self._extract_between_text(full_text, "您希望以何种方式来促进健康呢", "第九部分：症状评估")
        if goal_block:
            goals.extend(self._checked_labels(goal_block))
            priority_match = re.search(r"最希望医生帮您解决的问题是.*?A\s*([^\sB]+)", goal_block)
            if priority_match:
                goals.append(priority_match.group(1))

        chief_concerns = self._dedupe(goals[:2] + chief_concerns + known_conditions[:2])
        sleep_quality = "；".join(self._dedupe(sleep_quality_parts)) or None
        diet_pattern = "；".join(self._dedupe(diet_pattern_parts)) or None
        bowel_habits = "、".join(self._dedupe(bowel_markers)) or None
        work_pattern = "；".join(self._dedupe(work_pattern_parts)) or None
        if sleep_quality:
            additional_notes.append(f"睡眠：{sleep_quality}")

        return Questionnaire(
            age=age,
            sex=sex,
            chief_concerns=self._dedupe(chief_concerns),
            symptoms=self._dedupe(symptoms),
            known_conditions=self._dedupe(known_conditions),
            family_history=self._dedupe(family_history),
            medications=self._dedupe(medications),
            allergies=self._dedupe(allergies),
            food_sensitivities=self._dedupe(food_sensitivities),
            pregnant_or_lactating=None,
            diet_pattern=diet_pattern,
            work_pattern=work_pattern,
            sitting_hours_per_day=sitting_hours_per_day,
            dining_out_frequency=dining_out_frequency,
            seafood_intake_ratio=seafood_intake_ratio,
            red_meat_intake_ratio=red_meat_intake_ratio,
            supplement_use=supplement_use,
            chemical_sensitivity=chemical_sensitivity,
            sleep_hours=sleep_hours,
            sleep_quality=sleep_quality,
            exercise_frequency=exercise_frequency,
            bowel_habits=bowel_habits,
            stress_level=None,
            emotional_state=self._dedupe(emotional_state),
            goals=self._dedupe(goals),
            msq_system_scores=msq_system_scores,
            additional_notes="；".join(self._dedupe(additional_notes)) or None,
        )

    def _build_questionnaire(self, *, paragraphs: list[str], tables: list[list[list[str]]]) -> Questionnaire:
        demographic_lines = self._docx_demographic_lines(paragraphs, tables)
        age, sex, _, _ = self._extract_patient_demographics(demographic_lines)

        known_conditions: list[str] = []
        chief_concerns: list[str] = []
        family_history: list[str] = []
        medications: list[str] = []
        allergies: list[str] = []
        food_sensitivities: list[str] = []
        symptoms: list[str] = []
        emotional_state: list[str] = []
        goals: list[str] = []
        msq_system_scores: dict[str, int] = {}
        additional_notes: list[str] = []

        major_problem_table = self._find_table(tables, "您最近一次体检查出的主要问题")
        if major_problem_table:
            if len(major_problem_table) > 1 and major_problem_table[1]:
                major_items = self._split_terms(major_problem_table[1][0])
                known_conditions.extend(major_items)
                chief_concerns.extend(major_items[:2])
            medication_row_index, medication_row = self._find_row(major_problem_table, "服用药物")
            allergy_row_index, allergy_row = self._find_row(major_problem_table, "药物过敏")
            if medication_row and not self._is_no_selected(" ".join(medication_row)):
                next_boundary = allergy_row_index if allergy_row_index is not None else len(major_problem_table)
                medication_rows = major_problem_table[
                    (medication_row_index or 0) + 1 : next_boundary
                ]
                medications.extend(
                    self._extract_docx_medications(medication_rows)
                )
            if allergy_row and self._is_yes_selected(" ".join(allergy_row)):
                detail_index = (allergy_row_index or 0) + 1
                if detail_index < len(major_problem_table):
                    allergies.extend(self._split_terms(" ".join(major_problem_table[detail_index])))

        family_table = self._find_table(tables, "目前正患有何种慢性疾病")
        if family_table:
            for row in family_table[1:]:
                if not row:
                    continue
                who = row[0]
                if "本人" in who:
                    for cell in row[1:3]:
                        known_conditions.extend(self._split_terms(cell))
                else:
                    normalized_who = self._normalize_family_member(who)
                    items: list[str] = []
                    for cell in row[1:3]:
                        items.extend(self._split_terms(cell))
                    if items:
                        family_history.append(f"{normalized_who}：{'、'.join(self._dedupe(items))}")

        sleep_table = self._find_table(tables, "您的睡眠质量如何")
        sleep_quality_parts: list[str] = []
        sleep_hours: float | None = None
        if sleep_table:
            for row in sleep_table[1:3]:
                sleep_quality_parts.extend(self._checked_labels(" ".join(row)))
            bedtime = self._extract_time_value(" ".join(sleep_table[4])) if len(sleep_table) > 4 else None
            wake_time = self._extract_time_value(" ".join(sleep_table[5])) if len(sleep_table) > 5 else None
            if bedtime is not None and wake_time is not None:
                if wake_time < bedtime:
                    wake_time += 24
                sleep_hours = round(wake_time - bedtime, 1)

        diet_table = self._find_table(tables, "您日常三餐主要食用")
        diet_pattern_parts: list[str] = []
        dining_out_frequency: str | None = None
        seafood_intake_ratio: str | None = None
        red_meat_intake_ratio: str | None = None
        if diet_table:
            primary_diet_line = " ".join(diet_table[1]) if len(diet_table) > 1 else ""
            breakfast_line = " ".join(diet_table[3]) if len(diet_table) > 3 else ""
            water_line = " ".join(diet_table[4]) if len(diet_table) > 4 else ""
            alcohol_line = " ".join(diet_table[5]) if len(diet_table) > 5 else ""
            dining_line = " ".join(diet_table[8]) if len(diet_table) > 8 else ""
            processed_food_line = " ".join(diet_table[9]) if len(diet_table) > 9 else ""
            additive_line = " ".join(diet_table[10]) if len(diet_table) > 10 else ""

            red_meat_intake_ratio = self._extract_percent_after(primary_diet_line, "红肉")
            seafood_intake_ratio = self._extract_percent_after(primary_diet_line, "鱼类和海鲜")
            rice_ratio = self._extract_percent_after(primary_diet_line, "米饭")
            noodle_ratio = self._extract_percent_after(primary_diet_line, "面食")
            vegetable_ratio = self._extract_percent_after(primary_diet_line, "蔬菜")
            fruit_ratio = self._extract_percent_after(primary_diet_line, "水果")

            if rice_ratio or noodle_ratio or vegetable_ratio or fruit_ratio:
                summary_parts: list[str] = []
                if rice_ratio:
                    summary_parts.append(f"米饭约{rice_ratio}")
                if noodle_ratio:
                    summary_parts.append(f"面食约{noodle_ratio}")
                if vegetable_ratio:
                    summary_parts.append(f"蔬菜约{vegetable_ratio}")
                if fruit_ratio:
                    summary_parts.append(f"水果约{fruit_ratio}")
                if red_meat_intake_ratio:
                    summary_parts.append(f"红肉约{red_meat_intake_ratio}")
                if seafood_intake_ratio:
                    summary_parts.append(f"鱼海鲜约{seafood_intake_ratio}")
                diet_pattern_parts.append("三餐结构：" + "，".join(summary_parts))

            breakfast_choices = self._checked_labels(breakfast_line)
            if breakfast_choices:
                diet_pattern_parts.append(f"早餐：{'、'.join(self._dedupe(breakfast_choices))}")

            water_match = re.search(r"白开水.*?每日(\d+)杯", water_line)
            if water_match and "☑ 白开水" in water_line:
                temp = "热" if "☑热" in water_line or "☑ 热" in water_line else "常温"
                diet_pattern_parts.append(f"饮水：{temp}白开水每日{water_match.group(1)}杯")

            if self._is_no_selected(alcohol_line):
                diet_pattern_parts.append("不饮酒")
            if self._is_no_selected(processed_food_line):
                diet_pattern_parts.append("较少使用快餐或加工食物")
            if self._is_yes_selected(additive_line):
                food_sensitivities.append("食品添加剂/防腐剂")

            dining_match = re.search(r"每周外出就餐次数？\s*(\d+次)", dining_line)
            if dining_match:
                dining_out_frequency = f"每周{dining_match.group(1)}"

        supplement_table = self._find_table(tables, "您有补充营养食品吗")
        supplement_use: str | None = None
        if supplement_table:
            header_line = " ".join(supplement_table[0])
            if self._is_no_selected(header_line):
                supplement_use = "无营养补充剂"
            else:
                supplement_items: list[str] = []
                for row in supplement_table[3:]:
                    if len(row) >= 6:
                        if self._contains_checked_marker(row[0]) or row[2]:
                            supplement_items.append(self._compose_named_frequency(row[1], row[2]))
                        if self._contains_checked_marker(row[3]) or row[5]:
                            supplement_items.append(self._compose_named_frequency(row[4], row[5]))
                if supplement_items:
                    supplement_use = "；".join(self._dedupe(supplement_items))

        work_table = self._find_table(tables, "您在工作日每天工作小时数")
        work_pattern_parts: list[str] = []
        sitting_hours_per_day: float | None = None
        chemical_sensitivity: str | None = None
        if work_table:
            work_hours_match = re.search(r"(\d+(?:\.\d+)?)\s*小时/日", " ".join(work_table[0]))
            computer_hours_match = re.search(r"(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?小时/日", " ".join(work_table[1]))
            if work_hours_match:
                work_pattern_parts.append(f"工作{work_hours_match.group(1)}小时/日")
            if computer_hours_match:
                if computer_hours_match.group(2):
                    start = float(computer_hours_match.group(1))
                    end = float(computer_hours_match.group(2))
                    sitting_hours_per_day = round((start + end) / 2, 1)
                    work_pattern_parts.append(f"电脑{computer_hours_match.group(1)}-{computer_hours_match.group(2)}小时/日")
                else:
                    sitting_hours_per_day = float(computer_hours_match.group(1))
                    work_pattern_parts.append(f"电脑{computer_hours_match.group(1)}小时/日")
            if len(work_table) > 4 and self._is_yes_selected(" ".join(work_table[4])):
                chemical_sensitivity = "对杀虫剂、烟雾、香水等刺激明显敏感"
            if len(work_table) > 7 and self._is_yes_selected(" ".join(work_table[7])):
                additional_notes.append("长途旅行后有时差反应")

        exercise_table = self._find_table(tables, "有运动习惯")
        exercise_frequency: str | None = None
        if exercise_table:
            joined_text = " ".join(" ".join(row) for row in exercise_table)
            if "有运动习惯？ □ 是 ☑ 否" in joined_text:
                exercise_frequency = "无规律运动"
            elif "有运动习惯？ ☑ 是" in joined_text:
                exercise_frequency = "有运动习惯"
            if len(exercise_table) > 2 and self._is_yes_selected(" ".join(exercise_table[2])):
                additional_notes.append("有瑜伽、静坐或其他减压习惯")

        female_table = self._find_table(tables, "初次月经年龄")
        if female_table:
            menstrual_cycle = self._extract_inline_text(female_table, "月经周期")
            menstrual_duration = self._extract_inline_text(female_table, "每次月经持续时间")
            pregnancy_count = self._extract_inline_text(female_table, "怀孕次数")
            child_count = self._extract_inline_text(female_table, "孩子数量")
            delivery_mode = self._extract_inline_text(female_table, "生产方式")
            female_notes: list[str] = []
            if menstrual_cycle:
                female_notes.append(f"月经周期{menstrual_cycle}")
            if menstrual_duration:
                female_notes.append(f"经期{menstrual_duration}")
            if pregnancy_count:
                female_notes.append(f"怀孕{pregnancy_count}")
            if child_count:
                female_notes.append(f"育有{child_count}")
            if delivery_mode:
                female_notes.append(f"生产方式{delivery_mode}")
            if female_notes:
                additional_notes.append("；".join(female_notes))

        bowel_markers: list[str] = []
        for row, current_section, status, score in self._msq_symptom_rows(tables):
            symptom_name = row[1] if len(row) > 1 else ""
            if status != "valid" or not symptom_name or score is None:
                continue
            if score > 0:
                symptoms.append(symptom_name)
                if symptom_name in {"便秘", "腹泻"}:
                    bowel_markers.append(symptom_name)
                if any(keyword in symptom_name for keyword in ("忧郁", "焦虑", "烦躁", "紧张", "情绪", "暴躁")):
                    emotional_state.append(symptom_name)
            if current_section and score > 0:
                for mapped_section in self._MSQ_SECTION_MAP[current_section]:
                    msq_system_scores[mapped_section] = max(
                        msq_system_scores.get(mapped_section, 0),
                        score,
                    )

        goal_table = self._find_table(tables, "您希望以何种方式来促进健康呢")
        if goal_table:
            if len(goal_table) > 1:
                goals.extend(self._checked_labels(" ".join(goal_table[1])))
            if len(goal_table) > 2:
                goals.extend(self._checked_labels(" ".join(goal_table[2])))
            if len(goal_table) > 3:
                priority_match = re.search(r"最希望医生帮您解决的问题是.*?A\s*([^\sB]+)", " ".join(goal_table[3]))
                if priority_match:
                    goals.append(priority_match.group(1))

        chief_concerns = self._dedupe(goals[:2] + chief_concerns + known_conditions[:2])

        sleep_quality = "；".join(self._dedupe(sleep_quality_parts)) or None
        diet_pattern = "；".join(self._dedupe(diet_pattern_parts)) or None
        bowel_habits = "、".join(self._dedupe(bowel_markers)) or None
        work_pattern = "；".join(self._dedupe(work_pattern_parts)) or None

        if not chief_concerns and known_conditions:
            chief_concerns = known_conditions[:2]
        if sleep_quality:
            additional_notes.append(f"睡眠：{sleep_quality}")
        if not supplement_use:
            supplement_use = None
        if not chemical_sensitivity:
            chemical_sensitivity = None

        if tables:
            additional_notes.insert(0, "由已填写 MSQ 问卷自动导入，建议人工核对后再生成最终报告。")

        return Questionnaire(
            age=age,
            sex=sex,
            chief_concerns=self._dedupe(chief_concerns),
            symptoms=self._dedupe(symptoms),
            known_conditions=self._dedupe(known_conditions),
            family_history=self._dedupe(family_history),
            medications=self._dedupe(medications),
            allergies=self._dedupe(allergies),
            food_sensitivities=self._dedupe(food_sensitivities),
            pregnant_or_lactating=None,
            diet_pattern=diet_pattern,
            work_pattern=work_pattern,
            sitting_hours_per_day=sitting_hours_per_day,
            dining_out_frequency=dining_out_frequency,
            seafood_intake_ratio=seafood_intake_ratio,
            red_meat_intake_ratio=red_meat_intake_ratio,
            supplement_use=supplement_use,
            chemical_sensitivity=chemical_sensitivity,
            sleep_hours=sleep_hours,
            sleep_quality=sleep_quality,
            exercise_frequency=exercise_frequency,
            bowel_habits=bowel_habits,
            stress_level=None,
            emotional_state=self._dedupe(emotional_state),
            goals=self._dedupe(goals),
            msq_system_scores=msq_system_scores,
            additional_notes="；".join(self._dedupe(additional_notes)) or None,
        )

    def _assess_docx_result(
        self,
        questionnaire: Questionnaire,
        paragraphs: list[str],
        tables: list[list[list[str]]],
    ) -> QuestionnaireParseResult:
        result = QuestionnaireParseResult(questionnaire=questionnaire)
        demographic_lines = self._docx_demographic_lines(paragraphs, tables)
        _, _, age_candidates, sex_candidates = self._extract_patient_demographics(
            demographic_lines
        )
        demographic_text = "\n".join(demographic_lines)
        invalid_patient_age = self._has_invalid_patient_age(
            demographic_lines
        )
        if invalid_patient_age:
            self._mark_uncertain(
                result,
                "age",
                demographic_text,
                "MSQ 患者年龄超出 0–120 范围，已留空，请人工确认。",
            )
        elif not age_candidates:
            self._mark_uncertain(
                result,
                "age",
                demographic_text,
                "MSQ 患者年龄未明确填写，已留空，请人工确认。",
            )
        elif len({value for value, _ in age_candidates}) > 1:
            self._mark_uncertain(
                result,
                "age",
                demographic_text,
                "MSQ 患者年龄存在多个冲突值，已留空，请人工确认。",
            )
        if len({value for value, _ in sex_candidates}) > 1:
            self._mark_uncertain(
                result,
                "sex",
                demographic_text,
                "MSQ 患者性别存在冲突，已留空，请人工确认。",
            )

        pregnancy_rows = [
            " ".join(row)
            for table in tables
            for row in table
            if "怀孕次数" not in " ".join(row)
            and any(
                keyword in " ".join(row)
                for keyword in ("目前是否怀孕", "当前是否怀孕", "妊娠状态", "正在哺乳", "哺乳期")
            )
        ]
        pregnancy_states = {
            self._yes_no_state(row)
            for row in pregnancy_rows
            if self._yes_no_state(row) != "blank"
        }
        if "conflict" in pregnancy_states or {"yes", "no"}.issubset(pregnancy_states):
            self._mark_uncertain(
                result,
                "pregnant_or_lactating",
                "\n".join(pregnancy_rows),
                "MSQ 妊娠或哺乳状态选项存在冲突，已留空，请人工确认。",
            )
        elif pregnancy_states == {"yes"}:
            result.questionnaire = result.questionnaire.model_copy(
                update={"pregnant_or_lactating": True}
            )
        elif pregnancy_states == {"no"}:
            result.questionnaire = result.questionnaire.model_copy(
                update={"pregnant_or_lactating": False}
            )

        major_table = self._find_table(tables, "您最近一次体检查出的主要问题")
        if major_table:
            medication_index, medication_row = self._find_row(major_table, "服用药物")
            allergy_index, allergy_row = self._find_row(major_table, "药物过敏")
            if medication_row:
                medication_line = " ".join(medication_row)
                medication_state = self._yes_no_state(medication_line)
                medication_end = allergy_index if allergy_index is not None else len(major_table)
                medication_detail = " ".join(
                    " ".join(row)
                    for row in major_table[(medication_index or 0) + 1 : medication_end]
                )
                if medication_state == "conflict":
                    if (
                        questionnaire.medications
                        and not self._has_only_placeholder_terms(
                            questionnaire.medications
                        )
                    ):
                        self._mark_partial(
                            result,
                            "medications",
                            f"{medication_line}\n{medication_detail}",
                            "MSQ 用药选项存在冲突，已保留明确识别的药物，请人工确认其余内容。",
                        )
                    else:
                        self._mark_uncertain(
                            result,
                            "medications",
                            f"{medication_line}\n{medication_detail}",
                            "MSQ 用药选项存在冲突，当前用药已留空，请人工确认。",
                        )
                elif medication_state == "yes" and (
                    not questionnaire.medications
                    or self._has_only_placeholder_terms(questionnaire.medications)
                ):
                    self._mark_uncertain(
                        result,
                        "medications",
                        f"{medication_line}\n{medication_detail}",
                        "MSQ 勾选了正在用药但未可靠识别药物内容，已留空，请人工确认。",
                    )
            if allergy_row:
                allergy_line = " ".join(allergy_row)
                allergy_state = self._yes_no_state(allergy_line)
                allergy_detail = (
                    " ".join(major_table[(allergy_index or 0) + 1])
                    if (allergy_index or 0) + 1 < len(major_table)
                    else ""
                )
                if allergy_state == "conflict":
                    if (
                        questionnaire.allergies
                        and not self._has_only_placeholder_terms(
                            questionnaire.allergies
                        )
                    ):
                        self._mark_partial(
                            result,
                            "allergies",
                            f"{allergy_line}\n{allergy_detail}",
                            "MSQ 药物过敏选项存在冲突，已保留明确识别的过敏信息，请人工确认其余内容。",
                        )
                    else:
                        self._mark_uncertain(
                            result,
                            "allergies",
                            f"{allergy_line}\n{allergy_detail}",
                            "MSQ 药物过敏选项存在冲突，过敏信息已留空，请人工确认。",
                        )
                elif allergy_state == "yes" and (
                    not questionnaire.allergies
                    or self._has_only_placeholder_terms(questionnaire.allergies)
                ):
                    self._mark_uncertain(
                        result,
                        "allergies",
                        f"{allergy_line}\n{allergy_detail}",
                        "MSQ 勾选了药物过敏但未可靠识别具体内容，已留空，请人工确认。",
                    )

        sleep_table = self._find_table(tables, "您的睡眠质量如何")
        if sleep_table and len(sleep_table) < 6:
            excerpt = self._table_excerpt(sleep_table)
            self._mark_uncertain(
                result,
                "sleep_quality",
                excerpt,
                "MSQ 睡眠表格结构发生变化，睡眠质量已留空，请人工确认。",
            )
            self._mark_uncertain(
                result,
                "sleep_hours",
                excerpt,
                "MSQ 睡眠时间行无法可靠定位，睡眠时长已留空，请人工确认。",
            )

        diet_table = self._find_table(tables, "您日常三餐主要食用")
        if diet_table and len(diet_table) < 10:
            self._mark_uncertain(
                result,
                "diet_pattern",
                self._table_excerpt(diet_table),
                "MSQ 饮食表格结构发生变化，饮食模式已留空，请人工确认。",
            )

        exercise_table = self._find_table(tables, "有运动习惯")
        if exercise_table:
            exercise_row = next(
                (" ".join(row) for row in exercise_table if "有运动习惯" in " ".join(row)),
                "",
            )
            if self._yes_no_state(exercise_row) == "conflict":
                self._mark_uncertain(
                    result,
                    "exercise_frequency",
                    exercise_row,
                    "MSQ 运动习惯选项存在冲突，已留空，请人工确认。",
                )

        ambiguous_rows: list[str] = []
        ambiguous_systems: set[str] = set()
        for row, current_section, status, _ in self._msq_symptom_rows(tables):
            if status != "ambiguous":
                continue
            ambiguous_rows.append(" | ".join(row))
            ambiguous_systems.update(
                self._MSQ_SECTION_MAP.get(current_section, [])
            )
        if ambiguous_rows:
            evidence = "\n".join(ambiguous_rows[:12])
            self._mark_partial(
                result,
                "symptoms",
                evidence,
                f"MSQ 有 {len(ambiguous_rows)} 条症状评分存在多选或列错位，"
                "已忽略这些条目，其余已确认症状保留。",
            )
            retained_scores = {
                system_name: score
                for system_name, score in result.questionnaire.msq_system_scores.items()
                if system_name not in ambiguous_systems
            }
            result.questionnaire = result.questionnaire.model_copy(
                update={"msq_system_scores": retained_scores}
            )
            affected_systems = "、".join(sorted(ambiguous_systems))
            self._mark_partial(
                result,
                "msq_system_scores",
                evidence,
                (
                    f"MSQ 的{affected_systems}评分包含歧义条目，"
                    "对应系统评分已不进入自动规则。"
                    if affected_systems
                    else "MSQ 存在无法定位身体系统的歧义评分条目，已忽略该条目。"
                ),
            )
        return result

    def _assess_pdf_result(
        self,
        questionnaire: Questionnaire,
        text: str,
    ) -> QuestionnaireParseResult:
        result = QuestionnaireParseResult(questionnaire=questionnaire)
        lines = self._pdf_demographic_lines(text)
        _, _, age_candidates, sex_candidates = self._extract_patient_demographics(lines)
        demographic_text = "\n".join(lines)
        invalid_patient_age = self._has_invalid_patient_age(lines)
        if invalid_patient_age:
            self._mark_uncertain(
                result,
                "age",
                demographic_text,
                "MSQ PDF 患者年龄超出 0–120 范围，已留空，请人工确认。",
            )
        elif not age_candidates:
            self._mark_uncertain(
                result,
                "age",
                demographic_text,
                "MSQ PDF 患者年龄未明确填写，已留空，请人工确认。",
            )
        elif len({value for value, _ in age_candidates}) > 1:
            self._mark_uncertain(
                result,
                "age",
                demographic_text,
                "MSQ PDF 患者年龄存在冲突，已留空，请人工确认。",
            )
        if len({value for value, _ in sex_candidates}) > 1:
            self._mark_uncertain(
                result,
                "sex",
                demographic_text,
                "MSQ PDF 患者性别存在冲突，已留空，请人工确认。",
            )

        normalized = self._normalize_pdf_text(text)
        medication_block = self._extract_between_text(
            normalized,
            "3. 针对上述慢性疾病，您是否按照医嘱正在服用药物？",
            "4. 您是否对某些药物过敏？",
        )
        medication_state = self._yes_no_state(medication_block)
        if medication_state in {"yes", "conflict"}:
            if (
                questionnaire.medications
                and not self._has_only_placeholder_terms(
                    questionnaire.medications
                )
            ):
                self._mark_partial(
                    result,
                    "medications",
                    medication_block,
                    "MSQ PDF 用药信息不完整，已保留明确识别的药物，请人工确认其余内容。",
                )
            else:
                self._mark_uncertain(
                    result,
                    "medications",
                    medication_block,
                    (
                        "MSQ PDF 的用药选项存在冲突，当前用药已留空，请人工确认。"
                        if medication_state == "conflict"
                        else "MSQ PDF 勾选了正在用药但未可靠识别药物内容，已留空，请人工确认。"
                    ),
                )
        allergy_block = self._extract_between_text(
            normalized,
            "4. 您是否对某些药物过敏？",
            "第二部分：家族病史",
        )
        allergy_state = self._yes_no_state(allergy_block)
        if allergy_state in {"yes", "conflict"}:
            if (
                questionnaire.allergies
                and not self._has_only_placeholder_terms(
                    questionnaire.allergies
                )
            ):
                self._mark_partial(
                    result,
                    "allergies",
                    allergy_block,
                    "MSQ PDF 过敏信息不完整，已保留明确识别的内容，请人工确认其余信息。",
                )
            else:
                self._mark_uncertain(
                    result,
                    "allergies",
                    allergy_block,
                    (
                        "MSQ PDF 的过敏选项存在冲突，过敏信息已留空，请人工确认。"
                        if allergy_state == "conflict"
                        else "MSQ PDF 勾选了药物过敏但未可靠识别具体内容，已留空，请人工确认。"
                    ),
                )

        pregnancy_snippets = [
            normalized[max(0, match.start() - 20) : match.start() + 160]
            for match in re.finditer(
                r"目前是否怀孕|当前是否怀孕|妊娠状态|正在哺乳|哺乳期",
                normalized,
            )
            if "怀孕次数" not in normalized[max(0, match.start() - 20) : match.start() + 30]
        ]
        pregnancy_states = {
            self._yes_no_state(snippet)
            for snippet in pregnancy_snippets
            if self._yes_no_state(snippet) != "blank"
        }
        if "conflict" in pregnancy_states or {"yes", "no"}.issubset(pregnancy_states):
            self._mark_uncertain(
                result,
                "pregnant_or_lactating",
                "\n".join(pregnancy_snippets),
                "MSQ PDF 的妊娠或哺乳状态存在冲突，已留空，请人工确认。",
            )
        elif pregnancy_states == {"yes"}:
            result.questionnaire = result.questionnaire.model_copy(
                update={"pregnant_or_lactating": True}
            )
        elif pregnancy_states == {"no"}:
            result.questionnaire = result.questionnaire.model_copy(
                update={"pregnant_or_lactating": False}
            )

        symptom_block = self._extract_between_text(normalized, "第九部分：症状评估")
        (
            parsed_score_rows,
            ambiguous_score_rows,
            ambiguous_systems,
        ) = self._inspect_pdf_msq_rows(normalized)
        if ambiguous_score_rows:
            evidence = symptom_block[:3000]
            self._mark_partial(
                result,
                "symptoms",
                evidence,
                f"MSQ PDF 有 {ambiguous_score_rows} 条症状评分存在多选，"
                "已忽略这些条目，其余已确认症状保留。",
            )
            retained_scores = {
                system_name: score
                for system_name, score in result.questionnaire.msq_system_scores.items()
                if system_name not in ambiguous_systems
            }
            result.questionnaire = result.questionnaire.model_copy(
                update={"msq_system_scores": retained_scores}
            )
            affected_systems = "、".join(sorted(ambiguous_systems))
            self._mark_partial(
                result,
                "msq_system_scores",
                evidence,
                (
                    f"MSQ PDF 的{affected_systems}评分包含歧义条目，"
                    "对应系统评分已不进入自动规则。"
                    if affected_systems
                    else "MSQ PDF 存在无法定位身体系统的歧义评分条目，已忽略该条目。"
                ),
            )
        elif (
            symptom_block
            and not questionnaire.msq_system_scores
            and parsed_score_rows == 0
            and any(marker in symptom_block for marker in self._CHECKED_MARKERS)
        ):
            excerpt = symptom_block[:3000]
            self._mark_uncertain(
                result,
                "msq_system_scores",
                excerpt,
                "MSQ PDF 中存在勾选症状但系统评分无法可靠计算，评分已留空，请人工确认。",
            )
        return result

    def _mark_uncertain(
        self,
        result: QuestionnaireParseResult,
        field_name: str,
        evidence: str,
        message: str,
    ) -> None:
        result.add_uncertainty(field_name, evidence, message)
        if field_name in self._EMPTY_VALUES:
            empty_value = self._EMPTY_VALUES[field_name]
            if isinstance(empty_value, list):
                empty_value = list(empty_value)
            elif isinstance(empty_value, dict):
                empty_value = dict(empty_value)
            result.questionnaire = result.questionnaire.model_copy(
                update={field_name: empty_value}
            )

    @staticmethod
    def _mark_partial(
        result: QuestionnaireParseResult,
        field_name: str,
        evidence: str,
        message: str,
    ) -> None:
        result.add_partial(field_name, evidence, message)

    def _extract_patient_demographics(
        self,
        lines: list[str],
    ) -> tuple[int | None, str, list[tuple[int, str]], list[tuple[str, str]]]:
        age_candidates: list[tuple[int, str]] = []
        sex_candidates: list[tuple[str, str]] = []
        for raw_line in lines:
            source_line = self._clean_text(raw_line)
            line = self._without_derived_age_fields(source_line)
            if not line:
                continue
            is_demographic_line = (
                ("姓名" in line and ("年龄" in line or "性别" in line))
                or line.startswith("年龄")
                or line.startswith("性别")
            )
            if not is_demographic_line:
                continue
            age_match = re.search(
                r"年龄\s*[:：|、]?\s*[_＿—-]*\s*(\d{1,3})(?:\s*岁)?",
                line,
            )
            if age_match:
                age_value = int(age_match.group(1))
                if 0 <= age_value <= 120:
                    age_candidates.append((age_value, source_line))
            sex_match = re.search(
                r"性别\s*[:：|、]?\s*[_＿—-]*\s*(女|男|其他)",
                line,
            )
            if sex_match:
                sex_candidates.append(
                    (
                        {"女": "female", "男": "male", "其他": "other"}[sex_match.group(1)],
                        source_line,
                    )
                )
        ages = list(dict.fromkeys(value for value, _ in age_candidates))
        sexes = list(dict.fromkeys(value for value, _ in sex_candidates))
        return (
            ages[0] if len(ages) == 1 else None,
            sexes[0] if len(sexes) == 1 else "unknown",
            age_candidates,
            sex_candidates,
        )

    def _pdf_demographic_lines(self, text: str) -> list[str]:
        raw_lines = self._split_lines(text)
        header_lines: list[str] = []
        for line in raw_lines[:30]:
            prefix = line.split("第一部分", 1)[0]
            if prefix:
                header_lines.append(prefix)
            if "第一部分" in line:
                break
        return header_lines

    def _docx_demographic_lines(
        self,
        paragraphs: list[str],
        tables: list[list[list[str]]],
    ) -> list[str]:
        lines = [
            line
            for line in paragraphs[:20]
            if any(label in line for label in ("姓名", "性别", "年龄", "日期"))
        ]
        for table in tables[:4]:
            cells = [cell for row in table[:6] for cell in row if cell]
            joined = " ".join(cells)
            label_count = sum(
                label in joined for label in ("姓名", "性别", "年龄", "日期")
            )
            has_identity_pair = "姓名" in joined and "性别" in joined
            has_patient_age_cell = any(
                re.fullmatch(
                    r"年龄\s*[:：]?\s*[_＿—-]*",
                    self._clean_text(cell),
                )
                for cell in cells
            )
            if label_count >= 2 and (has_identity_pair or has_patient_age_cell):
                lines.extend(" | ".join(cell for cell in row if cell) for row in table[:6])
                break
        return list(dict.fromkeys(lines))

    def _has_invalid_patient_age(self, lines: list[str]) -> bool:
        for raw_line in lines:
            line = self._without_derived_age_fields(self._clean_text(raw_line))
            if not line:
                continue
            if "年龄" not in line or ("姓名" not in line and not line.startswith("年龄")):
                continue
            match = re.search(
                r"年龄\s*[:：|、]?\s*[_＿—-]*\s*(\d{1,3})",
                line,
            )
            if match and not 0 <= int(match.group(1)) <= 120:
                return True
        return False

    def _without_derived_age_fields(self, text: str) -> str:
        cleaned = text
        for label in self._DERIVED_AGE_LABELS:
            cleaned = re.sub(
                rf"{re.escape(label)}\s*[:：|]?\s*\d{{1,4}}\s*岁?",
                " ",
                cleaned,
            )
            cleaned = cleaned.replace(label, " ")
        return self._clean_text(cleaned)

    def _yes_no_state(self, text: str) -> str:
        labels = self._scan_checked_labels(text)
        yes = "是" in labels
        no = "否" in labels
        if yes and no:
            return "conflict"
        if yes:
            return "yes"
        if no:
            return "no"
        return "blank"

    def _table_excerpt(self, table: list[list[str]]) -> str:
        return "\n".join(" | ".join(row) for row in table[:16])[:3000]

    def _has_only_placeholder_terms(self, values: list[str]) -> bool:
        placeholders = {
            "如有",
            "如有请具体说明",
            "如有请说明",
            "请具体说明",
            "请说明",
            "名称",
            "剂量",
            "频率",
            "名称剂量频率",
        }
        return bool(values) and all(
            re.sub(r"[\s_:：,，;；。()（）]+", "", value) in placeholders
            for value in values
        )

    def _normalize_pdf_text(self, text: str) -> str:
        normalized = text.replace("\u2f64", "用").replace("\u2f63", "生")
        normalized = re.sub(r"(?<![A-Za-z])Y(?![A-Za-z])", "☑", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _extract_between_text(self, text: str, start: str, *ends: str) -> str:
        if start:
            start_index = text.find(start)
            if start_index < 0:
                return ""
            text = text[start_index + len(start) :]
        end_positions = [text.find(end) for end in ends if end and text.find(end) >= 0]
        if end_positions:
            text = text[: min(end_positions)]
        return self._clean_text(text)

    def _strip_pdf_markers(self, text: str) -> str:
        return self._clean_text(re.sub(r"[□☑√■＿_]+", " ", text))

    def _split_pdf_terms(self, text: str) -> list[str]:
        cleaned = self._strip_pdf_markers(text)
        cleaned = re.sub(r"(?:如有|请具体说明|请说明何种药物及症状).*", "", cleaned)
        cleaned = cleaned.replace("无", " ")
        parts = re.split(r"[、,，;；/\n\s]+", cleaned)
        return self._dedupe(parts)

    def _extract_pdf_drink_parts(self, text: str) -> list[str]:
        drink_parts: list[str] = []
        for label in ("茶", "咖啡", "白开水", "碳酸饮料", "果汁"):
            pattern = re.escape(label) + r".*?每日\s*(\d+)\s*(?:杯|毫升|cc|CC)"
            match = re.search(pattern, text)
            if match and self._is_option_checked_before_label(text, label):
                unit = "杯" if "杯" in match.group(0) else "毫升"
                drink_parts.append(f"{label}每日{match.group(1)}{unit}")
        return self._dedupe(drink_parts)

    def _is_option_checked_before_label(self, text: str, label: str) -> bool:
        label_index = text.find(label)
        if label_index < 0:
            return False
        prefix = text[max(0, label_index - 4) : label_index]
        return self._contains_checked_marker(prefix)

    def _extract_pdf_alcohol_summary(self, text: str) -> str | None:
        if not self._is_yes_selected(self._extract_between_text(text, "", "8. 您平时饮酒常饮用哪种酒？")):
            return None
        years_match = re.search(r"年数[:：]?\s*(\d+)\s*年", text)
        wine_type = self._extract_between_text(text, "8. 您平时饮酒常饮用哪种酒？", "9. 您每次喝酒的习惯？")
        habit_line = self._extract_between_text(text, "9. 您每次喝酒的习惯？", "10. 每周外出就餐次数？")
        habit_choices = self._checked_labels(habit_line)
        parts = []
        if years_match:
            parts.append(f"{years_match.group(1)}年")
        if wine_type:
            parts.append(self._strip_pdf_markers(wine_type))
        if habit_choices:
            parts.append(f"每次{habit_choices[0]}")
        return "饮酒：" + "，".join(part for part in parts if part) if parts else "饮酒"

    def _extract_pdf_supplement_use(self, text: str) -> str | None:
        header = self._extract_between_text(text, "您有补充营养食品吗？", "如有")
        if self._is_no_selected(header):
            return "无营养补充剂"
        if not self._is_yes_selected(header):
            return None

        supplement_names = [
            "抗氧化",
            "辅酶Q10",
            "蛋白粉",
            "亚麻油",
            "植物粉",
            "硫辛酸",
            "鱼油",
            "维生素E",
            "β胡萝卜素",
            "维生素A",
            "镁",
            "维生素D",
            "大豆异黄酮",
            "钙",
            "膳食纤维",
            "单种维生素B",
            "矿物质",
            "复合多种维生素B",
        ]
        items: list[str] = []
        for name in supplement_names:
            match = re.search(re.escape(name) + r"\s*((?:一日|每日)\s*[一二三四五六七八九十\d]*次)", text)
            if match:
                items.append(self._compose_named_frequency(name, match.group(1)))
        return "；".join(self._dedupe(items)) if items else None

    def _extract_pdf_msq_symptoms(self, text: str) -> tuple[list[str], list[str], list[str], dict[str, int]]:
        symptom_text = self._extract_between_text(text, "第九部分：症状评估")
        if not symptom_text:
            return [], [], [], {}

        symptom_text = re.sub(r"级别\s+序号\s+症状描述\s+从来没有\s+偶尔\s+轻微\s+中等\s+严重\s+0\s+1\s+2\s+3\s+4", " ", symptom_text)
        row_pattern = re.compile(r"(?<![\d.])(\d{1,2})\s+([^□☑]{2,50}?)\s+((?:[□☑]\s*){5})")
        matches = list(row_pattern.finditer(symptom_text))

        symptoms: list[str] = []
        emotional_state: list[str] = []
        bowel_markers: list[str] = []
        msq_system_scores: dict[str, int] = {}
        current_section = ""
        previous_end = 0

        for index, match in enumerate(matches):
            before = symptom_text[previous_end : match.start()]
            section_before = self._section_from_short_gap(before)
            current_section = section_before or current_section
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(symptom_text)
            after = symptom_text[match.end() : next_start]

            symptom_name = self._clean_text(match.group(2))
            prefix_section = self._section_prefix(symptom_name)
            if prefix_section:
                current_section = prefix_section
                symptom_name = self._clean_text(symptom_name[len(prefix_section) :])
            section_after = self._section_from_short_gap(after)
            row_section = prefix_section or section_before or section_after or self._section_for_pdf_symptom(symptom_name) or current_section

            score = self._extract_msq_score(re.findall(r"[□☑]", match.group(3)))
            if symptom_name and score is not None and score > 0:
                symptoms.append(symptom_name)
                if symptom_name in {"便秘", "腹泻"}:
                    bowel_markers.append(symptom_name)
                if any(keyword in symptom_name for keyword in ("忧郁", "焦虑", "烦躁", "紧张", "情绪", "暴躁")):
                    emotional_state.append(symptom_name)
                mapped_sections = self._MSQ_SECTION_MAP.get(row_section or "", [])
                for mapped_section in mapped_sections:
                    msq_system_scores[mapped_section] = max(msq_system_scores.get(mapped_section, 0), score)

            if section_after:
                current_section = section_after
            previous_end = match.end()

        return self._dedupe(symptoms), self._dedupe(emotional_state), self._dedupe(bowel_markers), msq_system_scores

    def _inspect_pdf_msq_rows(
        self,
        text: str,
    ) -> tuple[int, int, set[str]]:
        symptom_text = self._extract_between_text(text, "第九部分：症状评估")
        if not symptom_text:
            return 0, 0, set()

        symptom_text = re.sub(
            r"级别\s+序号\s+症状描述\s+从来没有\s+偶尔\s+轻微\s+中等\s+严重\s+0\s+1\s+2\s+3\s+4",
            " ",
            symptom_text,
        )
        row_pattern = re.compile(
            r"(?<![\d.])(\d{1,2})\s+([^□☑]{2,50}?)\s+((?:[□☑]\s*){5})"
        )
        matches = list(row_pattern.finditer(symptom_text))
        parsed_rows = 0
        ambiguous_rows = 0
        ambiguous_systems: set[str] = set()
        current_section = ""
        previous_end = 0

        for index, match in enumerate(matches):
            before = symptom_text[previous_end : match.start()]
            section_before = self._section_from_short_gap(before)
            current_section = section_before or current_section
            next_start = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(symptom_text)
            )
            after = symptom_text[match.end() : next_start]

            symptom_name = self._clean_text(match.group(2))
            prefix_section = self._section_prefix(symptom_name)
            if prefix_section:
                current_section = prefix_section
                symptom_name = self._clean_text(
                    symptom_name[len(prefix_section) :]
                )
            section_after = self._section_from_short_gap(after)
            row_section = (
                prefix_section
                or section_before
                or section_after
                or self._section_for_pdf_symptom(symptom_name)
                or current_section
            )
            markers = re.findall(r"[□☑]", match.group(3))
            checked_count = sum(
                marker in self._CHECKED_MARKERS
                for marker in markers
            )
            if checked_count == 1:
                parsed_rows += 1
            elif checked_count > 1:
                ambiguous_rows += 1
                ambiguous_systems.update(
                    self._MSQ_SECTION_MAP.get(row_section or "", [])
                )

            if section_after:
                current_section = section_after
            previous_end = match.end()

        return parsed_rows, ambiguous_rows, ambiguous_systems

    def _section_from_text(self, text: str) -> str:
        for section in self._MSQ_SECTION_MAP:
            if section in text:
                return section
        return ""

    def _section_from_short_gap(self, text: str) -> str:
        cleaned = self._clean_text(text)
        if len(cleaned) > 30:
            return ""
        return self._section_from_text(cleaned)

    def _section_prefix(self, text: str) -> str:
        for section in self._MSQ_SECTION_MAP:
            if text.startswith(section):
                return section
        return ""

    def _section_for_pdf_symptom(self, symptom: str) -> str:
        keyword_map = {
            "头/脑力方面": ("头昏", "头痛", "犹豫", "记忆", "注意力", "思虑"),
            "鼻子": ("打喷嚏", "鼻塞", "流鼻", "鼻窦", "打鼾"),
            "口腔": ("口腔", "溃疡", "蛀牙", "味嗅觉", "补牙"),
            "肺/喉咙": ("呼吸", "气喘", "胸闷", "胸痛", "咳嗽", "喉咙", "扁桃"),
            "消化功能": ("消化", "腹胀", "胀气", "胃", "恶心", "呕吐", "便秘", "腹泻", "胃酸"),
            "头发/皮肤": ("头发", "皮肤", "痤疮", "肤色", "伤口", "四肢冰冷", "多汗", "盗汗", "指甲", "水肿"),
            "体能及情绪": ("疲劳", "没精神", "昏沉", "失眠", "紧张", "焦虑", "烦闷", "情绪", "暴躁", "忧郁"),
            "排泄功能": ("尿", "膀胱", "粪便", "血便"),
            "慢性疲劳症": ("全身肌肉无力", "游走性", "睡眠障碍", "虚弱疲劳"),
        }
        for section, keywords in keyword_map.items():
            if any(keyword in symptom for keyword in keywords):
                return section
        return ""

    def _has_msq_symptom_header(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        return (
            all(token in compact for token in ("级别", "序号", "症状描述"))
            and re.search(r"从(?:来)?没有(?:过)?", compact) is not None
            and "01234" in compact
        )

    def _find_msq_symptom_tables(
        self,
        tables: list[list[list[str]]],
    ) -> list[list[list[str]]]:
        results: list[list[list[str]]] = []
        seen: set[tuple[tuple[str, ...], ...]] = set()
        for table in tables:
            header = "".join(cell for row in table[:2] for cell in row)
            if not self._has_msq_symptom_header(header):
                continue
            signature = tuple(
                tuple(self._clean_text(cell) for cell in row)
                for row in table
            )
            if signature in seen:
                continue
            seen.add(signature)
            results.append(table)
        return results

    def _parse_msq_symptom_row(self, row: list[str]) -> tuple[str, int | None]:
        if len(row) < 2:
            return "not_score_row", None
        marker_cells = row[2:7] if len(row) >= 7 else row[2:]
        markers = [
            marker
            for cell in marker_cells
            for marker in cell
            if marker in self._ALL_MARKERS
        ]
        if not markers:
            return "not_score_row", None
        if len(markers) != 5:
            return "ambiguous", None
        selected = [
            index
            for index, marker in enumerate(markers)
            if marker in self._CHECKED_MARKERS
        ]
        if not selected:
            return "blank", None
        if len(selected) == 1:
            return "valid", selected[0]
        return "ambiguous", None

    def _msq_symptom_rows(
        self,
        tables: list[list[list[str]]],
    ) -> list[tuple[list[str], str, str, int | None]]:
        results: list[tuple[list[str], str, str, int | None]] = []
        for table in self._find_msq_symptom_tables(tables):
            current_section = ""
            for row in table[2:]:
                if len(row) < 2:
                    continue
                first_cell = row[0]
                for raw_section in self._MSQ_SECTION_MAP:
                    if raw_section in first_cell:
                        current_section = raw_section
                        break
                status, score = self._parse_msq_symptom_row(row)
                results.append((row, current_section, status, score))
        return results

    def _find_table(self, tables: list[list[list[str]]], keyword: str) -> list[list[str]]:
        for table in tables:
            joined = " ".join(cell for row in table[:6] for cell in row)
            if keyword in joined:
                return table
        return []

    def _find_tables(self, tables: list[list[list[str]]], keyword: str) -> list[list[list[str]]]:
        return [table for table in tables if keyword in " ".join(cell for row in table[:6] for cell in row)]

    def _find_row(self, table: list[list[str]], keyword: str) -> tuple[int | None, list[str] | None]:
        for index, row in enumerate(table):
            if keyword in " ".join(row):
                return index, row
        return None, None

    def _extract_sex(self, text: str) -> str:
        match = re.search(r"性别[:：_ ]*(女|男|其他)", text)
        if not match:
            return "unknown"
        return {"女": "female", "男": "male", "其他": "other"}.get(match.group(1), "unknown")

    def _extract_int(self, text: str, pattern: str) -> int | None:
        match = re.search(pattern, text)
        if not match:
            return None
        return int(match.group(1))

    def _extract_percent_after(self, text: str, keyword: str) -> str | None:
        match = re.search(re.escape(keyword) + r".*?占比\s*(\d+%)", text)
        return match.group(1) if match else None

    def _extract_time_value(self, text: str) -> float | None:
        text = self._clean_text(text)
        if not text:
            return None

        half_match = re.search(r"(\d{1,2})\s*点半", text)
        if half_match:
            return float(half_match.group(1)) + 0.5

        time_match = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", text)
        if time_match:
            return float(time_match.group(1)) + int(time_match.group(2)) / 60

        hour_match = re.search(r"(\d{1,2})\s*[点时]", text)
        if hour_match:
            return float(hour_match.group(1))
        return None

    def _extract_msq_score(self, checks: list[str]) -> int | None:
        selected = [
            index
            for index, value in enumerate(checks)
            if self._contains_checked_marker(value)
        ]
        return selected[0] if len(selected) == 1 else None

    def _checked_labels(self, text: str) -> list[str]:
        return self._dedupe(self._scan_checked_labels(text))

    def _scan_checked_labels(self, text: str) -> list[str]:
        labels: list[str] = []
        index = 0
        while index < len(text):
            marker = text[index]
            if marker not in self._ALL_MARKERS:
                index += 1
                continue
            checked = marker in self._CHECKED_MARKERS
            index += 1
            start = index
            while index < len(text) and text[index] not in self._ALL_MARKERS:
                index += 1
            if checked:
                labels.append(self._normalize_option_label(text[start:index]))
        return [label for label in labels if label]

    def _is_yes_selected(self, text: str) -> bool:
        return "是" in self._scan_checked_labels(text)

    def _is_no_selected(self, text: str) -> bool:
        return "否" in self._scan_checked_labels(text)

    def _contains_checked_marker(self, text: str) -> bool:
        return any(marker in text for marker in self._CHECKED_MARKERS)

    def _normalize_option_label(self, label: str) -> str:
        return self._clean_text(label).strip("：:，,；;。()（）[]【】")

    def _normalize_family_member(self, label: str) -> str:
        normalized = self._clean_text(label)
        normalized = normalized.removeprefix("您的")
        normalized = normalized.removeprefix("你们的")
        return normalized or label

    def _split_terms(self, text: str) -> list[str]:
        cleaned = self._clean_text(text)
        if not cleaned:
            return []
        if cleaned in {"无", "未填写", "暂无"}:
            return []
        parts = re.split(r"[、,，;；/\n]+", cleaned)
        return self._dedupe(parts)

    def _extract_docx_medications(self, rows: list[list[str]]) -> list[str]:
        """Extract filled medication values without treating Word labels as data."""
        medications: list[str] = []
        placeholder_values = {
            "",
            "西药",
            "中药",
            "名称",
            "剂量",
            "频率",
            "名称剂量频率",
        }

        for row in rows:
            cells = [self._clean_text(cell) for cell in row]
            cells = [cell for cell in cells if cell]
            if not cells:
                continue

            compact_row = "".join(cells).replace(" ", "")
            if compact_row in {"西药中药", "中药西药"}:
                continue

            parsed_compact = False
            for cell in cells:
                compact = re.sub(r"[\s_]+", "", cell)
                if not all(label in compact for label in ("名称", "剂量", "频率")):
                    continue

                name_label = compact.find("名称")
                dose_label = compact.find("剂量", name_label + 2)
                frequency_label = compact.find("频率", dose_label + 2)
                if not (0 <= name_label < dose_label < frequency_label):
                    continue

                prefix = compact[:name_label]
                between_name_and_dose = compact[name_label + 2 : dose_label]
                dose = compact[dose_label + 2 : frequency_label]
                frequency = compact[frequency_label + 2 :]
                name = prefix or between_name_and_dose
                if name and name not in placeholder_values:
                    details = "；".join(
                        detail
                        for detail in (
                            f"剂量：{dose}" if dose else "",
                            f"频率：{frequency}" if frequency else "",
                        )
                        if detail
                    )
                    medications.append(
                        f"{name}（{details}）" if details else name
                    )
                parsed_compact = True
                break

            if parsed_compact:
                continue

            values = [
                cell
                for cell in cells
                if re.sub(r"[\s_]+", "", cell) not in placeholder_values
                and not cell.startswith("如有")
            ]
            medications.extend(self._split_terms("；".join(values)))

        return self._dedupe(medications)

    def _compose_named_frequency(self, name: str, frequency: str) -> str:
        normalized_name = self._clean_text(name)
        normalized_frequency = self._clean_text(frequency)
        if not normalized_name:
            return ""
        if not normalized_frequency:
            return normalized_name
        return f"{normalized_name}（{normalized_frequency}）"

    def _extract_inline_text(self, table: list[list[str]], label: str) -> str | None:
        for row in table:
            joined = " ".join(row)
            if label not in joined:
                continue
            value = joined.split(label, 1)[-1]
            value = re.sub(r"^[：:\s_]+", "", value)
            value = self._clean_text(value)
            return value or None
        return None

    def _has_meaningful_content(self, questionnaire: Questionnaire) -> bool:
        return any(
            [
                questionnaire.age is not None,
                questionnaire.sex != "unknown",
                bool(questionnaire.chief_concerns),
                bool(questionnaire.symptoms),
                bool(questionnaire.known_conditions),
                bool(questionnaire.family_history),
                bool(questionnaire.goals),
                bool(questionnaire.msq_system_scores),
            ]
        )

    def _split_lines(self, text: str) -> list[str]:
        return [self._clean_text(line) for line in text.splitlines() if self._clean_text(line)]

    def _clean_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    def _dedupe(self, items: list[str]) -> list[str]:
        results: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = self._clean_text(item).strip("，,；;。 ")
            if not normalized or normalized in {"无", "否", "未填写", "暂无"}:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            results.append(normalized)
        return results
