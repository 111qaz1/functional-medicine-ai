from __future__ import annotations

import re
from typing import Any, Callable


FOLLOW_UP_SECTION = "复查与随访计划"
SAFETY_SECTION = "安全警示"

FollowUpGenerator = Callable[[dict[str, Any]], dict[str, list[str]]]

_FOLLOW_UP_GROUPS = (
    ("2_3_months", "### 2-3个月复查"),
    ("six_months", "### 6个月复查"),
    ("priority_referrals", "### 专科转诊（必须执行）"),
)
_HIGH_RISK_CONDITION_TERMS = ("肿瘤", "癌", "肾衰", "肝硬化", "心肌梗死", "脑卒中")

_PUBLIC_RISK_REWRITES = {
    "未成年案例需要医生重点审核营养素种类与剂量。": "未成年人使用营养素时，应由医生结合年龄、体重和临床情况确认种类与剂量。",
    "孕期或哺乳期需要医生重点审核营养素种类与剂量。": "孕期或哺乳期使用任何营养素前，应由医生确认种类、剂量和使用时机。",
    "既往疾病提示需要医生重点审核产品适用性。": "已记录的重要既往疾病可能影响营养素适用性，应由原治疗团队或相关专科确认。",
    "当前用药与营养素之间可能存在相互作用，需要医生重点审核。": "已记录的特定处方药可能与营养素发生相互作用，开始方案前应由主治医生核对。",
    "空腹血糖达到重点关注阈值，营养素草案需结合临床情况审核。": "空腹血糖达到重点关注阈值，应优先完成临床评估；营养支持不能替代血糖相关诊疗。",
    "糖化血红蛋白达到重点关注阈值，营养素草案需结合临床情况审核。": "糖化血红蛋白达到重点关注阈值，应优先完成临床评估；营养支持不能替代血糖相关诊疗。",
    "炎症指标显著升高，营养素草案需结合急性风险审核。": "炎症指标显著升高时，应先排查感染或其他急性风险，再决定是否继续营养方案。",
    "肝功能指标明显异常，营养素草案需由医生重点审核。": "肝功能指标明显异常时，应优先完成医学评估；营养支持不能替代病因检查和治疗。",
}
_GENERIC_HISTORY_NOTICE = "既往疾病提示需要医生重点审核产品适用性。"


def build_report_closing_sections(
    *,
    case: Any,
    reviewed_findings: list[Any],
    recommended_items: list[Any],
    safety_decisions: list[Any],
    risk_notices: list[str],
    case_summary: list[str] | None = None,
    system_findings: list[Any] | None = None,
    report_guidance: list[str] | None = None,
    follow_up_generator: FollowUpGenerator | None = None,
) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    if follow_up_generator is not None:
        context = _build_follow_up_context(
            case=case,
            reviewed_findings=reviewed_findings,
            recommended_items=recommended_items,
            safety_decisions=safety_decisions,
            risk_notices=risk_notices,
            case_summary=case_summary or [],
            system_findings=system_findings or [],
            report_guidance=report_guidance or [],
        )
        try:
            generated = follow_up_generator(context)
        except Exception:
            generated = {}
        rendered = _render_follow_up_groups(generated)
        if rendered:
            sections[FOLLOW_UP_SECTION] = rendered

    sections[SAFETY_SECTION] = _build_safety_items(
        case=case,
        findings=reviewed_findings,
        recommended_items=recommended_items,
        safety_decisions=safety_decisions,
        risk_notices=risk_notices,
    )
    return sections


