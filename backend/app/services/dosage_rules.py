from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable


DOSAGE_SOURCE_VERSION = "client-dose-workbook-2026-07-v1"
_DOSE_SIGNAL_RE = re.compile(
    r"(每日|每周|隔日|单次|每次|加服|服用|\d+(?:\.\d+)?\s*粒|半粒|"
    r"\d+\s*[-–—至]\s*\d+\s*岁[：:])"
)
_INLINE_OPTION_RE = re.compile(
    r"^(?P<label>[^：:]{2,36})[：:]\s*(?P<instruction>.*(?:每日|每周|隔日|单次|每次|粒|半粒|同成人剂量).*)$"
)
_NUMBER_PREFIX_RE = re.compile(r"^\s*\d+\s*[、.．]\s*")
_PROMOTIONAL_TAIL_RE = re.compile(
    r"[，,；;](?:提前建立|减少|补充|长期维持|维持(?:基础|身体|细胞|肝|肠|胃|神经|皮肤|免疫|代谢|激素)|"
    r"强化(?:神经|免疫)|针对性|快速(?:镇痛|缓解|恢复)|辅助|提升|"
    r"优化|改善|预防|巩固|降低|加速|帮助|稳定情绪|修复神经|促进|清除|"
    r"同步改善|延缓衰老|MK-7).*$"
)
_GENERAL_TRIGGER_TERMS = {
    "日常",
    "基础",
    "养护",
    "预防",
    "支持",
    "强化",
    "调理",
    "改善",
    "需求",
    "人群",
    "专项",
    "特殊",
    "场景",
    "成人",
    "长期",
    "健康",
}
_EXPLICIT_EVENT_TERMS = (
    "急性",
    "突发",
    "剧烈",
    "短期重度",
    "酒后",
    "术后",
    "手术后",
    "感染恢复期",
    "感冒后",
    "流感后",
    "旅行",
    "水土不服",
    "倒时差",
    "病原暴露",
    "考试前",
)
_TIMING_TERMS = (
    "早餐后",
    "午餐后",
    "晚餐后",
    "早晚餐后",
    "餐前",
    "餐后",
    "随餐",
    "空腹",
    "睡前",
    "运动前",
    "早晨",
    "晚上",
)


@dataclass(frozen=True)
class DosageSelection:
    option: dict[str, Any]
    match_reasons: list[str]
    fallback_reason: str | None = None


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _clean_label(label: str) -> str:
    return _NUMBER_PREFIX_RE.sub("", label).strip().rstrip("：:")


def _clean_display_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip(" \n；;。")
    cleaned = re.sub(r"[（(](?:利用|用于|辅助)[^）)]*[）)]", "", cleaned)
    cleaned = _PROMOTIONAL_TAIL_RE.sub("", cleaned).strip(" \n；;。")
    cleaned = re.sub(r"[；;]\s*[；;]+", "；", cleaned)
    return f"{cleaned}。" if cleaned else ""


def _split_scenarios(dosage_text: str) -> list[tuple[str, str]]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in dosage_text.splitlines() if line.strip()]
    scenarios: list[tuple[str, list[str]]] = []
    current_label = ""
    current_instructions: list[str] = []

    def flush() -> None:
        nonlocal current_label, current_instructions
        if current_label and current_instructions:
            scenarios.append((current_label, current_instructions[:]))
        current_label = ""
        current_instructions = []

    for line in lines:
        inline = _INLINE_OPTION_RE.match(line)
        if inline:
            parent_label = current_label if current_label and not current_instructions else ""
            if current_instructions:
                flush()
            inline_label = _clean_label(inline.group("label"))
            scenarios.append(
                (
                    f"{parent_label} - {inline_label}" if parent_label else inline_label,
                    [inline.group("instruction").strip()],
                )
            )
            current_label = parent_label
            current_instructions = []
            continue
        if not _DOSE_SIGNAL_RE.search(line):
            flush()
            current_label = _clean_label(line)
            continue
        if not current_label:
            current_label = "标准用量"
        current_instructions.append(line)
    flush()

    deduplicated: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, instructions in scenarios:
        unique_instructions: list[str] = []
        for instruction in instructions:
            identity = _compact(instruction).strip("）)")
            if any(
                identity == existing or identity in existing or existing in identity
                for existing in (_compact(item).strip("）)") for item in unique_instructions)
            ):
                continue
            unique_instructions.append(instruction)
        display = _clean_display_text("；".join(unique_instructions))
        identity = (_compact(label), _compact(display))
        if not display or identity in seen:
            continue
        seen.add(identity)
        deduplicated.append((label, display))
    return deduplicated


