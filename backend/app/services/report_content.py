from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from app.services.body_systems import BODY_SYSTEMS, SYSTEM_NAMES, classify_text_to_system_ids


@dataclass(frozen=True)
class ReportAbnormalItem:
    item_id: str
    name: str
    result: str
    status_label: str
    system_ids: tuple[str, ...] = ()
    search_text: str = ""


_SYSTEM_ORDER = {system_id: index for index, (system_id, _) in enumerate(BODY_SYSTEMS)}
_PLAN_SUMMARY_MAX_CHARS = 100
_PLAN_SUMMARY_MAX_PROBLEMS = 3
_PLAN_SUMMARY_GENERIC = (
    "本方案针对当前已确认的重点健康问题，重点配合生活方式调整、必要复查及医生评估。"
)
_PLAN_SUMMARY_UNCOVERED_DIRECTION = "配合生活方式调整、复查与医生评估"
_SUMMARY_SUPPORT_DIRECTIONS = {
    "digestive_gut": "修复胃肠消化及黏膜屏障功能",
    "liver_detox": "改善肝胆代谢并加强抗氧化支持",
    "immune_inflammation": "调节免疫并抗氧化抗炎",
    "endocrine_metabolic": "稳定内分泌及整体代谢",
    "cardiovascular": "改善血脂循环并管理心血管风险",
    "respiratory": "改善呼吸功能并配合专科随访",
    "neuro_sleep": "调节睡眠压力及神经功能",
    "bone_muscle": "支持骨代谢、肌肉及活动功能",
    "urinary_renal": "保护肾脏泌尿功能并配合安全复查",
    "reproductive_breast": "调节生殖激素并配合专科随访",
    "skin_mucosa": "修复皮肤及黏膜屏障功能",
}
_SUMMARY_PRIORITY_ORDER = {"最高优先级": 0, "优先级高": 1, "中度关注": 2}
_HEALTH_PORTRAIT_MAX_MAINLINES = 5
_HEALTH_PORTRAIT_MAX_EVIDENCE = 3
_HEALTH_EVIDENCE_ORDER = {
    "clinical_confirmed": 0,
    "lab_abnormal": 1,
    "symptom": 2,
    "exposure": 3,
    "genetic_risk": 4,
    "follow_up_only": 5,
}
_HEALTH_SYSTEM_FALLBACKS = {
    "digestive_gut": "胃肠消化与肠道功能异常",
    "liver_detox": "肝胆代谢功能异常",
    "immune_inflammation": "免疫炎症调节异常",
    "endocrine_metabolic": "内分泌与代谢异常",
    "cardiovascular": "心血管与循环代谢异常",
    "respiratory": "呼吸系统功能异常",
    "neuro_sleep": "神经、情绪与睡眠节律异常",
    "bone_muscle": "骨骼肌肉与活动功能异常",
    "urinary_renal": "肾脏与泌尿代谢异常",
    "reproductive_breast": "生殖激素与乳腺相关异常",
    "skin_mucosa": "皮肤与黏膜屏障异常",
}
_HEALTH_INTERVENTION_TARGETS = {
    "digestive_gut": ("肠道微生态与屏障修复", "饮食与肠道节律调整"),
    "liver_detox": ("肝胆代谢减负", "饮食、作息与酒精暴露管理"),
    "immune_inflammation": ("免疫炎症调节", "饮食、睡眠与炎症管理"),
    "endocrine_metabolic": ("血糖与内分泌代谢稳定", "饮食与餐后活动调整"),
    "cardiovascular": ("心血管风险管理", "饮食与规律运动调整"),
    "respiratory": ("呼吸功能管理", "作息、活动与呼吸风险管理"),
    "neuro_sleep": ("睡眠压力与神经调节", "睡眠、压力与日间节律调整"),
    "bone_muscle": ("骨骼肌肉功能恢复", "活动、康复与蛋白质摄入调整"),
    "urinary_renal": ("肾脏减负", "饮食、饮水与代谢负荷管理"),
    "reproductive_breast": ("生殖激素节律管理", "作息、压力与体重管理"),
    "skin_mucosa": ("皮肤黏膜屏障修复", "饮食、睡眠与接触因素管理"),
}
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