def _build_follow_up_context(
    *,
    case: Any,
    reviewed_findings: list[Any],
    recommended_items: list[Any],
    safety_decisions: list[Any],
    risk_notices: list[str],
    case_summary: list[str],
    system_findings: list[Any],
    report_guidance: list[str],
) -> dict[str, Any]:
    questionnaire = getattr(case, "questionnaire", None)
    selected_skus = {
        str(getattr(item, "sku_id", "") or "")
        for item in recommended_items
        if str(getattr(item, "sku_id", "") or "")
    }
    nutrient_safety = []
    for decision in safety_decisions:
        sku_id = str(getattr(decision, "sku_id", "") or "")
        if sku_id not in selected_skus:
            continue
        action = getattr(
            getattr(decision, "action", None),
            "value",
            getattr(decision, "action", ""),
        )
        message = str(getattr(decision, "message", "") or "").strip()
        if action in {"requires_review", "warn"} and message:
            nutrient_safety.append({"action": str(action), "message": message[:300]})

    return {
        "final_abnormal_findings": [
            _serialize_reviewed_finding(item) for item in reviewed_findings
        ],
        "confirmed_clinical_findings": [
            _serialize_confirmed_finding(item)
            for item in (getattr(case, "confirmed_clinical_findings", []) or [])
        ],
        "doctor_case_summary": [str(item).strip() for item in case_summary if str(item).strip()][:8],
        "clinical_summary_text": str(getattr(case, "clinical_summary_text", "") or "").strip()[:3000],
        "system_analysis": [_serialize_system_finding(item) for item in system_findings][:12],
        "original_report_guidance": [
            str(item).strip() for item in report_guidance if str(item).strip()
        ][:12],
        "questionnaire_risks": {
            "age": getattr(questionnaire, "age", None) if questionnaire else None,
            "pregnant_or_lactating": (
                getattr(questionnaire, "pregnant_or_lactating", None)
                if questionnaire
                else None
            ),
            "known_conditions": list(getattr(questionnaire, "known_conditions", []) or [])[:12],
            "medications": list(getattr(questionnaire, "medications", []) or [])[:12],
            "allergies": list(getattr(questionnaire, "allergies", []) or [])[:12],
            "chief_concerns": list(getattr(questionnaire, "chief_concerns", []) or [])[:12],
            "symptoms": list(getattr(questionnaire, "symptoms", []) or [])[:12],
            "goals": list(getattr(questionnaire, "goals", []) or [])[:12],
        },
        "final_nutrient_plan": [
            {
                "nutrient_name": str(getattr(item, "display_name", "") or "").strip(),
                "dosage": str(getattr(item, "dosage", "") or "").strip(),
                "dosage_regimen": _model_dump(getattr(item, "dosage_regimen", None)),
            }
            for item in recommended_items
            if str(getattr(item, "display_name", "") or "").strip()
        ],
        "nutrient_safety": nutrient_safety[:12],
        "confirmed_risk_notices": [
            str(item).strip() for item in risk_notices if str(item).strip()
        ][:12],
    }


def _serialize_reviewed_finding(finding: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(finding, "id", "") or ""),
        "name": str(getattr(finding, "name", "") or ""),
        "result_text": str(getattr(finding, "result_text", "") or ""),
        "raw_value": str(getattr(finding, "raw_value", "") or ""),
        "unit": str(getattr(finding, "unit", "") or ""),
        "reference_range": str(getattr(finding, "reference_range", "") or ""),
        "abnormal_flag": str(getattr(finding, "abnormal_flag", "") or ""),
        "interpretation": str(getattr(finding, "interpretation", "") or "")[:500],
        "source_file": str(getattr(finding, "source_file_name", "") or ""),
        "source_page": getattr(finding, "source_page", None),
        "source_text": str(getattr(finding, "source_text", "") or "")[:1000],
    }


def _serialize_confirmed_finding(finding: Any) -> dict[str, Any]:
    span = getattr(finding, "source_span", None)
    evidence_class = getattr(
        getattr(finding, "evidence_class", None),
        "value",
        getattr(finding, "evidence_class", ""),
    )
    return {
        "id": str(getattr(finding, "finding_id", "") or ""),
        "name": str(getattr(finding, "finding_name", "") or ""),
        "finding_code": str(getattr(finding, "finding_code", "") or ""),
        "abnormal_flag": str(getattr(finding, "abnormal_flag", "") or ""),
        "evidence_class": str(evidence_class or ""),
        "source_file": str(getattr(span, "file_name", "") or ""),
        "source_page": getattr(span, "page", None),
        "source_text": str(getattr(span, "snippet", "") or "")[:1000],
    }


def _serialize_system_finding(finding: Any) -> dict[str, Any]:
    if isinstance(finding, str):
        return {"summary": finding[:800]}
    return {
        "system_name": str(getattr(finding, "system_name", "") or ""),
        "priority_level": str(getattr(finding, "priority_level", "") or ""),
        "summary": str(getattr(finding, "summary", "") or "")[:800],
    }


