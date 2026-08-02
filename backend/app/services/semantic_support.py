from __future__ import annotations

import json
import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

from app.domain.models import (
    AbnormalFinding,
    CaseAnalysis,
    ClinicalEvidenceClass,
    SemanticEvidenceReference,
    SemanticEvidenceStrength,
    SemanticSupportNeed,
    SupportDirection,
    SupportEligibilityStatus,
)
from app.services.body_systems import SYSTEM_NAMES
from app.services.evidence_policy import (
    classify_finding_evidence,
    is_underweight_finding,
    strongest_evidence_class,
)


class SemanticSupportService:
    """Validate model-proposed support needs without exposing products to the model."""

    _TRUSTED_QUESTIONNAIRE_CLINICAL_FIELDS = frozenset(
        {
            "symptoms",
            "known_conditions",
            "emotional_state",
            "chief_concerns",
            "chemical_sensitivity",
        }
    )
    _QUESTIONNAIRE_FIELD_GOAL_ALLOWLIST = {
        "chemical_sensitivity": frozenset({"antioxidant"}),
    }
    _MSQ_SCORE_GOAL_SECTIONS = {
        "sleep_stress": frozenset({"情绪", "能量/活动"}),
        "neuro_cognitive": frozenset({"思维", "头部"}),
        "energy_mitochondria": frozenset({"能量/活动"}),
    }

    _FOLLOW_UP_ONLY_PATTERNS = (
        "结节",
        "肿块",
        "占位",
        "bi-rads",
        "birads",
        "lung-rads",
        "lungrads",
        "自身抗体阳性",
        "肿瘤标志物",
        "癌症风险",
        "恶性风险",
    )

    def __init__(self, catalog_path: Path) -> None:
        payload = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
        self.version = str(payload.get("version") or "support-goals-unknown")
        self.goals = {
            str(item["code"]): item
            for item in payload.get("goals", [])
            if isinstance(item, dict) and item.get("code")
        }
        self.product_capabilities = {
            str(item["sku_id"]): item
            for item in payload.get("products", [])
            if isinstance(item, dict) and item.get("sku_id")
        }
        self.system_coverage_rules = sorted(
            (
                item
                for item in payload.get("system_coverage_rules", [])
                if isinstance(item, dict)
                and item.get("rule_id")
                and item.get("system_id") in SYSTEM_NAMES
                and item.get("goal_code") in self.goals
            ),
            key=lambda item: (int(item.get("priority") or 999), str(item.get("rule_id"))),
        )

    @property
    def goal_codes(self) -> tuple[str, ...]:
        return tuple(self.goals)

    def prompt_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "code": code,
                "name": str(item.get("name") or code),
                "definition": str(item.get("definition") or ""),
                "system_id": str(item.get("system_id") or ""),
                "eligible_directions": list(item.get("eligible_directions") or []),
                "objective_evidence_markers": list(
                    item.get("objective_evidence_markers") or []
                ),
                "objective_evidence_terms": list(
                    item.get("objective_evidence_terms") or []
                ),
                "safety_context_markers": list(
                    item.get("safety_context_markers") or []
                ),
            }
            for code, item in self.goals.items()
        ]

    def validate_needs(
        self,
        *,
        candidates: list[SemanticSupportNeed],
        analysis: CaseAnalysis,
        clinical_summary_text: str | None,
    ) -> list[SemanticSupportNeed]:
        findings = {
            item.id: item
            for item in (analysis.reviewed_abnormal_findings or analysis.abnormal_findings)
        }
        questionnaire = analysis.questionnaire
        allowed_document_pages = {
            (item.source_file_id, item.source_page)
            for item in findings.values()
        }
        validated: list[SemanticSupportNeed] = []
        seen: set[tuple[str, str, tuple[str, ...]]] = set()

        for candidate in candidates:
            notes: list[str] = []
            goal = self.goals.get(candidate.support_goal_code or "")
            goal_code = candidate.support_goal_code if goal else None
            if candidate.support_goal_code and not goal:
                notes.append("支持目标代码不在当前版本白名单中，仅保留叙述。")

            valid_refs: list[SemanticEvidenceReference] = []
            distinct_sources: set[str] = set()
            strengths: list[SemanticEvidenceStrength] = []
            evidence_classes: list[ClinicalEvidenceClass] = []
            referenced_findings: list[AbnormalFinding] = []
            has_doctor_summary = False
            has_trusted_questionnaire_evidence = False
            for evidence in candidate.evidence_refs:
                ref = evidence.ref.strip()
                if ref.startswith("finding:"):
                    raw_finding_id = ref.split(":", 1)[1].strip()
                    finding_id = self._resolve_finding_id(raw_finding_id, findings)
                    finding = findings.get(finding_id) if finding_id else None
                    if not finding:
                        notes.append(f"已拒绝不存在或已被医生删除的证据引用：{ref}。")
                        continue
                    canonical_ref = f"finding:{finding_id}"
                    if canonical_ref != ref:
                        notes.append(f"证据引用ID格式已规范化：{ref}。")
                    referenced_findings.append(finding)
                    evidence_class = classify_finding_evidence(finding)
                    evidence_classes.append(evidence_class)
                    if evidence_class not in {
                        ClinicalEvidenceClass.genetic_risk,
                        ClinicalEvidenceClass.follow_up_only,
                    }:
                        distinct_sources.add(f"file:{finding.source_file_id}")
                    local_strength = (
                        SemanticEvidenceStrength.direct
                        if finding.raw_value or finding.reference_range
                        else SemanticEvidenceStrength.explicit_conclusion
                    )
                    if evidence_class in {
                        ClinicalEvidenceClass.genetic_risk,
                        ClinicalEvidenceClass.follow_up_only,
                    }:
                        local_strength = SemanticEvidenceStrength.contextual
                    validated_evidence = evidence.model_copy(
                        update={
                            "ref": canonical_ref,
                            "evidence_strength": local_strength,
                        }
                    )
                    valid_refs.append(validated_evidence)
                    strengths.append(local_strength)
                    continue
                if ref.startswith("questionnaire:"):
                    field_path = ref.split(":", 1)[1].strip()
                    if not field_path:
                        notes.append("问卷证据字段名为空，已拒绝该引用。")
                        continue
                    field = field_path
                    msq_section: str | None = None
                    if field_path.startswith("msq_system_scores."):
                        path_parts = field_path.split(".")
                        if len(path_parts) != 2 or not path_parts[1].strip():
                            notes.append("MSQ 系统评分证据路径不合法，已拒绝该引用。")
                            continue
                        field = "msq_system_scores"
                        msq_section = path_parts[1].strip()
                        scores = questionnaire.msq_system_scores if questionnaire else {}
                        value = scores.get(msq_section)
                        if (
                            isinstance(value, bool)
                            or not isinstance(value, int)
                            or not 0 <= value <= 4
                            or value == 0
                        ):
                            notes.append(
                                f"MSQ 系统评分引用无有效正分值：{msq_section}。"
                            )
                            continue
                        allowed_sections = self._MSQ_SCORE_GOAL_SECTIONS.get(
                            goal_code or "",
                            frozenset(),
                        )
                        if msq_section not in allowed_sections:
                            notes.append(
                                f"MSQ 系统评分与支持目标不匹配：{msq_section}。"
                            )
                            continue
                        canonical_ref = (
                            f"questionnaire:msq_system_scores.{msq_section}"
                        )
                        has_trusted_questionnaire_evidence = True
                        distinct_sources.add(canonical_ref)
                        notes.append(
                            f"MSQ 系统评分已按患者自述证据准入：{msq_section}。"
                        )
                    else:
                        if "." in field_path:
                            notes.append("问卷证据路径不在允许范围内，已拒绝该引用。")
                            continue
                        if field_path == "msq_system_scores":
                            notes.append("MSQ 系统评分必须引用具体系统键，已拒绝整表引用。")
                            continue
                        value = (
                            getattr(questionnaire, field, None)
                            if questionnaire
                            else None
                        )
                        canonical_ref = f"questionnaire:{field}"
                    if not value:
                        notes.append(f"问卷证据字段为空：{field}。")
                        continue
                    is_trusted_clinical_field = (
                        msq_section is not None
                        or field in self._TRUSTED_QUESTIONNAIRE_CLINICAL_FIELDS
                    )
                    allowed_goals = self._QUESTIONNAIRE_FIELD_GOAL_ALLOWLIST.get(field)
                    if allowed_goals is not None and goal_code not in allowed_goals:
                        is_trusted_clinical_field = False
                        notes.append(
                            f"问卷字段{field}不能单独支持当前目标，已按背景证据处理。"
                        )
                    if is_trusted_clinical_field and msq_section is None:
                        has_trusted_questionnaire_evidence = True
                        distinct_sources.add(canonical_ref)
                        notes.append(
                            f"问卷临床字段已按患者自述证据准入：{field}。"
                        )
                    if not is_trusted_clinical_field:
                        notes.append(
                            f"问卷背景字段仅保留为上下文，不计入产品准入：{field}。"
                        )
                    is_patient_reported_condition = field == "known_conditions"
                    is_exposure_context = field == "chemical_sensitivity"
                    contextual = evidence.model_copy(
                        update={
                            "ref": canonical_ref,
                            "evidence_strength": (
                                SemanticEvidenceStrength.explicit_conclusion
                                if is_patient_reported_condition
                                else SemanticEvidenceStrength.contextual
                            )
                        }
                    )
                    valid_refs.append(contextual)
                    strengths.append(contextual.evidence_strength)
                    evidence_classes.append(
                        ClinicalEvidenceClass.clinical_confirmed
                        if is_patient_reported_condition
                        else (
                            ClinicalEvidenceClass.exposure
                            if is_exposure_context
                            else ClinicalEvidenceClass.symptom
                        )
                    )
                    continue
                if ref.startswith("clinical_summary:"):
                    if not (clinical_summary_text or "").strip():
                        notes.append("医生病例总结为空，不能作为支持证据。")
                        continue
                    has_doctor_summary = True
                    distinct_sources.add("clinical_summary")
                    contextual = evidence.model_copy(
                        update={"evidence_strength": SemanticEvidenceStrength.contextual}
                    )
                    valid_refs.append(contextual)
                    strengths.append(contextual.evidence_strength)
                    evidence_classes.append(ClinicalEvidenceClass.symptom)
                    continue
                if ref.startswith("document:"):
                    parts = ref.split(":")
                    if len(parts) != 3 or not parts[2].isdigit():
                        notes.append(f"文档证据格式错误：{ref}。")
                        continue
                    key = (parts[1], int(parts[2]))
                    if key not in allowed_document_pages:
                        notes.append(f"文档证据未关联医生保留的异常：{ref}。")
                        continue
                    related = [
                        item
                        for item in findings.values()
                        if item.source_file_id == parts[1] and item.source_page == int(parts[2])
                    ]
                    local_strength = (
                        SemanticEvidenceStrength.direct
                        if any(item.raw_value or item.reference_range for item in related)
                        else SemanticEvidenceStrength.explicit_conclusion
                    )
                    related_classes = [classify_finding_evidence(item) for item in related]
                    local_class = strongest_evidence_class(related_classes)
                    evidence_classes.append(local_class)
                    if local_class in {
                        ClinicalEvidenceClass.genetic_risk,
                        ClinicalEvidenceClass.follow_up_only,
                    }:
                        local_strength = SemanticEvidenceStrength.contextual
                    validated_evidence = evidence.model_copy(
                        update={"evidence_strength": local_strength}
                    )
                    if local_class not in {
                        ClinicalEvidenceClass.genetic_risk,
                        ClinicalEvidenceClass.follow_up_only,
                    }:
                        distinct_sources.add(f"file:{parts[1]}")
                    valid_refs.append(validated_evidence)
                    strengths.append(local_strength)
                    continue
                notes.append(f"未知证据引用类型：{ref}。")

            strongest = self._strongest(strengths)
            evidence_class = strongest_evidence_class(evidence_classes)
            blocked_follow_up_only = bool(referenced_findings) and all(
                self._is_follow_up_only(item) for item in referenced_findings
            )
            eligible = bool(goal_code and valid_refs)
            if blocked_follow_up_only:
                eligible = False
                notes.append("证据仅包含结节、肿块、孤立抗体或风险描述，不能单独触发营养素。")
            actionable_classes = {
                item
                for item in evidence_classes
                if item not in {
                    ClinicalEvidenceClass.genetic_risk,
                    ClinicalEvidenceClass.follow_up_only,
                }
            }
            if valid_refs and not actionable_classes:
                eligible = False
                notes.append("证据仅为遗传易感或疾病风险修饰信息，只进入报告叙述，不单独触发产品。")
            if strongest == SemanticEvidenceStrength.contextual and not (
                has_doctor_summary
                or has_trusted_questionnaire_evidence
                or len(distinct_sources) >= 2
            ):
                eligible = False
                notes.append("单一模糊情境证据不足，需要医生总结明确记录或至少两个独立来源。")
            if goal and strongest.value not in set(goal.get("allowed_evidence_types") or []):
                eligible = False
                notes.append("当前证据类型不满足该支持目标的准入要求。")

            blocked_finding_codes = set(goal.get("blocked_finding_codes") or []) if goal else set()
            referenced_finding_codes = {
                finding.finding_code
                for finding in referenced_findings
                if finding.finding_code
            }
            blocked_codes = sorted(blocked_finding_codes & referenced_finding_codes)
            if blocked_codes:
                eligible = False
                notes.append(
                    "医生确认事实与该支持目标方向冲突，禁止用于产品匹配："
                    + "、".join(blocked_codes)
                    + "。"
                )

            objective_marker_rules = (
                list(goal.get("objective_evidence_markers") or []) if goal else []
            )
            objective_text_terms = (
                list(goal.get("objective_evidence_terms") or []) if goal else []
            )
            safety_marker_rules = (
                list(goal.get("safety_context_markers") or []) if goal else []
            )
            if objective_marker_rules or objective_text_terms:
                positive_evidence = self._matching_goal_evidence(
                    referenced_findings,
                    marker_rules=objective_marker_rules,
                    text_terms=objective_text_terms,
                )
                safety_context = self._matching_goal_evidence(
                    referenced_findings,
                    marker_rules=safety_marker_rules,
                )
                if not positive_evidence:
                    eligible = False
                    goal_name = str(goal.get("name") or goal_code)
                    if safety_context:
                        notes.append(
                            f"当前引用仅命中{goal_name}的安全背景证据，不能单独触发产品。"
                        )
                    else:
                        notes.append(
                            f"当前引用未命中{goal_name}目录配置的正向证据，仅保留病例叙述。"
                        )
                else:
                    notes.append(
                        "支持目标已通过目录配置的客观正向证据校验。"
                    )

            eligible_directions = {
                str(value) for value in (goal.get("eligible_directions") or [])
            } if goal else set()
            if eligible_directions and candidate.support_direction.value not in eligible_directions:
                eligible = False
                if candidate.support_direction == SupportDirection.unknown:
                    notes.append("该支持目标必须明确干预方向；方向未知时仅保留病例叙述。")
                else:
                    notes.append("支持方向与当前产品能力不一致，仅保留病例叙述。")

            system_id = candidate.system_id if candidate.system_id in SYSTEM_NAMES else ""
            catalog_system_id = str(goal.get("system_id") or "") if goal else ""
            if catalog_system_id in SYSTEM_NAMES:
                if system_id and system_id != catalog_system_id:
                    notes.append("模型身体系统与支持目标目录不一致，已采用目录系统。")
                system_id = catalog_system_id
            if system_id not in SYSTEM_NAMES:
                eligible = False
                notes.append("身体系统代码无效。")

            signature = (
                goal_code or "",
                re.sub(r"\s+", "", candidate.support_need_text).lower(),
                tuple(sorted(item.ref for item in valid_refs)),
            )
            if signature in seen:
                continue
            seen.add(signature)
            validated.append(
                candidate.model_copy(
                    update={
                        "id": candidate.id or f"support_{uuid.uuid4().hex[:12]}",
                        "support_goal_code": goal_code,
                        "system_id": system_id or candidate.system_id,
                        "evidence_refs": valid_refs,
                        "evidence_strength": strongest,
                        "evidence_class": evidence_class,
                        "corroboration_count": len(distinct_sources),
                        "eligibility_status": (
                            SupportEligibilityStatus.eligible
                            if eligible
                            else SupportEligibilityStatus.narrative_only
                        ),
                        "validation_notes": list(dict.fromkeys(notes)),
                    }
                )
            )
        self._append_underweight_support_need(validated, findings)
        return validated

    def ensure_system_coverage(
        self,
        *,
        analysis: CaseAnalysis,
    ) -> list[SemanticSupportNeed]:
        """Add only catalog-approved support needs for locally validated systems."""

        needs = list(analysis.support_needs or [])
        covered_systems = {
            need.system_id
            for need in needs
            if need.eligibility_status == SupportEligibilityStatus.eligible
            and need.support_goal_code
            and self._goal_has_primary_product(
                need.support_goal_code,
                need.support_direction,
            )
        }
        findings = {
            item.id: item
            for item in (analysis.reviewed_abnormal_findings or analysis.abnormal_findings)
        }
        questionnaire = analysis.questionnaire

        for structured in analysis.final_structured_system_findings:
            system_id = structured.system_id
            if system_id in covered_systems:
                continue
            rules = [
                rule
                for rule in self.system_coverage_rules
                if str(rule.get("system_id")) == system_id
            ]
            for rule in rules:
                goal_code = str(rule.get("goal_code") or "")
                goal = self.goals.get(goal_code)
                direction_value = str(rule.get("support_direction") or "unknown")
                try:
                    direction = SupportDirection(direction_value)
                except ValueError:
                    direction = SupportDirection.unknown
                if not goal or not self._goal_has_primary_product(goal_code, direction):
                    continue
                evidence_refs, evidence_class, matched_findings = self._coverage_rule_evidence(
                    rule=rule,
                    finding_ids=structured.finding_ids,
                    findings=findings,
                    questionnaire=questionnaire,
                )
                if not evidence_refs:
                    continue
                strength = self._coverage_evidence_strength(evidence_class)
                if strength.value not in set(goal.get("allowed_evidence_types") or []):
                    continue
                if evidence_class in {
                    ClinicalEvidenceClass.genetic_risk,
                    ClinicalEvidenceClass.follow_up_only,
                }:
                    continue
                if not self._coverage_goal_evidence_allowed(goal, matched_findings):
                    continue
                eligible_directions = {
                    str(value) for value in (goal.get("eligible_directions") or [])
                }
                if eligible_directions and direction.value not in eligible_directions:
                    continue
                signature = "|".join(
                    [system_id, goal_code, *(sorted(ref.ref for ref in evidence_refs))]
                )
                rule_id = str(rule.get("rule_id"))
                confidence = {
                    ClinicalEvidenceClass.lab_abnormal: 0.88,
                    ClinicalEvidenceClass.clinical_confirmed: 0.82,
                    ClinicalEvidenceClass.symptom: 0.72,
                    ClinicalEvidenceClass.exposure: 0.62,
                }.get(evidence_class, 0.6)
                needs.append(
                    SemanticSupportNeed(
                        id="support_cov_"
                        + hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12],
                        support_need_text=(
                            f"{SYSTEM_NAMES[system_id]}："
                            f"{str(goal.get('name') or goal_code)}"
                        ),
                        support_goal_code=goal_code,
                        support_direction=direction,
                        system_id=system_id,
                        evidence_refs=evidence_refs,
                        evidence_strength=strength,
                        evidence_class=evidence_class,
                        corroboration_count=len({ref.ref for ref in evidence_refs}),
                        rationale=(
                            "该支持目标由本地版本化系统覆盖规则补全，"
                            "仅使用医生保留的异常或已验证问卷事实。"
                        ),
                        model_confidence=confidence,
                        eligibility_status=SupportEligibilityStatus.eligible,
                        validation_notes=[
                            f"local_system_coverage:{rule_id}",
                            "本地受控覆盖补全；模型未选择产品。",
                        ],
                    )
                )
                covered_systems.add(system_id)
                break
        return needs

    def _goal_has_primary_product(
        self,
        goal_code: str,
        direction: SupportDirection,
    ) -> bool:
        for item in self.product_capabilities.values():
            if not item.get("enabled", True) or goal_code not in set(
                item.get("primary_goal_codes") or []
            ):
                continue
            requirements = item.get("goal_direction_requirements") or {}
            allowed = {
                str(value) for value in requirements.get(goal_code, [])
            }
            if not allowed or direction.value in allowed:
                return True
        return False

    def _coverage_rule_evidence(
        self,
        *,
        rule: dict[str, Any],
        finding_ids: list[str],
        findings: dict[str, AbnormalFinding],
        questionnaire: Any,
    ) -> tuple[
        list[SemanticEvidenceReference],
        ClinicalEvidenceClass,
        list[AbnormalFinding],
    ]:
        terms = {
            re.sub(r"\s+", "", str(value or "")).lower()
            for value in (rule.get("finding_terms") or [])
            if str(value or "").strip()
        }
        matched_findings: list[AbnormalFinding] = []
        refs: list[SemanticEvidenceReference] = []
        classes: list[ClinicalEvidenceClass] = []
        for finding_id in finding_ids:
            finding = findings.get(str(finding_id))
            if not finding:
                continue
            text = re.sub(
                r"\s+",
                "",
                " ".join(
                    filter(
                        None,
                        (
                            finding.name,
                            finding.result_text,
                            finding.report_explanation,
                            finding.source_text,
                        ),
                    )
                ),
            ).lower()
            if terms and not any(term in text for term in terms):
                continue
            evidence_class = classify_finding_evidence(finding)
            if evidence_class in {
                ClinicalEvidenceClass.genetic_risk,
                ClinicalEvidenceClass.follow_up_only,
            }:
                continue
            strength = self._coverage_evidence_strength(evidence_class)
            refs.append(
                SemanticEvidenceReference(
                    ref=f"finding:{finding.id}",
                    evidence_strength=strength,
                )
            )
            matched_findings.append(finding)
            classes.append(evidence_class)

        for field in rule.get("questionnaire_fields") or []:
            if not questionnaire:
                continue
            value = getattr(questionnaire, str(field), None)
            values = value if isinstance(value, list) else [value]
            normalized_values = [
                re.sub(r"\s+", "", str(item or "")).lower()
                for item in values
                if str(item or "").strip()
            ]
            if not normalized_values:
                continue
            if terms and not any(
                term in item for term in terms for item in normalized_values
            ):
                continue
            field_name = str(field)
            evidence_class = (
                ClinicalEvidenceClass.clinical_confirmed
                if field_name == "known_conditions"
                else (
                    ClinicalEvidenceClass.exposure
                    if field_name == "chemical_sensitivity"
                    else ClinicalEvidenceClass.symptom
                )
            )
            refs.append(
                SemanticEvidenceReference(
                    ref=f"questionnaire:{field_name}",
                    evidence_strength=self._coverage_evidence_strength(evidence_class),
                )
            )
            classes.append(evidence_class)

        scores = questionnaire.msq_system_scores if questionnaire else {}
        for section in rule.get("msq_sections") or []:
            value = scores.get(str(section)) if isinstance(scores, dict) else None
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                continue
            refs.append(
                SemanticEvidenceReference(
                    ref=f"questionnaire:msq_system_scores.{section}",
                    evidence_strength=SemanticEvidenceStrength.contextual,
                )
            )
            classes.append(ClinicalEvidenceClass.symptom)

        unique_refs = {ref.ref: ref for ref in refs}
        return (
            list(unique_refs.values()),
            strongest_evidence_class(classes),
            matched_findings,
        )

    @staticmethod
    def _coverage_evidence_strength(
        evidence_class: ClinicalEvidenceClass,
    ) -> SemanticEvidenceStrength:
        if evidence_class == ClinicalEvidenceClass.lab_abnormal:
            return SemanticEvidenceStrength.direct
        if evidence_class == ClinicalEvidenceClass.clinical_confirmed:
            return SemanticEvidenceStrength.explicit_conclusion
        return SemanticEvidenceStrength.contextual

    def _coverage_goal_evidence_allowed(
        self,
        goal: dict[str, Any],
        matched_findings: list[AbnormalFinding],
    ) -> bool:
        marker_rules = list(goal.get("objective_evidence_markers") or [])
        text_terms = list(goal.get("objective_evidence_terms") or [])
        if not marker_rules and not text_terms:
            return True
        return bool(
            self._matching_goal_evidence(
                matched_findings,
                marker_rules=marker_rules,
                text_terms=text_terms,
            )
        )

    @staticmethod
    def _matching_goal_evidence(
        findings: list[AbnormalFinding],
        *,
        marker_rules: list[str],
        text_terms: list[str] | None = None,
    ) -> list[AbnormalFinding]:
        normalized_marker_rules = {
            str(rule or "").strip().lower()
            for rule in marker_rules
            if str(rule or "").strip()
        }
        normalized_terms = {
            re.sub(r"\s+", "", str(term or "")).lower()
            for term in (text_terms or [])
            if str(term or "").strip()
        }
        matched: list[AbnormalFinding] = []
        for finding in findings:
            flag = str(finding.abnormal_flag or "unknown").strip().lower()
            codes = {
                str(code or "").strip().lower()
                for code in (
                    finding.marker_code,
                    finding.finding_code,
                    finding.marker_code_candidate,
                    finding.finding_code_candidate,
                )
                if str(code or "").strip()
            }
            marker_keys = {*codes, *(f"{code}:{flag}" for code in codes)}
            text = re.sub(
                r"\s+",
                "",
                " ".join(
                    filter(
                        None,
                        (
                            finding.name,
                            finding.result_text,
                            finding.report_explanation,
                            finding.support_need_text,
                        ),
                    )
                ),
            ).lower()
            if normalized_marker_rules.intersection(marker_keys) or any(
                term in text for term in normalized_terms
            ):
                matched.append(finding)
        return matched

    def _append_underweight_support_need(
        self,
        validated: list[SemanticSupportNeed],
        findings: dict[str, AbnormalFinding],
    ) -> None:
        if "nutrition_repletion" not in self.goals or any(
            item.support_goal_code == "nutrition_repletion"
            and item.eligibility_status == SupportEligibilityStatus.eligible
            for item in validated
        ):
            return
        finding = next((item for item in findings.values() if is_underweight_finding(item)), None)
        if not finding:
            return
        evidence_class = classify_finding_evidence(finding)
        evidence_strength = (
            SemanticEvidenceStrength.direct
            if evidence_class == ClinicalEvidenceClass.lab_abnormal
            else SemanticEvidenceStrength.explicit_conclusion
        )
        validated.append(
            SemanticSupportNeed(
                id=f"support_{uuid.uuid4().hex[:12]}",
                support_need_text="体重过轻与营养恢复支持",
                support_goal_code="nutrition_repletion",
                support_direction=SupportDirection.increase,
                system_id="endocrine_metabolic",
                evidence_refs=[
                    SemanticEvidenceReference(
                        ref=f"finding:{finding.id}",
                        evidence_strength=evidence_strength,
                    )
                ],
                evidence_strength=evidence_strength,
                evidence_class=evidence_class,
                corroboration_count=1,
                rationale="BMI或医生确认结论提示体重过轻，需要正向支持营养摄入、吸收和体重恢复。",
                model_confidence=max(finding.mapping_confidence, finding.confidence),
                eligibility_status=SupportEligibilityStatus.eligible,
                validation_notes=["根据医生确认的体重过轻事实补充正向营养恢复需求。"],
            )
        )

    @staticmethod
    def _resolve_finding_id(
        raw_finding_id: str,
        findings: dict[str, AbnormalFinding],
    ) -> str | None:
        """Resolve provider formatting variants without fuzzy medical matching.

        Some OpenAI-compatible providers copy ``finding_xxx`` as ``xxx`` when
        they place it after the ``finding:`` reference namespace.  Accept the
        original ID first, then only the two exact prefix restorations used by
        this application.  Ambiguous aliases remain invalid.
        """
        candidate = raw_finding_id.strip()
        if not candidate:
            return None
        if candidate in findings:
            return candidate

        restored_ids = {
            restored
            for restored in (f"finding_{candidate}", f"finding-{candidate}")
            if restored in findings
        }
        if len(restored_ids) == 1:
            return next(iter(restored_ids))
        return None

    @staticmethod
    def _strongest(values: list[SemanticEvidenceStrength]) -> SemanticEvidenceStrength:
        for strength in (
            SemanticEvidenceStrength.direct,
            SemanticEvidenceStrength.explicit_conclusion,
            SemanticEvidenceStrength.contextual,
        ):
            if strength in values:
                return strength
        return SemanticEvidenceStrength.contextual

    def _is_follow_up_only(self, finding: AbnormalFinding) -> bool:
        text = " ".join(
            filter(
                None,
                [
                    finding.name,
                    finding.result_text,
                    finding.report_explanation,
                    finding.neutral_interpretation,
                    finding.interpretation,
                ],
            )
        ).lower()
        return any(pattern in text for pattern in self._FOLLOW_UP_ONLY_PATTERNS)