def group_abnormal_items(
    items: Iterable[ReportAbnormalItem],
    structured_system_findings: Iterable[Any],
) -> list[str]:
    # Upstream validation has already ordered systems by evidence certainty.
    # Preserve that order instead of moving questionnaire-only systems ahead
    # of objective findings based on score alone.
    structured = list(structured_system_findings or [])
    priority_system_ids = [
        str(getattr(item, "system_id", "") or "")
        for item in structured
        if str(getattr(item, "system_id", "") or "") in SYSTEM_NAMES
    ]
    priority_rank = {system_id: index for index, system_id in enumerate(priority_system_ids)}
    structured_systems_by_finding: dict[str, list[str]] = {}
    for finding in structured:
        system_id = str(getattr(finding, "system_id", "") or "")
        if system_id not in SYSTEM_NAMES:
            continue
        for finding_id in getattr(finding, "finding_ids", []) or []:
            structured_systems_by_finding.setdefault(str(finding_id), []).append(system_id)

    groups: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for item in items:
        name = _clean_text(item.name)
        result = _clean_text(item.result) or "异常"
        if not name:
            continue
        signature = (_compact(name), _compact(result))
        if signature in seen:
            continue
        seen.add(signature)

        candidate_systems = [
            system_id
            for system_id in structured_systems_by_finding.get(item.item_id, [])
            if system_id in SYSTEM_NAMES
        ]
        if not candidate_systems:
            candidate_systems = [
                system_id for system_id in item.system_ids if system_id in SYSTEM_NAMES
            ]
        if not candidate_systems:
            candidate_systems = classify_text_to_system_ids(
                item.name,
                item.result,
                item.search_text,
            )
        system_id = _choose_primary_system(candidate_systems, priority_rank)
        group_key = system_id or "__other__"
        status = _clean_text(item.status_label) or "异常"
        detail = "" if _is_redundant_abnormal_result(name, result) else f"：{result}"
        groups.setdefault(group_key, []).append(f"{name}{detail}（{status}）")

    ordered_systems = sorted(
        (system_id for system_id in groups if system_id != "__other__"),
        key=lambda system_id: (
            priority_rank.get(system_id, len(priority_rank) + _SYSTEM_ORDER.get(system_id, 999)),
            _SYSTEM_ORDER.get(system_id, 999),
        ),
    )
    if "__other__" in groups:
        ordered_systems.append("__other__")

    return [
        f"{SYSTEM_NAMES.get(system_id, '其他需关注')}：" + "；".join(groups[system_id])
        for system_id in ordered_systems
    ]


def _is_redundant_abnormal_result(name: str, result: str) -> bool:
    """Hide generic or name-repeating result text in patient-facing summaries."""
    compact_result = _compact(result)
    if not compact_result:
        return True
    if compact_result == _compact(name):
        return True
    return compact_result in {
        _compact("异常"),
        _compact("已发现异常"),
        _compact("发现异常"),
    }


def _is_food_sensitivity_text(*values: Any) -> bool:
    normalized = unicodedata.normalize(
        "NFKC",
        " ".join(str(value or "") for value in values),
    ).lower()
    compact = re.sub(r"[\s_\-（）()\[\]【】]+", "", normalized)
    if any(term in compact for term in _FOOD_SENSITIVITY_TERMS):
        return True
    return "igg" in compact and any(
        term in compact for term in ("食物", "过敏", "敏感", "food")
    )


