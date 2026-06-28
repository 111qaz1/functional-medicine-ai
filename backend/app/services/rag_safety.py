from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.models import ProductRule


CUSTOMER_RAG_PREFIX = "功能医学知识库（仅供参考）："


ACTIONABLE_DRUG_PATTERNS = (
    re.compile(
        r"(推荐|建议|可考虑|应当|需要).{0,20}(服用|使用|口服|给药|加用).{0,25}"
        r"(氟康唑|伊曲康唑|制霉菌素|特比萘芬|抗生素|华法林|二甲双胍|胰岛素注射)"
    ),
    re.compile(
        r"(氟康唑|伊曲康唑|制霉菌素|特比萘芬|抗生素|华法林|二甲双胍|胰岛素注射).{0,25}"
        r"(剂量|mg|毫克|每日|每天|口服|疗程|停药|加量|减量)"
    ),
)

HIGH_RISK_DRUG_TERMS = (
    "氟康唑",
    "伊曲康唑",
    "制霉菌素",
    "特比萘芬",
    "抗生素",
    "华法林",
    "二甲双胍",
    "胰岛素注射",
    "处方药",
)

ACTION_WORDS = ("推荐", "建议", "可考虑", "应当", "需要", "适合", "优先")
SUPPLEMENT_ACTION_WORDS = ("补充", "服用", "口服", "加用", "使用")
DOSING_WORDS = ("剂量", "每日", "每天", "每晚", "mg", "毫克", "微克", "iu", "粒", "片", "疗程")

CONTROLLED_SUPPLEMENT_TERMS = (
    "虫草素",
    "DHEA",
    "5-HTP",
    "褪黑素",
    "NAC",
    "谷胱甘肽",
    "硫辛酸",
    "小檗碱",
    "黄连素",
    "红曲",
    "银杏",
    "纳豆激酶",
    "奶蓟",
    "肌醇",
    "益生菌",
    "胶原蛋白",
    "肌酸",
    "胆碱",
    "牛磺酸",
    "谷氨酰胺",
    "精氨酸",
    "肉碱",
    "维生素A",
    "维生素D",
    "维生素E",
    "维生素C",
    "B族维生素",
    "叶酸",
    "镁",
    "锌",
    "硒",
    "碘",
    "铁",
    "钙",
    "鱼油",
    "Omega-3",
    "姜黄素",
)

PREGNANCY_CONFLICT_TERMS = ("主动排毒", "排毒", "禁食", "间歇性禁食", "生酮", "FMD", "高剂量")
ANTICOAGULANT_CONFLICT_TERMS = ("姜黄", "鱼油", "Omega-3", "银杏", "纳豆激酶", "维生素E")
SOURCE_LEAK_PATTERNS = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\.docx|\.pdf|ISBN|版权|页码|第\s*\d+\s*页", re.IGNORECASE),
)

TEXTBOOK_REF = r"[表图]\s*\d+(?:\s*[-－—–]\s*\d+)*"
TEXTBOOK_TITLE_WORDS = (
    "分型",
    "分类",
    "标准",
    "定义",
    "机制",
    "用量",
    "剂量",
    "特性",
    "指标",
    "方案",
    "流程",
    "列表",
)


