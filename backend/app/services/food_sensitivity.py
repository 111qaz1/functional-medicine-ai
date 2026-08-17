from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from app.domain.models import (
    ChronicFoodSensitivityResult,
    EvidenceStatus,
    FoodSensitivityItem,
)


_FOOD_ASSAY_TERM = r"(?:食物特异性\s*)?igg(?:抗体)?"
_FOOD_REACTION_TERM = r"(?:慢性(?:食物)?(?:敏感|过敏)(?:反应)?|食物不耐受)"
_FOOD_RESULT_TERM = r"(?:检测(?:结果)?|结果)?"
_FOOD_ASSAY_DESCRIPTOR = (
    rf"(?:{_FOOD_ASSAY_TERM}(?:\s*{_FOOD_REACTION_TERM})?"
    rf"|{_FOOD_REACTION_TERM}\s*{_FOOD_ASSAY_TERM}){_FOOD_RESULT_TERM}"
)
_FOOD_ASSAY_PREFIX_PATTERN = re.compile(
    rf"^(?:{_FOOD_ASSAY_DESCRIPTOR})(?:[\s_\-:：/\\]+|(?=[\u4e00-\u9fff]))",
    re.IGNORECASE,
)
_FOOD_ASSAY_SUFFIX_PATTERN = re.compile(
    rf"(?:[\s_\-:：/\\]*(?:[（(]\s*)?{_FOOD_ASSAY_DESCRIPTOR}(?:\s*[）)])?)$",
    re.IGNORECASE,
)
_FOOD_IDENTITY_SEPARATOR_PATTERN = re.compile(r"[\s_\-:：/\\（）()\[\]【】]+")


def normalize_food_sensitivity_name(value: str) -> str:
    """Return the patient-facing food name without redundant assay labels."""

    original = re.sub(
        r"\s+",
        " ",
        unicodedata.normalize("NFKC", str(value or "")),
    ).strip(" ：:，,；;、。._-")
    if not original:
        return ""

    normalized = _FOOD_ASSAY_PREFIX_PATTERN.sub("", original).strip(
        " ：:，,；;、。._-"
    )
    normalized = _FOOD_ASSAY_SUFFIX_PATTERN.sub("", normalized).strip(
        " ：:，,；;、。._-"
    )
    return normalized or original


def food_sensitivity_identity(value: str) -> str:
    normalized = normalize_food_sensitivity_name(value)
    return _FOOD_IDENTITY_SEPARATOR_PATTERN.sub("", normalized).lower()


def dedupe_food_sensitivity_names(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = normalize_food_sensitivity_name(value)
        identity = food_sensitivity_identity(name)
        if not name or not identity or identity in seen:
            continue
        seen.add(identity)
        deduped.append(name)
    return deduped


def dedupe_food_sensitivity_items(
    items: Iterable[FoodSensitivityItem],
) -> tuple[list[FoodSensitivityItem], list[str]]:
    """Deduplicate food rows while preserving the most complete evidence."""

    normalized_items: dict[str, FoodSensitivityItem] = {}
    conflicted: set[str] = set()
    warnings: list[str] = []

    for item in items:
        name = normalize_food_sensitivity_name(item.name)
        identity = food_sensitivity_identity(name)
        if not name or not identity:
            continue
        candidate = item.model_copy(update={"name": name})
        existing = normalized_items.get(identity)
        if existing is None:
            normalized_items[identity] = candidate
            continue

        has_severity_conflict = (
            existing.severity != "ungraded"
            and candidate.severity != "ungraded"
            and existing.severity != candidate.severity
        )
        preferred = (
            candidate
            if _food_item_quality(candidate) > _food_item_quality(existing)
            else existing
        )
        if has_severity_conflict or identity in conflicted:
            conflicted.add(identity)
            preferred = preferred.model_copy(
                update={
                    "severity": "ungraded",
                    "grading_basis": "同一食物的重复记录等级不一致，需人工核对",
                }
            )
            warning = f"慢性食物敏感项目{name}存在重复且等级不一致，已留待确认。"
            if warning not in warnings:
                warnings.append(warning)
        normalized_items[identity] = preferred

    return list(normalized_items.values()), warnings


def normalize_chronic_food_sensitivity_result(
    food: ChronicFoodSensitivityResult,
) -> ChronicFoodSensitivityResult:
    """Normalize a stored result without mutating persisted historical data."""

    items, duplicate_warnings = dedupe_food_sensitivity_items(food.items)
    if not items:
        return food.model_copy(
            update={
                "mild_foods": dedupe_food_sensitivity_names(food.mild_foods),
                "moderate_foods": dedupe_food_sensitivity_names(
                    food.moderate_foods
                ),
                "high_foods": dedupe_food_sensitivity_names(food.high_foods),
            }
        )

    grouped_names = {
        severity: dedupe_food_sensitivity_names(
            item.name for item in items if item.severity == severity
        )
        for severity in ("mild", "moderate", "high")
    }
    warning_parts = [
        *([food.warning] if food.warning else []),
        *duplicate_warnings,
    ]
    return food.model_copy(
        update={
            "source_page": min(item.source_page for item in items),
            "mild_foods": grouped_names["mild"],
            "moderate_foods": grouped_names["moderate"],
            "high_foods": grouped_names["high"],
            "items": items,
            "valid": True,
            "warning": "；".join(dict.fromkeys(warning_parts)) or None,
        }
    )


def _food_item_quality(item: FoodSensitivityItem) -> tuple[int, int]:
    completeness = sum(
        bool(value)
        for value in (
            item.raw_value,
            item.unit,
            item.reference_range,
            item.grading_basis,
            item.reported_grade,
            item.reported_grade_meaning,
            item.severity != "ungraded",
            item.source_text,
        )
    )
    evidence_rank = {
        EvidenceStatus.verified_text: 2,
        EvidenceStatus.visual_model_only: 1,
        EvidenceStatus.needs_review: 0,
    }.get(item.evidence_status, 0)
    return completeness, evidence_rank