def _extract_trigger_terms(label: str) -> list[str]:
    text = re.sub(r"[（）()]", "、", label)
    raw_terms = re.split(r"[/／、，,；;\s]+", text)
    terms: list[str] = []
    for raw in raw_terms:
        term = _NUMBER_PREFIX_RE.sub("", raw).strip()
        if len(term) < 2 or term in _GENERAL_TRIGGER_TERMS:
            continue
        if any(general == term for general in _GENERAL_TRIGGER_TERMS):
            continue
        terms.append(term)
        simplified = re.sub(r"(缓解|养护|支持|需求|控制|纠正|调理|维护)$", "", term).strip()
        if len(simplified) >= 2 and simplified != term:
            terms.append(simplified)
    return list(dict.fromkeys(terms))


def _marker_rules(sku_id: str, label: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    compact = _compact(label)
    ranges: list[dict[str, Any]] = []
    directions: list[dict[str, str]] = []
    if sku_id == "sku_vitamin_d3_k":
        if "<20" in compact or "＜20" in compact:
            ranges.append({"marker_code": "vitamin_d", "max_exclusive": 20})
        elif "20-30" in compact or "20–30" in compact:
            ranges.append({"marker_code": "vitamin_d", "min_inclusive": 20, "max_inclusive": 30})
    if sku_id == "sku_blood_sugar_complex":
        if "餐后血糖" in label:
            directions.append({"marker_code": "postprandial_glucose", "direction": "high"})
        elif "血糖偏高" in label or "代谢综合征" in label:
            directions.extend(
                [
                    {"marker_code": "fasting_glucose", "direction": "high"},
                    {"marker_code": "hba1c", "direction": "high"},
                ]
            )
    if "血脂偏高" in label:
        directions.extend(
            [
                {"marker_code": "total_cholesterol", "direction": "high"},
                {"marker_code": "ldl_c", "direction": "high"},
                {"marker_code": "triglycerides", "direction": "high"},
            ]
        )
    if "同型半胱氨酸" in label:
        directions.append({"marker_code": "homocysteine", "direction": "high"})
    if "缺锌" in label:
        directions.append({"marker_code": "zinc", "direction": "low"})
    return ranges, list({(item["marker_code"], item["direction"]): item for item in directions}.values())


def _regimen_from_text(text: str) -> dict[str, Any]:
    compact = text.replace(" ", "")
    quantity_matches = list(re.finditer(r"(\d+(?:\.\d+)?)\s*(?:[-–—至]\s*(\d+(?:\.\d+)?))?\s*粒", text))
    daily_match = re.search(r"每日\s*(\d+(?:\.\d+)?)\s*(?:[-–—至]\s*(\d+(?:\.\d+)?))?\s*粒", text)
    daily_max_match = re.search(r"每日(?:不超过|最多)\s*(\d+(?:\.\d+)?)\s*粒", text)
    frequency_match = re.search(r"分\s*(\d+(?:\.\d+)?)\s*(?:[-–—至]\s*(\d+(?:\.\d+)?))?\s*次", text)
    per_dose_match = re.search(
        r"(?:每次|各|单次(?:服用)?|单次可服用)\s*(\d+(?:\.\d+)?)\s*(?:[-–—至]\s*(\d+(?:\.\d+)?))?\s*粒",
        text,
    )
    weekly_match = re.search(r"每周\s*(\d+(?:\.\d+)?)\s*(?:[-–—至]\s*(\d+(?:\.\d+)?))?\s*粒", text)
    interval_match = re.search(r"间隔\s*(\d+(?:\.\d+)?)\s*(?:[-–—至]\s*(\d+(?:\.\d+)?))?\s*小时", text)
    duration_match = re.search(
        r"(连续(?:服用)?[^；。]{0,20}?(?:天|周|个月|月|经周期)|长期(?:坚持|使用|服用)?|按需使用)",
        text,
    )
    maintenance_match = re.search(r"([^；。]*(?:减至|调整为|达标后|受孕后|维持)[^；。]*)", text)

    daily_frequency_min = daily_frequency_max = None
    if frequency_match:
        daily_frequency_min = float(frequency_match.group(1))
        daily_frequency_max = float(frequency_match.group(2) or frequency_match.group(1))
    elif "早晚餐后各" in compact or "早晚两次" in compact:
        daily_frequency_min = daily_frequency_max = 2.0
    elif daily_match:
        daily_frequency_min = daily_frequency_max = 1.0

    if per_dose_match:
        single_min = float(per_dose_match.group(1))
        single_max = float(per_dose_match.group(2) or per_dose_match.group(1))
    elif daily_match and daily_frequency_min and daily_frequency_max:
        daily_minimum = float(daily_match.group(1))
        daily_maximum = float(daily_match.group(2) or daily_match.group(1))
        single_min = daily_minimum / daily_frequency_max
        single_max = daily_maximum / daily_frequency_min
    else:
        single_min = float(quantity_matches[0].group(1)) if quantity_matches else None
        single_max = float(quantity_matches[0].group(2) or quantity_matches[0].group(1)) if quantity_matches else None

    daily_max = None
    if daily_max_match:
        daily_max = float(daily_max_match.group(1))
    elif daily_match:
        daily_max = float(daily_match.group(2) or daily_match.group(1))
    elif "早晚餐后各1粒" in compact:
        daily_max = 2.0
    if single_max is not None and (daily_match or "当日" in text):
        daily_max = max(daily_max or 0.0, single_max)

    return {
        "unit": "粒",
        "single_dose_min": single_min,
        "single_dose_max": single_max,
        "daily_frequency_min": daily_frequency_min,
        "daily_frequency_max": daily_frequency_max,
        "weekly_frequency_min": float(weekly_match.group(1)) if weekly_match else None,
        "weekly_frequency_max": float(weekly_match.group(2) or weekly_match.group(1)) if weekly_match else None,
        "timing": [term for term in _TIMING_TERMS if term in text],
        "interval_hours_min": float(interval_match.group(1)) if interval_match else None,
        "interval_hours_max": float(interval_match.group(2) or interval_match.group(1)) if interval_match else None,
        "daily_max": daily_max,
        "duration": duration_match.group(1).strip() if duration_match else None,
        "maintenance": maintenance_match.group(1).strip() if maintenance_match else None,
    }


def _priority_for(label: str, regimen: dict[str, Any]) -> int:
    if any(term in label for term in _EXPLICIT_EVENT_TERMS):
        base = 100
    elif any(term in label for term in ("强化", "明显", "重度", "纠正", "中度", "偏高", "功能波动")):
        base = 80
    elif any(term in label for term in ("日常", "基础", "轻度", "预防", "成人基础")):
        base = 10
    else:
        base = 50
    return base + min(int(regimen.get("daily_max") or regimen.get("single_dose_max") or 0), 9)


def _is_default_label(label: str) -> bool:
    return any(term in label for term in ("日常", "基础", "轻度", "预防", "成人基础", "健康人群"))


def parse_dosage_options(
    sku_id: str,
    dosage_text: str,
    *,
    source_row: int,
    source_version: str = DOSAGE_SOURCE_VERSION,
) -> list[dict[str, Any]]:
    scenarios = _split_scenarios(dosage_text)
    if not scenarios:
        raise ValueError(f"{sku_id} 未能从剂量来源解析出有效档位")

    options: list[dict[str, Any]] = []
    for label, display_text in scenarios:
        regimen = _regimen_from_text(display_text)
        marker_ranges, marker_directions = _marker_rules(sku_id, label)
        trigger_terms = _extract_trigger_terms(label)
        explicit_terms = [term for term in _EXPLICIT_EVENT_TERMS if term in label]
        if sku_id == "sku_amino_acid_detox":
            if "酒后" in label or "术后" in label:
                explicit_terms = ["酒后", "术后", "手术后"]
            elif "强化" in label:
                trigger_terms.extend(["饮酒", "酒精伤肝", "毒素暴露", "免疫低下", "肠道黏膜损伤"])
        if sku_id == "sku_magnesium_glycinate":
            if "助眠" in label:
                trigger_terms.extend(["睡眠", "助眠", "入睡", "失眠"])
            if "痉挛" in label or "肌肉" in label:
                trigger_terms.extend(["肌肉紧张", "肌肉痉挛", "痉挛", "抽筋", "肌肉僵硬"])
        trigger_terms = list(dict.fromkeys(trigger_terms))
        age_range = re.search(r"(\d+)\s*[-–—至]\s*(\d+)\s*岁", label)
        sex = (
            "male"
            if "男性" in label and "女性" not in label and "男女" not in label
            else "female"
            if "女性" in label and "男性" not in label and "男女" not in label
            else None
        )
        requires_explicit_event = bool(explicit_terms)
        option_id = f"{sku_id}:dose_{hashlib.sha1(_compact(label).encode('utf-8')).hexdigest()[:10]}"
        options.append(
            {
                "option_id": option_id,
                "label": label,
                "enabled": True,
                "is_default": False,
                "priority": _priority_for(label, regimen),
                "triggers": {
                    "marker_ranges": marker_ranges,
                    "marker_directions": marker_directions,
                    "finding_terms": trigger_terms,
                    "symptom_terms": trigger_terms,
                    "condition_terms": trigger_terms,
                    "chief_concern_terms": trigger_terms,
                    "goal_terms": trigger_terms,
                    "lifestyle_terms": trigger_terms,
                    "event_terms": list(dict.fromkeys(explicit_terms)),
                    "age_min": (
                        int(age_range.group(1))
                        if age_range
                        else 65
                        if "老年" in label or "中老年" in label or "高龄" in label
                        else 11
                        if "11 岁以上" in label or "11岁以上" in label
                        else None
                    ),
                    "age_max": int(age_range.group(2)) if age_range else 17 if "儿童" in label else None,
                    "sex": sex,
                    "requires_explicit_event": requires_explicit_event,
                },
                "regimen": regimen,
                "display_text": display_text,
                "requires_review": bool(
                    re.search(r"(医师|医生|医嘱|儿童|受孕后)", display_text + label)
                    or (sku_id == "sku_zinc_complex" and age_range)
                ),
                "source": {
                    "sheet": "单粒泡罩（中文）",
                    "row": source_row,
                    "version": source_version,
                },
            }
        )

    defaults = [index for index, option in enumerate(options) if _is_default_label(option["label"])]
    default_index = min(defaults, key=lambda index: options[index]["priority"]) if defaults else 0
    options[default_index]["is_default"] = True
    return options


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _contains_term(values: list[str], term: str, normalize: Callable[[str], str]) -> bool:
    needle = normalize(term)
    return bool(needle) and any(needle in normalize(value) for value in values)


def _marker_items(context: Any, marker_code: str) -> list[Any]:
    aliases = {
        "vitamin_d": ("vitamin_d", "25_oh_vitamin_d", "25ohd"),
        "postprandial_glucose": ("postprandial_glucose", "glucose_2h", "ogtt_2h"),
    }
    codes = aliases.get(marker_code, (marker_code,))
    markers = getattr(context, "markers_by_code", {}) or {}
    return [item for code in codes for item in markers.get(code, [])]


def _match_marker_range(context: Any, rule: dict[str, Any]) -> str | None:
    marker_code = str(rule.get("marker_code") or "")
    for item in _marker_items(context, marker_code):
        value = getattr(item, "normalized_value", None)
        value = value if value is not None else getattr(item, "value", None)
        if value is None:
            continue
        if rule.get("min_inclusive") is not None and value < float(rule["min_inclusive"]):
            continue
        if rule.get("max_inclusive") is not None and value > float(rule["max_inclusive"]):
            continue
        if rule.get("max_exclusive") is not None and value >= float(rule["max_exclusive"]):
            continue
        return f"明确化验：{marker_code}={value:g}"
    return None


def _match_marker_direction(context: Any, rule: dict[str, Any]) -> str | None:
    marker_code = str(rule.get("marker_code") or "")
    expected = str(rule.get("direction") or "").lower()
    for item in _marker_items(context, marker_code):
        actual = _enum_value(getattr(item, "abnormal_flag", ""))
        if actual == expected:
            return f"明确化验：{marker_code} {expected}"
    return None


def _option_matches(option: dict[str, Any], context: Any, normalize: Callable[[str], str]) -> tuple[int, list[str]]:
    triggers = option.get("triggers") if isinstance(option.get("triggers"), dict) else {}
    reasons: list[str] = []
    specificity = 0

    marker_ranges = triggers.get("marker_ranges", [])
    marker_range_matched = False
    for rule in marker_ranges:
        reason = _match_marker_range(context, rule)
        if reason:
            reasons.append(reason)
            specificity = max(specificity, 4)
            marker_range_matched = True
    if marker_ranges and not marker_range_matched:
        return 0, []
    for rule in triggers.get("marker_directions", []):
        reason = _match_marker_direction(context, rule)
        if reason:
            reasons.append(reason)
            specificity = max(specificity, 4)

    findings = [
        str(value)
        for finding in getattr(context, "clinical_findings", []) or []
        for value in (getattr(finding, "finding_code", None), getattr(finding, "finding_name", None))
        if value
    ]
    reviewed_summary = str(getattr(context, "clinical_summary_text", "") or "").strip()
    if reviewed_summary:
        findings.append(reviewed_summary)
    buckets = (
        ("明确诊断/发现", findings + list(getattr(context, "conditions", set()) or []), "condition_terms", 4),
        ("明确症状", list(getattr(context, "symptoms", set()) or []), "symptom_terms", 3),
        (
            "主诉",
            list(getattr(context, "chief_concerns", set()) or []),
            "chief_concern_terms",
            2,
        ),
        (
            "支持目标",
            [
                *list(getattr(context, "goals", set()) or []),
                *list((getattr(context, "support_goal_findings", {}) or {}).keys()),
            ],
            "goal_terms",
            2,
        ),
        ("生活场景", list(getattr(context, "lifestyle_tags", set()) or []), "lifestyle_terms", 1),
    )
    for label, values, key, weight in buckets:
        for term in triggers.get(key, []):
            if _contains_term(values, str(term), normalize):
                reasons.append(f"{label}：{term}")
                specificity = max(specificity, weight)
                break

    event_terms = [str(term) for term in triggers.get("event_terms", []) if str(term).strip()]
    event_values = [
        *findings,
        *list(getattr(context, "conditions", set()) or []),
        *list(getattr(context, "symptoms", set()) or []),
        *list(getattr(context, "chief_concerns", set()) or []),
        str(getattr(context, "clinical_summary_text", "") or ""),
    ]
    explicit_event = next(
        (term for term in event_terms if _contains_term(event_values, term, normalize)),
        None,
    )
    if explicit_event:
        reasons.append(f"明确特殊事件：{explicit_event}")
        specificity = max(specificity, 3)
    if triggers.get("requires_explicit_event") and not explicit_event:
        return 0, []

    age = getattr(context, "age", None)
    age_min = triggers.get("age_min")
    age_max = triggers.get("age_max")
    if age_min is not None and age is not None and age >= int(age_min):
        reasons.append(f"年龄：{age} 岁")
        specificity = max(specificity, 3)
    if age_max is not None and age is not None and age <= int(age_max):
        reasons.append(f"年龄：{age} 岁")
        specificity = max(specificity, 3)
    expected_sex = str(triggers.get("sex") or "").strip().lower()
    if expected_sex:
        actual_sex = normalize(str(getattr(context, "sex", "") or ""))
        aliases = {
            "male": {"male", "m", normalize("男性"), normalize("男")},
            "female": {"female", "f", normalize("女性"), normalize("女")},
        }
        if actual_sex not in aliases.get(expected_sex, {expected_sex}):
            return 0, []
        reasons.append(f"性别：{expected_sex}")
        specificity = max(specificity, 3)

    return specificity, list(dict.fromkeys(reasons))


def select_dosage_option(
    product_mapping: dict[str, Any],
    context: Any,
    normalize: Callable[[str], str],
) -> DosageSelection:
    options = [
        option
        for option in product_mapping.get("dose_options", [])
        if isinstance(option, dict) and option.get("enabled", True)
    ]
    if not options:
        raise ValueError(f"{product_mapping.get('sku_id', 'unknown')} 没有启用的剂量档位")

    matches: list[tuple[int, int, int, dict[str, Any], list[str]]] = []
    for index, option in enumerate(options):
        specificity, reasons = _option_matches(option, context, normalize)
        if reasons:
            matches.append((int(option.get("priority") or 0), specificity, -index, option, reasons))
    if matches:
        _, _, _, option, reasons = max(matches, key=lambda item: item[:3])
        return DosageSelection(option=option, match_reasons=reasons)

    default = next((option for option in options if option.get("is_default")), options[0])
    return DosageSelection(
        option=default,
        match_reasons=[],
        fallback_reason="病例中未发现可触发更高档位的明确事实，使用默认基础档。",
    )
