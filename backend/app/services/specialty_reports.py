from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.domain.models import (
    AbnormalFlag,
    ChronicFoodSensitivityReport,
    GutFunctionReport,
    GutMicrobiomeReport,
    ReferenceRange,
    SourceSpan,
    SpecialtyMetric,
    SpecialtyReportResult,
    SpecialtyReviewStatus,
)
from app.providers.base import OCRExtraction


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\u3000", " ").replace("\xa0", " ")).strip()


def _canonical(value: str) -> str:
    return re.sub(r"\s+", "", (value or "")).lower()


def _split_items(value: str) -> list[str]:
    cleaned = _clean_text(value).strip("：:;；,，。 ")
    if not cleaned or cleaned.lower() in {"无", "none", "no"}:
        return []
    return [item.strip() for item in re.split(r"[,，、;；]", cleaned) if item.strip() and item.strip() != "无"]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


class SpecialtyReportParser:
    """Deterministic parsers for stable vendor report templates.

    Values are extracted from the document text/layout layer only. Missing or
    ambiguous fields remain empty and force manual review; the parser never
    infers patient results from explanatory prose.
    """

    _GUT_METRICS = (
        ("anti_gliadin_siga", "麦胶蛋白抗体", ("麦胶蛋白抗体", "Anti-Gliadin"), "U/L"),
        ("anti_httg_siga", "组织转麸胺酶抗体", ("组织转麸胺酶抗体", "组织转麸酰胺酶抗体", "Anti-htTG"), "U/L"),
        ("secretory_iga", "分泌型免疫球蛋白 sIgA", ("分泌型免疫球蛋白", "sIgA", "slgA"), "μg/mL"),
        ("fecal_calprotectin", "钙卫蛋白", ("钙卫蛋白", "Calprotectin"), "mg/kg"),
        ("zonulin", "解连蛋白", ("解连蛋白", "解连 蛋白", "Zonulin"), "ng/mL"),
        ("beta_glucuronidase", "β-葡萄糖醛酸酶", ("葡萄糖醛酸酶", "β-葡萄糖醛酸酶", "Beta-Glucuronidase"), "U/g"),
        ("pancreatic_elastase", "胰弹性蛋白酶", ("胰弹性蛋白酶", "Pancreatic Elastase"), "μg/g"),
    )

    def parse(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        extraction: OCRExtraction,
        file_id: str,
    ) -> list[SpecialtyReportResult]:
        layout_pages, plain_pages = self._document_pages(
            filename=filename,
            content_type=content_type,
            content=content,
            extraction=extraction,
        )
        combined = "\n".join(plain_pages.values()) + "\n" + extraction.text
        normalized = _canonical(combined)

        if self._is_food_sensitivity(normalized):
            return [self._parse_food_sensitivity(filename, file_id, layout_pages, plain_pages)]
        if self._is_gut_function(normalized):
            return [self._parse_gut_function(filename, file_id, layout_pages, plain_pages)]
        if self._is_gut_microbiome(normalized):
            return [self._parse_gut_microbiome(filename, file_id, layout_pages, plain_pages)]
        return []

    def _document_pages(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        extraction: OCRExtraction,
    ) -> tuple[dict[int, str], dict[int, str]]:
        fallback_pages: dict[int, list[str]] = {}
        for span in extraction.spans:
            fallback_pages.setdefault(span.page, []).append(span.snippet)
        fallback = {page: "\n".join(lines) for page, lines in fallback_pages.items()}

        suffix = Path(filename).suffix.lower()
        if suffix != ".pdf" and content_type != "application/pdf":
            return fallback, fallback

        try:
            reader = PdfReader(BytesIO(content))
        except Exception:
            return fallback, fallback

        layout_pages: dict[int, str] = {}
        plain_pages: dict[int, str] = {}
        for page_number, page in enumerate(reader.pages, start=1):
            plain = page.extract_text() or fallback.get(page_number, "")
            try:
                layout = page.extract_text(extraction_mode="layout") or plain
            except (TypeError, ValueError):
                layout = plain
            plain_pages[page_number] = plain
            layout_pages[page_number] = layout
        return layout_pages, plain_pages

    def _is_food_sensitivity(self, normalized: str) -> bool:
        anchors = ("慢性食物敏感", "建议报告", "轻度(mild)", "igg抗体")
        return sum(anchor in normalized for anchor in anchors) >= 3

    def _is_gut_function(self, normalized: str) -> bool:
        anchors = ("gifunction(stool)", "anti-gliadin", "calprotectin", "zonulin", "pancreaticelastase")
        return sum(anchor in normalized for anchor in anchors) >= 3

    def _is_gut_microbiome(self, normalized: str) -> bool:
        anchors = ("gutmicrobiotaprofile", "肠道菌群健康综合评估", "肠道菌群多样性", "报告总结")
        return sum(anchor in normalized for anchor in anchors) >= 2

    def _source_span(self, *, filename: str, file_id: str, page: int, snippet: str) -> SourceSpan:
        return SourceSpan(
            file_id=file_id,
            file_name=filename,
            page=page,
            snippet=_clean_text(snippet)[:500],
        )

    def _find_page(self, pages: dict[int, str], *anchors: str) -> tuple[int, str]:
        for page, text in pages.items():
            normalized = _canonical(text)
            if all(_canonical(anchor) in normalized for anchor in anchors):
                return page, text
        return 1, next(iter(pages.values()), "")

    def _find_best_page(self, pages: dict[int, str], *anchors: str) -> tuple[int, str]:
        ranked = []
        for page, text in pages.items():
            normalized = _canonical(text)
            score = sum(1 for anchor in anchors if _canonical(anchor) in normalized)
            ranked.append((score, page, text))
        if not ranked:
            return 1, ""
        score, page, text = max(ranked, key=lambda item: (item[0], item[1]))
        return (page, text) if score else (1, next(iter(pages.values()), ""))

    def _parse_food_sensitivity(
        self,
        filename: str,
        file_id: str,
        layout_pages: dict[int, str],
        plain_pages: dict[int, str],
    ) -> ChronicFoodSensitivityReport:
        page, text = self._find_page(plain_pages, "轻度", "中度", "重度", "建议报告")
        severity: dict[str, list[str]] = {}
        for key, label in (("mild", "轻度"), ("moderate", "中度"), ("high", "重度")):
            match = re.search(
                rf"{label}\s*(?:\([^)]*\))?\s*[：:]\s*([^\n\r]+)",
                text,
                flags=re.IGNORECASE,
            )
            severity[key] = _split_items(match.group(1) if match else "")

        full_text = "\n".join(plain_pages.values())
        interpretations = [
            _clean_text(match.group(0))
            for match in re.finditer(
                r"\d+[.、]\s*延迟性过敏反应\s*IgG.*?(?:。|$)",
                full_text,
                flags=re.IGNORECASE | re.DOTALL,
            )
        ]
        interpretations = _unique(interpretations)[:3]
        warnings: list[str] = []
        if not all(re.search(rf"{label}\s*(?:\([^)]*\))?\s*[：:]", text, flags=re.IGNORECASE) for label in ("轻度", "中度", "重度")):
            warnings.append("慢性食物敏感分级字段不完整，请人工核对。")
        if len(interpretations) < 3:
            warnings.append("慢性食物敏感报告解读提取不完整，请人工核对。")

        complete = not warnings
        summary_lines = []
        for label, values in (("轻度", severity["mild"]), ("中度", severity["moderate"]), ("重度", severity["high"])):
            summary_lines.append(f"{label}：{'、'.join(values) if values else '无'}")
        return ChronicFoodSensitivityReport(
            id=f"specialty_food_{file_id}",
            file_id=file_id,
            review_status=SpecialtyReviewStatus.pending_review if complete else SpecialtyReviewStatus.needs_review,
            confidence=0.96 if complete else 0.68,
            source_pages=[page],
            needs_manual_review=True,
            warnings=warnings,
            summary_lines=summary_lines,
            recommendations=interpretations,
            mild_foods=severity["mild"],
            moderate_foods=severity["moderate"],
            high_foods=severity["high"],
            interpretations=interpretations,
        )

    def _parse_gut_function(
        self,
        filename: str,
        file_id: str,
        layout_pages: dict[int, str],
        plain_pages: dict[int, str],
    ) -> GutFunctionReport:
        results_page, layout = self._find_page(layout_pages, "检测结果", "Calprotectin", "Zonulin")
        metrics: list[SpecialtyMetric] = []
        for index, (code, name, aliases, default_unit) in enumerate(self._GUT_METRICS):
            start = self._first_index(layout, aliases)
            if start < 0:
                continue
            next_aliases = self._GUT_METRICS[index + 1][2] if index + 1 < len(self._GUT_METRICS) else ()
            end = self._first_index(layout, next_aliases, start=start + 1) if next_aliases else len(layout)
            if end < 0:
                end = len(layout)
            block = layout[start:end]
            parsed = self._parse_metric_block(block)
            if not parsed:
                continue
            value, raw_value, unit, ref_range = parsed
            metrics.append(
                SpecialtyMetric(
                    code=code,
                    name=name,
                    value=value,
                    raw_value=raw_value,
                    unit=unit or default_unit,
                    ref_range=ref_range,
                    abnormal_flag=self._classify(value, ref_range),
                    confidence=0.97,
                    source_span=self._source_span(
                        filename=filename,
                        file_id=file_id,
                        page=results_page,
                        snippet=block,
                    ),
                )
            )

        full_plain = "\n".join(plain_pages.values())
        interpretations = _unique(
            [
                _clean_text(match.group(0))
                for match in re.finditer(r"(?:偏高|偏低|正常)[：:]\s*您的[^。]{3,260}。", full_plain, re.DOTALL)
            ]
        )
        for metric in metrics:
            aliases = next(item[2] for item in self._GUT_METRICS if item[0] == metric.code)
            metric.interpretation = next(
                (line for line in interpretations if any(_canonical(alias) in _canonical(line) for alias in aliases)),
                None,
            )

        recommendation_page, recommendation_text = self._find_page(plain_pages, "肠道健康改善建议")
        recommendations = self._recommendation_lines(recommendation_text)
        warnings: list[str] = []
        if len(metrics) != len(self._GUT_METRICS):
            warnings.append(f"肠道功能指标仅识别 {len(metrics)}/{len(self._GUT_METRICS)} 项，请人工核对。")
        if not interpretations:
            warnings.append("肠道功能原报告解释未完整提取，请人工核对。")
        complete = not warnings
        source_pages = sorted({results_page, recommendation_page, *[page for page, text in plain_pages.items() if "指标说明" in text]})
        summary_lines = [
            f"{metric.name}：{metric.raw_value or metric.value} {metric.unit or ''}（{self._flag_label(metric.abnormal_flag)}）".strip()
            for metric in metrics
        ]
        return GutFunctionReport(
            id=f"specialty_gut_function_{file_id}",
            file_id=file_id,
            review_status=SpecialtyReviewStatus.pending_review if complete else SpecialtyReviewStatus.needs_review,
            confidence=0.97 if complete else 0.62,
            source_pages=source_pages,
            needs_manual_review=True,
            warnings=warnings,
            summary_lines=summary_lines,
            recommendations=recommendations,
            metrics=metrics,
            interpretations=interpretations,
        )

    def _first_index(self, text: str, aliases: tuple[str, ...], *, start: int = 0) -> int:
        indexes = [text.lower().find(alias.lower(), start) for alias in aliases]
        valid = [index for index in indexes if index >= 0]
        return min(valid) if valid else -1

    def _parse_metric_block(self, block: str) -> tuple[float, str, str | None, ReferenceRange] | None:
        normalized = block.replace("＜", "<").replace("＞", ">").replace("—", "-").replace("–", "-")
        ref_match = re.search(r"(?<!\w)([<>])\s*(\d+(?:\.\d+)?)", normalized)
        range_match = re.search(r"(?<!\w)(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)", normalized)
        ref_range = ReferenceRange()
        ref_text = ""
        if range_match:
            lower, upper = float(range_match.group(1)), float(range_match.group(2))
            ref_range = ReferenceRange(lower=lower, upper=upper, raw=range_match.group(0))
            ref_text = range_match.group(0)
        elif ref_match:
            bound = float(ref_match.group(2))
            if ref_match.group(1) == "<":
                ref_range = ReferenceRange(upper=bound, raw=ref_match.group(0))
            else:
                ref_range = ReferenceRange(lower=bound, raw=ref_match.group(0))
            ref_text = ref_match.group(0)

        value_text = normalized.replace(ref_text, " ", 1) if ref_text else normalized
        numbers = re.findall(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)(?![A-Za-z0-9.])", value_text)
        if not numbers:
            return None
        raw_value = numbers[0]
        unit_match = re.search(r"(?:μ|u|mcg)?g/(?:mL|ml|g)|mg/kg|ng/mL|U/(?:L|g)", normalized, re.IGNORECASE)
        unit = unit_match.group(0) if unit_match else None
        return float(raw_value), raw_value, unit, ref_range

    def _classify(self, value: float, ref_range: ReferenceRange) -> AbnormalFlag:
        if ref_range.lower is not None and value < ref_range.lower:
            return AbnormalFlag.low
        if ref_range.upper is not None and value > ref_range.upper:
            return AbnormalFlag.high
        if ref_range.lower is not None or ref_range.upper is not None:
            return AbnormalFlag.normal
        return AbnormalFlag.unknown

    def _recommendation_lines(self, text: str) -> list[str]:
        skip = {
            "GI Function (Stool)",
            "肠道健康改善建议",
            "饮食结构调整",
            "生活方式调整",
            "相关检测意见",
            "保健品选择",
        }
        lines = []
        for line in text.splitlines():
            cleaned = _clean_text(line)
            if not cleaned or cleaned in skip or "本报告只对" in cleaned or re.fullmatch(r"\d{1,2}", cleaned):
                continue
            if len(cleaned) >= 6:
                lines.append(cleaned)
        return _unique(lines)[:16]

    def _parse_gut_microbiome(
        self,
        filename: str,
        file_id: str,
        layout_pages: dict[int, str],
        plain_pages: dict[int, str],
    ) -> GutMicrobiomeReport:
        score_page, score_text = self._find_best_page(
            plain_pages,
            "肠道菌群健康综合评估",
            "肠道健康评分",
            "本项目利用肠道微生物",
        )
        diversity_page, diversity_text = self._find_best_page(
            plain_pages,
            "肠道菌群多样性",
            "参考范围",
            "多样性指数",
        )
        summary_page, summary_plain = self._find_best_page(
            plain_pages,
            "报告总结",
            "含量偏低",
            "营养素成分存在一定的影响",
            "肠型",
        )
        summary_layout = layout_pages.get(summary_page, summary_plain)

        score_candidates = [
            float(value)
            for value in re.findall(r"(?m)^\s*(\d{1,3}(?:\.\d+)?)\s*$", score_text)
            if 0 <= float(value) <= 100 and float(value) not in {0, 1, 20, 40, 60, 80, 100}
        ]
        health_score = score_candidates[-1] if score_candidates else None

        diversity_range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)", diversity_text)
        diversity_reference = ReferenceRange()
        excluded: set[float] = set()
        if diversity_range_match:
            lower, upper = float(diversity_range_match.group(1)), float(diversity_range_match.group(2))
            diversity_reference = ReferenceRange(lower=lower, upper=upper, raw=diversity_range_match.group(0))
            excluded = {lower, upper}
        diversity_values = [
            float(value)
            for value in re.findall(r"\b\d+\.\d{2,4}\b", diversity_text)
            if float(value) not in excluded
        ]
        diversity_index = diversity_values[-1] if diversity_values else None

        plain_lines = [_clean_text(line) for line in summary_plain.splitlines() if _clean_text(line)]
        count_candidates = [int(value) for value in re.findall(r"(?m)^\s*(\d{2,3})\s*$", summary_plain)]
        detected_genera_count = next((value for value in reversed(count_candidates) if value > 20), None)
        tail_lines = plain_lines[-30:]
        genus_candidates = [line for line in tail_lines if re.fullmatch(r"[A-Za-z\u4e00-\u9fff\-]+菌属", line)]
        dominant_genus = genus_candidates[-1] if genus_candidates else None
        stability = next((value for value in ("较好", "良好", "一般", "较差") if value in tail_lines), None)
        diversity_status = "正常" if "正常" in tail_lines else None
        enterotype = next(
            (line for line in tail_lines if line.endswith("型") and any(token in line for token in ("碳水", "蛋白", "脂肪"))),
            None,
        )

        summary_lists = self._comma_lists(summary_plain)
        harmful = next(
            (
                items
                for items in summary_lists
                if len(items) >= 2 and all(item.endswith("菌属") for item in items)
            ),
            [],
        )
        low_segment = self._between(summary_layout, "含量偏低", "03")
        low_beneficial = self._first_genus_list(low_segment)
        prominent_risks = min(
            (
                items
                for items in summary_lists
                if any(token in "".join(items) for token in ("神经递质", "肝脏解毒"))
            ),
            key=len,
            default=[],
        )
        nutrient_impacts = max(
            (
                items
                for items in summary_lists
                if any(token in "".join(items) for token in ("维生素K", "B族维生素", "甲酸", "短链脂肪酸"))
            ),
            key=len,
            default=[],
        )
        risk_tokens = ("抑郁", "阿尔兹海默", "过敏", "癌", "炎症", "便", "肥胖", "心脑血管", "糖尿病", "多囊", "肌瘤")
        risk_categories = self._flatten_lists(
            items
            for items in summary_lists
            if items not in (harmful, prominent_risks, nutrient_impacts)
            and any(token in "".join(items) for token in risk_tokens)
        )

        recommendations = []
        for pattern in (
            r"维持良好的生活方式[^。]{2,160}。",
            r"日常饮食以高碳水化合物食物为主[^。]{2,260}。",
        ):
            match = re.search(pattern, summary_plain, re.DOTALL)
            if match:
                recommendations.append(_clean_text(match.group(0)))
        recommendations = _unique(recommendations)

        warnings: list[str] = []
        required = {
            "肠道健康评分": health_score,
            "菌群多样性": diversity_index,
            "检测菌属数量": detected_genera_count,
            "优势菌属": dominant_genus,
            "肠型": enterotype,
        }
        missing = [name for name, value in required.items() if value in (None, "")]
        if missing:
            warnings.append(f"肠道菌群关键字段缺失：{'、'.join(missing)}，请人工核对。")
        if not harmful and not low_beneficial:
            warnings.append("肠道菌群异常/不足菌属未完整提取，请人工核对。")
        complete = not warnings

        summary_lines = []
        if health_score is not None:
            summary_lines.append(f"肠道健康评分：{health_score:g}")
        if diversity_index is not None:
            summary_lines.append(f"菌群多样性：{diversity_index:g}（{diversity_status or '待核对'}）")
        if enterotype:
            summary_lines.append(f"肠型：{enterotype}")
        if harmful:
            summary_lines.append(f"可能不利菌属：{'、'.join(harmful)}")
        if low_beneficial:
            summary_lines.append(f"偏低有益菌属：{'、'.join(low_beneficial)}")

        return GutMicrobiomeReport(
            id=f"specialty_gut_microbiome_{file_id}",
            file_id=file_id,
            review_status=SpecialtyReviewStatus.pending_review if complete else SpecialtyReviewStatus.needs_review,
            confidence=0.95 if complete else 0.64,
            source_pages=sorted({score_page, diversity_page, summary_page}),
            needs_manual_review=True,
            warnings=warnings,
            summary_lines=summary_lines,
            recommendations=recommendations,
            health_score=health_score,
            diversity_index=diversity_index,
            diversity_reference=diversity_reference,
            detected_genera_count=detected_genera_count,
            dominant_genus=dominant_genus,
            stability=stability,
            diversity_status=diversity_status,
            enterotype=enterotype,
            harmful_or_elevated_genera=harmful,
            low_beneficial_genera=low_beneficial,
            risk_categories=risk_categories,
            prominent_risks=prominent_risks,
            nutrient_impacts=nutrient_impacts,
            summary_recommendation=" ".join(recommendations) or None,
        )

    def _between(self, text: str, start: str, end: str) -> str:
        start_index = text.find(start)
        if start_index < 0:
            return ""
        end_index = text.find(end, start_index + len(start))
        return text[start_index : end_index if end_index >= 0 else len(text)]

    def _comma_lists(self, text: str) -> list[list[str]]:
        lists: list[list[str]] = []
        for line in text.splitlines():
            cleaned = _clean_text(line)
            if "," not in cleaned and "，" not in cleaned and "、" not in cleaned:
                continue
            if len(cleaned) > 180 or "。" in cleaned:
                continue
            items = _split_items(cleaned)
            if len(items) >= 2:
                lists.append(items)
        return lists

    def _first_comma_list(self, text: str) -> list[str]:
        lists = self._comma_lists(text)
        return lists[0] if lists else []

    def _first_genus_list(self, text: str) -> list[str]:
        for line in text.splitlines():
            cleaned = _clean_text(line)
            if "菌属" not in cleaned or len(cleaned) > 120:
                continue
            items = _split_items(cleaned)
            if items and all(item.endswith("菌属") for item in items):
                return items
        return []

    def _flatten_lists(self, groups) -> list[str]:
        values = [item for group in groups for item in group if item]
        merged: list[str] = []
        index = 0
        while index < len(values):
            if index + 1 < len(values) and values[index] == "便" and values[index + 1] == "秘":
                merged.append("便秘")
                index += 2
                continue
            merged.append(values[index])
            index += 1
        return _unique(merged)

    def _flag_label(self, flag: AbnormalFlag) -> str:
        return {
            AbnormalFlag.high: "偏高",
            AbnormalFlag.low: "偏低",
            AbnormalFlag.normal: "正常",
            AbnormalFlag.unknown: "待核对",
        }[flag]