def _is_food_sensitivity_item(item: Any) -> bool:
    source_span = getattr(item, "source_span", None)
    return _is_food_sensitivity_text(
        getattr(item, "finding_name", ""),
        getattr(item, "name", ""),
        getattr(item, "finding_code", ""),
        getattr(item, "marker_code", ""),
        getattr(item, "source_file_name", ""),
        getattr(item, "source_text", ""),
        getattr(item, "result_text", ""),
        getattr(source_span, "file_name", ""),
    )


def _health_item_id(item: Any) -> str:
    return str(
        getattr(item, "finding_id", "")
        or getattr(item, "id", "")
        or ""
    )


def _is_food_sensitivity_only_structured_finding(
    finding: Any,
    *,
    excluded_finding_ids: set[str],
    eligible_finding_ids: set[str],
) -> bool:
    finding_ids = {str(value) for value in getattr(finding, "finding_ids", []) or []}
    if finding_ids and finding_ids.issubset(excluded_finding_ids):
        return True
    if finding_ids.intersection(eligible_finding_ids):
        return False
    return _is_food_sensitivity_text(
        getattr(finding, "title", ""),
        getattr(finding, "summary", ""),
        getattr(finding, "body", ""),
    )


def build_core_health_portrait(
    structured_system_findings: Iterable[Any],
    confirmed_findings: Iterable[Any] | None = None,
    abnormal_findings: Iterable[Any] | None = None,
    objective_evidence_items: Iterable[str] | None = None,
    risk_notices: Iterable[str] | None = None,
) -> list[str]:
    """Build one evidence-grounded, three-sentence patient-facing conclusion."""
    raw_confirmed = list(confirmed_findings or [])
    raw_abnormal = list(abnormal_findings or [])
    excluded_finding_ids = {
        item_id
        for item in [*raw_confirmed, *raw_abnormal]
        if _is_food_sensitivity_item(item)
        and (item_id := _health_item_id(item))
    }
    eligible_finding_ids = {
        item_id
        for item in [*raw_confirmed, *raw_abnormal]
        if not _is_food_sensitivity_item(item)
        and (item_id := _health_item_id(item))
    }
    confirmed = [item for item in raw_confirmed if not _is_food_sensitivity_item(item)]
    abnormal = [item for item in raw_abnormal if not _is_food_sensitivity_item(item)]
    structured = _ordered_health_findings(
        list(structured_system_findings or []),
        confirmed_findings=confirmed,
    )
    if not structured:
        return [
            "当前资料尚不足以形成明确的交叉主线。"
            "现有证据不足以确定单一核心干预枢纽。"
            "首月先完善必要检查与症状记录，生活方式基础调整优先于营养素补充。"
        ]

    selected: list[tuple[Any, str, str]] = []
    selected_system_ids: set[str] = set()
    selected_label_signatures: set[str] = set()
    for finding in structured:
        system_id = str(getattr(finding, "system_id", "") or "")
        if not system_id or system_id in selected_system_ids:
            continue
        if _is_food_sensitivity_only_structured_finding(
            finding,
            excluded_finding_ids=excluded_finding_ids,
            eligible_finding_ids=eligible_finding_ids,
        ):
            continue
        label = _health_mainline_label(
            finding,
            system_id=system_id,
            confirmed_findings=confirmed,
            abnormal_findings=abnormal,
        )
        label_signature = _compact(label)
        if label_signature in selected_label_signatures:
            label = _HEALTH_SYSTEM_FALLBACKS.get(
                system_id,
                f"{SYSTEM_NAMES.get(system_id, '相关身体系统')}功能异常",
            )
            label_signature = _compact(label)
        if not label_signature or label_signature in selected_label_signatures:
            continue
        selected.append((finding, label, system_id))
        selected_system_ids.add(system_id)
        selected_label_signatures.add(label_signature)
        if len(selected) >= _HEALTH_PORTRAIT_MAX_MAINLINES:
            break

    if not selected:
        return [
            "当前资料尚不足以形成明确的交叉主线。"
            "现有证据不足以确定单一核心干预枢纽。"
            "首月先完善必要检查与症状记录，生活方式基础调整优先于营养素补充。"
        ]

    labels = [label for _, label, _ in selected]
    count_label = "一二三四五"[len(labels) - 1]
    has_risk = any(
        _clean_text(item)
        for item in risk_notices or []
        if not _is_food_sensitivity_text(item)
    ) or _has_explicit_serious_abnormality(abnormal)
    timing_clause = (
        "应优先完成医学评估与风险控制"
        if has_risk
        else "当前是集中开展生活方式干预的重要窗口期"
    )
    first_sentence = (
        f"存在「{'—'.join(labels)}」{count_label}条主线的交叉联动，{timing_clause}。"
    )

    evidence = _health_objective_evidence(
        abnormal,
        selected=selected,
        fallback_items=list(objective_evidence_items or []),
    )
    evidence_text = "/".join(evidence) if evidence else "现有临床结论与症状证据"
    primary_label = labels[0]
    if len(labels) > 1:
        related = "、".join(labels[1:3])
        second_sentence = (
            f"{evidence_text}提示「{primary_label}」是当前核心干预枢纽，"
            f"并可能牵动{related}。"
        )
    else:
        second_sentence = f"{evidence_text}提示「{primary_label}」是当前核心干预重点。"

    primary_system_id = selected[0][2]
    target, lifestyle_priority = _HEALTH_INTERVENTION_TARGETS.get(
        primary_system_id,
        ("整体功能恢复", "饮食、睡眠与活动节律调整"),
    )
    third_sentence = (
        f"首月以「{target}」为核心，{lifestyle_priority}优先于营养素补充。"
    )
    return [first_sentence + second_sentence + third_sentence]


