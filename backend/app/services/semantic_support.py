from __future__ import annotations

import json
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
                    field = ref.split(":", 1)[1]
                    value = getattr(questionnaire, field, None) if questionnaire else None
                    if not value:
                        notes.append(f"问卷证据字段为空：{field}。")
                        continue
                    distinct_sources.add(f"questionnaire:{field}")
                    is_patient_reported_condition = bool(
                        field == "known_conditions"
                        and any(
                            str(finding.abnormal_flag or "").lower()
                            == "patient_reported"
                            for finding in findings.values()
                        )
                    )
                    contextual = evidence.model_copy(
                        update={
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
                        else ClinicalEvidenceClass.symptom
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
                has_doctor_summary or len(distinct_sources) >= 2
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
            if not system_id and goal:
                system_id = str(goal.get("system_id") or "")
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
