from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.domain.models import (
    CoreHealthPortraitDecision,
    CoreHealthPortraitResult,
    HealthInterventionHub,
    HealthInterventionStep,
    HealthMechanismChain,
    HealthPortraitFinding,
    HealthPortraitRiskAssessment,
)
from app.services.body_systems import SYSTEM_NAMES, classify_text_to_system_ids


HEALTH_PORTRAIT_RULE_VERSION = "health-portrait-v1-three-layer"
_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "health_axis_registry.json"
_FOOD_SENSITIVITY_TERMS = (
    "慢性食物敏感",
    "慢性食物过敏",
    "食物不耐受",
    "foodsensitivity",
    "foodintolerance",
    "foodallergyigg",
    "foodspecificigg",
    "foodigg",
)
_P0_TERMS = (
    "立即就医",
    "急诊",
    "危急值",
    "危重",
    "紧急转诊",
    "意识障碍",
    "严重呼吸困难",
    "持续性胸痛",
    "胸痛红旗",
    "及时就医",
)
_DOSE_CAUTION_TERMS = (
    "华法林",
    "抗凝",
    "胰岛素",
    "孕期",
    "妊娠",
    "哺乳",
    "多重用药",
    "肝功能",
    "肾功能",
)
_NON_INTERVENABLE_TERMS = (
    "占位",
    "结节",
    "肿块",
    "囊肿",
    "钙化",
    "先天",
    "遗传",
    "陈旧性",
    "病理确诊",
)
_IMAGING_PATHOLOGY_TERMS = (
    "超声",
    "ct",
    "mri",
    "核磁",
    "影像",
    "病理",
    "活检",
    "内镜",
)
_SYMPTOM_TERMS = ("症状", "疼痛", "乏力", "失眠", "腹胀", "便秘", "腹泻", "不适")
_ORGAN_DAMAGE_TERMS = (
    "损伤",
    "损害",
    "纤维化",
    "坏死",
    "梗死",
    "心肌",
    "蛋白尿",
    "肾功能不全",
    "肝功能异常",
)
_T2_TEXT_TERMS = ("显著升高", "明显升高", "重度", "严重异常", "强阳性")
_NUMBER_WORDS = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五", 6: "六"}


def build_core_health_portrait_result(
    structured_system_findings: Iterable[Any],
    confirmed_findings: Iterable[Any] | None = None,
    abnormal_findings: Iterable[Any] | None = None,
    objective_evidence_items: Iterable[str] | None = None,
    risk_notices: Iterable[str] | None = None,
    *,
    age: int | None = None,
    medication_count: int = 0,
    current_supplement_count: int = 0,
    recommended_items: Iterable[Any] | None = None,
    lifestyle_plan: Any | None = None,
) -> CoreHealthPortraitResult:
    """Create the three-layer conclusion without ever breaking report generation."""
    inputs = {
        "structured": list(structured_system_findings or []),
        "confirmed": list(confirmed_findings or []),
        "abnormal": list(abnormal_findings or []),
        "objective_evidence": list(objective_evidence_items or []),
        "risk_notices": list(risk_notices or []),
        "age": age,
        "medication_count": max(0, int(medication_count or 0)),
        "current_supplement_count": max(0, int(current_supplement_count or 0)),
        "recommended_items": list(recommended_items or []),
        "lifestyle_plan": lifestyle_plan,
    }
    try:
        first = _compute_portrait(**inputs)
        violations = validate_core_health_portrait(first)
        if not violations:
            return first

        # A single deterministic recomputation protects against transient/adaptor issues.
        second = _compute_portrait(**inputs)
        second_violations = validate_core_health_portrait(second)
        if not second_violations:
            return second
        return _degraded_result(
            "现有证据未通过三层规则的完整性校验，暂不强行建立机制主线。",
            violations=list(dict.fromkeys([*violations, *second_violations])),
        )
    except Exception as exc:  # the validator is deliberately not a report-generation fuse
        return _degraded_result(
            "核心健康画像判读已转入安全降级，暂不强行建立机制主线。",
            violations=[f"portrait_internal_error:{type(exc).__name__}"],
        )


