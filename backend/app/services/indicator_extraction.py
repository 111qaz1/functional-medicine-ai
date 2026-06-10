from __future__ import annotations

import re

from app.domain.models import AbnormalFlag, CaseIndicator, IndicatorStatus, SourceSpan


def _canonicalize_exponent_units(value: str) -> str:
    return value.replace("∧", "^").replace("＾", "^").replace("ˆ", "^")


def _normalize_snippet(value: str) -> str:
    value = _canonicalize_exponent_units(value)
    value = value.replace("—", "-").replace("–", "-").replace("~", "-").replace("～", "-")
    return re.sub(r"\s+", "", value).strip().lower()


class CaseIndicatorService:
    _ADMIN_METADATA_PREFIXES = (
        "医嘱名",
        "姓名",
        "姓 名",
        "性别",
        "性 别",
        "年龄",
        "年 龄",
        "登记号",
        "采集时间",
        "接收时间",
        "标本类型",
        "标本号",
        "床号",
        "科室",
        "诊断",
    )

    def build(self, case) -> list[CaseIndicator]:
        indicators: list[CaseIndicator] = []
        indicators.extend(getattr(case, "manual_indicators", []))
        indicators.extend(self._from_lab_items(case.extracted_lab_items))
        indicators.extend(self._from_case_text(case))
        return self._sort_indicators(self._dedupe(indicators))

    def _from_lab_items(self, lab_items) -> list[CaseIndicator]:
        indicators: list[CaseIndicator] = []
        for item in lab_items:
            value = item.normalized_value if item.normalized_value is not None else item.value
            unit = item.normalized_unit or item.unit or ""
            if value is None:
                continue
            status = self._status_from_abnormal_flag(item.abnormal_flag)
            if item.marker_code == "hematocrit" and unit == "%" and 0 < value <= 1:
                value = round(value * 100, 1)
                status = self._status_from_reference_range(value, item.ref_range.lower, item.ref_range.upper)
            source_span = item.source_span
            if source_span and source_span.snippet:
                if self._is_admin_metadata_line(source_span.snippet):
                    continue
                if item.marker_code == "uric_acid" and "尿酸碱度" in source_span.snippet:
                    continue
                if "↑" in source_span.snippet or "↓" in source_span.snippet:
                    status = IndicatorStatus.attention
                sanitized_snippet = self._sanitize_lab_snippet(source_span.snippet)
                if sanitized_snippet != source_span.snippet:
                    source_span = self._clone_span(source_span, sanitized_snippet)
            indicators.append(
                CaseIndicator(
                    indicator_name=item.marker_name,
                    result_text=f"{value:g} {unit}".strip(),
                    status=status,
                    category="lab",
                    source_span=source_span,
                )
            )
        return indicators

    def _from_case_text(self, case) -> list[CaseIndicator]:
        indicators: list[CaseIndicator] = []
        known_lab_snippets = {
            _normalize_snippet(item.source_span.snippet)
            for item in case.extracted_lab_items
            if item.source_span and item.source_span.snippet
        }

        for uploaded_file in case.files:
            lines = self._collect_lines(uploaded_file)
            if not lines:
                continue

            indicators.extend(self._extract_chief_complaint(lines))
            indicators.extend(self._extract_vitals(lines))
            indicators.extend(self._extract_positive_findings(lines))
            indicators.extend(self._extract_summary_findings(lines))
            indicators.extend(self._extract_exam_conclusions(lines))
            indicators.extend(self._extract_generic_lab_rows(lines, known_lab_snippets))
            indicators.extend(self._extract_stacked_lab_rows(lines, known_lab_snippets))
        return indicators

    def _collect_lines(self, uploaded_file) -> list[tuple[str, SourceSpan]]:
        text = uploaded_file.corrected_text or uploaded_file.raw_extracted_text or ""
        if text.strip():
            lines: list[tuple[str, SourceSpan]] = []
            for index, raw_line in enumerate(text.splitlines(), start=1):
                cleaned = raw_line.strip()
                if not cleaned:
                    continue
                lines.append(
                    (
                        cleaned,
                        SourceSpan(
                            file_id=uploaded_file.id,
                            file_name=uploaded_file.filename,
                            page=1,
                            line_number=index,
                            snippet=cleaned,
                        ),
                    )
                )
            return lines

        if uploaded_file.source_spans:
            return [(span.snippet.strip(), span) for span in uploaded_file.source_spans if span.snippet.strip()]

        return []

    def _extract_chief_complaint(self, lines: list[tuple[str, SourceSpan]]) -> list[CaseIndicator]:
        indicators: list[CaseIndicator] = []
        for index, (line, span) in enumerate(lines):
            compact = _normalize_snippet(line)
            if compact in {"主诉", "|主诉", "【主诉】"} and index + 1 < len(lines):
                next_line, next_span = lines[index + 1]
                indicators.append(
                    CaseIndicator(
                        indicator_name="主诉",
                        result_text=next_line,
                        status=IndicatorStatus.attention,
                        category="case_text",
                        source_span=self._clone_span(next_span, next_line),
                    )
                )
                break

            if compact.startswith("主诉") and len(compact) > 2:
                result_text = re.sub(r"^主诉[:：| ]*", "", line).strip()
                if result_text:
                    indicators.append(
                        CaseIndicator(
                            indicator_name="主诉",
                            result_text=result_text,
                            status=IndicatorStatus.attention,
                            category="case_text",
                            source_span=self._clone_span(span, line.strip()),
                        )
                    )
                    break
        return indicators

    def _extract_vitals(self, lines: list[tuple[str, SourceSpan]]) -> list[CaseIndicator]:
        indicators: list[CaseIndicator] = []
        for line, span in lines:
            matches = [
                ("体温", re.search(r"(?:^|[ ,;，])T[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*[℃C]", line, re.IGNORECASE)),
                ("心率", re.search(r"(?:^|[ ,;，])P[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*次/?分", line, re.IGNORECASE)),
                ("呼吸", re.search(r"(?:^|[ ,;，])R[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*次/?分", line, re.IGNORECASE)),
                ("血压", re.search(r"(?:^|[ ,;，])BP[:：]?\s*([0-9]{2,3}/[0-9]{2,3})\s*mmHg", line, re.IGNORECASE)),
                ("体重", re.search(r"体重[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*kg", line, re.IGNORECASE)),
                ("身高", re.search(r"身高[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*cm", line, re.IGNORECASE)),
                ("体质指数", re.search(r"(?:BMI值|BMI|体质指数)[:：]?\s*([0-9]+(?:\.[0-9]+)?)", line, re.IGNORECASE)),
            ]
            for indicator_name, match in matches:
                if not match:
                    continue
                raw_value = match.group(1)
                snippet = match.group(0).strip(" ,;，")
                indicators.append(
                    CaseIndicator(
                        indicator_name=indicator_name,
                        result_text=self._format_vital_result(indicator_name, raw_value),
                        status=self._classify_vital(indicator_name, raw_value),
                        category="vital_sign",
                        source_span=self._clone_span(span, snippet),
                    )
                )
        return indicators

    def _extract_summary_findings(self, lines: list[tuple[str, SourceSpan]]) -> list[CaseIndicator]:
        indicators: list[CaseIndicator] = []
        in_summary = False
        finding_labels = {
            "脂肪肝": IndicatorStatus.positive,
            "超重": IndicatorStatus.attention,
        }
        for line, span in lines:
            if "总检汇总分析" in line:
                in_summary = True
                continue
            if in_summary and any(line.startswith(prefix) for prefix in ("初审医生", "终审医生", "身高体重检查")):
                break
            if not in_summary:
                continue
            match = re.match(r"^\s*\d+[、.．]\s*(脂肪肝|超重)\s*$", line)
            if not match:
                continue
            name = match.group(1)
            indicators.append(
                CaseIndicator(
                    indicator_name=name,
                    result_text="总检提示",
                    status=finding_labels[name],
                    category="clinical_finding",
                    source_span=self._clone_span(span, line),
                )
            )
        return indicators

    def _extract_positive_findings(self, lines: list[tuple[str, SourceSpan]]) -> list[CaseIndicator]:
        indicators: list[CaseIndicator] = []
        keywords = [
            "周围性面瘫",
            "眼闭合无力",
            "口角左偏",
            "口角下垂",
            "额纹变浅",
            "鼻唇沟变浅",
            "耳后疼痛",
        ]
        for line, span in lines:
            if "主诉" in line:
                continue
            matched_segments = [segment for segment in re.split(r"[，。；;]", line) if any(key in segment for key in keywords)]
            if not matched_segments:
                continue
            snippet = "；".join(dict.fromkeys(segment.strip() for segment in matched_segments if segment.strip()))
            indicators.append(
                CaseIndicator(
                    indicator_name="阳性体征",
                    result_text=snippet,
                    status=IndicatorStatus.positive,
                    category="clinical_finding",
                    source_span=self._clone_span(span, snippet),
                )
            )
            break
        return indicators

    def _extract_exam_conclusions(self, lines: list[tuple[str, SourceSpan]]) -> list[CaseIndicator]:
        indicators: list[CaseIndicator] = []
        patterns = [
            ("血常规", r"血常规[:：]?\s*([^。；;\n]+)"),
            ("生化", r"生化[:：]?\s*([^。；;\n]+)"),
            ("甲状腺功能", r"甲状腺功能[:：]?\s*([^。；;\n]+)"),
            ("病毒标志物", r"病毒标志物[:：]?\s*([^。；;\n]+)"),
            ("尿常规及便常规", r"尿常规及便常规[:：]?\s*([^。；;\n]+)"),
            ("心电图", r"心电图[:：]?\s*([^。；;\n]+)"),
            ("头CT", r"头CT[:：]?\s*([^。；;\n]+)"),
            ("头磁共振", r"头磁共振[:：]?\s*([^。；;\n]+)"),
        ]
        indicator_names = [name for name, _ in patterns]
        for line, span in lines:
            for indicator_name, pattern in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if not match:
                    continue
                result_text = self._trim_exam_result(match.group(1).strip(), indicator_name, indicator_names)
                if not result_text:
                    continue
                if not self._looks_like_exam_conclusion_match(match.group(0), result_text):
                    continue
                snippet = self._build_exam_snippet(match.group(0).strip(), indicator_name, result_text)
                indicators.append(
                    CaseIndicator(
                        indicator_name=indicator_name,
                        result_text=result_text,
                        status=self._classify_exam_conclusion(result_text),
                        category="exam_conclusion",
                        source_span=self._clone_span(span, snippet),
                    )
                )
        return indicators

    def _extract_generic_lab_rows(
        self,
        lines: list[tuple[str, SourceSpan]],
        known_lab_snippets: set[str],
    ) -> list[CaseIndicator]:
        indicators: list[CaseIndicator] = []
        seen_segments: set[tuple[str, str]] = set()

        for line, span in lines:
            if self._is_section_header(line):
                continue

            for parsed in self._parse_generic_lab_row(line):
                sanitized_snippet = self._sanitize_lab_snippet(parsed["snippet"])
                compact = _normalize_snippet(sanitized_snippet)
                signature = (parsed["name"], parsed["result_text"])
                if compact in known_lab_snippets or signature in seen_segments:
                    continue

                seen_segments.add(signature)
                indicators.append(
                    CaseIndicator(
                        indicator_name=parsed["name"],
                        result_text=parsed["result_text"],
                        status=parsed["status"],
                        category="lab",
                        source_span=SourceSpan(
                            file_id=span.file_id,
                            file_name=span.file_name,
                            page=span.page,
                            line_number=span.line_number,
                            snippet=sanitized_snippet,
                        ),
                    )
                )

        return indicators

    def _extract_stacked_lab_rows(
        self,
        lines: list[tuple[str, SourceSpan]],
        known_lab_snippets: set[str],
    ) -> list[CaseIndicator]:
        indicators: list[CaseIndicator] = []
        seen: set[tuple[str, str]] = set()

        for span, block_lines in self._build_stacked_lab_blocks(lines):
            joined = " ".join(block_lines)
            sanitized_joined = self._sanitize_lab_snippet(joined)
            compact = _normalize_snippet(sanitized_joined)
            if compact in known_lab_snippets:
                continue

            parsed = self._parse_stacked_lab_block(block_lines)
            if not parsed:
                continue

            signature = (parsed["name"], parsed["result_text"])
            if signature in seen:
                continue
            seen.add(signature)

            indicators.append(
                CaseIndicator(
                    indicator_name=parsed["name"],
                    result_text=parsed["result_text"],
                    status=parsed["status"],
                    category="lab",
                    source_span=SourceSpan(
                        file_id=span.file_id,
                        file_name=span.file_name,
                        page=span.page,
                        line_number=span.line_number,
                        snippet=sanitized_joined,
                    ),
                )
            )

        return indicators

    def _build_stacked_lab_blocks(self, lines: list[tuple[str, SourceSpan]]) -> list[tuple[SourceSpan, list[str]]]:
        blocks: list[tuple[SourceSpan, list[str]]] = []
        index = 0
        while index < len(lines):
            line, span = lines[index]
            if not self._looks_like_marker_line(line):
                index += 1
                continue

            if index + 1 >= len(lines):
                break

            next_line = lines[index + 1][0]
            value_index = index + 1
            if self._looks_like_abbreviation_line(next_line):
                if index + 2 >= len(lines):
                    index += 1
                    continue
                third_line = lines[index + 2][0]
                if not (self._looks_like_numeric_result(third_line) or self._looks_like_qualitative_result(third_line)):
                    index += 1
                    continue
                value_index = index + 2
            elif not (self._looks_like_numeric_result(next_line) or self._looks_like_qualitative_result(next_line)):
                index += 1
                continue

            block_lines = [line.strip()]
            cursor = index + 1
            while cursor < len(lines) and len(block_lines) < 7:
                candidate = lines[cursor][0].strip()
                if self._is_section_header(candidate):
                    break
                if len(block_lines) >= 2 and self._looks_like_marker_line(candidate):
                    break
                block_lines.append(candidate)
                cursor += 1
                if self._looks_like_qualitative_result(candidate):
                    break

            if len(block_lines) >= 2:
                blocks.append((span, block_lines))

            index += 1

        return blocks

    def _parse_generic_lab_row(self, line: str) -> list[dict]:
        cleaned = (
            _canonicalize_exponent_units(line)
            .replace("—", "-")
            .replace("–", "-")
            .replace("~", "-")
            .replace("～", "-")
            .replace("至", "-")
            .replace("＞", ">")
            .replace("＜", "<")
            .strip()
        )
        cleaned = re.sub(r"\s*[|｜]\s*", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not re.search(r"\d", cleaned):
            return []
        if not re.search(r"[\u4e00-\u9fff]", cleaned):
            return []

        indicators: list[dict] = []
        cursor = 0
        range_pattern = re.compile(r"(-?\d+(?:\.\d+)?)\s*(--|[-~])\s*(-?\d+(?:\.\d+)?)")

        while cursor < len(cleaned):
            range_match = range_pattern.search(cleaned, cursor)
            if not range_match:
                break

            prefix = cleaned[cursor:range_match.start()]
            value_match = re.search(
                r"(?P<name>.*?)(?P<value>-?\d+(?:\.\d+)?)\s*(?P<flag>[↑↓]?)\s*"
                r"(?P<prefix_unit>(?:\*?10\^\d+/[A-Za-zμu%^0-9]+|[A-Za-zμu/%][A-Za-zμu/%^0-9]*(?:/[A-Za-zμu%^0-9]+)?))?\s*$",
                prefix,
                re.IGNORECASE,
            )
            if not value_match:
                cursor = range_match.end()
                continue

            raw_name = value_match.group("name").strip()
            name = self._clean_indicator_name(raw_name)
            if not name:
                cursor = range_match.end()
                continue

            value = float(value_match.group("value"))
            lower = float(range_match.group(1))
            upper = float(range_match.group(3))
            if range_match.group(2) == "--" and lower >= 0 and upper < 0:
                upper = abs(upper)

            after_range = cleaned[range_match.end():]
            unit_token = value_match.group("prefix_unit") or ""
            after_range_unit, unit_end = self._extract_unit_token(after_range)
            if not unit_token:
                unit_token = after_range_unit
            else:
                unit_end = 0
            status_token, status_end = self._extract_status_token(after_range[unit_end:])
            if not unit_token and not status_token and self._looks_like_next_marker_fragment(after_range):
                cursor = range_match.end()
                continue

            segment_end = range_match.end() + unit_end + status_end
            snippet = cleaned[value_match.start():segment_end].strip()
            result_text = f"{value:g} {unit_token}".strip()
            status = self._status_from_tokens(
                value=value,
                lower=lower,
                upper=upper,
                flag=value_match.group("flag"),
                status_token=status_token,
            )

            indicators.append(
                {
                    "name": name,
                    "result_text": result_text,
                    "status": status,
                    "snippet": snippet,
                }
            )
            cursor = max(segment_end, range_match.end())

        return indicators

    def _clean_indicator_name(self, raw_name: str) -> str:
        value = re.sub(r"\s+", "", raw_name)
        value = re.sub(r"^\d{1,2}(?=[\u4e00-\u9fffA-Za-z(（])", "", value)
        if re.search(r"[\u4e00-\u9fff]", value):
            chinese_tail = re.search(r"((?:\d{1,2}[-.]*)?[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9()（）/%#·+\-]*)$", value)
            if chinese_tail:
                value = chinese_tail.group(1)
            value = re.sub(r"^\d{1,2}(?=[\u4e00-\u9fff])", "", value)
            value = re.sub(r"[（(][A-Za-z0-9/#%+\-]+[)）]$", "", value).strip()
            value = self._strip_attached_latin_abbreviation(value)
        return value.strip(" :：|-")

    def _strip_attached_latin_abbreviation(self, value: str) -> str:
        if len(re.findall(r"[\u4e00-\u9fff]", value)) < 2:
            return value

        match = re.search(r"([A-Za-z]{2,}[A-Za-z0-9]*(?:-[A-Za-z0-9]+)*#?)$", value)
        if not match:
            return value

        trimmed = value[: match.start()].strip()
        if len(re.findall(r"[\u4e00-\u9fff]", trimmed)) < 2:
            return value
        return trimmed

    def _extract_unit_token(self, text: str) -> tuple[str, int]:
        stripped = text.lstrip()
        leading_space = len(text) - len(stripped)
        if not stripped:
            return "", leading_space

        token = stripped.split(maxsplit=1)[0]
        if self._is_supported_unit_token(token):
            return token, leading_space + len(token)
        return "", leading_space

    def _is_supported_unit_token(self, token: str) -> bool:
        token = _canonicalize_exponent_units(token)
        normalized = token.replace("μ", "u").replace("µ", "u").lower().strip().lstrip("*")
        if normalized in {
            "%",
            "ratio",
            "fl",
            "pg",
            "fg",
            "iu/l",
            "miu/l",
            "u/l",
            "g/l",
            "mg/l",
            "mg/dl",
            "mmol/l",
            "umol/l",
            "nmol/l",
            "pmol/l",
            "ng/ml",
            "pg/ml",
            "iu/ml",
            "miu/ml",
            "10^9/l",
            "10^12/l",
            "l/l",
        }:
            return True
        return re.fullmatch(r"10\^\d+/[a-z0-9^%]+", normalized, re.IGNORECASE) is not None

    def _looks_like_next_marker_fragment(self, text: str) -> bool:
        stripped = text.lstrip()
        if not stripped:
            return False
        token = stripped.split(maxsplit=1)[0].strip("|｜,，;；")
        if not token or self._is_supported_unit_token(token):
            return False
        if token in {"↑", "↓", "正常", "阴性", "阳性", "偏低", "偏高", "降低", "升高", "异常"}:
            return False
        return re.fullmatch(r"(?=.*[A-Za-z])[A-Za-z][A-Za-z0-9/%#()+.\-]{1,14}", token) is not None

    def _extract_status_token(self, text: str) -> tuple[str | None, int]:
        stripped = text.lstrip()
        leading_space = len(text) - len(stripped)
        candidates = ("↑", "↓", "正常", "阴性", "阳性", "偏低", "偏高", "降低", "升高", "异常")
        for candidate in candidates:
            if stripped.startswith(candidate):
                return candidate, leading_space + len(candidate)
        return None, 0

    def _status_from_tokens(
        self,
        *,
        value: float,
        lower: float,
        upper: float,
        flag: str,
        status_token: str | None,
    ) -> IndicatorStatus:
        if flag in {"↑", "↓"} or status_token in {"↑", "↓", "偏低", "偏高", "降低", "升高", "异常"}:
            return IndicatorStatus.attention
        if status_token == "阳性":
            return IndicatorStatus.positive
        if status_token in {"正常", "阴性"}:
            return IndicatorStatus.normal
        if value < lower or value > upper:
            return IndicatorStatus.attention
        return IndicatorStatus.normal

    def _parse_stacked_lab_block(self, block_lines: list[str]) -> dict | None:
        name = block_lines[0].strip()
        value_index = 1
        if len(block_lines) > 2 and self._looks_like_abbreviation_line(block_lines[1]) and (
            self._looks_like_numeric_result(block_lines[2]) or self._looks_like_qualitative_result(block_lines[2])
        ):
            value_index = 2

        value_line = block_lines[value_index].strip() if len(block_lines) > value_index else ""
        trailing_lines = block_lines[value_index + 1 :]
        qualitative_line = next(
            (line.strip() for line in trailing_lines if self._looks_like_qualitative_result(line)),
            None,
        )

        if self._looks_like_qualitative_result(value_line):
            normalized_result = self._normalize_qualitative_result(value_line)
            return {
                "name": name,
                "result_text": normalized_result,
                "status": self._status_from_qualitative_result(normalized_result),
            }

        value_match = re.search(r"(?P<value>-?\d+(?:\.\d+)?)(?P<flag>[↑↓]?)", value_line)
        if not value_match:
            return None

        value = float(value_match.group("value"))
        flag = value_match.group("flag")
        trailing_text = " ".join(trailing_lines)
        unit = self._extract_unit(trailing_lines, trailing_text)
        lower, upper = self._extract_range(trailing_text)

        # PPTX/表格类 OCR 常把“序号列”拆成单独数字行，例如:
        # 亚硝酸盐 / NIT / 13 / PH值 ...
        # 这里的 13 是行号而不是检验结果。没有单位、参考范围、异常箭头，
        # 且值是纯整数时，不把它当作结构化指标。
        if not flag and lower is None and upper is None and not unit:
            if qualitative_line:
                normalized_result = self._normalize_qualitative_result(qualitative_line)
                return {
                    "name": name,
                    "result_text": normalized_result,
                    "status": self._status_from_qualitative_result(normalized_result),
                }
            if "." not in value_line and not self._looks_like_measurement_hint(trailing_text):
                return None

        if flag in {"↑", "↓"}:
            status = IndicatorStatus.attention
        elif lower is not None or upper is not None:
            if lower is not None and value < lower:
                status = IndicatorStatus.attention
            elif upper is not None and value > upper:
                status = IndicatorStatus.attention
            else:
                status = IndicatorStatus.normal
        else:
            status = IndicatorStatus.info

        return {
            "name": name,
            "result_text": f"{value:g} {unit}".strip(),
            "status": status,
        }

    def _extract_unit(self, trailing_lines: list[str], trailing_text: str) -> str:
        for line in trailing_lines:
            stripped = line.strip()
            if self._looks_like_unit_line(stripped):
                return stripped

        unit_match = re.search(r"([A-Za-zμu/%]+(?:/[A-Za-zμu%]+)?)", trailing_text, re.IGNORECASE)
        if unit_match:
            return unit_match.group(1)
        return ""

    def _extract_range(self, trailing_text: str) -> tuple[float | None, float | None]:
        normalized = trailing_text.replace("—", "-").replace("–", "-").replace("~", "-").replace("至", "-")
        normalized = re.sub(r"([0-9])\s*--\s*([0-9])", r"\1--\2", normalized)

        pairs = re.findall(r"(-?\d+(?:\.\d+)?)\s*(?:--|[-~])\s*(-?\d+(?:\.\d+)?)", normalized)
        if pairs:
            lowers = [float(lower) for lower, _ in pairs]
            uppers = [float(upper) for _, upper in pairs]
            uppers = [abs(value) if value < 0 else value for value in uppers]
            return min(lowers), max(uppers)

        upper_match = re.search(r"[<＜]\s*(\d+(?:\.\d+)?)", normalized)
        if upper_match:
            return None, float(upper_match.group(1))

        lower_match = re.search(r"[>＞]\s*(\d+(?:\.\d+)?)", normalized)
        if lower_match:
            return float(lower_match.group(1)), None

        return None, None

    def _looks_like_marker_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if self._is_section_header(stripped):
            return False
        if self._looks_like_qualitative_result(stripped):
            return False
        if stripped in {"检查", "结果", "单位", "参考范围", "项目名称", "英文缩写", "序号"}:
            return False
        if len(stripped) > 40:
            return False
        if re.fullmatch(r"-?\d+(?:\.\d+)?[↑↓]?", stripped):
            return False
        if re.search(r"\d+\s*(?:--|[-~])\s*\d+", stripped):
            return False
        if re.search(r"\d+(?:\.\d+)?\s*(?:mmol/L|umol/L|μmol/L|ng/mL|pg/mL|g/L|U/L|fL|%|ratio)\b", stripped, re.IGNORECASE):
            return False
        return re.search(r"[\u4e00-\u9fff]", stripped) is not None

    def _looks_like_numeric_result(self, line: str) -> bool:
        stripped = line.strip()
        return re.fullmatch(r"-?\d+(?:\.\d+)?[↑↓]?", stripped) is not None

    def _looks_like_abbreviation_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        chinese_count = len(re.findall(r"[\u4e00-\u9fff]", stripped))
        if chinese_count > 2:
            return False
        if len(stripped) > 24:
            return False
        return re.fullmatch(r"(?=.*[A-Za-z])[A-Za-z0-9/#()+\-. \u4e00-\u9fff]+", stripped) is not None

    def _looks_like_qualitative_result(self, line: str) -> bool:
        stripped = re.sub(r"\s+", "", line.strip())
        if not stripped:
            return False
        normalized = stripped.upper()
        return normalized in {
            "阴性",
            "阳性",
            "弱阳性",
            "未检出",
            "未见",
            "正常",
            "-",
            "+",
            "±",
            "+-",
            "++",
            "+++",
            "++++",
            "TRACE",
            "微量",
        }

    def _looks_like_unit_line(self, line: str) -> bool:
        stripped = _canonicalize_exponent_units(line.strip())
        if not stripped:
            return False
        if re.search(r"[\u4e00-\u9fff]", stripped):
            return False
        if re.fullmatch(r"[A-Za-zμu0-9^%]+/[A-Za-zμu0-9^%]+", stripped, re.IGNORECASE):
            return True
        return stripped.lower() in {
            "%",
            "ratio",
            "fl",
            "pg",
            "fg",
            "iu/l",
            "miu/l",
            "u/l",
            "g/l",
            "mg/l",
            "mg/dl",
            "mmol/l",
            "umol/l",
            "μmol/l",
            "nmol/l",
            "pmol/l",
            "ng/ml",
            "pg/ml",
            "iu/ml",
            "miu/ml",
            "10^9/l",
            "10^12/l",
        }

    def _is_section_header(self, line: str) -> bool:
        stripped = line.strip()
        header_prefixes = ("检验项目", "检验时间", "分析项目", "结果", "参考值", "单位", "标志", "检查医生")
        return any(stripped.startswith(prefix) for prefix in header_prefixes)

    def _format_vital_result(self, indicator_name: str, raw_value: str) -> str:
        suffix_map = {
            "体温": "℃",
            "心率": "次/分",
            "呼吸": "次/分",
            "血压": "mmHg",
            "体重": "kg",
            "身高": "cm",
            "体质指数": "",
        }
        return f"{raw_value}{suffix_map.get(indicator_name, '')}"

    def _classify_vital(self, indicator_name: str, raw_value: str) -> IndicatorStatus:
        if indicator_name == "血压":
            systolic, diastolic = [int(part) for part in raw_value.split("/", 1)]
            if systolic >= 140 or diastolic >= 90 or systolic < 90 or diastolic < 60:
                return IndicatorStatus.attention
            return IndicatorStatus.normal

        numeric = float(raw_value)
        ranges = {
            "体温": (36.0, 37.3),
            "心率": (60, 100),
            "呼吸": (12, 20),
            "体重": (0, 1000),
            "身高": (0, 300),
            "体质指数": (18.5, 23.9),
        }
        lower, upper = ranges.get(indicator_name, (float("-inf"), float("inf")))
        return IndicatorStatus.normal if lower <= numeric <= upper else IndicatorStatus.attention

    def _classify_exam_conclusion(self, result_text: str) -> IndicatorStatus:
        normalized = result_text.replace(" ", "")
        if any(token in normalized for token in ("无异常", "正常", "未见出血", "无新发")):
            return IndicatorStatus.normal
        if any(token in normalized for token in ("异常", "阳性", "增高", "降低", "梗死", "出血")):
            return IndicatorStatus.attention
        return IndicatorStatus.info

    def _normalize_qualitative_result(self, value: str) -> str:
        normalized = re.sub(r"\s+", "", value.strip())
        return normalized.upper() if normalized.upper() == "TRACE" else normalized

    def _status_from_qualitative_result(self, value: str) -> IndicatorStatus:
        normalized = self._normalize_qualitative_result(value)
        if normalized in {"阴性", "未检出", "未见", "正常", "-"}:
            return IndicatorStatus.normal
        if normalized in {"阳性", "弱阳性", "+", "++", "+++", "++++", "±", "+-", "TRACE", "微量"}:
            return IndicatorStatus.attention
        return IndicatorStatus.info

    def _looks_like_measurement_hint(self, text: str) -> bool:
        normalized = _canonicalize_exponent_units(text).replace("μ", "u").replace("µ", "u")
        return bool(
            re.search(r"\d+\s*(?:--|[-~])\s*\d+", normalized)
            or re.search(r"[A-Za-zu/%]+(?:/[A-Za-zu%]+)?", normalized, re.IGNORECASE)
            or any(keyword in text for keyword in ("计算法", "酶法", "直接测定", "比浊法", "化学发光"))
        )

    def _sanitize_lab_snippet(self, snippet: str) -> str:
        cleaned = _canonicalize_exponent_units(snippet.strip())
        cleaned = re.sub(
            r"((?:\d+(?:\.\d+)?\s*(?:--|[-~])\s*\d+(?:\.\d+)?)(?:\s*[A-Za-zμu/%]+(?:/[A-Za-zμu%]+)?)?)(?:\s+\d{1,3})$",
            r"\1",
            cleaned,
        )
        if re.search(r"\d+\s*(?:--|[-~])\s*\d+", cleaned):
            cleaned = re.sub(r"\s+\d{1,3}$", "", cleaned)
        return cleaned

    def _clone_span(self, span: SourceSpan, snippet: str) -> SourceSpan:
        return SourceSpan(
            file_id=span.file_id,
            file_name=span.file_name,
            page=span.page,
            line_number=span.line_number,
            snippet=snippet.strip(),
        )

    def _trim_exam_result(self, result_text: str, current_name: str, indicator_names: list[str]) -> str:
        cutoff = len(result_text)
        for other_name in indicator_names:
            if other_name == current_name:
                continue
            match = re.search(re.escape(other_name), result_text, re.IGNORECASE)
            if match:
                cutoff = min(cutoff, match.start())
        trimmed = result_text[:cutoff].strip(" ：:，,;；")
        if trimmed in {"检查", "(五分类)", "（五分类）", "五分类"}:
            return ""
        return trimmed

    def _looks_like_exam_conclusion_match(self, matched_text: str, result_text: str) -> bool:
        if re.search(r"[:：]", matched_text):
            return True

        normalized_result = re.sub(r"\s+", "", result_text)
        if any(
            normalized_result.startswith(prefix)
            for prefix in (
                "无异常",
                "正常",
                "未见",
                "无新发",
                "阳性",
                "阴性",
                "异常",
                "增高",
                "降低",
                "偏高",
                "偏低",
                "窦性",
            )
        ):
            return True

        return False

    def _build_exam_snippet(self, matched_text: str, indicator_name: str, result_text: str) -> str:
        separator = " "
        if "：" in matched_text:
            separator = "："
        elif ":" in matched_text:
            separator = ": "
        return f"{indicator_name}{separator}{result_text}".strip()

    def _status_from_abnormal_flag(self, abnormal_flag: AbnormalFlag) -> IndicatorStatus:
        if abnormal_flag == AbnormalFlag.normal:
            return IndicatorStatus.normal
        if abnormal_flag in {AbnormalFlag.high, AbnormalFlag.low}:
            return IndicatorStatus.attention
        return IndicatorStatus.info

    def _status_from_reference_range(
        self,
        value: float,
        lower: float | None,
        upper: float | None,
    ) -> IndicatorStatus:
        if lower is not None and value < lower:
            return IndicatorStatus.attention
        if upper is not None and value > upper:
            return IndicatorStatus.attention
        if lower is not None or upper is not None:
            return IndicatorStatus.normal
        return IndicatorStatus.info

    def _is_admin_metadata_line(self, line: str) -> bool:
        normalized = re.sub(r"\s+", "", line).strip()
        if not normalized:
            return False
        return any(normalized.startswith(prefix.replace(" ", "")) for prefix in self._ADMIN_METADATA_PREFIXES)

    def _dedupe(self, indicators: list[CaseIndicator]) -> list[CaseIndicator]:
        seen: set[tuple[str, str, str]] = set()
        deduped: list[CaseIndicator] = []
        for indicator in indicators:
            category = "measurement" if indicator.category in {"lab", "manual"} else indicator.category
            signature = (
                category,
                self._canonical_indicator_name(indicator.indicator_name),
                indicator.result_text.strip(),
            )
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(indicator)
        return deduped

    def _canonical_indicator_name(self, value: str) -> str:
        return self._strip_attached_latin_abbreviation(re.sub(r"\s+", "", value.strip()))

    def _sort_indicators(self, indicators: list[CaseIndicator]) -> list[CaseIndicator]:
        status_priority = {
            IndicatorStatus.positive: 0,
            IndicatorStatus.attention: 1,
            IndicatorStatus.normal: 2,
            IndicatorStatus.info: 3,
        }
        category_priority = {
            "lab": 0,
            "manual": 0,
            "clinical_finding": 1,
            "exam_conclusion": 2,
            "vital_sign": 3,
            "case_text": 4,
        }

        return sorted(
            indicators,
            key=lambda item: (
                status_priority.get(item.status, 99),
                category_priority.get(item.category, 99),
                item.indicator_name,
            ),
        )