def _ordered_health_findings(
    structured: list[Any],
    *,
    confirmed_findings: list[Any],
) -> list[Any]:
    indexed = list(enumerate(structured))
    indexed.sort(
        key=lambda pair: (
            _health_finding_evidence_rank(pair[1], confirmed_findings),
            _SUMMARY_PRIORITY_ORDER.get(
                str(getattr(pair[1], "priority_level", "") or ""),
                len(_SUMMARY_PRIORITY_ORDER),
            ),
            -float(
                getattr(pair[1], "priority_score", None)
                or getattr(pair[1], "score", 0.0)
                or 0.0
            ),
            _SYSTEM_ORDER.get(str(getattr(pair[1], "system_id", "") or ""), 999),
            pair[0],
        )
    )
    return [item for _, item in indexed]


def _health_finding_evidence_rank(finding: Any, confirmed_findings: list[Any]) -> int:
    finding_ids = {str(value) for value in getattr(finding, "finding_ids", []) or []}
    system_id = str(getattr(finding, "system_id", "") or "")
    ranks: list[int] = []
    for item in confirmed_findings:
        item_id = str(getattr(item, "finding_id", "") or "")
        item_system_ids = {
            str(value) for value in getattr(item, "system_ids", []) or [] if str(value)
        }
        if finding_ids:
            if item_id not in finding_ids:
                continue
        elif system_id not in item_system_ids:
            continue
        evidence_class = _enum_text(getattr(item, "evidence_class", ""))
        ranks.append(_HEALTH_EVIDENCE_ORDER.get(evidence_class, 99))
    return min(ranks, default=99)