def validate_core_health_portrait(result: CoreHealthPortraitResult) -> list[str]:
    violations: list[str] = []
    decision = result.decision
    sentences = [item for item in re.split(r"(?<=[。！？])", result.text.strip()) if item.strip()]
    if len(sentences) != 3:
        violations.append("portrait_must_have_exactly_three_sentences")
    if "重要窗口期" in result.text:
        violations.append("forbidden_timing_claim")
    if result.status == "ready":
        if not 1 <= len(decision.mechanism_chains) <= 3:
            violations.append("ready_portrait_requires_one_to_three_chains")
        if not 1 <= len(decision.intervention_hubs) <= 2:
            violations.append("ready_portrait_requires_one_or_two_hubs")
        if len(decision.mechanism_chains) == 1 and len(decision.intervention_hubs) != 1:
            violations.append("single_chain_requires_one_hub")
        if not 3 <= len(decision.intervention_steps) <= 6:
            violations.append("ready_portrait_requires_three_to_six_steps")
        if not decision.steady_state_axis:
            violations.append("ready_portrait_requires_steady_state_axis")
    if result.status == "referral_only" and decision.intervention_hubs:
        violations.append("referral_only_must_not_create_nutrition_hubs")

    by_id = {item.finding_id: item for item in decision.findings}
    p0_ids = {
        item.finding_id for item in decision.findings if item.deviation_tier == "P0"
    }
    for chain in decision.mechanism_chains:
        independent = {
            finding_id
            for finding_id in chain.supporting_finding_ids
            if finding_id in by_id
            and by_id[finding_id].objective
            and not by_id[finding_id].food_sensitivity
        }
        if len(independent) < 2:
            violations.append(f"chain_without_two_independent_objective_findings:{chain.chain_id}")
    for hub in decision.intervention_hubs:
        if p0_ids.intersection(hub.supporting_finding_ids):
            violations.append(f"p0_finding_used_as_intervention_target:{hub.system_id}")
        eligible = [
            by_id[finding_id]
            for finding_id in hub.supporting_finding_ids
            if finding_id in by_id
            and by_id[finding_id].objective
            and not by_id[finding_id].food_sensitivity
        ]
        if not eligible:
            violations.append(f"food_sensitivity_only_hub:{hub.system_id}")
    return list(dict.fromkeys(violations))


def _compute_portrait(
    *,
    structured: list[Any],
    confirmed: list[Any],
    abnormal: list[Any],
    objective_evidence: list[str],
    risk_notices: list[str],
    age: int | None,
    medication_count: int,
    current_supplement_count: int,
    recommended_items: list[Any],
    lifestyle_plan: Any | None,
) -> CoreHealthPortraitResult:
    risks = _classify_risks(risk_notices)
    findings = _assess_findings(structured, confirmed, abnormal, objective_evidence)
    decision = CoreHealthPortraitDecision(findings=findings, risks=risks)

    eligible_non_food = [
        item
        for item in findings
        if not item.food_sensitivity and item.intervenable and item.deviation_tier != "P0"
    ]
    eligible_objective_ids = {
        item.finding_id for item in eligible_non_food if item.objective
    }
    if risks.p0_referral and len(eligible_objective_ids) < 2:
        return _referral_only_result(decision)

    registry = _load_registry()
    chains = _build_chains(registry, findings, structured)
    if not chains:
        return _degraded_result(
            "现有证据尚不足以形成经过白名单验证的跨系统机制主线。",
            decision=decision,
            violations=["no_whitelisted_chain_with_independent_objective_support"],
        )

    max_hubs = 1 if (
        len(chains) == 1
        or (age is not None and age >= 65)
        or medication_count >= 5
        or current_supplement_count >= 5
    ) else 2
    hubs = _select_hubs(chains, findings, registry, max_hubs=max_hubs)
    if not hubs:
        if risks.p0_referral:
            decision.mechanism_chains = chains
            return _referral_only_result(decision)
        return _degraded_result(
            "机制主线已有初步证据，但尚无可安全聚焦的首月干预枢纽。",
            decision=decision.model_copy(update={"mechanism_chains": chains}),
            violations=["no_intervenable_hub"],
        )

    selected_chain = _choose_closure_chain(chains, hubs)
    steps = _build_steps(
        selected_chain,
        registry,
        recommended_items=recommended_items,
        lifestyle_plan=lifestyle_plan,
    )
    decision = decision.model_copy(
        update={
            "mechanism_chains": chains[:3],
            "intervention_hubs": hubs[:max_hubs],
            "intervention_steps": steps,
            "steady_state_axis": selected_chain.axis_name,
        }
    )
    text = _render_ready(decision)
    return CoreHealthPortraitResult(
        text=text,
        status="ready",
        manual_review_required=bool(risks.p0_referral or risks.review_required),
        decision=decision,
        rule_version=HEALTH_PORTRAIT_RULE_VERSION,
    )