def strip_textbook_internal_markers(text: str) -> str:
    """Remove textbook-only table/figure references while preserving clinical text."""

    cleaned = str(text or "")
    if not cleaned:
        return ""

    cleaned = re.sub(rf"[（(]\s*(?:见|详见|参见)?\s*{TEXTBOOK_REF}\s*[）)]", "", cleaned)
    cleaned = re.sub(rf"(?:见|详见|参见)\s*{TEXTBOOK_REF}", "", cleaned)
    cleaned = re.sub(rf"如\s*{TEXTBOOK_REF}\s*所示", "", cleaned)
    cleaned = re.sub(r"(?:如下|下|上)?[表图]\s*所示", "", cleaned)
    cleaned = re.sub(r"如下表所示|如下图所示|见下表|见下图|详见下表|详见下图", "", cleaned)
    cleaned = re.sub(r"(?:(?<=^)|(?<=[\s。；;，,]))续\s*表(?=$|[\s。；;，,])", "", cleaned)

    title_words = "|".join(TEXTBOOK_TITLE_WORDS)
    cleaned = re.sub(
        rf"(?:(?<=^)|(?<=[\s。；;，,])){TEXTBOOK_REF}"
        rf"[^\s。；;，,，。；:：]{{0,24}}(?:{title_words})[^\s。；;，,，。；:：]{{0,16}}",
        "",
        cleaned,
    )
    cleaned = re.sub(rf"(?:(?<=^)|(?<=[\s。；;，,])){TEXTBOOK_REF}(?=(?:如下|如上|所示|$|[\s。；;，,]))", "", cleaned)
    cleaned = re.sub(r"[（(]\s*[）)]", "", cleaned)
    cleaned = re.sub(r"[ \t\u3000]+", " ", cleaned)
    cleaned = re.sub(r"[ \t\u3000]*([，、。；：！？])[ \t\u3000]*", r"\1", cleaned)
    cleaned = re.sub(r"([，、；：])([。；])", r"\1", cleaned)
    cleaned = re.sub(r"([。；，、]){2,}", lambda match: match.group(1), cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(" ，。；")


@dataclass(frozen=True)
class SafeRagHit:
    chunk_id: str
    excerpt: str
    score: float
    source_kind: str
    topic_tags: list[str] = field(default_factory=list)
    needs_review: bool = False

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "excerpt": self.excerpt,
            "score": self.score,
            "source_kind": self.source_kind,
            "topic_tags": self.topic_tags,
            "needs_review": self.needs_review,
        }


@dataclass(frozen=True)
class RagSafetyRejection:
    chunk_id: str
    reason: str


