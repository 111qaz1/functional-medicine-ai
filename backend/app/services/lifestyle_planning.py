from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.domain.models import (
    LifestyleAction,
    LifestylePlan,
    LifestyleProtocolSelection,
    LifestyleSection,
)


_GENERIC_CONFIRMATION_PATTERNS = (
    re.compile(r"(?:仅)?在(?:医生或营养师|医生|顾问)确认(?:适用)?后(?:再)?[，,]?\s*"),
    re.compile(
        r"(?:执行|实施|开始)前(?:需|需要|请)?(?:先)?(?:由|经|与)?"
        r"(?:医生|顾问)(?:评估|沟通)?确认(?:适用)?[。；;，,]?\s*"
    ),
    re.compile(r"(?:需|需要)?(?:先)?(?:由|经)(?:医生|顾问)确认后(?:再)?执行[。；;，,]?\s*"),
)


def remove_generic_lifestyle_confirmation(value: str) -> str:
    """Remove generic pre-execution approval boilerplate without hiding safety actions."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for pattern in _GENERIC_CONFIRMATION_PATTERNS:
        text = pattern.sub("", text)
    text = re.sub(r"^[；;，,。\s]+", "", text)
    text = re.sub(r"[；;，,]\s*。", "。", text)
    text = re.sub(r"([；;，,。])\1+", r"\1", text)
    text = re.sub(r"[；;，,\s]+$", "", text).strip()
    if text and not re.search(r"[。！？!?]$", text):
        text += "。"
    return text


DOMAIN_TITLES = {
    "diet": "饮食建议",
    "movement": "运动建议",
    "sleep": "睡眠与节律建议",
    "stress": "压力与情绪调节",
}

ADMISSION_SAFETY_LEVEL = {
    "direct": "standard",
    "review": "review",
    "referral": "referral",
}


@dataclass(frozen=True)
class ProtocolMatch:
    protocol: dict[str, Any]
    score: int
    reason: str
    anchor_refs: tuple[str, ...]
    anchor_text: str


class LifestylePlanningService:
    """Deterministic lifestyle protocol selection and patient-facing action assembly."""

    def __init__(self, registry_path: Path | None = None) -> None:
        path = registry_path or Path(__file__).resolve().parents[1] / "data" / "lifestyle_protocol_registry.json"
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        protocols = payload.get("protocols")
        if not isinstance(protocols, list) or len(protocols) != 24:
            raise ValueError("Lifestyle protocol registry must contain LP01-LP24.")
        protocol_ids = [str(item.get("protocol_id") or "") for item in protocols]
        expected_ids = [f"LP{index:02d}" for index in range(1, 25)]
        if protocol_ids != expected_ids:
            raise ValueError("Lifestyle protocol registry IDs must be ordered LP01-LP24.")
        self.version = str(payload.get("version") or "lifestyle-v2")
        self.max_parallel_protocols = int(payload.get("max_parallel_protocols") or 3)
        self.forbidden_output_terms = tuple(
            str(item).strip() for item in payload.get("forbidden_output_terms", []) if str(item).strip()
        )
        self.protocols = protocols
        self.protocols_by_id = {item["protocol_id"]: item for item in protocols}

    def build_plan(self, case: Any, context: Any) -> LifestylePlan:
        evidence = self._evidence(case, context)
        mental_red_flag = self._first_matching_term(
            evidence["normalized_text"],
            ("自杀意念", "自杀想法", "自伤", "躁狂", "精神病性症状", "幻觉", "妄想"),
        )
        if mental_red_flag:
            return self._mental_health_referral_plan(mental_red_flag)

        matches = [
            match
            for protocol in self.protocols
            if (match := self._match_protocol(protocol, evidence, context)) is not None
        ]
        selected = self._select_protocols(matches, context)
        sections_by_domain: dict[str, list[LifestyleAction]] = {
            "diet": [],
            "movement": [],
            "sleep": [],
            "stress": [],
        }
        action_keys_by_domain: dict[str, set[tuple[str, str]]] = {
            domain: set() for domain in sections_by_domain
        }
        selections: list[LifestyleProtocolSelection] = []
        monitoring: list[str] = []
        missing_info: list[str] = []

        for match in selected:
            protocol = match.protocol
            admission = str(protocol.get("admission") or "review")
            protocol_action_added = False
            for domain, raw_actions in (protocol.get("actions") or {}).items():
                if domain not in sections_by_domain or not isinstance(raw_actions, list):
                    continue
                for index, raw_action in enumerate(raw_actions, start=1):
                    action = self._build_action(
                        protocol=protocol,
                        raw_action=raw_action,
                        domain=domain,
                        index=index,
                        match=match,
                        context=context,
                        case=case,
                    )
                    if action is None:
                        continue
                    action_key = (match.anchor_text, action.text)
                    if action_key in action_keys_by_domain[domain]:
                        continue
                    sections_by_domain[domain].append(action)
                    action_keys_by_domain[domain].add(action_key)
                    protocol_action_added = True
            if protocol_action_added:
                selections.append(
                    LifestyleProtocolSelection(
                        protocol_id=protocol["protocol_id"],
                        title=protocol["title"],
                        admission=admission,
                        reason=match.reason,
                        anchor_refs=list(match.anchor_refs),
                    )
                )
                monitoring.extend(
                    str(item).strip()
                    for item in protocol.get("monitoring", [])
                    if str(item).strip()
                )

        self._add_bmi_diet_support(sections_by_domain, case, context)
        self._add_movement_intensity_support(sections_by_domain, case, context)
        self._add_osa_priority_action(sections_by_domain, evidence)
        self._dedupe_sections(sections_by_domain)
        self._cap_sections(sections_by_domain)

        if not selections and not any(sections_by_domain.values()):
            missing_info.append("现有资料未命中可安全执行的生活方式协议，不生成通用模板。")
        if "sleep_recovery" not in context.lifestyle_tags and not self._has_sleep_terms(evidence):
            sections_by_domain["sleep"] = []
        if "stress_support" not in context.lifestyle_tags and not self._has_stress_terms(evidence):
            sections_by_domain["stress"] = []

        sections = [
            LifestyleSection(domain=domain, title=DOMAIN_TITLES[domain], actions=actions)
            for domain, actions in sections_by_domain.items()
            if actions
        ]
        action_requires_review = any(
            action.clinician_review_required for section in sections for action in section.actions
        )
        requires_review = any(item.admission != "direct" for item in selections) or action_requires_review
        referral_selected = any(item.admission == "referral" for item in selections)
        if requires_review or referral_selected:
            status = "needs_review"
        elif sections:
            status = "ready"
        else:
            status = "partial"
        return LifestylePlan(
            status=status,
            rule_version=self.version,
            selected_protocols=selections,
            sections=sections,
            monitoring=list(dict.fromkeys(monitoring))[:6],
            missing_info=missing_info,
            clinician_review_required=requires_review or referral_selected,
        )

    @classmethod
    def report_items(cls, plan: LifestylePlan) -> list[str]:
        items: list[str] = []
        section_number = 1
        selections = {item.protocol_id: item for item in plan.selected_protocols}
        displayed_basis_terms: list[str] = []
        for section in plan.sections:
            items.append(f"### {section_number}. {section.title}")
            action_groups: dict[
                tuple[tuple[str, ...], str],
                tuple[str, str, list[LifestyleAction]],
            ] = {}
            primary_group_by_refs: dict[
                tuple[str, ...],
                tuple[tuple[tuple[str, ...], str], str, str],
            ] = {}
            for action in section.actions:
                protocol_id = action.action_id.split("_", 1)[0]
                selection = selections.get(protocol_id)
                if selection is None:
                    continue
                title, basis = cls._problem_group_meta(action, selection)
                refs = tuple(sorted(action.anchor_refs))
                group_key = (refs, cls._normalize(basis))
                primary_group_by_refs.setdefault(refs, (group_key, title, basis))
            for action in section.actions:
                protocol_id = action.action_id.split("_", 1)[0]
                selection = selections.get(protocol_id)
                title, basis = cls._problem_group_meta(action, selection)
                refs = tuple(sorted(action.anchor_refs))
                if selection is not None:
                    group_key = (refs, cls._normalize(basis))
                else:
                    primary_group = primary_group_by_refs.get(refs)
                    if primary_group is not None:
                        group_key, title, basis = primary_group
                    else:
                        group_key = (refs or (protocol_id,), cls._normalize(basis))
                if group_key not in action_groups:
                    action_groups[group_key] = (title, basis, [])
                action_groups[group_key][2].append(action)

            for group_number, (_, (title, basis, actions)) in enumerate(action_groups.items(), start=1):
                visible_basis, new_terms = cls._unique_visible_basis(basis, displayed_basis_terms)
                if visible_basis:
                    items.append(f"### {section_number}.{group_number} {title}（依据：{visible_basis}）")
                    displayed_basis_terms.extend(new_terms)
                else:
                    items.append(f"### {section_number}.{group_number} {title}")
                for action in actions:
                    text = remove_generic_lifestyle_confirmation(action.text)
                    if text:
                        label = cls._category_label(section.domain, action.category)
                        items.append(f"{label}：{text}" if label else text)
            section_number += 1
        if plan.monitoring:
            items.append(f"### {section_number}. 监测与复查")
            items.extend(plan.monitoring)
        return items

    @classmethod
    def _unique_visible_basis(cls, basis: str, displayed_terms: list[str]) -> tuple[str, list[str]]:
        raw_terms = [
            item.strip()
            for item in re.split(r"[、；;]+", basis)
            if item.strip()
        ]
        visible: list[str] = []
        new_normalized: list[str] = []
        for term in raw_terms:
            normalized = cls._normalize(term)
            if not normalized:
                continue
            if any(
                normalized == existing
                or normalized in existing
                or existing in normalized
                for existing in displayed_terms
            ):
                continue
            visible.append(term)
            new_normalized.append(normalized)
        return "、".join(visible), new_normalized

    def legacy_actions(self, plan: LifestylePlan) -> list[str]:
        return [action.text for section in plan.sections for action in section.actions][:18]

    def _evidence(self, case: Any, context: Any) -> dict[str, Any]:
        questionnaire = case.questionnaire
        original_terms: list[tuple[str, str]] = []
        if questionnaire:
            field_values = {
                "chief_concerns": questionnaire.chief_concerns,
                "symptoms": questionnaire.symptoms,
                "known_conditions": questionnaire.known_conditions,
                "goals": questionnaire.goals,
                "emotional_state": questionnaire.emotional_state,
                "work_pattern": [questionnaire.work_pattern] if questionnaire.work_pattern else [],
                "chemical_sensitivity": [questionnaire.chemical_sensitivity] if questionnaire.chemical_sensitivity else [],
                "diet_pattern": [questionnaire.diet_pattern] if questionnaire.diet_pattern else [],
                "exercise_frequency": [questionnaire.exercise_frequency] if questionnaire.exercise_frequency else [],
                "bowel_habits": [questionnaire.bowel_habits] if questionnaire.bowel_habits else [],
                "dining_out_frequency": [questionnaire.dining_out_frequency] if questionnaire.dining_out_frequency else [],
                "food_sensitivities": questionnaire.food_sensitivities,
                "additional_notes": [questionnaire.additional_notes] if questionnaire.additional_notes else [],
            }
            for field_name, values in field_values.items():
                for value in values or []:
                    cleaned = str(value).strip()
                    if cleaned:
                        original_terms.append((f"questionnaire:{field_name}", cleaned))
        for finding in context.clinical_findings:
            if finding.finding_name:
                original_terms.append((f"finding:{finding.finding_id}", finding.finding_name))
        for food in sorted(getattr(context, "food_sensitivities", set()) or set()):
            if str(food).strip():
                original_terms.append(("case:food_sensitivities", str(food).strip()))

        markers: dict[str, list[Any]] = context.markers_by_code
        normalized_text = self._normalize(" ".join(value for _, value in original_terms))
        return {
            "markers": markers,
            "finding_codes": context.clinical_findings_by_code,
            "original_terms": original_terms,
            "normalized_text": normalized_text,
            "age": context.age,
            "pregnancy": context.pregnancy,
            "underweight": self._has_marker(context, "bmi", "low") or "underweight" in context.clinical_findings_by_code,
        }

    def _match_protocol(self, protocol: dict[str, Any], evidence: dict[str, Any], context: Any) -> ProtocolMatch | None:
        if not self._evidence_gate_satisfied(protocol, evidence):
            return None
        trigger = protocol.get("triggers") or {}
        refs: list[str] = []
        anchor_texts: list[str] = []
        match_count = 0

        for condition in trigger.get("markers", []):
            marker_code, _, flag = str(condition).partition(":")
            observations = evidence["markers"].get(marker_code, [])
            matched = [
                item
                for item in observations
                if not flag or getattr(getattr(item, "abnormal_flag", None), "value", "") == flag
            ]
            if matched:
                match_count += 1
                item = matched[0]
                refs.append(f"marker:{marker_code}")
                direction = "偏高" if flag == "high" else "偏低" if flag == "low" else "异常"
                anchor_texts.append(f"{item.marker_name}{direction}")

        for finding_code in trigger.get("findings", []):
            findings = evidence["finding_codes"].get(str(finding_code), [])
            if findings:
                match_count += 1
                finding = findings[0]
                refs.append(f"finding:{finding.finding_id}")
                anchor_texts.append(finding.finding_name)

        for term in trigger.get("terms", []):
            normalized_term = self._normalize(str(term))
            if normalized_term and normalized_term in evidence["normalized_text"]:
                matched_source = next(
                    (
                        (source_ref, value)
                        for source_ref, value in evidence["original_terms"]
                        if normalized_term in self._normalize(value)
                        and not self._is_non_evidence_protocol_heading(
                            str(protocol.get("protocol_id") or ""),
                            value,
                        )
                    ),
                    None,
                )
                if matched_source:
                    match_count += 1
                    refs.append(matched_source[0])
                    anchor_texts.append(matched_source[1])

        for tag in trigger.get("lifestyle_tags", []):
            if tag in context.lifestyle_tags:
                match_count += 1
                if tag == "energy_support" and any(
                    self._is_specific_fatigue_anchor(text) for text in anchor_texts
                ):
                    continue
                ref, text = self._lifestyle_tag_anchor(tag, context)
                refs.append(ref)
                anchor_texts.append(text)

        if protocol.get("protocol_id") == "LP02" and getattr(context, "food_sensitivities", set()):
            foods = sorted(str(item).strip() for item in context.food_sensitivities if str(item).strip())
            match_count += 1
            refs.append("case:food_sensitivities")
            anchor_texts.append(f"已记录的食物敏感项目（{'、'.join(foods[:5])}）")

        if match_count < int(protocol.get("min_trigger_matches") or 1):
            return None
        contraindication = self._matched_contraindication(protocol, evidence)
        if contraindication:
            return None
        admission = str(protocol.get("admission") or "review")
        score = int(protocol.get("priority") or 0) + match_count * 12
        if admission == "referral":
            score += 20
        refs = list(dict.fromkeys(refs))
        anchor_texts = list(dict.fromkeys(anchor_texts))
        anchor_text = "、".join(anchor_texts[:2]) or "已确认的生活方式问题"
        return ProtocolMatch(
            protocol=protocol,
            score=score,
            reason=f"依据{anchor_text}命中",
            anchor_refs=tuple(refs[:4]),
            anchor_text=anchor_text,
        )

    @staticmethod
    def _evidence_gate_satisfied(protocol: dict[str, Any], evidence: dict[str, Any]) -> bool:
        gate = str(protocol.get("evidence_gate") or "").strip()
        if not gate:
            return True
        if gate == "confirmed_heavy_metal":
            marker_codes = {
                "blood_lead",
                "urine_lead",
                "blood_mercury",
                "urine_mercury",
                "blood_arsenic",
                "urine_arsenic",
            }
            return any(
                getattr(getattr(item, "abnormal_flag", None), "value", "") == "high"
                for marker_code in marker_codes
                for item in evidence["markers"].get(marker_code, [])
            )
        return False

    def _select_protocols(self, matches: list[ProtocolMatch], context: Any) -> list[ProtocolMatch]:
        family_matches: dict[str, ProtocolMatch] = {}
        independent_matches: list[ProtocolMatch] = []
        for match in matches:
            family = str(match.protocol.get("selection_family") or "").strip()
            if not family:
                independent_matches.append(match)
                continue
            existing = family_matches.get(family)
            candidate_rank = (
                int(match.protocol.get("family_priority") or 0),
                match.score,
            )
            existing_rank = (
                int(existing.protocol.get("family_priority") or 0),
                existing.score,
            ) if existing is not None else (-1, -1)
            if existing is None or candidate_rank > existing_rank:
                family_matches[family] = match

        ranked = sorted(
            [*independent_matches, *family_matches.values()],
            key=lambda item: (-item.score, item.protocol["protocol_id"]),
        )
        selected: list[ProtocolMatch] = []

        def reserve_protocols(protocol_ids: tuple[str, ...]) -> None:
            for protocol_id in protocol_ids:
                match = next((item for item in ranked if item.protocol["protocol_id"] == protocol_id), None)
                if match and match not in selected and len(selected) < self.max_parallel_protocols:
                    selected.append(match)
                    return

        def reserve_domain(domain: str) -> None:
            match = next(
                (
                    item
                    for item in ranked
                    if domain in (item.protocol.get("actions") or {})
                    and (item.protocol.get("actions") or {}).get(domain)
                ),
                None,
            )
            if match and match not in selected and len(selected) < self.max_parallel_protocols:
                selected.append(match)

        # A matched diet or movement rule must survive the three-protocol cap.
        reserve_domain("diet")
        reserve_domain("movement")
        # Explicit fatigue/energy evidence must not be displaced by a duplicate
        # stress protocol after the three-protocol cap is applied.
        reserve_protocols(("LP09",))
        if "sleep_recovery" in context.lifestyle_tags:
            reserve_protocols(("LP06", "LP05"))
        if "stress_support" in context.lifestyle_tags:
            reserve_protocols(("LP24", "LP20", "LP05"))
        for match in ranked:
            if len(selected) >= self.max_parallel_protocols:
                break
            if match not in selected:
                selected.append(match)
        return selected

    def _build_action(
        self,
        *,
        protocol: dict[str, Any],
        raw_action: dict[str, Any],
        domain: str,
        index: int,
        match: ProtocolMatch,
        context: Any,
        case: Any,
    ) -> LifestyleAction | None:
        text = str(raw_action.get("text") or "").strip()
        quantity = str(raw_action.get("quantity") or "").strip()
        if not text or not quantity:
            return None
        if protocol.get("protocol_id") == "LP02" and raw_action.get("category") == "limit":
            foods = sorted(str(item).strip() for item in context.food_sensitivities if str(item).strip())
            if foods:
                text = (
                    "仅对上述已有记录支持的食物做2–4周试验性回避，"
                    "不依据食物IgG结果直接诊断过敏。"
                )
        context_text = self._case_context_text(case, context)
        if raw_action.get("blocked_during_pregnancy") and context.pregnancy:
            return None
        if any(
            self._normalize(str(term)) in context_text
            for term in raw_action.get("blocked_by_terms", [])
            if self._normalize(str(term))
        ):
            return None
        if protocol.get("protocol_id") == "LP18" and self._has_acute_heavy_metal_poisoning(case, context):
            if domain == "movement" or raw_action.get("omit_in_acute_heavy_metal"):
                return None
        if not self._action_dependencies_available(raw_action, case=case, context=context):
            return None
        safety_text = f"针对您的{match.anchor_text}，{text}"
        if not self._action_is_safe(safety_text, domain=domain, context=context, case=case):
            return None
        admission = str(protocol.get("admission") or "review")
        action_requires_review = (
            admission != "direct"
            or context.pregnancy
            or (context.age is not None and context.age < 18)
            or (
                domain == "movement"
                and (
                    protocol.get("protocol_id") == "LP13"
                    or self._has_cardiovascular_risk(case, context)
                )
            )
        )
        return LifestyleAction(
            action_id=f"{protocol['protocol_id']}_{domain}_{index}",
            domain=domain,
            category=str(raw_action.get("category") or "execution"),
            text=text,
            anchor_refs=list(match.anchor_refs),
            quantity=quantity,
            safety_level=(
                "review"
                if action_requires_review and admission == "direct"
                else ADMISSION_SAFETY_LEVEL.get(admission, "review")
            ),
            clinician_review_required=action_requires_review,
        )

    def _action_dependencies_available(self, raw_action: dict[str, Any], *, case: Any, context: Any) -> bool:
        required = {str(item) for item in raw_action.get("requires", []) if str(item)}
        text = str(raw_action.get("text") or "")
        if any(term in text for term in ("高蛋白", "蛋白质目标", "每千克体重", "富钾")):
            required.add("known_conditions")
        if "抗凝" in text:
            required.add("medications")
        if any(term in text for term in ("中等强度", "高强度", "目标心率", "Zone 2", "zone 2")):
            required.update(("age", "known_conditions"))
        if any(term in text for term in ("禁食", "生酮", "桑拿", "排毒", "酒精", "减重", "热量缺口")):
            required.add("pregnancy_status")
        if not required:
            return True
        questionnaire = getattr(case, "questionnaire", None)
        unresolved = set(getattr(context, "unresolved_questionnaire_fields", set()) or set())
        checks = {
            "age": context.age is not None and "age" not in unresolved,
            "bmi": bool(context.markers_by_code.get("bmi")),
            "exercise_baseline": bool(
                questionnaire
                and questionnaire.exercise_frequency
                and "exercise_frequency" not in unresolved
            ),
            "pregnancy_status": bool(
                getattr(context, "sex", None) == "male"
                or (
                    questionnaire
                    and questionnaire.pregnant_or_lactating is not None
                    and "pregnant_or_lactating" not in unresolved
                )
            ),
            "medications": bool(questionnaire and "medications" not in unresolved),
            "known_conditions": bool(questionnaire and "known_conditions" not in unresolved),
        }
        return all(checks.get(item, False) for item in required)

    @staticmethod
    def _support_action(
        action_id: str,
        domain: str,
        category: str,
        text: str,
        quantity: str,
        anchor_refs: list[str],
        *,
        safety_level: str = "standard",
    ) -> LifestyleAction:
        return LifestyleAction(
            action_id=action_id,
            domain=domain,
            category=category,
            text=text,
            anchor_refs=anchor_refs,
            quantity=quantity,
            safety_level=safety_level,
            clinician_review_required=safety_level != "standard",
        )

    @staticmethod
    def _problem_group_meta(
        action: LifestyleAction,
        selection: LifestyleProtocolSelection | None,
    ) -> tuple[str, str]:
        if selection is not None:
            basis = re.sub(r"^依据|命中$", "", selection.reason).strip("，,。 ")
            return selection.title, basis
        action_id = action.action_id
        support_groups = (
            ("SUPPORT-BMI-LOW", "增重方向饮食调整", "BMI偏低"),
            ("SUPPORT-BMI-HIGH", "减重方向饮食调整", "BMI偏高"),
            ("SUPPORT-MOVEMENT-PREGNANCY", "孕期运动分级", "孕期或哺乳状态"),
            ("SUPPORT-MOVEMENT-YOUTH", "青少年运动分级", "年龄信息"),
            ("SUPPORT-MOVEMENT-OLDER", "老年运动分级", "年龄信息"),
            ("SUPPORT-MOVEMENT-CARDIAC", "心脏康复级运动安排", "心血管相关情况"),
            ("SUPPORT-MOVEMENT-ASSESSMENT", "运动起步评估", "年龄信息"),
            ("SUPPORT-MOVEMENT-ADULT", "常规成人运动安排", "年龄和当前运动基线"),
            ("OSAHS", "睡眠呼吸暂停优先处理", "睡眠呼吸暂停相关问题"),
        )
        for prefix, title, basis in support_groups:
            if action_id.startswith(prefix):
                return title, basis
        return DOMAIN_TITLES.get(action.domain, "生活方式调整"), ""

    def _dedupe_sections(self, sections: dict[str, list[LifestyleAction]]) -> None:
        for domain, actions in sections.items():
            unique: list[LifestyleAction] = []
            normalized: list[str] = []
            for action in actions:
                candidate = self._normalize(action.text)
                duplicate_index = next(
                    (
                        index
                        for index, existing in enumerate(normalized)
                        if candidate == existing
                        or SequenceMatcher(None, candidate, existing).ratio() >= 0.86
                    ),
                    None,
                )
                if duplicate_index is not None:
                    retained = unique[duplicate_index]
                    retained.anchor_refs = list(dict.fromkeys([*retained.anchor_refs, *action.anchor_refs]))
                    retained.clinician_review_required = (
                        retained.clinician_review_required or action.clinician_review_required
                    )
                    safety_rank = {"standard": 0, "review": 1, "referral": 2}
                    if safety_rank[action.safety_level] > safety_rank[retained.safety_level]:
                        retained.safety_level = action.safety_level
                    continue
                unique.append(action)
                normalized.append(candidate)
            sections[domain] = unique

    def _has_renal_risk(self, case: Any, context: Any) -> bool:
        text = self._normalize(
            " ".join(
                [
                    *(item.finding_name for item in context.clinical_findings),
                    *((case.questionnaire.known_conditions or []) if case.questionnaire else []),
                ]
            )
        )
        if any(term in text for term in ("肾功能不全", "慢性肾病", "肾衰", "透析", "高钾血症")):
            return True
        return any(
            self._has_marker(context, code, flag)
            for code, flag in (("egfr", "low"), ("creatinine", "high"), ("potassium", "high"))
        )

    @staticmethod
    def _renal_status_known(case: Any, context: Any) -> bool:
        questionnaire = getattr(case, "questionnaire", None)
        unresolved = set(getattr(context, "unresolved_questionnaire_fields", set()) or set())
        return bool(questionnaire and "known_conditions" not in unresolved)

    def _has_movement_red_flag(self, case: Any, context: Any) -> bool:
        text = self._normalize(
            " ".join(
                [
                    *(item.finding_name for item in context.clinical_findings),
                    *((case.questionnaire.known_conditions or []) if case.questionnaire else []),
                ]
            )
        )
        return any(
            term in text
            for term in (
                "急性心肌梗死",
                "近期心肌梗死",
                "急性深静脉血栓",
                "严重主动脉狭窄",
                "活动性出血",
                "未控制高血压",
            )
        )

    def _case_context_text(self, case: Any, context: Any) -> str:
        questionnaire = getattr(case, "questionnaire", None)
        values = [item.finding_name for item in getattr(context, "clinical_findings", [])]
        if questionnaire:
            values.extend(questionnaire.known_conditions or [])
            values.extend(questionnaire.symptoms or [])
            values.extend(questionnaire.chief_concerns or [])
            if questionnaire.additional_notes:
                values.append(questionnaire.additional_notes)
        return self._normalize(" ".join(str(item) for item in values if str(item).strip()))

    def _has_acute_heavy_metal_poisoning(self, case: Any, context: Any) -> bool:
        text = self._case_context_text(case, context)
        return any(
            self._normalize(term) in text
            for term in ("急性重金属中毒", "急性铅中毒", "急性汞中毒", "急性砷中毒")
        )

    def _action_is_safe(self, text: str, *, domain: str, context: Any, case: Any) -> bool:
        if any(term.lower() in text.lower() for term in self.forbidden_output_terms):
            return False
        age = context.age
        if age is not None and age < 18 and any(term in text for term in ("减重", "减脂", "成人", "酒精", "烟草")):
            return False
        if context.pregnancy and any(term in text for term in ("禁食", "生酮", "桑拿", "排毒")):
            return False
        if self._has_marker(context, "bmi", "low") and any(term in text for term in ("减重", "热量缺口", "限时进食")):
            return False
        if domain == "movement" and self._has_cardiovascular_risk(case, context):
            if any(term in text for term in ("中等强度", "高强度", "目标心率")) and not any(
                term in text for term in ("医生", "评估", "心脏康复")
            ):
                return False
        return True

    def _add_bmi_diet_support(
        self,
        sections: dict[str, list[LifestyleAction]],
        case: Any,
        context: Any,
    ) -> None:
        if context.age is not None and context.age < 18:
            return
        questionnaire = getattr(case, "questionnaire", None)
        unresolved = set(getattr(context, "unresolved_questionnaire_fields", set()) or set())
        pregnancy_status_known = bool(
            getattr(context, "sex", None) == "male"
            or (
                questionnaire
                and questionnaire.pregnant_or_lactating is not None
                and "pregnant_or_lactating" not in unresolved
            )
        )
        if not pregnancy_status_known:
            return
        diet = sections["diet"]
        renal_safe = self._renal_status_known(case, context) and not self._has_renal_risk(case, context)
        if self._has_marker(context, "bmi", "low"):
            diet.extend(
                [
                    self._support_action(
                        "SUPPORT-BMI-LOW_diet_1", "diet", "recommend",
                        "每日在当前饮食基础上增加300–500千卡，并使用3餐加2–3次加餐的结构逐步增加摄入。",
                        "增加300–500千卡/日；加餐2–3次/日", ["marker:bmi"],
                    ),
                    self._support_action(
                        "SUPPORT-BMI-LOW_diet_2", "diet", "execution",
                        "每周在同一时间记录1次体重，连续4周观察趋势，避免使用任何减脂或低热量方案。",
                        "1次/周；连续4周", ["marker:bmi"],
                    ),
                ]
            )
            if renal_safe:
                diet.append(
                    self._support_action(
                        "SUPPORT-BMI-LOW_diet_3", "diet", "recommend",
                        "蛋白质目标按每日每千克体重1.2–1.5克安排，并分散到三餐和加餐。",
                        "1.2–1.5克/千克/日", ["marker:bmi"],
                    )
                )
        elif self._has_marker(context, "bmi", "high"):
            diet.extend(
                [
                    self._support_action(
                        "SUPPORT-BMI-HIGH_diet_1", "diet", "limit",
                        "每日在当前摄入基础上形成300–500千卡的温和热量缺口，不采用低于1200千卡的极端节食。",
                        "缺口300–500千卡/日；不低于1200千卡/日", ["marker:bmi"],
                    ),
                    self._support_action(
                        "SUPPORT-BMI-HIGH_diet_2", "diet", "execution",
                        "每周在同一时间记录1次体重，减重速度控制在每周0.5–1千克，连续观察4周。",
                        "0.5–1千克/周；连续4周", ["marker:bmi"],
                    ),
                ]
            )
            if renal_safe:
                diet.append(
                    self._support_action(
                        "SUPPORT-BMI-HIGH_diet_3", "diet", "recommend",
                        "蛋白质目标按每日每千克体重1.0–1.2克安排，用于减重期间保留肌肉量。",
                        "1.0–1.2克/千克/日", ["marker:bmi"],
                    )
                )

    def _add_movement_intensity_support(
        self,
        sections: dict[str, list[LifestyleAction]],
        case: Any,
        context: Any,
    ) -> None:
        questionnaire = getattr(case, "questionnaire", None)
        age = context.age
        unresolved = set(getattr(context, "unresolved_questionnaire_fields", set()) or set())
        if self._has_movement_red_flag(case, context):
            sections["movement"] = []
            return
        if context.pregnancy:
            sections["movement"].extend(
                [
                    self._support_action(
                        "SUPPORT-MOVEMENT-PREGNANCY_movement_1", "movement", "principle",
                        "运动开始前先完成1次产科确认，强度使用谈话测试或RPE评估，不采用统一目标心率。",
                        "产科确认1次", ["questionnaire:pregnant_or_lactating"], safety_level="review",
                    ),
                    self._support_action(
                        "SUPPORT-MOVEMENT-PREGNANCY_movement_2", "movement", "plan",
                        "优先选择步行、游泳或孕期瑜伽，每周根据耐受安排3–5次。",
                        "3–5次/周", ["questionnaire:pregnant_or_lactating"], safety_level="review",
                    ),
                    self._support_action(
                        "SUPPORT-MOVEMENT-PREGNANCY_movement_3", "movement", "safety",
                        "避免长时间仰卧位和有跌倒风险的动作；出现出血、腹痛、胸闷或头晕时立即停止并联系产科。",
                        "出现症状立即停止", ["questionnaire:pregnant_or_lactating"], safety_level="review",
                    ),
                ]
            )
            return
        movement_anchor_refs = next(
            (
                list(action.anchor_refs)
                for action in sections["movement"]
                if action.anchor_refs
            ),
            [],
        )
        if not movement_anchor_refs:
            return
        if age is None or "age" in unresolved:
            self._merge_movement_assessment(sections, movement_anchor_refs)
            return
        if age < 18:
            sections["movement"].extend(
                [
                    self._support_action(
                        "SUPPORT-MOVEMENT-YOUTH_movement_1", "movement", "principle",
                        "运动以生长发育、兴趣和家庭参与为前提，可从每日累计20–30分钟开始逐步增加。",
                        "20–30分钟/日起步", movement_anchor_refs, safety_level="review",
                    ),
                    self._support_action(
                        "SUPPORT-MOVEMENT-YOUTH_movement_2", "movement", "plan",
                        "逐步接近每日累计60分钟中高强度活动，优先选择球类、游泳、骑行等喜欢的项目。",
                        "目标60分钟/日", movement_anchor_refs, safety_level="review",
                    ),
                    self._support_action(
                        "SUPPORT-MOVEMENT-YOUTH_movement_3", "movement", "safety",
                        "每日娱乐性屏幕时间控制在2小时以内，出现明显疼痛、胸闷或头晕时停止活动。",
                        "屏幕时间<2小时/日", movement_anchor_refs, safety_level="review",
                    ),
                ]
            )
            return
        if age >= 65:
            sections["movement"].extend(
                [
                    self._support_action(
                        "SUPPORT-MOVEMENT-OLDER_movement_1", "movement", "plan",
                        "每周安排2–3次弹力带或自重抗阻训练，覆盖主要肌群。",
                        "2–3次/周", movement_anchor_refs,
                    ),
                    self._support_action(
                        "SUPPORT-MOVEMENT-OLDER_movement_2", "movement", "plan",
                        "每日安排5–10分钟扶椅单脚站立、脚跟脚尖行走等平衡练习。",
                        "5–10分钟/日", movement_anchor_refs,
                    ),
                    self._support_action(
                        "SUPPORT-MOVEMENT-OLDER_movement_3", "movement", "safety",
                        "开始计划前完成1次跌倒风险评估；出现胸痛、明显气促、头晕或急性关节痛时立即停止。",
                        "评估1次；出现症状立即停止", movement_anchor_refs,
                    ),
                ]
            )
            return

        conditions_known = bool(questionnaire and "known_conditions" not in unresolved)
        pregnancy_status_known = bool(
            getattr(context, "sex", None) == "male"
            or (
                questionnaire
                and questionnaire.pregnant_or_lactating is not None
                and "pregnant_or_lactating" not in unresolved
            )
        )
        exercise_baseline = (
            questionnaire.exercise_frequency
            if questionnaire and "exercise_frequency" not in unresolved
            else None
        )
        if not conditions_known or not pregnancy_status_known or not exercise_baseline:
            self._merge_movement_assessment(sections, movement_anchor_refs)
            return

        if self._has_cardiovascular_risk(case, context):
            sections["movement"].extend(
                [
                    self._support_action(
                        "SUPPORT-MOVEMENT-CARDIAC_movement_1", "movement", "principle",
                        "经医生许可或心脏康复评估后确定运动强度，使用RPE 11–13或医生给出的范围。",
                        "RPE 11–13", movement_anchor_refs, safety_level="review",
                    ),
                    self._support_action(
                        "SUPPORT-MOVEMENT-CARDIAC_movement_2", "movement", "plan",
                        "评估允许后从每日5–10分钟步行开始，每周总量增加不超过10%。",
                        "5–10分钟/日；增量≤10%/周", movement_anchor_refs, safety_level="review",
                    ),
                    self._support_action(
                        "SUPPORT-MOVEMENT-CARDIAC_movement_3", "movement", "safety",
                        "出现胸痛、胸闷、心悸、头晕或气促不缓解时立即停止并及时就医。",
                        "出现症状立即停止", movement_anchor_refs, safety_level="review",
                    ),
                ]
            )
            return

        normalized_baseline = self._normalize(exercise_baseline)
        sedentary = normalized_baseline in {"rare", "none", "很少", "无", "无规律运动"}
        if sedentary:
            sections["movement"].append(
                self._support_action(
                    "SUPPORT-MOVEMENT-ADULT_movement_1", "movement", "plan",
                    "第1–2周从每日15–20分钟步行开始，每周总量增加不超过10%。",
                    "15–20分钟/日；增量≤10%/周", movement_anchor_refs,
                )
            )
        sections["movement"].extend(
            [
                self._support_action(
                    "SUPPORT-MOVEMENT-ADULT_movement_2", "movement", "plan",
                    "逐步达到每周150分钟中等强度有氧活动，可拆分为每次30分钟、每周5次。",
                    "150分钟/周；30分钟×5次", movement_anchor_refs,
                ),
                self._support_action(
                    "SUPPORT-MOVEMENT-ADULT_movement_3", "movement", "plan",
                    "每周安排2次覆盖主要肌群的抗阻训练，两次之间至少间隔1天。",
                    "2次/周；间隔≥1天", movement_anchor_refs,
                ),
                self._support_action(
                    "SUPPORT-MOVEMENT-ADULT_movement_4", "movement", "safety",
                    "出现胸痛、胸闷、明显气促、头晕或急性关节疼痛时立即停止并联系医生。",
                    "出现症状立即停止", movement_anchor_refs,
                ),
            ]
        )

    def _merge_movement_assessment(
        self,
        sections: dict[str, list[LifestyleAction]],
        movement_anchor_refs: list[str],
    ) -> None:
        principle = next(
            (
                action
                for action in sections["movement"]
                if action.category == "principle"
                and action.anchor_refs == movement_anchor_refs
            ),
            None,
        )
        assessment_text = (
            "开始增加运动量前完成1次PAR-Q+或等效功能筛查，"
            "并记录当前可耐受的活动类型和时长"
        )
        if principle is not None:
            if "PAR-Q+" not in principle.text:
                principle.text = f"{principle.text.rstrip('。')}；{assessment_text}。"
                principle.quantity = f"{principle.quantity or ''}；筛查1次".strip("；")
            return
        sections["movement"].insert(
            0,
            self._support_action(
                "SUPPORT-MOVEMENT-ASSESSMENT_movement_1", "movement", "principle",
                f"{assessment_text}。",
                "筛查1次", movement_anchor_refs,
            ),
        )

    def _add_osa_priority_action(self, sections: dict[str, list[LifestyleAction]], evidence: dict[str, Any]) -> None:
        if not self._first_matching_term(evidence["normalized_text"], ("OSAHS", "睡眠呼吸暂停")):
            return
        refs = [
            source_ref
            for source_ref, value in evidence["original_terms"]
            if any(term.lower() in value.lower() for term in ("osahs", "睡眠呼吸暂停"))
        ][:2]
        sections["sleep"].insert(
            0,
            LifestyleAction(
                action_id="OSAHS_sleep_priority",
                domain="sleep",
                category="safety",
                text=(
                    "针对已记录的睡眠呼吸暂停相关问题，应优先完成睡眠专科评估；"
                    "如已开具CPAP，请每晚按医嘱使用并记录佩戴时长，生活方式建议不能替代治疗。"
                ),
                anchor_refs=refs or ["questionnaire:known_conditions"],
                quantity="每晚按医嘱使用并记录",
                safety_level="review",
                clinician_review_required=True,
            ),
        )

    def _mental_health_referral_plan(self, red_flag: str) -> LifestylePlan:
        action = LifestyleAction(
            action_id="LP24_stress_referral",
            domain="stress",
            category="safety",
            text=(
                f"针对已记录的{red_flag}，不要仅依靠生活方式干预；"
                "请立即联系精神专科、急诊或当地紧急医疗服务，并由可信任的家人陪同。"
            ),
            anchor_refs=["questionnaire:emotional_state"],
            quantity="立即求助",
            safety_level="referral",
            clinician_review_required=True,
        )
        return LifestylePlan(
            status="blocked",
            rule_version=self.version,
            selected_protocols=[
                LifestyleProtocolSelection(
                    protocol_id="LP24",
                    title="情绪与心理健康支持",
                    admission="referral",
                    reason=f"发现精神心理红旗：{red_flag}",
                    anchor_refs=["questionnaire:emotional_state"],
                )
            ],
            sections=[LifestyleSection(domain="stress", title=DOMAIN_TITLES["stress"], actions=[action])],
            monitoring=["红旗状态由精神专科持续评估。"],
            clinician_review_required=True,
        )

    def _matched_contraindication(self, protocol: dict[str, Any], evidence: dict[str, Any]) -> str | None:
        special_terms = set()
        if evidence["age"] is not None and evidence["age"] < 18:
            special_terms.add("未成年")
        if evidence["pregnancy"]:
            special_terms.update(("妊娠", "哺乳"))
        if evidence["underweight"]:
            special_terms.add("体重过轻")
        for term in protocol.get("contraindications", []):
            normalized_term = self._normalize(str(term))
            if term in special_terms or (normalized_term and normalized_term in evidence["normalized_text"]):
                return str(term)
        return None

    def _lifestyle_tag_anchor(self, tag: str, context: Any) -> tuple[str, str]:
        if tag == "energy_support":
            scores = getattr(context, "msq_system_scores", {}) or {}
            for score_name in ("能量/活动", "体能及情绪", "鑳介噺/娲诲姩"):
                try:
                    score = int(scores.get(score_name, 0) or 0)
                except (TypeError, ValueError):
                    score = 0
                if score >= 2:
                    return (
                        f"questionnaire:msq_system_scores.{score_name}",
                        f"MSQ能量/活动评分{score}分",
                    )
        labels = {
            "sleep_recovery": ("questionnaire:sleep", "睡眠时长或睡眠质量需要关注"),
            "stress_support": ("questionnaire:stress", "压力或情绪负担较高"),
            "movement": ("questionnaire:exercise_frequency", "当前缺少规律运动"),
            "sedentary_risk": ("questionnaire:sitting_hours_per_day", "当前久坐时间较长"),
            "metabolic_support": ("case:metabolic", "已确认的代谢相关问题"),
            "glucose_support": ("case:glucose", "已确认的血糖或胰岛素相关问题"),
            "lipid_support": ("case:lipid", "已确认的血脂相关问题"),
            "cardiovascular_support": ("case:cardiovascular", "已确认的心血管相关问题"),
            "gut_support": ("case:gut", "已记录的胃肠道相关问题"),
            "energy_support": ("case:energy", "已记录的疲劳或能量相关问题"),
            "chemical_sensitivity": ("questionnaire:chemical_sensitivity", "已记录的环境或化学暴露问题"),
        }
        return labels.get(tag, (f"lifestyle:{tag}", "已确认的生活方式问题"))

    def _has_sleep_terms(self, evidence: dict[str, Any]) -> bool:
        return bool(
            self._first_matching_term(
                evidence["normalized_text"],
                ("失眠", "入睡困难", "早醒", "日间嗜睡", "睡眠障碍", "睡眠呼吸暂停", "OSAHS"),
            )
        )

    def _has_stress_terms(self, evidence: dict[str, Any]) -> bool:
        return bool(
            self._first_matching_term(
                evidence["normalized_text"],
                ("压力", "焦虑", "情绪低落", "抑郁", "紧张", "慢性疼痛", "压力性失眠"),
            )
        )

    def _has_cardiovascular_risk(self, case: Any, context: Any) -> bool:
        text = self._normalize(
            " ".join(
                [
                    *(item.finding_name for item in context.clinical_findings),
                    *((case.questionnaire.known_conditions or []) if case.questionnaire else []),
                ]
            )
        )
        return any(term in text for term in ("冠心病", "心肌梗死", "心力衰竭", "冠状动脉", "高血压")) or any(
            self._has_marker(context, marker_code, "high")
            for marker_code in ("systolic_bp", "diastolic_bp")
        )

    def _cap_sections(self, sections: dict[str, list[LifestyleAction]]) -> None:
        caps = {"diet": 5, "movement": 5, "sleep": 5, "stress": 5}
        category_order = {
            "diet": {"limit": 0, "recommend": 1, "execution": 2},
            "movement": {"principle": 0, "plan": 1, "safety": 2},
            "sleep": {"safety": 0, "rhythm": 1, "behavior": 2, "execution": 3},
            "stress": {"practice": 0, "execution": 1, "support": 2, "safety": 3},
        }
        for domain, actions in sections.items():
            ordered = sorted(
                actions,
                key=lambda action: category_order.get(domain, {}).get(action.category, 99),
            )
            selected: list[LifestyleAction] = []
            selected_groups: set[str] = set()
            for action in ordered:
                group = self._action_problem_group(action)
                if group in selected_groups:
                    continue
                selected.append(action)
                selected_groups.add(group)
                if len(selected) >= caps[domain]:
                    break
            if domain == "movement":
                for group in list(selected_groups):
                    if len(selected) >= caps[domain]:
                        break
                    safety = next(
                        (
                            item
                            for item in ordered
                            if item.category == "safety"
                            and self._action_problem_group(item) == group
                        ),
                        None,
                    )
                    if safety is not None and safety not in selected:
                        selected.append(safety)
            for action in ordered:
                if len(selected) >= caps[domain]:
                    break
                if action not in selected:
                    selected.append(action)
            if domain == "movement" and ordered and not any(item.category == "safety" for item in selected):
                safety = next((item for item in ordered if item.category == "safety"), None)
                if safety is not None:
                    if len(selected) < caps[domain]:
                        selected.append(safety)
                    else:
                        selected[-1] = safety
            sections[domain] = selected

    @staticmethod
    def _action_problem_group(action: LifestyleAction) -> str:
        protocol_id = action.action_id.split("_", 1)[0]
        if protocol_id.startswith("SUPPORT-") and action.anchor_refs:
            return "|".join(sorted(action.anchor_refs))
        return protocol_id

    @classmethod
    def _is_non_evidence_protocol_heading(cls, protocol_id: str, value: str) -> bool:
        return protocol_id == "LP09" and cls._normalize(value) == cls._normalize("慢性疲劳症")

    @classmethod
    def _is_specific_fatigue_anchor(cls, value: str) -> bool:
        normalized = cls._normalize(value)
        return any(
            cls._normalize(term) in normalized
            for term in ("疲劳", "没精神", "运动不耐受", "能量下降")
        )

    @staticmethod
    def _has_marker(context: Any, marker_code: str, flag: str) -> bool:
        return any(
            getattr(getattr(item, "abnormal_flag", None), "value", "") == flag
            for item in context.markers_by_code.get(marker_code, [])
        )

    @staticmethod
    def _first_matching_term(text: str, terms: tuple[str, ...]) -> str | None:
        lowered = text.lower()
        return next((term for term in terms if term.lower() in lowered), None)

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[\s，。；、：:（）()“”\"'`/\\_-]+", "", str(value or "")).lower()

    @staticmethod
    def _category_label(domain: str, category: str) -> str:
        labels = {
            "diet": {
                "limit": "需回避或限制",
                "recommend": "推荐饮食模式",
                "execution": "执行与记录",
            },
            "movement": {
                "principle": "核心原则",
                "plan": "分阶段方案",
                "safety": "禁忌与停止指征",
            },
            "sleep": {
                "rhythm": "节律安排",
                "behavior": "行为调整",
                "execution": "执行与记录",
                "safety": "优先处理",
            },
            "stress": {
                "practice": "日常练习",
                "execution": "执行与记录",
                "support": "支持安排",
                "safety": "安全提示",
            },
        }
        return labels.get(domain, {}).get(category, "")