def _load_registry() -> dict[str, Any]:
    with _REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload.get("chains"), list):
        raise ValueError("health axis registry has no chains")
    return payload


def _assess_findings(
    structured: list[Any],
    confirmed: list[Any],
    abnormal: list[Any],
    objective_evidence: list[str],
) -> list[HealthPortraitFinding]:
    source_items = [*confirmed, *abnormal]
    trend_by_object_id = _derive_trends(source_items)
    system_ids_by_finding: dict[str, list[str]] = defaultdict(list)
    for item in structured:
        system_id = str(getattr(item, "system_id", "") or "")
        for finding_id in getattr(item, "finding_ids", []) or []:
            if system_id and system_id not in system_ids_by_finding[str(finding_id)]:
                system_ids_by_finding[str(finding_id)].append(system_id)

    assessed: list[HealthPortraitFinding] = []
    for source_type, items in (("confirmed", confirmed), ("abnormal", abnormal)):
        for index, item in enumerate(items):
            finding_id = str(
                getattr(item, "finding_id", "")
                or getattr(item, "id", "")
                or f"{source_type}_{index}"
            )
            text = _item_text(item)
            food = _is_food_sensitivity_text(text)
            evidence_level = _evidence_level(item, text, source_type)
            tier = _deviation_tier(item, text)
            systems = list(
                dict.fromkeys(
                    [
                        *(getattr(item, "system_ids", []) or []),
                        *system_ids_by_finding.get(finding_id, []),
                    ]
                )
            )
            if not systems:
                systems = classify_text_to_system_ids(text)
            objective = evidence_level in {"objective_lab", "imaging_pathology"}
            assessed.append(
                HealthPortraitFinding(
                    finding_id=finding_id,
                    name=_clean_text(
                        getattr(item, "finding_name", "")
                        or getattr(item, "name", "")
                        or text
                    )[:120],
                    system_ids=systems,
                    evidence_level=evidence_level,
                    display_value=_display_value(item, evidence_level),
                    deviation_tier=tier,
                    trend=trend_by_object_id.get(id(item), "unknown"),
                    food_sensitivity=food,
                    intervenable=not any(term in text.lower() for term in _NON_INTERVENABLE_TERMS),
                    objective=objective,
                    organ_damage_signal=(
                        objective
                        and (
                            evidence_level == "imaging_pathology"
                            or any(term in text.lower() for term in _ORGAN_DAMAGE_TERMS)
                            or (tier in {"P0", "T2"} and _is_organ_marker(item, text))
                        )
                        and (
                            evidence_level == "imaging_pathology"
                            or bool(_display_value(item, evidence_level))
                        )
                    ),
                )
            )

    # Legacy objective strings are accepted only as low-detail objective evidence, never
    # as a substitute for two findings when they duplicate an existing item.
    existing_signatures = {_compact(item.name) for item in assessed}
    for index, text_value in enumerate(objective_evidence):
        text = _clean_text(text_value)
        if not text or _is_food_sensitivity_text(text):
            continue
        signature = _compact(text)
        if any(signature in existing or existing in signature for existing in existing_signatures if existing):
            continue
        systems = classify_text_to_system_ids(text)
        if not systems:
            continue
        assessed.append(
            HealthPortraitFinding(
                finding_id=f"legacy_objective_{index}",
                name=text[:120],
                system_ids=systems,
                evidence_level="objective_lab",
                deviation_tier="T1",
                objective=True,
                organ_damage_signal=False,
            )
        )
    return _merge_assessed_findings(assessed)


