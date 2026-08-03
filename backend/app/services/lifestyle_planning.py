from __future__ import annotations

import json
import re
from dataclasses import dataclass
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
        selections: list[LifestyleProtocolSelection] = []
        monitoring: list[str] = []
        missing_info: list[str] = []

        for match in selected:
            protocol = match.protocol
            admission = str(protocol.get("admission") or "review")
            selections.append(
                LifestyleProtocolSelection(
                    protocol_id=protocol["protocol_id"],
                    title=protocol["title"],
                    admission=admission,
                    reason=match.reason,
                    anchor_refs=list(match.anchor_refs),
                )
            )
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
                    if action is not None and action.text not in {item.text for item in sections_by_domain[domain]}:
                        sections_by_domain[domain].append(action)
            monitoring.extend(str(item).strip() for item in protocol.get("monitoring", []) if str(item).strip())

        self._add_age_or_pregnancy_movement_adaptation(sections_by_domain, case, context)
        self._add_osa_priority_action(sections_by_domain, evidence)
        self._cap_sections(sections_by_domain)

        if not selected:
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
        if referral_selected:
            status = "blocked"
        elif requires_review:
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

    def report_items(self, plan: LifestylePlan) -> list[str]:
        items: list[str] = []
        section_number = 1
        for section in plan.sections:
            items.append(f"### {section_number}. {section.title}")
            actions_by_category: dict[str, list[LifestyleAction]] = {}
            for action in section.actions:
                actions_by_category.setdefault(action.category, []).append(action)

            category_number = 1
            for category, actions in actions_by_category.items():
                label = self._category_label(section.domain, category)
                if label:
                    items.append(f"### {section_number}.{category_number} {label}")
                    category_number += 1
                for action in actions:
                    text = remove_generic_lifestyle_confirmation(action.text)
                    if text:
                        items.append(text)
            section_number += 1
        return items

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
            }
            for field_name, values in field_values.items():
                for value in values or []:
                    cleaned = str(value).strip()
                    if cleaned:
                        original_terms.append((f"questionnaire:{field_name}", cleaned))
        for finding in context.clinical_findings:
            if finding.finding_name:
                original_terms.append((f"finding:{finding.finding_id}", finding.finding_name))

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
                ref, text = self._lifestyle_tag_anchor(tag, context)
                refs.append(ref)
                anchor_texts.append(text)

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

    def _select_protocols(self, matches: list[ProtocolMatch], context: Any) -> list[ProtocolMatch]:
        ranked = sorted(matches, key=lambda item: (-item.score, item.protocol["protocol_id"]))
        selected: list[ProtocolMatch] = []

        def reserve(protocol_ids: tuple[str, ...]) -> None:
            for protocol_id in protocol_ids:
                match = next((item for item in ranked if item.protocol["protocol_id"] == protocol_id), None)
                if match and match not in selected and len(selected) < self.max_parallel_protocols:
                    selected.append(match)
                    return

        if "sleep_recovery" in context.lifestyle_tags:
            reserve(("LP06", "LP05"))
        if "stress_support" in context.lifestyle_tags:
            reserve(("LP24", "LP20", "LP05"))
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
        patient_text = f"针对您的{match.anchor_text}，{text}"
        if not self._action_is_safe(patient_text, domain=domain, context=context, case=case):
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
            text=patient_text,
            anchor_refs=list(match.anchor_refs),
            quantity=quantity,
            safety_level=(
                "review"
                if action_requires_review and admission == "direct"
                else ADMISSION_SAFETY_LEVEL.get(admission, "review")
            ),
            clinician_review_required=action_requires_review,
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

    def _add_age_or_pregnancy_movement_adaptation(
        self,
        sections: dict[str, list[LifestyleAction]],
        case: Any,
        context: Any,
    ) -> None:
        movement = sections["movement"]
        if not movement:
            return
        if context.age is not None and context.age < 18:
            movement.insert(
                0,
                LifestyleAction(
                    action_id="AGE_ADAPTATION_movement",
                    domain="movement",
                    category="principle",
                    text=(
                        f"针对您的年龄为{context.age}岁，运动以生长发育、兴趣和家庭参与为前提；"
                        "久坐或无规律运动者可从每日累计20–30分钟开始，再逐步接近每日60分钟活动。"
                    ),
                    anchor_refs=["questionnaire:age"],
                    quantity="20–30分钟起步；目标60分钟/日",
                    safety_level="review",
                    clinician_review_required=True,
                ),
            )
        if context.pregnancy:
            movement.insert(
                0,
                LifestyleAction(
                    action_id="PREGNANCY_ADAPTATION_movement",
                    domain="movement",
                    category="principle",
                    text=(
                        "针对您处于孕期或哺乳期，运动需先由产科确认；强度使用谈话测试或RPE评估，"
                        "不采用统一目标心率。"
                    ),
                    anchor_refs=["questionnaire:pregnant_or_lactating"],
                    quantity="运动前产科确认",
                    safety_level="review",
                    clinician_review_required=True,
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
        labels = {
            "sleep_recovery": ("questionnaire:sleep", "睡眠时长或睡眠质量需要关注"),
            "stress_support": ("questionnaire:stress", "压力或情绪负担较高"),
            "movement": ("questionnaire:exercise_frequency", "当前缺少规律运动"),
            "sedentary_risk": ("questionnaire:sitting_hours_per_day", "当前久坐时间较长"),
            "metabolic_support": ("case:metabolic", "已确认的代谢相关问题"),
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
        return any(term in text for term in ("冠心病", "心肌梗死", "心力衰竭", "冠状动脉", "高血压"))

    def _cap_sections(self, sections: dict[str, list[LifestyleAction]]) -> None:
        caps = {"diet": 10, "movement": 7, "sleep": 4, "stress": 4}
        category_order = {
            "diet": {"limit": 0, "recommend": 1, "execution": 2},
            "movement": {"principle": 0, "plan": 1, "safety": 2},
            "sleep": {"safety": 0, "rhythm": 1, "behavior": 2, "execution": 3},
            "stress": {"practice": 0, "execution": 1, "support": 2, "safety": 3},
        }
        for domain, actions in sections.items():
            sections[domain] = sorted(
                actions,
                key=lambda action: category_order.get(domain, {}).get(action.category, 99),
            )[: caps[domain]]

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
