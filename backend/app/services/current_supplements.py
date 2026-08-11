from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable

from app.domain.models import CurrentSupplement, DocumentAnalysisResult, ProductRule


_NEGATIVE_VALUES = {
    "无",
    "没有",
    "未服用",
    "未使用",
    "无营养补充剂",
    "无补充剂",
    "none",
    "no",
}
_TRAILING_USAGE_HINT = re.compile(
    r"(?:每日|每天|一日|每周|每次|睡前|餐前|餐后|随餐|空腹|"
    r"\d+(?:\.\d+)?\s*(?:mg|mcg|μg|ug|g|iu|粒|片|滴|次))",
    re.IGNORECASE,
)


def normalize_supplement_name(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", normalized)


def parse_supplement_use(value: str | None) -> list[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text or normalize_supplement_name(text) in {
        normalize_supplement_name(item) for item in _NEGATIVE_VALUES
    }:
        return []
    text = re.sub(r"^(?:当前|目前)?(?:正在)?(?:服用|使用)?(?:的)?(?:营养)?补充剂\s*[:：]\s*", "", text)
    candidates = re.split(r"[\n,，、;；]+", text)
    names: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        name = re.sub(r"^\s*(?:\d+[.、)]\s*|[-•·]\s*)", "", candidate).strip()
        name = re.sub(
            r"\s*[（(]([^）)]*)[）)]\s*$",
            lambda match: "" if _TRAILING_USAGE_HINT.search(match.group(1)) else match.group(0),
            name,
        ).strip()
        name = re.sub(
            r"\s+\d+(?:\.\d+)?\s*(?:mg|mcg|μg|ug|g|iu|粒|片|滴)(?:\s*/?\s*(?:日|天|次))?\s*$",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        key = normalize_supplement_name(name)
        if not key or key in {normalize_supplement_name(item) for item in _NEGATIVE_VALUES}:
            continue
        if key not in seen and len(name) <= 120:
            seen.add(key)
            names.append(name)
    return names


def collect_current_supplements(
    document_results: Iterable[DocumentAnalysisResult],
) -> list[CurrentSupplement]:
    merged: dict[str, CurrentSupplement] = {}
    for result in document_results:
        names = list(result.current_supplements)
        questionnaire = result.questionnaire or {}
        names.extend(parse_supplement_use(questionnaire.get("supplement_use")))
        for raw_name in names:
            for name in parse_supplement_use(raw_name):
                key = normalize_supplement_name(name)
                if not key:
                    continue
                existing = merged.get(key)
                if existing:
                    existing.source_file_ids = list(
                        dict.fromkeys([*existing.source_file_ids, result.file_id])
                    )
                    existing.source_file_names = list(
                        dict.fromkeys([*existing.source_file_names, result.file_name])
                    )
                    continue
                merged[key] = CurrentSupplement(
                    id=f"supplement_{uuid.uuid4().hex[:12]}",
                    name=name,
                    source_file_ids=[result.file_id],
                    source_file_names=[result.file_name],
                )
    return list(merged.values())


def product_overlap_notice(
    *,
    product: ProductRule,
    canonical_name: str,
    dosage_mapping: dict,
    current_supplements: Iterable[CurrentSupplement],
) -> str | None:
    aliases = {
        normalize_supplement_name(value)
        for value in (
            canonical_name,
            product.display_name,
            dosage_mapping.get("excel_product_name"),
            *(dosage_mapping.get("aliases") or []),
        )
        if isinstance(value, str) and value.strip()
    }
    matched = [item.name for item in current_supplements if normalize_supplement_name(item.name) in aliases]
    matched = list(dict.fromkeys(matched))
    if not matched:
        return None
    names = "、".join(f"“{name}”" for name in matched[:3])
    return f"患者当前已在服用{names}，与本次推荐可能重复，请核对后决定是否继续或调整。"
