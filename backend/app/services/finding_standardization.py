from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from app.domain.models import (
    AbnormalFinding,
    AbnormalFlag,
    ConfirmedClinicalFinding,
    ExtractedLabItem,
    FindingStandardizationStatus,
    ReferenceRange,
    SourceSpan,
)
from app.services.body_systems import AXIS_SYSTEM_MAP, SYSTEM_NAMES, classify_text_to_system_ids


STANDARDIZATION_VERSION = "finding-standardization-v3-exact-identity"

_SUPPORT_FALLBACK_BLOCKED_PATTERNS = (
    "结节",
    "肿块",
    "占位",
    "birads",
    "bi-rads",
    "lungrads",
    "lung-rads",
    "自身抗体",
    "抗体阳性",
    "肿瘤标志物",
    "病理",
    "恶性",
    "癌症",
    "癌",
)


class FindingStandardizationService:
    """Validate model-proposed codes against local dictionaries and structured evidence."""

    def __init__(
        self,
        marker_catalog_path: Path,
        clinical_catalog_path: Path,
        product_tag_matrix_path: Path | None = None,
    ) -> None:
        self.markers = json.loads(marker_catalog_path.read_text(encoding="utf-8-sig"))
        self.clinical_findings = json.loads(clinical_catalog_path.read_text(encoding="utf-8-sig"))
        self.markers_by_code = {item["code"]: item for item in self.markers}
        self.findings_by_code = {item["code"]: item for item in self.clinical_findings}
        self.axis_system_map = dict(AXIS_SYSTEM_MAP)
        if product_tag_matrix_path and product_tag_matrix_path.exists():
            payload = json.loads(product_tag_matrix_path.read_text(encoding="utf-8-sig"))
            configured = payload.get("axis_system_map") if isinstance(payload, dict) else None
            if isinstance(configured, dict):
                self.axis_system_map = {
                    str(axis): tuple(
                        system_id
                        for system_id in systems
                        if system_id in SYSTEM_NAMES
                    )
                    for axis, systems in configured.items()
                    if isinstance(systems, list)
                }

    @property
    def marker_codes(self) -> tuple[str, ...]:
        return tuple(self.markers_by_code)

    @property
    def finding_codes(self) -> tuple[str, ...]:
        return tuple(self.findings_by_code)

    @property
    def system_codes(self) -> tuple[str, ...]:
        return tuple(SYSTEM_NAMES)

    @property
    def support_goal_codes(self) -> tuple[str, ...]:
        return tuple(self.axis_system_map)

    def standardize(self, finding: AbnormalFinding, *, doctor_confirmed: bool = False) -> AbnormalFinding:
        notes: list[str] = []
        marker_code = self._validated_marker_code(finding, notes)
        finding_code = None if marker_code else self._validated_finding_code(finding, notes)
        support_goals = self._validated_support_goals(finding, notes)
        system_ids = self._validated_system_ids(finding, support_goals, notes)

        if marker_code:
            flag = self._validated_marker_flag(finding, notes)
            return finding.model_copy(
                update={
                    "abnormal_flag": flag,
                    "marker_code": marker_code,
                    "finding_code": None,
                    "system_ids": system_ids,
                    "support_goals": support_goals,
                    "standardization_status": FindingStandardizationStatus.validated,
                    "standardization_notes": list(dict.fromkeys(notes or ["已匹配标准检验指标。"])),
                }
            )
        if finding_code:
            return finding.model_copy(
                update={
                    "marker_code": None,
                    "finding_code": finding_code,
                    "system_ids": system_ids,
                    "support_goals": support_goals,
                    "standardization_status": FindingStandardizationStatus.validated,
                    "standardization_notes": list(dict.fromkeys(notes or ["已匹配标准临床发现。"])),
                }
            )

        if support_goals:
            notes.append("未匹配精准代码，已通过营养支持目标白名单和证据校验。")
            return finding.model_copy(
                update={
                    "marker_code": None,
                    "finding_code": None,
                    "system_ids": system_ids,
                    "support_goals": support_goals,
                    "standardization_status": FindingStandardizationStatus.support_mapped,
                    "standardization_notes": list(dict.fromkeys(notes)),
                }
            )

        if system_ids:
            notes.append("未建立营养支持目标，仅保留身体系统归类。")
            return finding.model_copy(
                update={
                    "marker_code": None,
                    "finding_code": None,
                    "system_ids": system_ids,
                    "support_goals": [],
                    "standardization_status": FindingStandardizationStatus.system_mapped,
                    "standardization_notes": list(dict.fromkeys(notes)),
                }
            )

        candidate_present = bool(
            finding.marker_code_candidate
            or finding.finding_code_candidate
            or finding.system_id_candidates
            or finding.support_goal_candidates
        )
        if candidate_present:
            status = FindingStandardizationStatus.rejected
            notes.append("模型候选代码未通过本地字典与证据校验。")
        else:
            status = FindingStandardizationStatus.unmapped
            notes.append("未匹配标准指标代码；该异常保留展示，但不触发精准产品规则。")
        if doctor_confirmed:
            notes.append("医生已确认异常内容，未标准化状态不阻止生成草案。")
        return finding.model_copy(
            update={
                "marker_code": None,
                "finding_code": None,
                "system_ids": [],
                "support_goals": [],
                "standardization_status": status,
                "standardization_notes": list(dict.fromkeys(notes)),
            }
        )

    def to_lab_item(self, finding: AbnormalFinding) -> ExtractedLabItem | None:
        if not finding.marker_code or finding.standardization_status != FindingStandardizationStatus.validated:
            return None
        marker = self.markers_by_code[finding.marker_code]
        raw_value = finding.raw_value or finding.result_text
        value = self._number(raw_value)
        unit = (finding.unit or "").strip() or None
        normalized_value = value
        normalized_unit = marker.get("normalized_unit") or unit
        factor = (marker.get("unit_factors") or {}).get(unit) if unit else None
        if value is not None and factor is not None:
            normalized_value = value * float(factor)
        lower, upper = self._reference_bounds(finding.reference_range)
        return ExtractedLabItem(
            marker_code=finding.marker_code,
            marker_name=marker.get("display_name") or finding.name,
            raw_name=finding.name,
            raw_value=raw_value,
            value=value,
            unit=unit,
            normalized_value=normalized_value,
            normalized_unit=normalized_unit,
            ref_range=ReferenceRange(lower=lower, upper=upper, raw=finding.reference_range),
            abnormal_flag=AbnormalFlag(self._marker_flag(finding.abnormal_flag)),
            confidence=finding.confidence,
            source_span=self._source_span(finding),
        )

    def to_clinical_finding(self, finding: AbnormalFinding) -> ConfirmedClinicalFinding | None:
        if finding.marker_code:
            return None
        if finding.standardization_status not in {
            FindingStandardizationStatus.validated,
            FindingStandardizationStatus.support_mapped,
            FindingStandardizationStatus.system_mapped,
        }:
            return None
        if not finding.finding_code and not finding.system_ids and not finding.support_goals:
            return None
        metadata = self.findings_by_code.get(finding.finding_code or "", {})
        return ConfirmedClinicalFinding(
            finding_id=finding.id,
            finding_code=finding.finding_code,
            finding_name=metadata.get("display_name") or finding.name,
            system_ids=finding.system_ids,
            support_goals=finding.support_goals,
            mapping_confidence=finding.mapping_confidence,
            standardization_status=finding.standardization_status,
            abnormal_flag=finding.abnormal_flag,
            confidence=finding.confidence,
            source_span=self._source_span(finding),
        )

    def _validated_support_goals(self, finding: AbnormalFinding, notes: list[str]) -> list[str]:
        candidates = list(dict.fromkeys(finding.support_goal_candidates))
        invalid = [goal for goal in candidates if goal not in self.axis_system_map]
        if invalid:
            notes.append("模型营养支持目标不在白名单中，已忽略。")
        valid = [goal for goal in candidates if goal in self.axis_system_map]
        if not valid:
            return []
        if self._support_fallback_blocked(finding):
            notes.append("该异常属于临床随访优先类型，禁止通过模糊支持目标触发产品。")
            return []
        notes.append("模型营养支持目标已通过本地白名单校验。")
        return valid

    def _validated_system_ids(
        self,
        finding: AbnormalFinding,
        support_goals: list[str],
        notes: list[str],
    ) -> list[str]:
        candidates = list(dict.fromkeys(finding.system_id_candidates))
        invalid = [system_id for system_id in candidates if system_id not in SYSTEM_NAMES]
        if invalid:
            notes.append("模型身体系统候选不在白名单中，已忽略。")
        system_ids = [system_id for system_id in candidates if system_id in SYSTEM_NAMES]
        if not system_ids:
            for goal in support_goals:
                system_ids.extend(self.axis_system_map.get(goal, ()))
        if not system_ids:
            system_ids.extend(
                classify_text_to_system_ids(
                    finding.name,
                    finding.result_text,
                    finding.interpretation,
                )
            )
        return list(dict.fromkeys(system_ids))

    def _support_fallback_blocked(self, finding: AbnormalFinding) -> bool:
        text = self._normalize(
            " ".join(
                filter(
                    None,
                    [finding.name, finding.result_text, finding.interpretation, finding.source_text],
                )
            )
        )
        return any(self._normalize(pattern) in text for pattern in _SUPPORT_FALLBACK_BLOCKED_PATTERNS)

    def _validated_marker_code(self, finding: AbnormalFinding, notes: list[str]) -> str | None:
        candidate = (finding.marker_code_candidate or finding.marker_code or "").strip()
        if candidate:
            if candidate not in self.markers_by_code:
                notes.append(f"候选检验指标代码不存在：{candidate}。")
            elif self._metadata_matches(self.markers_by_code[candidate], finding) and self._marker_unit_compatible(
                self.markers_by_code[candidate], finding
            ):
                notes.append("模型候选检验指标代码已通过本地校验。")
                return candidate
            else:
                notes.append(f"候选检验指标代码与名称或证据不一致：{candidate}。")
        matches = [
            code
            for code, item in self.markers_by_code.items()
            if self._metadata_matches(item, finding) and self._marker_unit_compatible(item, finding)
        ]
        if len(matches) == 1:
            notes.append("已通过完整标准名或完整别名匹配标准检验指标代码。")
            return matches[0]
        if len(matches) > 1:
            notes.append("检验指标名称存在多个可能映射。")
        return None

    def _validated_finding_code(self, finding: AbnormalFinding, notes: list[str]) -> str | None:
        candidate = (finding.finding_code_candidate or finding.finding_code or "").strip()
        if candidate:
            if candidate not in self.findings_by_code:
                notes.append(f"候选临床发现代码不存在：{candidate}。")
            elif self._metadata_matches(self.findings_by_code[candidate], finding):
                notes.append("模型候选临床发现代码已通过本地校验。")
                return candidate
            else:
                notes.append(f"候选临床发现代码与名称或证据不一致：{candidate}。")
        matches = [code for code, item in self.findings_by_code.items() if self._metadata_matches(item, finding)]
        if len(matches) == 1:
            notes.append("已通过完整标准名或完整别名匹配标准临床发现代码。")
            return matches[0]
        if len(matches) > 1:
            notes.append("临床发现名称存在多个可能映射。")
        return None

    def _validated_marker_flag(self, finding: AbnormalFinding, notes: list[str]) -> str:
        supplied = self._marker_flag(finding.abnormal_flag)
        value = self._number(finding.raw_value or finding.result_text)
        lower, upper = self._reference_bounds(finding.reference_range)
        calculated = None
        if value is not None:
            if lower is not None and value < lower:
                calculated = "low"
            elif upper is not None and value > upper:
                calculated = "high"
        if calculated and supplied not in {"unknown", calculated}:
            notes.append(f"异常方向已按结构化数值和参考范围由 {supplied} 校正为 {calculated}。")
            return calculated
        return calculated or supplied

    def _metadata_matches(self, metadata: dict, finding: AbnormalFinding) -> bool:
        # Indicator identity must come from the finding name itself. Results and
        # narrative interpretation are deliberately excluded: matching aliases as
        # substrings there caused beta-glucuronidase to become glucose and
        # transferrin to become ferritin.
        identity_tokens = self._identity_tokens(finding.name)
        aliases = [metadata.get("display_name", ""), *(metadata.get("synonyms") or [])]
        for alias in aliases:
            normalized_alias = self._normalize(alias)
            if normalized_alias and normalized_alias in identity_tokens:
                return True
        return False

    @staticmethod
    def _marker_unit_compatible(metadata: dict, finding: AbnormalFinding) -> bool:
        if not (finding.unit or "").strip():
            return True
        unit = unicodedata.normalize("NFKC", finding.unit or "").replace("μ", "u").lower()
        allowed = {
            unicodedata.normalize("NFKC", str(value)).replace("μ", "u").lower()
            for value in [
                metadata.get("normalized_unit"),
                *((metadata.get("unit_factors") or {}).keys()),
            ]
            if value
        }
        return not allowed or unit in allowed

    def _identity_tokens(self, value: str) -> set[str]:
        raw = unicodedata.normalize("NFKC", value or "").strip()
        parts = [raw, *re.split(r"[()（）/／|｜:：,，;；\[\]【】]", raw)]
        tokens = {normalized for part in parts if (normalized := self._normalize(part))}
        directional_suffixes = (
            "明显偏高",
            "明显偏低",
            "偏高",
            "偏低",
            "升高",
            "增高",
            "降低",
            "减少",
            "阳性",
            "异常",
        )
        for part in list(tokens):
            for suffix in directional_suffixes:
                normalized_suffix = self._normalize(suffix)
                if part.endswith(normalized_suffix) and len(part) > len(normalized_suffix):
                    tokens.add(part[: -len(normalized_suffix)])
        return tokens

    @staticmethod
    def _marker_flag(value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized in {"low", "below", "down", "偏低", "降低", "减少"}:
            return "low"
        if normalized in {"high", "above", "up", "positive", "偏高", "升高", "增高", "阳性"}:
            return "high"
        if normalized == "normal":
            return "normal"
        return "unknown"

    @staticmethod
    def _number(value: str | None) -> float | None:
        match = re.search(r"-?\d+(?:\.\d+)?", value or "")
        return float(match.group(0)) if match else None

    @staticmethod
    def _reference_bounds(value: str | None) -> tuple[float | None, float | None]:
        numbers = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", value or "")]
        if len(numbers) >= 2:
            return numbers[0], numbers[1]
        return (None, numbers[0]) if numbers and any(token in (value or "") for token in ("<", "≤")) else (None, None)

    @staticmethod
    def _source_span(finding: AbnormalFinding) -> SourceSpan:
        return SourceSpan(
            file_id=finding.source_file_id,
            file_name=finding.source_file_name,
            page=finding.source_page,
            snippet=finding.source_text,
        )

    @staticmethod
    def _normalize(value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "").lower()
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", value)