def _derive_trends(items: list[Any]) -> dict[int, str]:
    """Infer direction only from comparable, timestamped repeated measurements."""
    grouped: dict[str, list[tuple[datetime, float, Any]]] = defaultdict(list)
    for item in items:
        observed_at = getattr(item, "observed_at", None)
        value = _numeric_value(item)
        marker = str(
            getattr(item, "marker_code", "")
            or getattr(item, "marker_code_candidate", "")
            or getattr(item, "finding_code", "")
            or getattr(item, "name", "")
            or getattr(item, "finding_name", "")
            or ""
        ).strip().lower()
        timestamp = _parse_observed_at(observed_at)
        if not marker or timestamp is None or value is None:
            continue
        grouped[marker].append((timestamp, value, item))

    trends: dict[int, str] = {}
    for measurements in grouped.values():
        if len(measurements) < 2:
            continue
        measurements.sort(key=lambda value: value[0])
        _, previous, _ = measurements[-2]
        _, latest, latest_item = measurements[-1]
        tolerance = max(abs(previous) * 0.02, 1e-9)
        if abs(latest - previous) <= tolerance:
            trends[id(latest_item)] = "stable"
            continue
        flag = str(getattr(latest_item, "abnormal_flag", "") or "").lower()
        if flag == "low":
            trends[id(latest_item)] = "improving" if latest > previous else "worsening"
        else:
            trends[id(latest_item)] = "worsening" if latest > previous else "improving"
    return trends


def _parse_observed_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _merge_assessed_findings(
    findings: list[HealthPortraitFinding],
) -> list[HealthPortraitFinding]:
    evidence_rank = {
        "patient_reported": 0,
        "symptom_cluster": 1,
        "imaging_pathology": 2,
        "objective_lab": 3,
    }
    tier_rank = {"unknown": 0, "T1": 1, "T2": 2, "P0": 3}
    merged: dict[str, HealthPortraitFinding] = {}
    for item in findings:
        current = merged.get(item.finding_id)
        if current is None:
            merged[item.finding_id] = item
            continue
        stronger_evidence = max(
            (current.evidence_level, item.evidence_level),
            key=lambda value: evidence_rank[value],
        )
        stronger_tier = max(
            (current.deviation_tier, item.deviation_tier),
            key=lambda value: tier_rank[value],
        )
        merged[item.finding_id] = current.model_copy(
            update={
                "name": item.name if len(item.name) > len(current.name) else current.name,
                "system_ids": list(dict.fromkeys([*current.system_ids, *item.system_ids])),
                "evidence_level": stronger_evidence,
                "display_value": item.display_value or current.display_value,
                "deviation_tier": stronger_tier,
                "food_sensitivity": current.food_sensitivity or item.food_sensitivity,
                "intervenable": current.intervenable and item.intervenable,
                "objective": current.objective or item.objective,
                "organ_damage_signal": current.organ_damage_signal or item.organ_damage_signal,
            }
        )
    return list(merged.values())


def _build_chains(
    registry: dict[str, Any],
    findings: list[HealthPortraitFinding],
    structured: list[Any],
) -> list[HealthMechanismChain]:
    present_systems = {
        system_id for item in findings for system_id in item.system_ids if system_id in SYSTEM_NAMES
    }
    present_systems.update(
        str(getattr(item, "system_id", "") or "") for item in structured
    )
    candidates: list[HealthMechanismChain] = []
    for config in registry["chains"]:
        path = [str(item) for item in config.get("system_path", [])]
        required_terms = [
            str(item).lower() for item in config.get("required_terms_any", []) if str(item).strip()
        ]
        finding_text = " ".join(item.name for item in findings).lower()
        if required_terms and not any(term in finding_text for term in required_terms):
            continue
        matched = [system_id for system_id in path if system_id in present_systems]
        if len(matched) < int(config.get("minimum_present_systems", len(path))):
            continue
        required_present = set(config.get("required_present_systems", []))
        if not required_present.issubset(present_systems):
            continue
        required_objective = set(config.get("required_objective_systems", []))
        objective_ids = {
            item.finding_id
            for item in findings
            if item.objective
            and not item.food_sensitivity
            and item.deviation_tier != "P0"
            and set(item.system_ids).intersection(path)
        }
        objective_systems = {
            system_id
            for item in findings
            if item.objective and not item.food_sensitivity and item.deviation_tier != "P0"
            for system_id in item.system_ids
        }
        if len(objective_ids) < 2 or not required_objective.issubset(objective_systems):
            continue
        auxiliary_food_ids = {
            item.finding_id
            for item in findings
            if item.food_sensitivity and set(item.system_ids).intersection(path)
        }
        display_lookup = dict(zip(path, config.get("display_path", path)))
        candidates.append(
            HealthMechanismChain(
                chain_id=str(config["id"]),
                axis_name=str(config["axis_name"]),
                system_path=matched,
                display_path=[str(display_lookup.get(system_id, SYSTEM_NAMES.get(system_id, system_id))) for system_id in matched],
                supporting_finding_ids=sorted(objective_ids),
                auxiliary_food_sensitivity_ids=sorted(auxiliary_food_ids),
                objective_support_count=len(objective_ids),
            )
        )
    candidates.sort(
        key=lambda item: (-item.objective_support_count, -len(item.system_path), item.chain_id)
    )
    return candidates[:3]