def _health_mainline_label(
    finding: Any,
    *,
    system_id: str,
    confirmed_findings: list[Any],
    abnormal_findings: list[Any],
) -> str:
    finding_ids = {str(value) for value in getattr(finding, "finding_ids", []) or []}
    candidates: list[tuple[int, str]] = []
    for item in confirmed_findings:
        item_id = str(getattr(item, "finding_id", "") or "")
        item_system_ids = {
            str(value) for value in getattr(item, "system_ids", []) or [] if str(value)
        }
        if finding_ids:
            if item_id not in finding_ids:
                continue
        elif system_id not in item_system_ids:
            continue
        name = _safe_problem_name(str(getattr(item, "finding_name", "") or ""))
        if not name:
            continue
        evidence_class = _enum_text(getattr(item, "evidence_class", ""))
        candidates.append((_HEALTH_EVIDENCE_ORDER.get(evidence_class, 99), name))
    supporting_names: list[str] = []
    for item in abnormal_findings:
        item_id = str(getattr(item, "id", "") or "")
        if finding_ids and item_id not in finding_ids:
            continue
        name = _safe_problem_name(str(getattr(item, "name", "") or ""))
        if name and _compact(name) not in {_compact(existing) for existing in supporting_names}:
            supporting_names.append(name)

    names: list[str] = []
    for _, name in sorted(candidates, key=lambda value: value[0]):
        if _compact(name) not in {_compact(existing) for existing in names}:
            names.append(name)
    if not names:
        return _HEALTH_SYSTEM_FALLBACKS.get(
            system_id,
            f"{SYSTEM_NAMES.get(system_id, '相关身体系统')}功能异常",
        )

    primary = names[0]
    if _looks_like_clinical_condition(primary):
        supporting_candidates = [*names[1:], *supporting_names]
        unique_supporting: list[str] = []
        for name in supporting_candidates:
            signature = _compact(name)
            if signature == _compact(primary):
                continue
            if signature in {_compact(value) for value in unique_supporting}:
                continue
            unique_supporting.append(name)
        supporting = "+".join(unique_supporting[:2])
        if supporting:
            return f"{primary}（{supporting}）"
    return primary


def _health_objective_evidence(
    abnormal_findings: list[Any],
    *,
    selected: list[tuple[Any, str, str]],
    fallback_items: list[str],
) -> list[str]:
    finding_rank: dict[str, int] = {}
    for rank, (finding, _, _) in enumerate(selected):
        for finding_id in getattr(finding, "finding_ids", []) or []:
            finding_rank[str(finding_id)] = rank

    candidates: list[tuple[int, str]] = []
    for index, item in enumerate(abnormal_findings):
        if _is_food_sensitivity_item(item):
            continue
        abnormal_flag = _enum_text(getattr(item, "abnormal_flag", ""))
        evidence_class = _enum_text(getattr(item, "evidence_class", ""))
        if abnormal_flag not in {"high", "low", "positive"}:
            continue
        if evidence_class in {"symptom", "exposure", "genetic_risk", "follow_up_only"}:
            continue
        name = _clean_text(getattr(item, "name", ""))
        value = _clean_text(
            getattr(item, "result_text", "")
            or getattr(item, "raw_value", "")
        )
        unit = _clean_text(getattr(item, "unit", ""))
        if unit and unit.lower() not in value.lower():
            value = f"{value}{unit}"
        if abnormal_flag == "high" and "↑" not in value:
            value += "↑"
        elif abnormal_flag == "low" and "↓" not in value:
            value += "↓"
        elif abnormal_flag == "positive" and not value:
            value = "阳性"
        if not name or not value:
            continue
        item_id = str(getattr(item, "id", "") or "")
        candidates.append((finding_rank.get(item_id, len(selected) + index), f"{name}{value}"))

    evidence: list[str] = []
    for _, text in sorted(candidates, key=lambda value: value[0]):
        if _compact(text) not in {_compact(existing) for existing in evidence}:
            evidence.append(text)
        if len(evidence) >= _HEALTH_PORTRAIT_MAX_EVIDENCE:
            return evidence
    for raw_item in fallback_items:
        text = _clean_text(raw_item)
        if _is_food_sensitivity_text(text):
            continue
        if any(term in text for term in ("患者自述", "自述", "主诉", "症状评分", "MSQ")):
            continue
        text = re.sub(r"[（(](?:需关注|阳性/?异常|阳性)[）)]$", "", text).strip()
        text = re.sub(r"\s*[:：]\s*", "", text, count=1)
        if text and _compact(text) not in {_compact(existing) for existing in evidence}:
            evidence.append(text)
        if len(evidence) >= _HEALTH_PORTRAIT_MAX_EVIDENCE:
            break
    return evidence