class RagSafetyFilter:
    """Allow RAG only as conservative narrative context, never as a recommendation engine."""

    def __init__(self, products: list[ProductRule]) -> None:
        self.products = list(products)
        self.catalog_terms = self._build_catalog_terms(self.products)
        self.product_names = {
            self._normalize(product.display_name)
            for product in self.products
            if product.display_name and len(product.display_name.strip()) >= 2
        }

    def filter_hits(
        self,
        hits: list[Any],
        *,
        context: Any,
        red_flags: list[str],
        contraindications: list[str],
        max_hits: int = 5,
    ) -> tuple[list[SafeRagHit], list[RagSafetyRejection]]:
        if red_flags:
            return [], [RagSafetyRejection(chunk_id="rag", reason="skipped_due_to_red_flags")]

        safe_hits: list[SafeRagHit] = []
        rejections: list[RagSafetyRejection] = []
        for hit in hits:
            decision = self._screen_hit(hit, context=context, contraindications=contraindications)
            if isinstance(decision, RagSafetyRejection):
                rejections.append(decision)
                continue
            safe_hits.append(decision)
            if len(safe_hits) >= max_hits:
                break
        return safe_hits, rejections

    def _screen_hit(
        self,
        hit: Any,
        *,
        context: Any,
        contraindications: list[str],
    ) -> SafeRagHit | RagSafetyRejection:
        chunk_id = str(getattr(hit, "chunk_id", "unknown"))
        text = self._clean_text(str(getattr(hit, "text", "")))
        if not text:
            return RagSafetyRejection(chunk_id=chunk_id, reason="empty_text")
        language_reason = self._language_quality_reason(text)
        if language_reason:
            return RagSafetyRejection(chunk_id=chunk_id, reason=language_reason)
        if bool(getattr(hit, "needs_review", False)):
            return RagSafetyRejection(chunk_id=chunk_id, reason="source_marked_needs_review")
        if any(pattern.search(text) for pattern in SOURCE_LEAK_PATTERNS):
            return RagSafetyRejection(chunk_id=chunk_id, reason="source_or_page_leak")
        if any(term in text for term in HIGH_RISK_DRUG_TERMS):
            return RagSafetyRejection(chunk_id=chunk_id, reason="high_risk_drug_term")
        if any(pattern.search(text) for pattern in ACTIONABLE_DRUG_PATTERNS):
            return RagSafetyRejection(chunk_id=chunk_id, reason="actionable_drug_recommendation")
        if self._mentions_catalog_product(text):
            return RagSafetyRejection(chunk_id=chunk_id, reason="direct_product_reference")
        if self._outside_catalog_supplement_instruction(text):
            return RagSafetyRejection(chunk_id=chunk_id, reason="outside_catalog_supplement_instruction")
        conflict_reason = self._context_conflict_reason(text, context=context, contraindications=contraindications)
        if conflict_reason:
            return RagSafetyRejection(chunk_id=chunk_id, reason=conflict_reason)

        excerpt = self._safe_excerpt(text)
        if not excerpt:
            return RagSafetyRejection(chunk_id=chunk_id, reason="no_safe_excerpt")
        excerpt_language_reason = self._language_quality_reason(excerpt)
        if excerpt_language_reason:
            return RagSafetyRejection(chunk_id=chunk_id, reason=excerpt_language_reason)
        return SafeRagHit(
            chunk_id=chunk_id,
            excerpt=excerpt,
            score=round(float(getattr(hit, "score", 0.0) or 0.0), 6),
            source_kind=str(getattr(hit, "source_kind", "")),
            topic_tags=list(getattr(hit, "topic_tags", []) or []),
        )

    def _build_catalog_terms(self, products: list[ProductRule]) -> set[str]:
        terms: set[str] = set()
        for product in products:
            values = [
                product.display_name,
                product.category,
                product.formula_summary,
                *product.core_ingredients,
                *product.candidate_use_cases,
            ]
            for value in values:
                for term in self._term_variants(str(value or "")):
                    terms.add(self._normalize(term))
        return {term for term in terms if term}

    def _term_variants(self, value: str) -> set[str]:
        value = value.strip()
        if not value:
            return set()
        terms = {value}
        for token in re.findall(r"[A-Za-z0-9.+-]+|[\u4e00-\u9fff]{1,12}", value):
            if len(token) >= 2 or "\u4e00" <= token <= "\u9fff":
                terms.add(token)
        return terms

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"[\r\n\t|]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        return strip_textbook_internal_markers(text).strip()

    def _language_quality_reason(self, text: str) -> str | None:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return "empty_text"

        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", compact))
        latin_count = len(re.findall(r"[A-Za-z]", compact))
        stripped = text.strip()

        if re.search(
            r"\b(potassium|chloride|bilirubin|alkaline|phosphatase|prostate specific antigen)\b",
            text,
            re.IGNORECASE,
        ) and cjk_count < 20:
            return "english_lab_list_fragment"

        if re.match(r"^[a-z]{3,}\b", stripped) and cjk_count < 20:
            return "english_continuation_fragment"

        if latin_count >= 30 and cjk_count < 8:
            return "non_chinese_fragment"

        if latin_count > max(cjk_count * 4, 120) and cjk_count < 30:
            return "non_chinese_fragment"

        return None

    def _safe_excerpt(self, text: str) -> str:
        structured_excerpt = self._structured_excerpt(text)
        if structured_excerpt:
            return structured_excerpt

        sentences = [
            sentence.strip(" -—:：，,。；;")
            for sentence in re.split(r"(?<=[。！？；;])\s+|[。！？；;]", text)
            if sentence and sentence.strip()
        ]
        if not sentences:
            sentences = [text]

        for sentence in sentences[:8]:
            sentence = self._polish_sentence(sentence)
            sentence = strip_textbook_internal_markers(sentence)
            if len(sentence) < 18:
                continue
            if self._language_quality_reason(sentence):
                continue
            if len(sentence) > 180:
                sentence = sentence[:180].rstrip(" ，,；;")
            if any(term in sentence for term in HIGH_RISK_DRUG_TERMS):
                continue
            if any(pattern.search(sentence) for pattern in ACTIONABLE_DRUG_PATTERNS):
                continue
            if self._mentions_catalog_product(sentence):
                continue
            if self._outside_catalog_supplement_instruction(sentence):
                continue
            if any(word.lower() in sentence.lower() for word in DOSING_WORDS) and any(
                word in sentence for word in SUPPLEMENT_ACTION_WORDS
            ):
                continue
            return sentence
        return ""

    def _structured_excerpt(self, text: str) -> str:
        if "指标名" not in text:
            return ""

        fields: dict[str, str] = {}
        for raw_part in re.split(r"[；;]\s*", text):
            if ":" not in raw_part and "：" not in raw_part:
                continue
            key, value = re.split(r"[:：]", raw_part, maxsplit=1)
            key = key.strip()
            value = value.strip(" ，,。；;")
            if value in {"同上", "-", "—", "无"}:
                continue
            if key and value:
                fields[key] = value

        indicator = fields.get("指标名")
        meaning = fields.get("生理意义")
        upstream = fields.get("上游关联")
        downstream = fields.get("下游影响")
        if not indicator:
            return ""

        parts = []
        if meaning:
            parts.append(f"{indicator}可帮助理解{meaning}")
        else:
            parts.append(f"{indicator}需要结合症状、病史和相关指标综合观察")
        if upstream:
            upstream_items = [item.strip(" ，,") for item in re.split(r"[、,/]+", upstream) if item.strip()]
            if upstream_items:
                parts.append(f"并关注{ '、'.join(upstream_items[:4]) }等上游因素")
        if downstream:
            parts.append(f"后续解读可结合{downstream}")

        excerpt = "，".join(parts)
        excerpt = strip_textbook_internal_markers(excerpt)
        if len(excerpt) > 180:
            excerpt = excerpt[:180].rstrip(" ，,；;")
        if any(pattern.search(excerpt) for pattern in ACTIONABLE_DRUG_PATTERNS):
            return ""
        if self._outside_catalog_supplement_instruction(excerpt):
            return ""
        return excerpt

    def _polish_sentence(self, sentence: str) -> str:
        sentence = strip_textbook_internal_markers(sentence).strip(" -—:：，,。；;")
        if "促进甲状腺激素正常合成的因素" in sentence:
            return (
                "甲状腺激素合成与碘、酪氨酸、锌、硒及多种维生素等营养与抗氧化状态有关，"
                "解读时仍需结合甲状腺功能、抗体指标和个体耐受情况"
            )
        if "维生素 D 受体在甲状腺细胞高表达" in sentence or "维生素D受体在甲状腺细胞高表达" in sentence:
            return "维生素D状态可作为理解甲状腺免疫调节和整体恢复背景的参考，仍需结合复查趋势判断"
        if "改变生活方式" in sentence and "运动" in sentence and "久坐" in sentence:
            return "减少久坐并增加规律运动，可作为降低炎症负担和改善代谢恢复的生活方式参考"
        sentence = re.sub(r"^第[一二三四五六七八九十0-9]+[章节]\s*", "", sentence)
        sentence = re.sub(r"^[一二三四五六七八九十0-9]+\s*[、.．]\s*", "", sentence)
        sentence = re.sub(r"^[a-zA-Z]\)\s*", "", sentence)
        sentence = re.sub(r"^各成分的功效说明\s*[:：]\s*", "", sentence)
        sentence = re.sub(r"^功能医学研究\s*[:：]\s*", "", sentence)
        return sentence.strip(" -—:：，,。；;")

    def _mentions_catalog_product(self, text: str) -> bool:
        normalized = self._normalize(text)
        if "sku_" in normalized:
            return True
        return any(product_name and product_name in normalized for product_name in self.product_names)

    def _outside_catalog_supplement_instruction(self, text: str) -> bool:
        if not (
            any(action in text for action in ACTION_WORDS)
            and any(action in text for action in SUPPLEMENT_ACTION_WORDS)
        ):
            return False

        normalized_text = self._normalize(text)
        for term in CONTROLLED_SUPPLEMENT_TERMS:
            normalized_term = self._normalize(term)
            if normalized_term not in normalized_text:
                continue
            if not self._is_catalog_allowed(normalized_term):
                return True
        return False

    def _context_conflict_reason(self, text: str, *, context: Any, contraindications: list[str]) -> str | None:
        normalized = self._normalize(text)
        context_text = self._normalize(
            " ".join(
                [
                    *[str(item) for item in getattr(context, "medications", set()) or set()],
                    *[str(item) for item in getattr(context, "conditions", set()) or set()],
                    *[str(item) for item in getattr(context, "allergies", set()) or set()],
                    *[str(item) for item in contraindications],
                ]
            )
        )
        actionable = any(action in text for action in ACTION_WORDS)

        if bool(getattr(context, "pregnancy", False)) and actionable:
            if any(self._normalize(term) in normalized for term in PREGNANCY_CONFLICT_TERMS):
                return "pregnancy_context_conflict"

        if any(term in context_text for term in ("华法林", "warfarin", "抗凝")) and actionable:
            if any(self._normalize(term) in normalized for term in ANTICOAGULANT_CONFLICT_TERMS):
                return "anticoagulant_context_conflict"
        return None

    def _is_catalog_allowed(self, normalized_term: str) -> bool:
        return any(
            normalized_term in catalog_term or catalog_term in normalized_term
            for catalog_term in self.catalog_terms
        )

    def _normalize(self, value: str) -> str:
        return re.sub(r"\s+", "", value or "").lower()