def _select_hubs(
    chains: list[HealthMechanismChain],
    findings: list[HealthPortraitFinding],
    registry: dict[str, Any],
    *,
    max_hubs: int,
) -> list[HealthInterventionHub]:
    configs = {str(item["id"]): item for item in registry["chains"]}
    intersection_count: dict[str, int] = defaultdict(int)
    upstream_score: dict[str, int] = defaultdict(int)
    downstream_reach: dict[str, int] = defaultdict(int)
    for chain in chains:
        for index, system_id in enumerate(chain.system_path):
            intersection_count[system_id] += 1
            upstream_score[system_id] += len(chain.system_path) - index
            downstream_reach[system_id] += max(0, len(chain.system_path) - index - 1)

    hubs: list[HealthInterventionHub] = []
    for system_id in intersection_count:
        support = [
            item
            for item in findings
            if system_id in item.system_ids
            and not item.food_sensitivity
            and item.intervenable
            and item.deviation_tier != "P0"
            and item.objective
        ]
        if not support:
            continue
        label = next(
            (
                str(configs[chain.chain_id].get("target_labels", {}).get(system_id))
                for chain in chains
                if system_id in chain.system_path
                and configs[chain.chain_id].get("target_labels", {}).get(system_id)
            ),
            f"{SYSTEM_NAMES.get(system_id, system_id)}管理",
        )
        evidence_score = max(_evidence_score(item) for item in support)
        hubs.append(
            HealthInterventionHub(
                system_id=system_id,
                label=label,
                supporting_finding_ids=[item.finding_id for item in support],
                chain_intersection_count=intersection_count[system_id],
                upstream_score=upstream_score[system_id],
                evidence_score=evidence_score,
                downstream_reach=downstream_reach[system_id],
            )
        )
    hubs.sort(
        key=lambda item: (
            -item.chain_intersection_count,
            -item.upstream_score,
            -item.evidence_score,
            -item.downstream_reach,
            item.system_id,
        )
    )
    return hubs[:max_hubs]


def _choose_closure_chain(
    chains: list[HealthMechanismChain],
    hubs: list[HealthInterventionHub],
) -> HealthMechanismChain:
    hub_ids = {item.system_id for item in hubs}
    return sorted(
        chains,
        key=lambda item: (
            -len(hub_ids.intersection(item.system_path)),
            -item.objective_support_count,
            item.chain_id,
        ),
    )[0]


def _build_steps(
    chain: HealthMechanismChain,
    registry: dict[str, Any],
    *,
    recommended_items: list[Any],
    lifestyle_plan: Any | None,
) -> list[HealthInterventionStep]:
    config = next(item for item in registry["chains"] if item["id"] == chain.chain_id)
    recommendation_ids = [
        str(getattr(item, "sku_id", "") or "")
        for item in recommended_items
        if set(getattr(item, "covered_system_ids", []) or []).intersection(chain.system_path)
    ]
    lifestyle_ids: list[str] = []
    for section in getattr(lifestyle_plan, "sections", []) or []:
        for action in getattr(section, "actions", []) or []:
            action_id = str(getattr(action, "action_id", "") or "")
            if action_id:
                lifestyle_ids.append(action_id)
    steps = [str(item) for item in config.get("steps", [])][:6]
    if len(steps) < 3:
        raise ValueError(f"chain {chain.chain_id} has fewer than three steps")
    return [
        HealthInterventionStep(
            order=index,
            label=label,
            target_system_ids=list(chain.system_path),
            linked_recommendation_ids=recommendation_ids,
            linked_lifestyle_action_ids=lifestyle_ids,
        )
        for index, label in enumerate(steps, start=1)
    ]