def _has_explicit_serious_abnormality(abnormal_findings: list[Any]) -> bool:
    serious_terms = ("危急值", "危重", "严重异常", "显著升高", "显著降低", "明显异常", "立即就医", "急诊")
    negated_terms = ("无严重异常", "未见严重异常", "无明显异常", "未见明显异常")
    for item in abnormal_findings:
        if _is_food_sensitivity_item(item):
            continue
        if _enum_text(getattr(item, "abnormal_flag", "")) not in {"high", "low", "positive"}:
            continue
        detail = " ".join(
            _clean_text(getattr(item, field_name, ""))
            for field_name in (
                "interpretation",
                "report_explanation",
                "neutral_interpretation",
                "support_need_text",
            )
        )
        if any(term in detail for term in negated_terms):
            continue
        if any(term in detail for term in serious_terms):
            return True
    return False


def _looks_like_clinical_condition(value: str) -> bool:
    return any(
        term in value
        for term in ("炎", "病", "症", "异常", "硬化", "失衡", "术后", "功能不全", "综合征")
    )


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def build_plan_summary(
    structured_system_findings: Iterable[Any],
    recommended_items: Iterable[Any],
    finding_names_by_id: dict[str, str] | None = None,
    *,
    max_named_problems: int = _PLAN_SUMMARY_MAX_PROBLEMS,
) -> list[str]:
    structured = list(structured_system_findings or [])
    if not structured:
        return []

    problem_limit = max(
        1,
        min(int(max_named_problems), _PLAN_SUMMARY_MAX_PROBLEMS),
    )
    finding_names = finding_names_by_id or {}
    recommendations = list(recommended_items or [])

    pairs: list[tuple[str, str, str]] = []
    seen_problem_names: set[str] = set()
    selected_system_ids: list[str] = []
    for finding in _ordered_summary_findings(structured):
        system_id = str(getattr(finding, "system_id", "") or "")
        if not system_id or system_id in selected_system_ids:
            continue
        names = [
            _safe_problem_name(finding_names.get(str(finding_id), ""))
            for finding_id in getattr(finding, "finding_ids", []) or []
        ]
        names = list(dict.fromkeys(name for name in names if name))
        problem_name = names[0] if names else _system_problem_name(system_id)
        problem_signature = _compact(problem_name)
        if not problem_signature or problem_signature in seen_problem_names:
            continue
        linked = _has_linked_recommendation(
            finding,
            system_id=system_id,
            recommendations=recommendations,
        )
        direction = (
            _summary_support_direction(system_id, problem_name)
            if linked
            else _PLAN_SUMMARY_UNCOVERED_DIRECTION
        )
        selected_system_ids.append(system_id)
        seen_problem_names.add(problem_signature)
        pairs.append((problem_name, direction, system_id))
        if len(pairs) >= problem_limit:
            break

    if not pairs:
        return []

    return [_fit_plan_summary(pairs)]


def normalize_plan_summary_items(items: Iterable[Any]) -> list[str]:
    """Collapse legacy summary content into one complete sentence within the hard limit."""
    raw_items = [items] if isinstance(items, str) else list(items or [])
    cleaned_items = [
        re.sub(r"^[\-•]\s*", "", _clean_text(str(item)))
        for item in raw_items
    ]
    cleaned_items = [item for item in cleaned_items if item]
    if not cleaned_items:
        return []
    summary = "；".join(cleaned_items).rstrip("；。！？") + "。"
    if len(summary) <= _PLAN_SUMMARY_MAX_CHARS:
        return [summary]
    return [_PLAN_SUMMARY_GENERIC]


