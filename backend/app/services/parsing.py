from __future__ import annotations

import json
import re
from pathlib import Path

from app.domain.models import AbnormalFlag, ExtractedLabItem, ReferenceRange, SourceSpan
from app.providers.base import OCRExtraction, OCRProvider


def _clean_token(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value).strip()


def _canonicalize_exponent_units(value: str) -> str:
    return value.replace("∧", "^").replace("＾", "^").replace("ˆ", "^")


class LabNormalizationService:
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

    def __init__(self, marker_catalog_path: Path) -> None:
        payload = json.loads(marker_catalog_path.read_text(encoding="utf-8-sig"))
        self.markers = payload

    def normalize(self, *, spans: list[SourceSpan]) -> list[ExtractedLabItem]:
        normalized_items: list[ExtractedLabItem] = []
        seen: set[tuple[str, float | None, str | None, str, str]] = set()

        for span in self._iter_candidate_spans(spans):
            line = span.snippet.strip()
            if not self._looks_like_candidate_line(line):
                continue

            marker = self._match_marker(line)
            if not marker:
                continue
            if marker["code"] == "uric_acid" and "尿酸碱度" in line:
                continue

            parsed = self._parse_numeric_line(line, marker)
            if not parsed:
                continue

            value, unit, raw_range, parsed_range, raw_name = parsed
            converted = self._convert_value(marker, value=value, unit=unit, parsed_range=parsed_range)
            if not converted:
                continue

            normalized_value, normalized_unit, reference_range = converted
            abnormal_flag = self._explicit_abnormal_flag(line) or self._classify(normalized_value, reference_range)
            signature = (
                marker["code"],
                normalized_value,
                normalized_unit,
                raw_range or "",
                span.file_id or span.file_name or "",
            )
            if signature in seen:
                continue
            seen.add(signature)

            normalized_items.append(
                ExtractedLabItem(
                    marker_code=marker["code"],
                    marker_name=marker["display_name"],
                    raw_name=raw_name,
                    raw_value=str(value),
                    value=value,
                    unit=unit,
                    normalized_value=normalized_value,
                    normalized_unit=normalized_unit,
                    ref_range=ReferenceRange(
                        lower=reference_range[0],
                        upper=reference_range[1],
                        raw=raw_range,
                    ),
                    abnormal_flag=abnormal_flag,
                    confidence=0.92,
                    source_span=span,
                )
            )

        return normalized_items

    def _iter_candidate_spans(self, spans: list[SourceSpan]) -> list[SourceSpan]:
        candidates: list[SourceSpan] = []
        candidates.extend(self._build_structured_table_candidates(spans))
        candidates.extend(span for span in spans if span.snippet.strip())
        candidates.extend(self._build_multiline_candidates(spans))
        return candidates

    def _build_structured_table_candidates(self, spans: list[SourceSpan]) -> list[SourceSpan]:
        table_rows: list[SourceSpan] = []
        cleaned_spans = [span for span in spans if span.snippet.strip()]

        index = 0
        while index < len(cleaned_spans):
            current = cleaned_spans[index]
            current_text = current.snippet.strip()
            if not self._looks_like_multiline_marker(current_text) or not self._match_marker(current_text):
                index += 1
                continue

            cursor = index + 1
            if cursor >= len(cleaned_spans):
                break

            block = [current]
            next_text = cleaned_spans[cursor].snippet.strip()
            if self._looks_like_abbreviation_line(next_text) and not self._looks_like_value_line(next_text):
                block.append(cleaned_spans[cursor])
                cursor += 1

            if cursor >= len(cleaned_spans):
                index += 1
                continue

            value_text = cleaned_spans[cursor].snippet.strip()
            if not (self._looks_like_value_line(value_text) or self._looks_like_qualitative_result(value_text)):
                index += 1
                continue

            value_relative_index = len(block)
            block.append(cleaned_spans[cursor])
            cursor += 1

            while cursor < len(cleaned_spans) and len(block) < 8:
                candidate = cleaned_spans[cursor]
                candidate_text = candidate.snippet.strip()
                if self._is_section_header(candidate_text):
                    break
                if self._looks_like_multiline_marker(candidate_text) and self._match_marker(candidate_text):
                    break
                if self._looks_like_table_row_number(candidate_text):
                    break
                if (
                    self._looks_like_unit_line(candidate_text)
                    or self._looks_like_range_line(candidate_text)
                    or self._looks_like_method_line(candidate_text)
                    or self._looks_like_reference_note_line(candidate_text)
                ):
                    block.append(candidate)
                    cursor += 1
                    continue
                break

            if self._table_block_has_measurement_evidence(
                [item.snippet.strip() for item in block],
                value_relative_index,
            ):
                table_rows.append(
                    SourceSpan(
                        file_id=current.file_id,
                        file_name=current.file_name,
                        page=current.page,
                        line_number=current.line_number,
                        snippet=self._join_table_block(block),
                    )
                )

            index += 1

        return table_rows

    def _build_multiline_candidates(self, spans: list[SourceSpan]) -> list[SourceSpan]:
        multiline: list[SourceSpan] = []
        cleaned_spans = [span for span in spans if span.snippet.strip()]

        index = 0
        while index < len(cleaned_spans):
            current = cleaned_spans[index]
            current_text = current.snippet.strip()
            if not self._looks_like_multiline_marker(current_text):
                index += 1
                continue

            if index + 1 >= len(cleaned_spans):
                break

            next_text = cleaned_spans[index + 1].snippet.strip()
            value_index = index + 1
            if self._looks_like_abbreviation_line(next_text):
                if index + 2 >= len(cleaned_spans):
                    index += 1
                    continue
                third_text = cleaned_spans[index + 2].snippet.strip()
                if not (self._looks_like_value_line(third_text) or self._looks_like_qualitative_result(third_text)):
                    index += 1
                    continue
                value_index = index + 2
            elif not (self._looks_like_value_line(next_text) or self._looks_like_qualitative_result(next_text)):
                index += 1
                continue

            block = [current]
            cursor = index + 1
            while cursor < len(cleaned_spans) and len(block) < 8:
                candidate = cleaned_spans[cursor]
                candidate_text = candidate.snippet.strip()
                if self._is_section_header(candidate_text):
                    break
                if len(block) >= 2 and self._looks_like_multiline_marker(candidate_text):
                    break
                block.append(candidate)
                cursor += 1
                if self._looks_like_qualitative_result(candidate_text):
                    break

            relative_value_index = value_index - index
            block_texts = [item.snippet.strip() for item in block]
            if len(block) >= 2 and self._table_block_has_measurement_evidence(block_texts, relative_value_index):
                multiline.append(
                    SourceSpan(
                        file_id=current.file_id,
                        file_name=current.file_name,
                        page=current.page,
                        line_number=current.line_number,
                        snippet=self._join_table_block(block),
                    )
                )

            index += 1

        return multiline

    def _match_marker(self, line: str) -> dict | None:
        best_match: tuple[int, dict, str] | None = None
        for marker in self.markers:
            for synonym in marker.get("synonyms", []):
                if not self._synonym_matches(line, synonym):
                    continue
                score = len(_clean_token(synonym))
                if not best_match or score > best_match[0]:
                    best_match = (score, marker, synonym)
        return best_match[1] if best_match else None

    def _parse_numeric_line(
        self,
        line: str,
        marker: dict,
    ) -> tuple[float, str | None, str | None, tuple[float | None, float | None], str | None] | None:
        if self._is_admin_metadata_line(line):
            return None
        raw_name = self._extract_raw_name(line, marker)
        working = (
            _canonicalize_exponent_units(line)
            .replace("—", "-")
            .replace("–", "-")
            .replace("~", "-")
            .replace("～", "-")
            .replace("至", "-")
            .replace("＞", ">")
            .replace("＜", "<")
        )
        working = re.sub(r"\s*[|｜]\s*", " ", working)
        working = re.sub(r"\s+", " ", working).strip()
        working = re.sub(r"([0-9])\s*--\s*([0-9])", r"\1--\2", working)

        inline_value_range = self._parse_inline_value_with_range(working)
        if inline_value_range:
            value, unit, raw_range, parsed_range = inline_value_range
            if unit and not self._unit_looks_valid(marker, unit):
                return None
            return value, unit, raw_range, parsed_range, raw_name

        marker_match = self._find_marker_match(working, marker)
        tail = working[marker_match[1] :] if marker_match else working
        tail = tail.strip(" :：|")
        if not tail:
            return None
        tail = re.sub(
            r"^(?:[A-Za-z][A-Za-z0-9/#()+\-]*)(?:\s+[A-Za-z0-9/#()+\-]+){0,2}\s+(?=-?\d)",
            "",
            tail,
        )

        value_match = re.search(
            r"(?P<value>-?\d+(?:\.\d+)?)\s*(?P<flag>[↑↓]?)\s*(?P<unit>(?:10\^\d+/[A-Za-zμu%^0-9]+|[A-Za-zμu/%][A-Za-zμu/%^0-9]*(?:/[A-Za-zμu%^0-9]+)?))?",
            tail,
            re.IGNORECASE,
        )
        if not value_match:
            return None

        value = float(value_match.group("value"))
        unit = value_match.group("unit")
        remainder = tail[value_match.end() :]

        parsed_range = (None, None)
        raw_range = None

        range_match = re.search(
            r"(?P<lower>-?\d+(?:\.\d+)?)\s*(?P<sep>--|[-~])\s*(?P<upper>-?\d+(?:\.\d+)?)",
            remainder,
            re.IGNORECASE,
        )
        if range_match:
            lower = float(range_match.group("lower"))
            upper = float(range_match.group("upper"))
            if range_match.group("sep") == "--" and lower >= 0 and upper < 0:
                upper = abs(upper)
            parsed_range = (lower, upper)
            raw_range = f"{lower:g}-{upper:g}"

            if not unit:
                unit_match = re.search(
                    r"(10\^\d+/[A-Za-zμu%^0-9]+|[A-Za-zμu/%][A-Za-zμu/%^0-9]*(?:/[A-Za-zμu%^0-9]+)?)",
                    remainder[range_match.end() :],
                    re.IGNORECASE,
                )
                if unit_match:
                    unit = unit_match.group(1)
        else:
            upper_only_match = re.search(r"<\s*(?P<upper>-?\d+(?:\.\d+)?)", remainder, re.IGNORECASE)
            if upper_only_match:
                upper = float(upper_only_match.group("upper"))
                parsed_range = (None, upper)
                raw_range = f"<{upper:g}"
                if not unit:
                    unit_match = re.search(
                        r"(10\^\d+/[A-Za-zμu%^0-9]+|[A-Za-zμu/%][A-Za-zμu/%^0-9]*(?:/[A-Za-zμu%^0-9]+)?)",
                        remainder[upper_only_match.end() :],
                        re.IGNORECASE,
                    )
                    if unit_match:
                        unit = unit_match.group(1)
            else:
                lower_only_match = re.search(r">\s*(?P<lower>-?\d+(?:\.\d+)?)", remainder, re.IGNORECASE)
                if lower_only_match:
                    lower = float(lower_only_match.group("lower"))
                    parsed_range = (lower, None)
                    raw_range = f">{lower:g}"
                    if not unit:
                        unit_match = re.search(
                            r"(10\^\d+/[A-Za-zμu%^0-9]+|[A-Za-zμu/%][A-Za-zμu/%^0-9]*(?:/[A-Za-zμu%^0-9]+)?)",
                            remainder[lower_only_match.end() :],
                            re.IGNORECASE,
                        )
                        if unit_match:
                            unit = unit_match.group(1)

        if unit and not self._unit_looks_valid(marker, unit):
            return None

        return value, unit, raw_range, parsed_range, raw_name

    def _looks_like_candidate_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if self._is_admin_metadata_line(stripped):
            return False
        if len(stripped) > 220:
            return False
        if not re.search(r"\d", stripped):
            return False
        if re.search(r"[A-Za-z0-9]{18,}", stripped):
            return False
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", stripped):
            return False
        if "无异常" in stripped and stripped.count(" ") < 2:
            return False
        if re.search(r"^(?:若|当).{0,40}(?:在|达到|超过|低于)\s*\d", stripped):
            return False
        return True

    def _is_admin_metadata_line(self, line: str) -> bool:
        normalized = re.sub(r"\s+", "", line).strip()
        if not normalized:
            return False
        return any(normalized.startswith(prefix.replace(" ", "")) for prefix in self._ADMIN_METADATA_PREFIXES)

    def _looks_like_multiline_marker(self, line: str) -> bool:
        stripped = _canonicalize_exponent_units(line.strip())
        if not stripped:
            return False
        if self._is_section_header(stripped):
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

    def _looks_like_value_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
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

    def _looks_like_range_line(self, line: str) -> bool:
        normalized = line.replace("—", "-").replace("–", "-").replace("~", "-").replace("至", "-").strip()
        if not normalized:
            return False
        if re.search(r"(?:男|女)?[:：]?\s*-?\d+(?:\.\d+)?\s*(?:--|[-~])\s*-?\d+(?:\.\d+)?", normalized):
            return True
        return re.search(r"[<>＜＞]\s*-?\d+(?:\.\d+)?", normalized) is not None

    def _looks_like_table_row_number(self, line: str) -> bool:
        stripped = line.strip()
        return re.fullmatch(r"\d{1,3}", stripped) is not None

    def _looks_like_method_line(self, line: str) -> bool:
        return any(keyword in line for keyword in ("计算法", "酶法", "比色法", "比浊法", "化学发光", "直接测定"))

    def _looks_like_reference_note_line(self, line: str) -> bool:
        normalized = line.strip()
        if len(normalized) > 40:
            return False
        return any(keyword in normalized for keyword in ("为正常", "为临界", "偏高", "偏低"))

    def _table_block_has_measurement_evidence(self, block_lines: list[str], value_index: int) -> bool:
        if value_index < 0 or value_index >= len(block_lines):
            return False
        value_line = block_lines[value_index].strip()
        if self._looks_like_qualitative_result(value_line):
            return True
        if re.search(r"[↑↓]", value_line):
            return True

        trailing_lines = block_lines[value_index + 1 :]
        if any(self._looks_like_unit_line(line) for line in trailing_lines):
            return True
        if any(self._looks_like_range_line(line) for line in trailing_lines):
            return True
        if any(self._looks_like_method_line(line) for line in trailing_lines):
            return True

        combined = " ".join(block_lines[value_index + 1 :])
        return bool(
            re.search(r"\d+\s*(?:--|[-~])\s*\d+", combined.replace("—", "-").replace("–", "-").replace("~", "-"))
            or re.search(r"\d+(?:\.\d+)?\s*(?:mmol/L|umol/L|μmol/L|ng/mL|pg/mL|g/L|U/L|fL|%|ratio)\b", combined, re.IGNORECASE)
        )

    def _join_table_block(self, block: list[SourceSpan]) -> str:
        parts = [item.snippet.strip() for item in block if item.snippet.strip()]
        cleaned = " ".join(parts)
        if re.search(r"\d+\s*(?:--|[-~])\s*\d+", cleaned):
            cleaned = re.sub(r"\s+\d{1,3}$", "", cleaned)
        return cleaned

    def _synonym_matches(self, line: str, synonym: str) -> bool:
        cleaned_synonym = _clean_token(synonym)
        if not cleaned_synonym:
            return False

        if re.fullmatch(r"[A-Za-z][A-Za-z0-9/-]{0,9}", cleaned_synonym):
            pattern = rf"(?<![A-Za-z]){re.escape(cleaned_synonym)}(?![A-Za-z])"
            return re.search(pattern, line, re.IGNORECASE) is not None

        return cleaned_synonym.lower() in _clean_token(line).lower()

    def _unit_looks_valid(self, marker: dict, unit: str | None) -> bool:
        expected_units = marker.get("unit_factors", {}).keys()
        if not unit or not expected_units:
            return True

        unit = _canonicalize_exponent_units(unit)
        comparable_unit = unit.replace("μ", "u").replace("µ", "u").lower()
        comparable_expected = {
            expected.replace("μ", "u").replace("µ", "u").lower()
            for expected in expected_units
        }
        return comparable_unit in comparable_expected

    def _convert_value(
        self,
        marker: dict,
        *,
        value: float,
        unit: str | None,
        parsed_range: tuple[float | None, float | None],
    ) -> tuple[float, str | None, tuple[float | None, float | None]] | None:
        unit_factors = marker.get("unit_factors", {})
        normalized_unit = marker.get("normalized_unit")
        factor = 1.0

        if unit and unit_factors:
            unit = _canonicalize_exponent_units(unit)
            comparable_key = unit.replace("μ", "u").replace("µ", "u").lower()
            matched_factor = None
            for raw_unit, raw_factor in unit_factors.items():
                comparable_raw = raw_unit.replace("μ", "u").replace("µ", "u").lower()
                if comparable_raw == comparable_key:
                    matched_factor = raw_factor
                    break
            if matched_factor is None:
                return None
            factor = float(matched_factor)
        elif unit_factors and not unit:
            factor = 1.0
            normalized_unit = marker.get("normalized_unit")

        normalized_value = round(value * factor, 4)
        lower, upper = parsed_range
        if lower is not None:
            lower = round(lower * factor, 4)
        if upper is not None:
            upper = round(upper * factor, 4)

        if lower is None and upper is None:
            default_range = marker.get("default_range", {})
            lower = default_range.get("lower")
            upper = default_range.get("upper")

        return normalized_value, normalized_unit or unit, (lower, upper)

    def _classify(self, value: float, reference_range: tuple[float | None, float | None]) -> AbnormalFlag:
        lower, upper = reference_range
        if lower is not None and value < lower:
            return AbnormalFlag.low
        if upper is not None and value > upper:
            return AbnormalFlag.high
        if lower is not None or upper is not None:
            return AbnormalFlag.normal
        return AbnormalFlag.unknown

    def _explicit_abnormal_flag(self, line: str) -> AbnormalFlag | None:
        has_high = "↑" in line
        has_low = "↓" in line
        if has_high == has_low:
            return None
        return AbnormalFlag.high if has_high else AbnormalFlag.low

    def _marker_pattern(self, marker: dict) -> str:
        synonyms = sorted((re.escape(item) for item in marker.get("synonyms", [])), key=len, reverse=True)
        return "|".join(synonyms)

    def _parse_inline_value_with_range(
        self,
        line: str,
    ) -> tuple[float, str | None, str | None, tuple[float | None, float | None]] | None:
        patterns = [
            re.compile(
                r"(?P<value>-?\d+(?:\.\d+)?)\s+(?P<unit>10\^\d+/[A-Za-zμu%^0-9]+|[A-Za-zμu/%][A-Za-zμu/%^0-9]*(?:/[A-Za-zμu%^0-9]+)?)\s+"
                r"(?P<lower>-?\d+(?:\.\d+)?)\s*(?P<sep>--|[-~])\s*(?P<upper>-?\d+(?:\.\d+)?)",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?P<value>-?\d+(?:\.\d+)?)\s+"
                r"(?P<lower>-?\d+(?:\.\d+)?)\s*(?P<sep>--|[-~])\s*(?P<upper>-?\d+(?:\.\d+)?)",
                re.IGNORECASE,
            ),
        ]

        for pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            value = float(match.group("value"))
            unit = match.groupdict().get("unit")
            lower = float(match.group("lower"))
            upper = float(match.group("upper"))
            if match.group("sep") == "--" and lower >= 0 and upper < 0:
                upper = abs(upper)
            if not unit:
                unit_tail = line[match.end() :]
                unit_match = re.search(
                    r"\b(10\^\d+/[A-Za-zμu%^0-9]+|[A-Za-zμu/%][A-Za-zμu/%^0-9]*(?:/[A-Za-zμu%^0-9]+)?)\b",
                    unit_tail,
                    re.IGNORECASE,
                )
                if unit_match:
                    unit = unit_match.group(1)
            return value, unit, f"{lower:g}-{upper:g}", (lower, upper)

        return None

    def _find_marker_match(self, line: str, marker: dict) -> tuple[int, int, str] | None:
        best_match: tuple[int, int, str] | None = None
        for synonym in marker.get("synonyms", []):
            match = self._find_synonym_in_line(line, synonym)
            if not match:
                continue
            start, end = match
            if best_match is None:
                best_match = (start, end, synonym)
                continue
            best_start, _, best_synonym = best_match
            if start < best_start:
                best_match = (start, end, synonym)
                continue
            if start == best_start and len(_clean_token(synonym)) > len(_clean_token(best_synonym)):
                best_match = (start, end, synonym)
        return best_match

    def _find_synonym_in_line(self, line: str, synonym: str) -> tuple[int, int] | None:
        cleaned_synonym = _clean_token(synonym)
        if not cleaned_synonym:
            return None

        if re.fullmatch(r"[A-Za-z][A-Za-z0-9/-]{0,9}", cleaned_synonym):
            pattern = rf"(?<![A-Za-z]){re.escape(cleaned_synonym)}(?![A-Za-z])"
            match = re.search(pattern, line, re.IGNORECASE)
            if not match:
                return None
            return match.start(), match.end()

        cleaned_line = _clean_token(line)
        start_in_clean = cleaned_line.find(cleaned_synonym)
        if start_in_clean < 0:
            return None

        visible_positions = [index for index, char in enumerate(line) if not char.isspace()]
        if start_in_clean >= len(visible_positions):
            return None

        end_in_clean = start_in_clean + len(cleaned_synonym) - 1
        if end_in_clean >= len(visible_positions):
            return None

        original_start = visible_positions[start_in_clean]
        original_end = visible_positions[end_in_clean] + 1
        return original_start, original_end

    def _extract_raw_name(self, line: str, marker: dict) -> str | None:
        marker_match = self._find_marker_match(line, marker)
        if marker_match:
            return marker_match[2]
        for synonym in sorted(marker.get("synonyms", []), key=len, reverse=True):
            if self._synonym_matches(line, synonym):
                return synonym
        return marker.get("display_name")


class DocumentParsingService:
    def __init__(self, *, ocr_provider: OCRProvider, normalization_service: LabNormalizationService) -> None:
        self.ocr_provider = ocr_provider
        self.normalization_service = normalization_service

    def extract_text(self, *, filename: str, content_type: str, content: bytes) -> OCRExtraction:
        return self.ocr_provider.extract(filename=filename, content_type=content_type, content=content)

    def parse(self, *, filename: str, content_type: str, content: bytes) -> tuple[OCRExtraction, list[ExtractedLabItem]]:
        extraction = self.extract_text(filename=filename, content_type=content_type, content=content)
        lab_items = self.normalization_service.normalize(spans=extraction.spans)
        return extraction, lab_items