def _model_dump(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value if isinstance(value, dict) else None


def _render_follow_up_groups(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    rendered: list[str] = []
    for group_id, heading in _FOLLOW_UP_GROUPS:
        raw_items = payload.get(group_id)
        if not isinstance(raw_items, list):
            continue
        items: list[str] = []
        for raw_item in raw_items[:8]:
            if not isinstance(raw_item, str):
                continue
            text = re.sub(r"\s+", " ", raw_item).strip(" -•·\t\r\n")
            if 8 <= len(text) <= 320:
                items.append(text)
        items = list(dict.fromkeys(items))
        if items:
            rendered.append(heading)
            rendered.extend(items)
    return rendered


def _build_safety_items(
    *,
    case: Any,
    findings: list[Any],
    recommended_items: list[Any],
    safety_decisions: list[Any],
    risk_notices: list[str],
) -> list[str]:
    items = [
        "本报告用于营养与生活方式支持，不能替代临床诊断和治疗；现有处方药不得自行停用、减量或调整服用时间。",
        "如出现胸痛、呼吸困难、持续高热、黑便或便血、明显水肿、严重头晕或晕厥、严重过敏反应，请暂停新增营养素并立即就医。",
        "如新增营养素后出现持续胃肠不适、皮疹、心悸或其他明显不耐受，请暂停新增项目并联系医生。",
    ]
    items.extend(
        _dynamic_safety_items(
            case=case,
            findings=findings,
            recommended_items=recommended_items,
            safety_decisions=safety_decisions,
            risk_notices=risk_notices,
        )[:6]
    )
    return list(dict.fromkeys(items))


def _dynamic_safety_items(
    *,
    case: Any,
    findings: list[Any],
    recommended_items: list[Any],
    safety_decisions: list[Any],
    risk_notices: list[str],
) -> list[str]:
    questionnaire = getattr(case, "questionnaire", None)
    conditions = list(getattr(questionnaire, "known_conditions", []) or []) if questionnaire else []
    high_risk_conditions = [
        str(condition).strip()
        for condition in conditions
        if str(condition).strip()
        and any(term.lower() in str(condition).lower() for term in _HIGH_RISK_CONDITION_TERMS)
    ]
    items = [
        _PUBLIC_RISK_REWRITES[text]
        for item in risk_notices
        if (text := str(item).strip()) in _PUBLIC_RISK_REWRITES
        and not (text == _GENERIC_HISTORY_NOTICE and high_risk_conditions)
    ]
    for condition in high_risk_conditions:
        items.append(f"已记录重要既往病史“{condition}”，营养方案应与原治疗团队确认后执行。")

    selected_skus = {str(getattr(item, "sku_id", "") or "") for item in recommended_items}
    display_names = {
        str(getattr(item, "sku_id", "") or ""): str(getattr(item, "display_name", "") or "")
        for item in recommended_items
    }
    for decision in safety_decisions:
        sku_id = str(getattr(decision, "sku_id", "") or "")
        action = getattr(getattr(decision, "action", None), "value", getattr(decision, "action", ""))
        message = str(getattr(decision, "message", "") or "").strip()
        if sku_id not in selected_skus or action != "requires_review" or not message:
            continue
        if "sku" in message.lower() or "规格" in message:
            continue
        product_name = display_names.get(sku_id)
        items.append(f"{product_name}：{message}" if product_name else message)

    items.extend(_threshold_safety_items(findings))
    return list(dict.fromkeys(items))


def _threshold_safety_items(findings: list[Any]) -> list[str]:
    items: list[str] = []
    for finding in findings:
        code = _normalize(str(getattr(finding, "marker_code", "") or ""))
        value = _numeric_value(finding)
        if value is None:
            continue
        if code == "fastingglucose" and value >= 7.0:
            items.append("空腹血糖达到重点关注阈值，应优先完成临床评估；营养支持不能替代血糖相关诊疗。")
        elif code == "hba1c" and value >= 6.5:
            items.append("糖化血红蛋白达到重点关注阈值，应优先完成临床评估；营养支持不能替代血糖相关诊疗。")
        elif code == "hscrp" and value >= 10:
            items.append("炎症指标显著升高时，应先排查感染或其他急性风险，再决定是否继续营养方案。")
        elif code == "alt" and value >= 120:
            items.append("肝功能指标明显异常时，应优先完成医学评估；营养支持不能替代病因检查和治疗。")
    return items


def _numeric_value(finding: Any) -> float | None:
    raw = str(getattr(finding, "raw_value", "") or getattr(finding, "result_text", "") or "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", raw.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u4dbf\u4e00-\u9fff]+", "", str(value or "").lower())