def _ordered_summary_findings(structured: list[Any]) -> list[Any]:
    indexed = list(enumerate(structured))
    indexed.sort(
        key=lambda pair: (
            _SUMMARY_PRIORITY_ORDER.get(
                str(getattr(pair[1], "priority_level", "") or ""),
                len(_SUMMARY_PRIORITY_ORDER),
            ),
            -float(getattr(pair[1], "priority_score", 0.0) or 0.0),
            pair[0],
        )
    )
    return [item for _, item in indexed]


def _has_linked_recommendation(
    finding: Any,
    *,
    system_id: str,
    recommendations: list[Any],
) -> bool:
    finding_ids = {str(item) for item in getattr(finding, "finding_ids", []) or []}
    for item in recommendations:
        covered_system_ids = {
            str(value)
            for value in (
                getattr(item, "covered_system_ids", [])
                or [getattr(item, "primary_system_id", "")]
            )
            if str(value)
        }
        if system_id not in covered_system_ids:
            continue
        matched_finding_ids = {
            str(value)
            for value in getattr(item, "matched_finding_ids", []) or []
            if str(value)
        }
        if not finding_ids or not matched_finding_ids or finding_ids.intersection(matched_finding_ids):
            return True
    return False


def _summary_support_direction(system_id: str, problem_name: str) -> str:
    compact_problem = _compact(problem_name).lower()
    if system_id == "endocrine_metabolic":
        if any(term in compact_problem for term in ("血糖", "胰岛素", "糖化血红蛋白")):
            return "稳定血糖代谢"
        if any(term in compact_problem for term in ("甲状腺", "tsh", "ft3", "ft4")):
            return "支持甲状腺及激素代谢稳定"
    return _SUMMARY_SUPPORT_DIRECTIONS.get(system_id, "支持相关身体系统功能恢复")


def _fit_plan_summary(pairs: list[tuple[str, str, str]]) -> str:
    for pair_count in range(len(pairs), 0, -1):
        candidate = _render_plan_summary(pairs[:pair_count])
        if len(candidate) <= _PLAN_SUMMARY_MAX_CHARS:
            return candidate

    _, direction, system_id = pairs[0]
    shortened = [(_system_problem_name(system_id), direction, system_id)]
    candidate = _render_plan_summary(shortened)
    if len(candidate) <= _PLAN_SUMMARY_MAX_CHARS:
        return candidate
    return _PLAN_SUMMARY_GENERIC


def _render_plan_summary(pairs: list[tuple[str, str, str]]) -> str:
    problem_text = "、".join(problem for problem, _, _ in pairs)
    directions = [direction for _, direction, _ in pairs]
    if len(pairs) == 1:
        return f"本方案针对{problem_text}，重点{directions[0]}。"
    count_label = "两类" if len(pairs) == 2 else "三类"
    if len(set(directions)) == 1:
        return f"本方案针对{problem_text}{count_label}问题，均{directions[0]}。"
    return f"本方案针对{problem_text}{count_label}问题，分别{'、'.join(directions)}。"


def _system_problem_name(system_id: str) -> str:
    return f"{SYSTEM_NAMES.get(system_id, '相关身体系统')}相关问题"


def _choose_primary_system(candidate_systems: Iterable[str], priority_rank: dict[str, int]) -> str | None:
    candidates = list(dict.fromkeys(system_id for system_id in candidate_systems if system_id in SYSTEM_NAMES))
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda system_id: (
            priority_rank.get(system_id, len(priority_rank) + _SYSTEM_ORDER.get(system_id, 999)),
            _SYSTEM_ORDER.get(system_id, 999),
        ),
    )


def _safe_problem_name(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    normalized = cleaned.lower()
    if "cd19" in normalized or "b细胞" in normalized:
        if "免疫炎症" in normalized:
            return "B细胞相关免疫炎症"
        return "B细胞相关免疫指标异常"
    cleaned = re.sub(r"[（(]?(?:偏高|偏低|升高|降低|阳性)[）)]?$", "", cleaned).strip("，。；： ")
    return cleaned


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip("；;。 ")


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()