def _render_ready(decision: CoreHealthPortraitDecision) -> str:
    chain_labels = ["—".join(chain.display_path) for chain in decision.mechanism_chains]
    count = _NUMBER_WORDS[len(chain_labels)]
    first = f"存在「{'」「'.join(chain_labels)}」{count}条主线的交叉联动"
    organ_signals = [
        f"{item.name} {item.display_value}".strip()
        for item in decision.findings
        if item.organ_damage_signal
    ]
    if len(organ_signals) >= 3:
        first += f"，已出现靶器官损害信号（{'、'.join(organ_signals[:5])}）"
    if decision.risks.p0_referral:
        first += "，同时存在需优先转诊评估的安全红旗"
    first += "。"
    hub_labels = [item.label for item in decision.intervention_hubs]
    second = f"首月干预严格聚焦「{'」与「'.join(hub_labels)}」核心，优先处理机制上游与交叉枢纽。"
    step_labels = [item.label for item in decision.intervention_steps]
    third = (
        f"通过「{'—'.join(step_labels)}」{_NUMBER_WORDS[len(step_labels)]}步闭环，"
        f"重建{decision.steady_state_axis}功能稳态。"
    )
    return first + second + third


def _referral_only_result(decision: CoreHealthPortraitDecision) -> CoreHealthPortraitResult:
    text = (
        "当前存在需优先转诊或紧急医学评估的安全红旗，暂不将其包装为营养干预主线。"
        "首月先完成相应专科评估与风险处置，在安全边界明确前不设营养干预枢纽。"
        "报告其余部分继续生成并保留已核实信息，后续依据复评结果重建可干预的功能稳态路径。"
    )
    return CoreHealthPortraitResult(
        text=text,
        status="referral_only",
        manual_review_required=True,
        decision=decision,
        rule_version=HEALTH_PORTRAIT_RULE_VERSION,
    )


def _degraded_result(
    reason: str,
    *,
    decision: CoreHealthPortraitDecision | None = None,
    violations: list[str] | None = None,
) -> CoreHealthPortraitResult:
    text = (
        f"{reason}"
        "首月先聚焦资料补全、必要复查与安全边界确认，不依据单项严重度或食物敏感结果强设干预枢纽。"
        "报告其余部分继续生成并保留可执行建议，待独立客观证据补足后再形成有方向的机制闭环。"
    )
    return CoreHealthPortraitResult(
        text=text,
        status="degraded",
        manual_review_required=True,
        validation_violations=list(violations or []),
        decision=decision or CoreHealthPortraitDecision(),
        rule_version=HEALTH_PORTRAIT_RULE_VERSION,
    )


def _classify_risks(risk_notices: list[str]) -> HealthPortraitRiskAssessment:
    p0: list[str] = []
    review: list[str] = []
    dose: list[str] = []
    for value in risk_notices:
        text = _clean_text(value)
        if not text:
            continue
        lowered = text.lower()
        if any(term in lowered for term in _P0_TERMS):
            p0.append(text)
            continue
        review.append(text)
        if any(term in lowered for term in _DOSE_CAUTION_TERMS):
            dose.append(text)
    return HealthPortraitRiskAssessment(
        p0_referral=list(dict.fromkeys(p0)),
        review_required=list(dict.fromkeys(review)),
        dose_caution=list(dict.fromkeys(dose)),
    )


def _evidence_level(item: Any, text: str, source_type: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in _IMAGING_PATHOLOGY_TERMS):
        return "imaging_pathology"
    evidence_class = str(getattr(item, "evidence_class", "") or "").lower()
    if "lab_abnormal" in evidence_class or source_type == "abnormal":
        return "objective_lab"
    if "clinical_confirmed" in evidence_class:
        return "imaging_pathology" if any(term in lowered for term in _IMAGING_PATHOLOGY_TERMS) else "objective_lab"
    if "symptom" in evidence_class or any(term in lowered for term in _SYMPTOM_TERMS):
        return "symptom_cluster"
    return "patient_reported"


