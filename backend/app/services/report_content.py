from __future__ import annotations

import re
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
_SUMMARY_SUPPORT_DIRECTIONS = {
    "digestive_gut": "支持胃肠消化、黏膜屏障与肠道功能恢复",
    "liver_detox": "支持肝胆代谢、抗氧化与生物转化管理",
    "immune_inflammation": "支持免疫平衡、抗氧化与炎症管理",
    "endocrine_metabolic": "支持血糖、激素及整体代谢稳定",
    "cardiovascular": "支持血脂、循环与心血管风险管理",
    "respiratory": "支持呼吸系统管理并配合专科随访",
    "neuro_sleep": "支持睡眠节律、压力恢复与神经调节",
    "bone_muscle": "支持骨代谢、肌肉状态与活动能力",
    "urinary_renal": "配合肾脏及泌尿系统复查与安全管理",
    "reproductive_breast": "支持生殖激素节律并配合妇科或乳腺随访",
    "skin_mucosa": "支持皮肤黏膜屏障与恢复管理",
}


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


def build_plan_summary(
    structured_system_findings: Iterable[Any],
    recommended_items: Iterable[Any],
    finding_names_by_id: dict[str, str] | None = None,
    *,
    max_named_problems: int = 5,
) -> list[str]:
    structured = list(structured_system_findings or [])
    if not structured:
        return []

    priority_findings = [
        item
        for item in structured
        if str(getattr(item, "priority_level", "")) in {"最高优先级", "优先级高"}
    ]
    candidates = priority_findings or structured[:1]
    selected = candidates[:max_named_problems]
    finding_names = finding_names_by_id or {}

    problem_phrases: list[str] = []
    selected_system_ids: list[str] = []
    for finding in selected:
        system_id = str(getattr(finding, "system_id", "") or "")
        if not system_id or system_id in selected_system_ids:
            continue
        selected_system_ids.append(system_id)
        names = [
            _safe_problem_name(finding_names.get(str(finding_id), ""))
            for finding_id in getattr(finding, "finding_ids", []) or []
        ]
        names = list(dict.fromkeys(name for name in names if name))
        problem_phrases.append(
            "、".join(names[:2])
            or f"{SYSTEM_NAMES.get(system_id, '相关身体系统')}相关健康问题"
        )

    if not problem_phrases:
        return []

    recommendations = list(recommended_items or [])
    has_omitted_problems = len(candidates) > len(selected) or len(structured) > len(selected)
    problem_text = "、".join(problem_phrases)
    if has_omitted_problems:
        problem_text += "等相关问题"
    elif len(problem_phrases) == 1:
        problem_text += "这一重点问题"
    else:
        problem_text += "等重点问题"

    if not recommendations:
        return [
            f"本方案重点针对{problem_text}，当前以生活方式调整、必要复查及医生评估为主，"
            "并结合症状变化和复查趋势决定后续营养支持方向。"
        ]

    directions: list[str] = []
    for finding in selected:
        system_id = str(getattr(finding, "system_id", "") or "")
        finding_ids = {str(item) for item in getattr(finding, "finding_ids", []) or []}
        linked = any(
            system_id
            in set(
                str(value)
                for value in (
                    getattr(item, "covered_system_ids", [])
                    or [getattr(item, "primary_system_id", "")]
                )
                if str(value)
            )
            and (
                not finding_ids
                or not getattr(item, "matched_finding_ids", [])
                or bool(finding_ids.intersection(str(value) for value in getattr(item, "matched_finding_ids", []) or []))
            )
            for item in recommendations
        )
        direction = (
            _SUMMARY_SUPPORT_DIRECTIONS.get(system_id, "支持相关身体系统功能与整体恢复")
            if linked
            else "配合生活方式调整、复查与医生评估"
        )
        if direction not in directions:
            directions.append(direction)

    direction_text = "、".join(directions)
    return [
        f"本方案重点针对{problem_text}，主要从{direction_text}等方向开展首月支持，"
        "并结合症状变化、耐受情况及复查趋势评估后续调整。"
    ]


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
        return "B细胞相关免疫指标异常"
    cleaned = re.sub(r"[（(]?(?:偏高|偏低|升高|降低|阳性|异常)[）)]?$", "", cleaned).strip("，。；： ")
    return cleaned


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip("；;。 ")


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()