def _deviation_tier(item: Any, text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in _P0_TERMS):
        return "P0"
    marker = str(
        getattr(item, "marker_code", "")
        or getattr(item, "marker_code_candidate", "")
        or getattr(item, "finding_code", "")
        or ""
    ).lower()
    value = _numeric_value(item)
    unit = _compact(getattr(item, "unit", ""))
    if value is not None:
        if "fasting_glucose" in marker or marker in {"fpg", "glu"}:
            if ("mgdl" in unit and value >= 126) or ("mgdl" not in unit and value >= 7.0):
                return "T2"
        if "hba1c" in marker and (not unit or "%" in str(getattr(item, "unit", ""))) and value >= 6.5:
            return "T2"
        if ("hs_crp" in marker or "hscrp" in marker) and (not unit or "mgl" in unit) and value >= 10:
            return "T2"
        if marker in {"alt", "alanine_aminotransferase"} and (not unit or "ul" in unit) and value >= 120:
            return "T2"
    if any(term in lowered for term in _T2_TEXT_TERMS):
        return "T2"
    flag = str(getattr(item, "abnormal_flag", "") or "").lower()
    if flag in {"high", "low", "positive", "abnormal"} or any(
        term in lowered for term in ("升高", "降低", "阳性", "异常")
    ):
        return "T1"
    return "unknown"


def _numeric_value(item: Any) -> float | None:
    for attribute in ("normalized_value", "value", "raw_value", "result_text"):
        raw = getattr(item, attribute, None)
        if isinstance(raw, (int, float)):
            return float(raw)
        match = re.search(r"-?\d+(?:\.\d+)?", str(raw or ""))
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                pass
    return None


def _is_organ_marker(item: Any, text: str) -> bool:
    marker = str(
        getattr(item, "marker_code", "")
        or getattr(item, "marker_code_candidate", "")
        or ""
    ).lower()
    return marker in {"alt", "ast", "ggt", "creatinine", "egfr", "troponin", "ck_mb"} or any(
        term in text.lower() for term in _ORGAN_DAMAGE_TERMS
    )


def _evidence_score(item: HealthPortraitFinding) -> int:
    level = {"objective_lab": 4, "imaging_pathology": 3, "symptom_cluster": 2, "patient_reported": 1}[item.evidence_level]
    tier = {"T2": 3, "T1": 2, "unknown": 1, "P0": 0}[item.deviation_tier]
    return level * 10 + tier


def _item_text(item: Any) -> str:
    source_span = getattr(item, "source_span", None)
    values = (
        getattr(item, "finding_name", ""),
        getattr(item, "name", ""),
        getattr(item, "result_text", ""),
        getattr(item, "interpretation", ""),
        getattr(item, "source_file_name", ""),
        getattr(item, "source_text", ""),
        getattr(source_span, "file_name", ""),
        getattr(source_span, "snippet", ""),
    )
    return " ".join(_clean_text(value) for value in values if _clean_text(value))


def _display_value(item: Any, evidence_level: str) -> str | None:
    if evidence_level == "imaging_pathology":
        result = _clean_text(
            getattr(item, "result_text", "")
            or getattr(item, "interpretation", "")
        )
        return result[:100] or None
    raw = _clean_text(
        getattr(item, "result_text", "")
        or getattr(item, "raw_value", "")
    )
    if not raw or re.search(r"\d", raw) is None:
        return None
    unit = _clean_text(getattr(item, "unit", ""))
    if not unit and re.fullmatch(r"[-+]?\d+(?:\.\d+)?", raw):
        return None
    if unit and unit.lower() not in raw.lower():
        raw = f"{raw} {unit}"
    return raw[:80]


def _is_food_sensitivity_text(*values: Any) -> bool:
    normalized = unicodedata.normalize("NFKC", " ".join(str(value or "") for value in values)).lower()
    compact = re.sub(r"[\s_\-（）()\[\]【】]+", "", normalized)
    return any(term in compact for term in _FOOD_SENSITIVITY_TERMS) or (
        "igg" in compact and any(term in compact for term in ("食物", "过敏", "敏感", "food"))
    )


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ，,；;。")


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", str(value or "")).lower())
